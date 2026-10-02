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
    """Matches per word, keyed by the word as written in the word list."""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


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
    only' wins over 'internal'. Terms that are nothing but '*' are dropped:
    they would match every word."""
    terms: dict[str, str] = {}
    for word in words:
        word = " ".join(word.split())
        if word.replace("*", "").strip():
            terms.setdefault(_fold(word)[0].casefold(), word)
    return sorted(terms.values(), key=len, reverse=True)


def build_pattern(words: Iterable[str]) -> Optional[re.Pattern]:
    """One whole-word, case- and accent-insensitive regex for the whole list
    (to be run on _fold()ed text), or None if the list has nothing in it.
    '*' in a term stands for any run of letters. Capture group N is the
    N-th entry of _terms(words)."""
    alternatives = []
    for term in _terms(words):
        parts = [
            r"\w*".join(re.escape(piece) for piece in part.split("*"))
            for part in _fold(term)[0].split()
        ]
        alternatives.append("(%s)" % r"\s+".join(parts))
    if not alternatives:
        return None
    return re.compile(
        r"(?<!\w)(?:%s)(?!\w)" % "|".join(alternatives), re.IGNORECASE
    )


def flag_document(
    src: str, dst: str, words: Iterable[str], colour: str = "yellow"
) -> FlagResult:
    """Write a copy of `src` to `dst` with every match highlighted."""
    words = list(words)
    pattern = build_pattern(words)
    if pattern is None:
        raise ValueError("Η λίστα λέξεων είναι κενή.")
    if colour not in HIGHLIGHT_COLOURS:
        raise ValueError(f"Το '{colour}' δεν είναι χρώμα επισήμανσης του Word.")

    try:
        pkg = Package.load(src)
    except zipfile.BadZipFile as exc:
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.") from exc
    if not pkg.has_part(DOCUMENT_PART):
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.")

    terms = _terms(words)
    result = FlagResult()
    for part_name in list(pkg.parts):
        if not _TEXT_PART_RE.match(part_name):
            continue
        root = pkg.get_xml(part_name)
        for paragraph in list(root.iter(W_P)):
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


def _flag_paragraph(
    paragraph: etree._Element,
    pattern: re.Pattern,
    colour: str,
    terms: list[str],
    result: FlagResult,
) -> None:
    run_atoms = []  # (run, [(child, start, end), ...])
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

    text = "".join(buffer)
    folded, origin = _fold(text)
    intervals = []
    matched = []
    for match in pattern.finditer(folded):
        # Map back to the real text; the end also takes in any accents that
        # trail the last matched letter.
        end = origin[match.end()] if match.end() < len(origin) else len(text)
        intervals.append((origin[match.start()], end))
        matched.append(terms[match.lastindex - 1])
    if not intervals:
        return

    # Text boxes are stored twice (a DrawingML version and a VML fallback);
    # highlight both, but count the match once.
    if not any(a.tag == MC_FALLBACK for a in paragraph.iterancestors()):
        for word in matched:
            result.counts[word] = result.counts.get(word, 0) + 1

    for run, atoms in run_atoms:
        start, end = atoms[0][1], atoms[-1][2]
        if any(a < end and b > start for a, b in intervals):
            _split_run(run, atoms, intervals, colour)


def _cut(start: int, end: int, intervals: list[tuple[int, int]]):
    """Split [start, end) into (start, end, is_match) pieces."""
    pos = start
    for a, b in intervals:
        if b <= pos:
            continue
        if a >= end:
            break
        if a > pos:
            yield pos, a, False
            pos = a
        stop = min(b, end)
        yield pos, stop, True
        pos = stop
    if pos < end:
        yield pos, end, False


def _split_run(run, atoms, intervals, colour: str) -> None:
    """Replace `run` with consecutive runs carrying the same formatting, so
    that matched text sits in runs of its own, and highlight those."""
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
            for a, b, flag in _cut(span[0], span[1], intervals):
                piece = etree.Element(W_T, nsmap=run.nsmap)
                piece.set(XML_SPACE, "preserve")
                piece.text = text[a - span[0]: b - span[0]]
                emit(piece, flag)
        else:
            emit(child, next(_cut(span[0], span[1], intervals))[2])

    new_runs = []
    for flag, elements in groups:
        new_run = etree.Element(run.tag, attrib=dict(run.attrib), nsmap=run.nsmap)
        if rpr is not None:
            new_run.append(copy.deepcopy(rpr))
        new_run.extend(elements)
        if flag:
            _set_highlight(new_run, colour)
        new_runs.append(new_run)
    new_runs[-1].tail = run.tail

    parent = run.getparent()
    index = parent.index(run)
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
