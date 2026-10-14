"""Build OcrResults with synthetic geometry (fixtures for the stub engine and tests)."""
from __future__ import annotations

from docflag.compare.ocr.base import OcrLine, OcrResult, OcrWord

CHAR_W = 10.0
SPACE_W = 8.0
LINE_H = 20.0
PITCH = 28.0


def _words_at(text: str, x: float, y: float) -> list[OcrWord]:
    words = []
    for token in text.split():
        w = CHAR_W * len(token)
        words.append(OcrWord(token, x, y, w, LINE_H))
        x += w + SPACE_W
    return words


def paragraph_lines(lines: list[str], x: float = 20.0, y: float = 20.0, gaps_before: tuple[int, ...] = ()) -> OcrResult:
    """Lines of running text; ``gaps_before`` lists line indexes preceded by a blank line."""
    out = []
    for i, text in enumerate(lines):
        if i in gaps_before:
            y += PITCH
        words = _words_at(text, x, y)
        out.append(OcrLine(text, words))
        y += PITCH
    return OcrResult(out)


def table(rows: list[list[str]], col_x: list[float], y: float = 20.0) -> OcrResult:
    """A table: each cell starts at ``col_x[c]``; one OCR line per cell-run (as Windows OCR may do)."""
    out = []
    for cells in rows:
        for text, x in zip(cells, col_x):
            if text:
                out.append(OcrLine(text, _words_at(text, x, y)))
        y += PITCH
    return OcrResult(out)


def combine(*parts: OcrResult) -> OcrResult:
    """Stack results vertically (parts are built starting at y=20), with a gap between them."""
    lines: list[OcrLine] = []
    bottom = 0.0
    for part in parts:
        shift = bottom + PITCH - 20.0 if lines else 0.0
        for ln in part.lines:
            lines.append(OcrLine(ln.text, [OcrWord(w.text, w.x, w.y + shift, w.width, w.height) for w in ln.words]))
        bottom = max(w.bottom for ln in lines for w in ln.words)
    return OcrResult(lines)
