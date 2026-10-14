"""Stub OCR engine: returns canned results, for development and tests off Windows."""
from __future__ import annotations

import json
from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from docflag.compare.ocr.base import OcrEngine, OcrLine, OcrResult, OcrWord

if TYPE_CHECKING:  # pragma: no cover
    from PIL.Image import Image

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def result_from_dict(data: dict) -> OcrResult:
    lines = []
    for ln in data["lines"]:
        words = [OcrWord(w["text"], w["x"], w["y"], w["w"], w["h"]) for w in ln["words"]]
        lines.append(OcrLine(text=ln.get("text") or " ".join(w.text for w in words), words=words))
    return OcrResult(lines=lines, language=data.get("language"))


def load_fixture(name: str) -> OcrResult:
    """Load ``fixtures/<name>.json`` as an OcrResult."""
    path = FIXTURE_DIR / f"{name}.json"
    return result_from_dict(json.loads(path.read_text(encoding="utf-8")))


class StubOcrEngine(OcrEngine):
    """Returns the given fixture(s) regardless of the image.

    ``fixtures`` may be a single :class:`OcrResult`, a fixture name, or an iterable
    of either; with several, they are returned in turn (cycling).
    """

    name = "stub"
    is_real = False

    def __init__(self, fixtures: OcrResult | str | Iterable[OcrResult | str] = ("table_v1",)):
        if isinstance(fixtures, (OcrResult, str)):
            fixtures = [fixtures]
        self._items = list(fixtures)
        self._cycle = cycle(self._items)
        self.calls = 0

    def recognize(self, image: "Image") -> OcrResult:
        self.calls += 1
        item = next(self._cycle)
        return load_fixture(item) if isinstance(item, str) else item
