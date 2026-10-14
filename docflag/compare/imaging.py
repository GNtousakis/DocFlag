"""Image preprocessing before OCR."""
from __future__ import annotations

from PIL import Image, ImageFilter

MAX_DIMENSION = 9000  # Windows OCR limit is 10000


def preprocess(image: Image.Image, max_dimension: int = MAX_DIMENSION) -> Image.Image:
    """Upscale 2-3x, convert to grayscale and apply mild sharpening."""
    w, h = image.size
    factor = 3 if max(w, h) < 800 else 2
    factor = min(factor, max_dimension / max(w, h))
    if factor > 1:
        image = image.convert("RGB").resize(
            (max(1, round(w * factor)), max(1, round(h * factor))), Image.LANCZOS
        )
    gray = image.convert("L")
    return gray.filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))
