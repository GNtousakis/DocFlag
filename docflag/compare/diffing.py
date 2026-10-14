"""Word-level and table-aware comparison."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from docflag.compare.normalize import CompareOptions, nfc, normalize_word

EQUAL, ADDED, REMOVED, CHANGED = "equal", "added", "removed", "changed"
CELL_SEP = " | "


@dataclass(frozen=True)
class Segment:
    text: str
    kind: str = EQUAL


@dataclass
class DiffStats:
    added: int = 0
    removed: int = 0
    changed: int = 0
    matched: int = 0  # compared words present in both texts
    words_a: int = 0  # compared words per side (punctuation-only tokens excluded)
    words_b: int = 0

    @property
    def total(self) -> int:
        return self.added + self.removed + self.changed

    @property
    def similarity(self) -> float:
        """0..100, ``2 * matched / (words_a + words_b)`` (the SequenceMatcher ratio)."""
        n = self.words_a + self.words_b
        return 100.0 if n == 0 else 100.0 * 2 * self.matched / n

    def add(self, other: "DiffStats") -> None:
        self.added += other.added
        self.removed += other.removed
        self.changed += other.changed
        self.matched += other.matched
        self.words_a += other.words_a
        self.words_b += other.words_b


@dataclass
class DiffResult:
    left: list[Segment] = field(default_factory=list)
    right: list[Segment] = field(default_factory=list)
    stats: DiffStats = field(default_factory=DiffStats)
    structured: bool = False  # True when compared row by row


@dataclass(frozen=True)
class Token:
    text: str
    key: str | None  # None => not compared (pure punctuation / separator)
    start: int
    end: int


_WORD = re.compile(r"\S+")


def tokenize(text: str, options: CompareOptions) -> list[Token]:
    out = []
    for m in _WORD.finditer(text):
        raw = m.group()
        key = None if raw == "|" else (normalize_word(raw, options) or None)
        out.append(Token(raw, key, m.start(), m.end()))
    return out


def is_table_row(line: str) -> bool:
    return CELL_SEP in line


def _matcher(a: list[str], b: list[str]) -> SequenceMatcher:
    return SequenceMatcher(None, a, b, autojunk=False)


def _render(text: str, tokens: list[Token], kinds: dict[int, str]) -> list[Segment]:
    """Build segments covering all of ``text``; ``kinds`` maps token index -> kind."""
    segs: list[Segment] = []

    def push(t: str, kind: str) -> None:
        if not t:
            return
        if segs and segs[-1].kind == kind:
            segs[-1] = Segment(segs[-1].text + t, kind)
        else:
            segs.append(Segment(t, kind))

    pos = 0
    prev_kind: str | None = None
    for i, tok in enumerate(tokens):
        gap = text[pos : tok.start]
        kind = kinds.get(i, EQUAL)
        if gap:
            gap_kind = prev_kind if (prev_kind and prev_kind == kind and kind != EQUAL and "\n" not in gap) else EQUAL
            push(gap, gap_kind)
        push(tok.text, kind)
        pos = tok.end
        prev_kind = kind
    push(text[pos:], EQUAL)
    return segs


def diff_words(a: str, b: str, options: CompareOptions = CompareOptions()) -> DiffResult:
    """Word-level diff of two texts, ignoring whitespace/line breaks (and case/punctuation per options)."""
    a, b = nfc(a), nfc(b)
    ta, tb = tokenize(a, options), tokenize(b, options)
    ia = [i for i, t in enumerate(ta) if t.key is not None]
    ib = [i for i, t in enumerate(tb) if t.key is not None]
    ka = [ta[i].key for i in ia]
    kb = [tb[i].key for i in ib]
    kinds_a: dict[int, str] = {}
    kinds_b: dict[int, str] = {}
    stats = DiffStats(words_a=len(ka), words_b=len(kb))
    for tag, i1, i2, j1, j2 in _matcher(ka, kb).get_opcodes():
        if tag == "equal":
            stats.matched += i2 - i1
            continue
        if tag == "delete":
            stats.removed += 1
            for i in range(i1, i2):
                kinds_a[ia[i]] = REMOVED
        elif tag == "insert":
            stats.added += 1
            for j in range(j1, j2):
                kinds_b[ib[j]] = ADDED
        else:
            stats.changed += 1
            for i in range(i1, i2):
                kinds_a[ia[i]] = CHANGED
            for j in range(j1, j2):
                kinds_b[ib[j]] = CHANGED
    return DiffResult(_render(a, ta, kinds_a), _render(b, tb, kinds_b), stats)


def _line_keys(line: str, options: CompareOptions) -> list[str]:
    return [t.key for t in tokenize(line, options) if t.key is not None]


def _similarity(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 1.0
    return _matcher(a, b).ratio()


def _align_lines(la: list[list[str]], lb: list[list[str]], threshold: float = 0.3) -> list[tuple[int | None, int | None]]:
    """Monotonic alignment of two small blocks of lines by similarity (Needleman-Wunsch)."""
    m, n = len(la), len(lb)
    if m == n:
        return [(i, i) for i in range(m)]
    sim = [[_similarity(la[i], lb[j]) for j in range(n)] for i in range(m)]
    score = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            if sim[i][j] >= threshold:
                best = max(best, score[i + 1][j + 1] + sim[i][j])
            score[i][j] = best
    pairs: list[tuple[int | None, int | None]] = []
    i = j = 0
    while i < m and j < n:
        if sim[i][j] >= threshold and score[i][j] == score[i + 1][j + 1] + sim[i][j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif score[i][j] == score[i + 1][j]:
            pairs.append((i, None))
            i += 1
        else:
            pairs.append((None, j))
            j += 1
    pairs += [(k, None) for k in range(i, m)]
    pairs += [(None, k) for k in range(j, n)]
    return pairs


def _whole_line(line: str, kind: str) -> list[Segment]:
    return [Segment(line, kind)] if line else []


def _diff_line_pair(a: str, b: str, options: CompareOptions) -> DiffResult:
    """Diff two lines; table rows with equal cell counts are compared cell by cell."""
    if is_table_row(a) and is_table_row(b):
        ca, cb = a.split(CELL_SEP), b.split(CELL_SEP)
        if len(ca) == len(cb):
            res = DiffResult(structured=True)
            for k, (x, y) in enumerate(zip(ca, cb)):
                if k:
                    res.left.append(Segment(CELL_SEP))
                    res.right.append(Segment(CELL_SEP))
                sub = diff_words(x, y, options)
                res.left += sub.left
                res.right += sub.right
                res.stats.add(sub.stats)
            return res
    return diff_words(a, b, options)


def _structured_diff(a: str, b: str, options: CompareOptions) -> DiffResult:
    la = [ln.strip() for ln in nfc(a).splitlines() if ln.strip()]
    lb = [ln.strip() for ln in nfc(b).splitlines() if ln.strip()]
    ka = [_line_keys(x, options) for x in la]
    kb = [_line_keys(x, options) for x in lb]
    res = DiffResult(structured=True)

    def emit(i: int | None, j: int | None, pair_res: DiffResult | None = None) -> None:
        if pair_res is not None:
            res.left += pair_res.left
            res.right += pair_res.right
            res.stats.add(pair_res.stats)
        elif i is not None:
            res.left += _whole_line(la[i], REMOVED)
            res.stats.removed += 1
            res.stats.words_a += len(ka[i])
        elif j is not None:
            res.right += _whole_line(lb[j], ADDED)
            res.stats.added += 1
            res.stats.words_b += len(kb[j])
        # newline handling: each side gets a newline only for lines it actually has
        if i is not None or pair_res is not None:
            res.left.append(Segment("\n"))
        if j is not None or pair_res is not None:
            res.right.append(Segment("\n"))

    for tag, i1, i2, j1, j2 in _matcher(["\x00".join(k) for k in ka], ["\x00".join(k) for k in kb]).get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                same = DiffStats(matched=len(ka[i]), words_a=len(ka[i]), words_b=len(kb[j]))
                emit(i, j, DiffResult(_whole_line(la[i], EQUAL), _whole_line(lb[j], EQUAL), same))
            continue
        pairs = _align_lines(ka[i1:i2], kb[j1:j2])
        for pi, pj in pairs:
            if pi is not None and pj is not None:
                emit(i1 + pi, j1 + pj, _diff_line_pair(la[i1 + pi], lb[j1 + pj], options))
            else:
                emit(None if pi is None else i1 + pi, None if pj is None else j1 + pj)
    for side in (res.left, res.right):
        if side and side[-1].text == "\n":
            side.pop()
    return res


def diff_texts(a: str, b: str, options: CompareOptions = CompareOptions()) -> DiffResult:
    """Compare two texts. Row-by-row when both contain table rows, otherwise word-level."""
    if any(is_table_row(x) for x in a.splitlines()) and any(is_table_row(x) for x in b.splitlines()):
        return _structured_diff(a, b, options)
    return diff_words(a, b, options)
