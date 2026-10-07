"""Highlighting engine: find every occurrence of a word list in a .docx and
mark it with Word's native highlighter, leaving everything else untouched.

Word stores a paragraph's text as a sequence of "runs" (w:r), and splits a
run wherever formatting, spell-check state or editing history changes -- so
a single visible word is often spread over several runs. Matching is
therefore done on the paragraph's joined text, and the runs a match touches
are then split at the match boundaries so only the match gets highlighted.
"""
from __future__ import annotations

import copy
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

from lxml import etree

from .package_io import Package
from .xml_utils import qn

# The only colours Word's highlighter supports (ST_HighlightColor).
HIGHLIGHT_COLOURS = [
    "yellow", "green", "cyan", "magenta", "red", "blue", "lightGray",
    "darkYellow", "darkGreen", "darkCyan", "darkMagenta", "darkRed",
    "darkBlue", "darkGray",
]

DOCUMENT_PART = "word/document.xml"
_TEXT_PART_RE = re.compile(
    r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$"
)

W_P = qn("w:p")
W_R = qn("w:r")
W_T = qn("w:t")
W_RPR = qn("w:rPr")
W_HIGHLIGHT = qn("w:highlight")
W_VAL = qn("w:val")
MC_FALLBACK = qn("mc:Fallback")
W_PREFIX = W_P[: -len("p")]
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Run children that are not w:t but still separate words on the page.
_BREAKS = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n"}

# Inline wrappers whose runs belong to the enclosing paragraph's text.
# Deliberately excludes w:del / w:moveFrom (tracked-deleted text).
_RUN_CONTAINERS = {
    qn(t) for t in (
        "w:hyperlink", "w:ins", "w:moveTo", "w:smartTag", "w:fldSimple",
        "w:sdt", "w:sdtContent", "w:customXml", "w:dir", "w:bdo",
    )
}

# w:rPr children that the schema requires to come *after* w:highlight.
_AFTER_HIGHLIGHT = {
    qn(t) for t in (
        "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign",
        "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
        "w:specVanish", "w:oMath", "w:rPrChange",
    )
}


@dataclass
class FlagResult:
    """Matches per word, keyed by the word as written in the word list, and
    replacements made, keyed by the (source, target) pair."""

    counts: dict[str, int] = field(default_factory=dict)
    replacements: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def total_replacements(self) -> int:
        return sum(self.replacements.values())


def normalise_term(word: str) -> str:
    """Collapse every run of whitespace to one space. Spaces at the start or
    end are kept: they are part of the term (' .' is not '.')."""
    return re.sub(r"\s+", " ", word)


def _fold(text: str) -> tuple[str, list[int]]:
    """Strip accents (so 'ΕΜΠΙΣΤΕΥΤΙΚΟ' can match 'εμπιστευτικό'). Returns the
    folded text and, for each folded character, its index in `text`."""
    folded = []
    origin = []
    for index, char in enumerate(text):
        for base in unicodedata.normalize("NFD", char):
            if not unicodedata.combining(base):
                folded.append(base)
                origin.append(index)
    return "".join(folded), origin


def _terms(words: Iterable[str]) -> list[str]:
    """The list's distinct terms as written, longest first so 'internal use
    only' wins over 'internal'. Terms that are nothing but '*' and spaces are
    dropped: they would match every word or every space."""
    terms: dict[str, str] = {}
    for word in words:
        word = normalise_term(word)
        if word.replace("*", "").strip():
            terms.setdefault(_fold(word)[0].casefold(), word)
    return sorted(terms.values(), key=len, reverse=True)


def _compile(terms: list[str], wildcards: bool, space: str) -> Optional[re.Pattern]:
    """One case-insensitive regex for `terms` (to be run on _fold()ed text),
    capture group N being terms[N-1]. A space in a term matches any run of
    `space`. An end of a term that is a letter must be a word boundary in
    the text; an end that is punctuation or a space need not be, so 'τηλ.'
    also matches in 'τηλ.2101234'."""
    alternatives = []
    for term in terms:
        folded = _fold(term)[0]
        body = "".join(
            space if piece == " "
            else r"\w*".join(re.escape(p) for p in piece.split("*")) if wildcards
            else re.escape(piece)
            for piece in re.split(r"( )", folded) if piece
        )
        edge = r"[\w*]" if wildcards else r"\w"
        if re.match(edge, folded):
            body = r"(?<!\w)" + body
        if re.search(edge + "$", folded):
            body += r"(?!\w)"
        alternatives.append("(%s)" % body)
    if not alternatives:
        return None
    return re.compile("|".join(alternatives), re.IGNORECASE)


def build_pattern(words: Iterable[str]) -> Optional[re.Pattern]:
    """One whole-word, case- and accent-insensitive regex for the whole list
    (to be run on _fold()ed text), or None if the list has nothing in it.
    '*' in a term stands for any run of letters. Capture group N is the
    N-th entry of _terms(words)."""
    return _compile(_terms(words), wildcards=True, space=r"\s+")


