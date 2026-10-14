"""The checks `DocFlag.exe --selftest` runs in CI (OCR is skipped off Windows)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docflag.selftest import run_selftest


def test_selftest_passes_and_writes_a_report(tmp_path):
    report = tmp_path / "selftest.txt"
    assert run_selftest(str(report)) == 0
    text = report.read_text(encoding="utf-8")
    for check in ("ocr", "image", "word", "pdf"):
        assert f"{check}: OK" in text
