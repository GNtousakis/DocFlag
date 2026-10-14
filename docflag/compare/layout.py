"""Rebuild structured text (paragraphs, table rows, columns) from OCR word boxes.

Pipeline: words -> rows (vertical clustering) -> segments (horizontal gaps) ->
blocks of consecutive rows sharing vertical gutters -> table rows ("a | b | c") or
multi-column prose -> paragraph merging for plain lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median

from docflag.compare.ocr.base import OcrResult, OcrWord

CELL_SEP = " | "
#: A horizontal gap wider than this many median word heights separates cells/columns.
GAP_FACTOR = 1.5
#: Blocks whose cells hold at least this many words (median) are prose columns, not a table.
PROSE_WORDS_PER_CELL = 8
#: Vertical gap (in word heights) above which a new paragraph starts.
PARAGRAPH_GAP = 0.7
#: A line shorter than this fraction of the paragraph width ends a paragraph.
SHORT_LINE_FRACTION = 0.75
_LIST_START = re.compile(r"^(\d+[.)]|[a-zA-Zα-ωΑ-Ω][.)]|[•·▪◦\-–—*])\s")
_HYPHENS = "-­‐"


@dataclass
class _Row:
    words: list[OcrWord]

    @property
    def y0(self) -> float:
        return min(w.y for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.right for w in self.words)


@dataclass
class _Line:
    text: str
    x0: float
    x1: float
    y0: float
    y1: float
    is_row: bool = False  # table row: never merged
    group: int = 0  # lines of one prose column share a group (own left/right margins)


def group_rows(words: list[OcrWord]) -> list[_Row]:
    rows: list[list[OcrWord]] = []
    centers: list[float] = []
    for w in sorted(words, key=lambda w: w.center_y):
        if rows:
            row_h = median(x.height for x in rows[-1])
            if abs(w.center_y - centers[-1]) <= 0.5 * row_h:
                rows[-1].append(w)
                centers[-1] = sum(x.center_y for x in rows[-1]) / len(rows[-1])
                continue
        rows.append([w])
        centers.append(w.center_y)
    return [_Row(sorted(r, key=lambda w: w.x)) for r in rows]


def segments(row: _Row, gap: float) -> list[list[OcrWord]]:
    segs: list[list[OcrWord]] = [[row.words[0]]]
    for w in row.words[1:]:
        if w.x - segs[-1][-1].right > gap:
            segs.append([w])
        else:
            segs[-1].append(w)
    return segs


def _gutters(rows: list[_Row], gap: float) -> list[float]:
    """x positions (gap midpoints) of vertical whitespace bands free of words in all rows."""
    spans = sorted((w.x, w.right) for r in rows for w in r.words)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] < gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(merged[i][1] + merged[i + 1][0]) / 2 for i in range(len(merged) - 1)]


def _crosses(row: _Row, gutters: list[float]) -> bool:
    return any(w.x < g < w.right for w in row.words for g in gutters)


def _find_blocks(rows: list[_Row], gap: float) -> list[tuple[int, int]]:
    """Index ranges (inclusive) of consecutive rows that share column gutters."""
    multi = [len(segments(r, gap)) >= 2 for r in rows]
    runs: list[list[int]] = []
    i = 0
    while i < len(rows):
        if multi[i]:
            j = i
            while j + 1 < len(rows) and multi[j + 1]:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    merged: list[list[int]] = []
    for run in runs:
        if merged and run[0] - merged[-1][1] - 1 <= 2:
            prev = merged[-1]
            core = [r for k, r in enumerate(rows[prev[0] : run[1] + 1], prev[0]) if multi[k]]
            gutters = _gutters(core, gap)
            between = rows[prev[1] + 1 : run[0]]
            if gutters and not any(_crosses(r, gutters) for r in between):
                prev[1] = run[1]
                continue
        merged.append(run)
    return [(a, b) for a, b in merged if b - a + 1 >= 2]


def _cell_text(words: list[OcrWord]) -> str:
    return " ".join(w.text for w in sorted(words, key=lambda w: w.x))


def _block_lines(block: list[_Row], gap: float, group_base: int) -> list[_Line] | None:
    gutters = _gutters(block, gap)
    if not gutters:
        return None
    ncols = len(gutters) + 1
    table: list[list[list[OcrWord]]] = []
    for r in block:
        cells: list[list[OcrWord]] = [[] for _ in range(ncols)]
        for w in r.words:
            col = sum(1 for g in gutters if w.center_x > g)
            cells[col].append(w)
        table.append(cells)
    filled = [len(c) for cells in table for c in cells if c]
    if filled and median(filled) >= PROSE_WORDS_PER_CELL:
        # multi-column prose: read column by column
        out: list[_Line] = []
        for c in range(ncols):
            for r, cells in zip(block, table):
                if cells[c]:
                    ws = cells[c]
                    out.append(_Line(_cell_text(ws), min(w.x for w in ws), max(w.right for w in ws), min(w.y for w in ws), max(w.bottom for w in ws), group=group_base + c))
        return out
    return [
        _Line(
            CELL_SEP.join(_cell_text(c) for c in cells),
            r.x0, r.x1, r.y0, r.y1, is_row=True,
        )
        for r, cells in zip(block, table)
    ]


def _plain_line(row: _Row) -> _Line:
    return _Line(_cell_text(row.words), row.x0, row.x1, row.y0, row.y1)


def _starts_new_paragraph(prev: _Line, cur: _Line, left: float, right: float, h: float) -> bool:
    if prev.is_row or cur.is_row or prev.group != cur.group:
        return True
    if cur.y0 - prev.y1 > PARAGRAPH_GAP * h:
        return True
    if prev.x1 < left + SHORT_LINE_FRACTION * (right - left):
        return True
    if cur.x0 > left + 1.5 * h:
        return True
    return bool(_LIST_START.match(cur.text))


def merge_paragraphs(lines: list[_Line], h: float) -> list[str]:
    if not lines:
        return []
    margins: dict[int, tuple[float, float]] = {}
    for g in {ln.group for ln in lines}:
        grp = [ln for ln in lines if ln.group == g and not ln.is_row]
        margins[g] = (min((ln.x0 for ln in grp), default=0.0), max((ln.x1 for ln in grp), default=0.0))
    out: list[str] = []
    prev: _Line | None = None
    for ln in lines:
        if prev is not None and not _starts_new_paragraph(prev, ln, *margins[ln.group], h):
            cur = out[-1]
            if cur[-1:] in _HYPHENS and len(cur) > 1 and cur[-2].isalpha() and ln.text[:1].islower():
                out[-1] = cur[:-1] + ln.text
            else:
                out[-1] = cur + " " + ln.text
        else:
            out.append(ln.text)
        prev = ln
    return out


def reconstruct_text(result: OcrResult) -> str:
    """Structured text for an OCR result: one paragraph or table row per line."""
    words = [w for w in result.words if w.text.strip()]
    if not words:
        return "\n".join(ln.text for ln in result.lines if ln.text.strip())
    h = median(w.height for w in words) or 1.0
    gap = GAP_FACTOR * h
    rows = group_rows(words)
    in_block: dict[int, list[_Line]] = {}
    block_start: dict[int, int] = {}
    for n, (a, b) in enumerate(_find_blocks(rows, gap), start=1):
        lines = _block_lines(rows[a : b + 1], gap, group_base=n * 100)
        if lines is not None:
            block_start[a] = b
            in_block[a] = lines
    lines_out: list[_Line] = []
    i = 0
    while i < len(rows):
        if i in in_block:
            lines_out += in_block[i]
            i = block_start[i] + 1
        else:
            lines_out.append(_plain_line(rows[i]))
            i += 1
    return "\n".join(merge_paragraphs(lines_out, h))