def build_replace_pattern(sources: Iterable[str]) -> Optional[re.Pattern]:
    """Like build_pattern, but '*' is an ordinary character (a wildcard
    would replace whole families of words with one fixed text), and spaces
    never match a tab or line break, which cannot be rewritten as text."""
    return _compile(_terms(sources), wildcards=False, space=r"[^\S\t\n]+")


def _upper(text: str) -> str:
    """Upper case; Greek capitals drop the tonos (ΤΗΛΕΦΩΝΟ, not ΤΗΛΈΦΩΝΟ)."""
    text = unicodedata.normalize("NFD", text.upper())
    text = re.sub(r"([\u0370-\u03ff][\u0300-\u036f]*?)\u0301", r"\1", text)
    return unicodedata.normalize("NFC", text)


def match_case(found: str, replacement: str) -> str:
    """Raise the replacement to the case of the text it replaces: 'ΤΗΛ.'
    gives 'ΤΗΛΕΦΩΝΟ', 'Τηλ.' gives 'Τηλέφωνο'. It is never lowered, so a
    replacement written in capitals stays that way."""
    letters = [c for c in found if c.isalpha()]
    if not letters or not letters[0].isupper():
        return replacement
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return _upper(replacement)
    for i, char in enumerate(replacement):
        if char.isalpha():
            return replacement[:i] + char.upper() + replacement[i + 1:]
    return replacement


def flag_document(
    src: str,
    dst: str,
    words: Iterable[str],
    colour: str = "yellow",
    replacements: Iterable[tuple[str, str]] = (),
    replace_colour: Optional[str] = None,
) -> FlagResult:
    """Write a copy of `src` to `dst` with every (source, target) pair in
    `replacements` applied, then every match of `words` highlighted in
    `colour`. Replaced text is highlighted in `replace_colour`, if given."""
    words = list(words)
    targets: dict[str, tuple[str, str]] = {}
    for source, target in replacements:
        targets.setdefault(_fold(normalise_term(source))[0].casefold(), (source, target))
    pattern = build_pattern(words)
    replace_pattern = build_replace_pattern(source for source, _ in targets.values())
    if pattern is None and replace_pattern is None:
        raise ValueError("Οι λίστες λέξεων και αντικαταστάσεων είναι κενές.")
    for c in (colour, replace_colour):
        if c is not None and c not in HIGHLIGHT_COLOURS:
            raise ValueError(f"Το '{c}' δεν είναι χρώμα επισήμανσης του Word.")

    try:
        pkg = Package.load(src)
    except zipfile.BadZipFile as exc:
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.") from exc
    if not pkg.has_part(DOCUMENT_PART):
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.")

    terms = _terms(words)
    pairs = [
        targets[_fold(term)[0].casefold()]
        for term in _terms(source for source, _ in targets.values())
    ]
    result = FlagResult()
    for part_name in list(pkg.parts):
        if not _TEXT_PART_RE.match(part_name):
            continue
        root = pkg.get_xml(part_name)
        for paragraph in list(root.iter(W_P)):
            if replace_pattern is not None:
                _replace_paragraph(paragraph, replace_pattern, replace_colour, pairs, result)
            if pattern is not None:
                _flag_paragraph(paragraph, pattern, colour, terms, result)
    pkg.save(dst)
    return result


def _own_runs(container: etree._Element) -> Iterator[etree._Element]:
    """Runs that make up this paragraph's own text, in order -- not those of
    paragraphs nested inside it (text boxes), which are visited separately."""
    for child in container:
        if child.tag == W_R:
            yield child
        elif child.tag in _RUN_CONTAINERS:
            yield from _own_runs(child)


def _paragraph_text(paragraph: etree._Element):
    """The paragraph's text, and its runs with the span of the text each of
    their children holds: [(run, [(child, start, end), ...]), ...]."""
    run_atoms = []
    buffer = []
    pos = 0
    for run in _own_runs(paragraph):
        atoms = []
        for child in run:
            if child.tag == W_T:
                text = child.text or ""
            elif child.tag in _BREAKS:
                text = _BREAKS[child.tag]
            else:
                continue
            buffer.append(text)
            atoms.append((child, pos, pos + len(text)))
            pos += len(text)
        if atoms:
            run_atoms.append((run, atoms))
    return "".join(buffer), run_atoms


def _find(pattern: re.Pattern, text: str) -> list[tuple[int, int, int]]:
    """(start, end, term index) of every match in the real text."""
    folded, origin = _fold(text)
    found = []
    for match in pattern.finditer(folded):
        # Map back to the real text; the end also takes in any accents that
        # trail the last matched letter.
        end = origin[match.end()] if match.end() < len(origin) else len(text)
        found.append((origin[match.start()], end, match.lastindex - 1))
    return found


def _counts_once(paragraph: etree._Element) -> bool:
    """Text boxes are stored twice (a DrawingML version and a VML fallback);
    both get changed, but only the first one counts."""
    return not any(a.tag == MC_FALLBACK for a in paragraph.iterancestors())


