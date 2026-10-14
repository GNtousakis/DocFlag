"""Self-test checks: real OCR on a generated Greek/English image, and an
image diff. Run by docflag/selftest.py (``DocFlag.exe --selftest out.txt``).
Greek OCR missing is only a warning (English is checked instead).
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from docflag.compare.normalize import normalize_word

ENGLISH_LINES = ["Quarterly report invoice total", "Payment delivery agreement"]
GREEK_LINES = ["Καλημέρα κόσμε σύμβαση παράδοση", "Πληρωμή εταιρεία προϊόντα"]
_FONTS = [
    "arial.ttf", "segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
    "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise RuntimeError("no TrueType font with Greek support found")


def render_lines(lines: list[str], size: int = 40) -> Image.Image:
    font = load_font(size)
    img = Image.new("RGB", (1100, 40 + len(lines) * int(size * 1.8)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 20 + i * int(size * 1.8)), line, fill="black", font=font)
    return img


def keywords_found(text: str, lines: list[str], minimum: int = 3) -> tuple[bool, list[str], list[str]]:
    wanted = [normalize_word(w) for ln in lines for w in ln.split()]
    got = {normalize_word(w) for w in text.split()}
    found = [w for w in wanted if w in got]
    missing = [w for w in wanted if w not in got]
    return len(found) >= minimum, found, missing


def check_ocr(log) -> bool:
    from docflag.compare.ocr import windows
    from docflag.compare.pipeline import image_to_text

    if not windows.is_supported():
        log("SKIP: Windows OCR not available on this platform")
        return True
    engine = windows.WindowsOcrEngine()
    log(f"OCR language: {engine.language_tag} (greek fallback: {engine.language_fallback})")
    try:
        log(f"Installed OCR languages: {windows.available_language_tags()}")
    except Exception as exc:  # informational only
        log(f"could not list OCR languages: {exc}")
    ok = True
    raw = engine.recognize(render_lines(ENGLISH_LINES))
    if not raw.words:
        log("FAIL: no word boxes returned")
        return False
    out = image_to_text(engine, render_lines(ENGLISH_LINES))
    good, found, missing = keywords_found(out.text, ENGLISH_LINES)
    log(f"English text: {out.text!r} found={found} missing={missing}")
    ok &= good
    if engine.language_fallback or not (engine.language_tag or "").lower().startswith("el"):
        log("WARNING: Greek OCR language is not installed; Greek check skipped")
    else:
        out = image_to_text(engine, render_lines(GREEK_LINES))
        good, found, missing = keywords_found(out.text, GREEK_LINES)
        log(f"Greek text: {out.text!r} found={found} missing={missing}")
        ok &= good
    return bool(ok)


def check_image(log) -> bool:
    """Image diff end to end (verifies numpy/opencv are bundled and ORB alignment works)."""
    from docflag.compare.imagediff import compare_images

    def page(lines: list[str]) -> Image.Image:
        img = Image.new("RGB", (900, 300), "white")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=32)  # no system font needed
        for i, line in enumerate(lines):
            draw.text((30, 20 + i * 60), line, fill="black", font=font)
        return img

    lines = ENGLISH_LINES + ["Customer reference number 88231"]
    a = page(lines)
    b = page(lines[:2] + ["Customer reference number 99999"])
    same = compare_images(a, a.copy())
    changed = compare_images(a, b)
    log(f"Image diff: identical={same.similarity:.1f}% changed={changed.similarity:.1f}% "
        f"boxes={len(changed.boxes)} aligned={changed.aligned}")
    return same.similarity > 99 and not same.boxes and changed.similarity < same.similarity and bool(changed.boxes)
