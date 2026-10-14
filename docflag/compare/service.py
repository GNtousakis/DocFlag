"""What the web page needs from the comparison code: one OCR engine shared by
everyone using the page, and the comparisons themselves. Everything here
blocks, so the page calls it through nicegui's run.io_bound.
"""
from __future__ import annotations

import io
import math
import os
import threading

from PIL import Image, UnidentifiedImageError

from docflag.compare.diffing import DiffResult, diff_texts
from docflag.compare.imagediff import ImageDiffOptions, ImageDiffResult, compare_images
from docflag.compare.normalize import CompareOptions
from docflag.compare.ocr import OcrEngine, create_engine
from docflag.compare.pipeline import OcrOutcome, image_to_text

STUB_ENV = "DOCFLAG_STUB_OCR"

_engine: OcrEngine | None = None
# One Windows OCR engine serves every user: create it once and let only one
# recognition run at a time.
_engine_lock = threading.RLock()


def engine() -> OcrEngine:
    """The Windows OCR engine, or the stub off Windows (or with DOCFLAG_STUB_OCR=1)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = create_engine(force_stub=os.environ.get(STUB_ENV) == "1")
        return _engine


def engine_status() -> tuple[bool, bool]:
    """(real OCR?, Greek OCR language missing?)."""
    eng = engine()
    return bool(getattr(eng, "is_real", False)), bool(getattr(eng, "language_fallback", False))


def load_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("Δεν είναι εικόνα που μπορεί να διαβαστεί.") from exc
    return image.convert("RGB")


def ocr_image(image: Image.Image) -> OcrOutcome:
    with _engine_lock:
        return image_to_text(engine(), image)


def compare_text(a: str, b: str, ignore_case: bool = True, ignore_punctuation: bool = True) -> DiffResult:
    return diff_texts(a, b, CompareOptions(ignore_case, ignore_punctuation))


def compare_image(a: Image.Image, b: Image.Image, sensitivity: int = 1) -> ImageDiffResult:
    return compare_images(a, b, ImageDiffOptions.from_sensitivity(sensitivity))


def format_similarity(value: float, perfect: bool) -> str:
    """'100%' only when nothing differs; otherwise round down so near-misses
    never read as 100 (as in DocDiff's ui/diff_view.py)."""
    if perfect:
        return "100%"
    v = min(math.floor(max(value, 0.0) * 10) / 10, 99.9)
    return f"{v:.1f}%".replace(".", ",")