def _rewrite(run_atoms, intervals, colour, texts=None) -> None:
    """Split every run that `intervals` touch (see _split_run)."""
    for run, atoms in run_atoms:
        start, end = atoms[0][1], atoms[-1][2]
        if any(a < end and b > start for a, b in intervals):
            _split_run(run, atoms, intervals, colour, texts)


def _flag_paragraph(
    paragraph: etree._Element,
    pattern: re.Pattern,
    colour: str,
    terms: list[str],
    result: FlagResult,
) -> None:
    text, run_atoms = _paragraph_text(paragraph)
    found = _find(pattern, text)
    if not found:
        return
    if _counts_once(paragraph):
        for _, _, index in found:
            word = terms[index]
            result.counts[word] = result.counts.get(word, 0) + 1
    _rewrite(run_atoms, [(a, b) for a, b, _ in found], colour)


def _replace_paragraph(
    paragraph: etree._Element,
    pattern: re.Pattern,
    colour: Optional[str],
    pairs: list[tuple[str, str]],
    result: FlagResult,
) -> None:
    text, run_atoms = _paragraph_text(paragraph)
    found = _find(pattern, text)
    if not found:
        return
    if _counts_once(paragraph):
        for _, _, index in found:
            pair = pairs[index]
            result.replacements[pair] = result.replacements.get(pair, 0) + 1
    texts = [match_case(text[a:b], pairs[index][1]) for a, b, index in found]
    _rewrite(run_atoms, [(a, b) for a, b, _ in found], colour, texts)


def _cut(start: int, end: int, intervals: list[tuple[int, int]]):
    """Split [start, end) into (start, end, i) pieces, i being the index of
    the interval the piece lies in, or None between intervals."""
    pos = start
    for i, (a, b) in enumerate(intervals):
        if b <= pos:
            continue
        if a >= end:
            break
        if a > pos:
            yield pos, a, None
            pos = a
        stop = min(b, end)
        yield pos, stop, i
        pos = stop
    if pos < end:
        yield pos, end, None


def _split_run(run, atoms, intervals, colour: Optional[str], texts=None) -> None:
    """Replace `run` with consecutive runs carrying the same formatting, so
    that matched text sits in runs of its own, and highlight those (unless
    `colour` is None). With `texts`, interval i's text becomes texts[i]:
    written where the match starts, the rest of the match is dropped (it
    may go on into the following runs)."""
    spans = {id(child): (start, end) for child, start, end in atoms}
    rpr = run.find(W_RPR)

    groups = []  # [is_match | None, [elements]]

    def emit(element, flag) -> None:
        if groups and (flag is None or groups[-1][0] in (None, flag)):
            if groups[-1][0] is None:
                groups[-1][0] = flag
            groups[-1][1].append(element)
        else:
            groups.append([flag, [element]])

    for child in list(run):
        if child.tag == W_RPR:
            continue
        span = spans.get(id(child))
        if span is None or span[0] == span[1]:
            emit(child, None)
        elif child.tag == W_T:
            text = child.text
            for a, b, i in _cut(span[0], span[1], intervals):
                piece_text = text[a - span[0]: b - span[0]]
                if texts is not None and i is not None:
                    piece_text = texts[i] if a == intervals[i][0] else ""
                    if not piece_text:
                        continue
                piece = etree.Element(W_T, nsmap=run.nsmap)
                piece.set(XML_SPACE, "preserve")
                piece.text = piece_text
                emit(piece, i is not None)
        else:
            emit(child, next(_cut(span[0], span[1], intervals))[2] is not None)

    new_runs = []
    for flag, elements in groups:
        new_run = etree.Element(run.tag, attrib=dict(run.attrib), nsmap=run.nsmap)
        if rpr is not None:
            new_run.append(copy.deepcopy(rpr))
        new_run.extend(elements)
        if flag and colour is not None:
            _set_highlight(new_run, colour)
        new_runs.append(new_run)

    parent = run.getparent()
    index = parent.index(run)
    if new_runs:
        new_runs[-1].tail = run.tail
    parent.remove(run)
    for offset, new_run in enumerate(new_runs):
        parent.insert(index + offset, new_run)


def _set_highlight(run: etree._Element, colour: str) -> None:
    rpr = run.find(W_RPR)
    if rpr is None:
        rpr = etree.Element(W_RPR, nsmap=run.nsmap)
        run.insert(0, rpr)
    for existing in rpr.findall(W_HIGHLIGHT):
        rpr.remove(existing)
    highlight = etree.Element(W_HIGHLIGHT, nsmap=run.nsmap)
    highlight.set(W_VAL, colour)
    # w:rPr children have a fixed order; Word rejects files that break it.
    for index, child in enumerate(rpr):
        if child.tag in _AFTER_HIGHLIGHT or not child.tag.startswith(W_PREFIX):
            rpr.insert(index, highlight)
            return
    rpr.append(highlight)
