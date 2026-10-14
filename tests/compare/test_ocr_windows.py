"""Real Windows OCR smoke test (skipped elsewhere). Greek missing => English only + warning."""
import sys
import warnings

import pytest

from docflag.compare.selftest import ENGLISH_LINES, GREEK_LINES, keywords_found, render_lines

pytestmark = [pytest.mark.windows_ocr, pytest.mark.skipif(sys.platform != "win32", reason="Windows only")]


def test_windows_ocr_english_and_greek():
    from docflag.compare.ocr.windows import WindowsOcrEngine
    from docflag.compare.pipeline import image_to_text

    engine = WindowsOcrEngine()
    out = image_to_text(engine, render_lines(ENGLISH_LINES))
    ok, found, missing = keywords_found(out.text, ENGLISH_LINES)
    assert ok, f"English words missing: {missing}; text={out.text!r}"
    raw = engine.recognize(render_lines(ENGLISH_LINES))
    assert raw.words and all(w.width > 0 for w in raw.words)
    if engine.language_fallback or not (engine.language_tag or "").lower().startswith("el"):
        warnings.warn("Greek OCR language not installed on this machine; Greek check skipped")
        print("WARNING: Greek OCR language not installed; Greek check skipped")
        return
    out = image_to_text(engine, render_lines(GREEK_LINES))
    ok, found, missing = keywords_found(out.text, GREEK_LINES)
    assert ok, f"Greek words missing: {missing}; text={out.text!r}"
