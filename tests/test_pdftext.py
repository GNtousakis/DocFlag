"""PDF -> plain text for the comparison tab."""
from __future__ import annotations

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docflag.compare.diffing import diff_texts
from docflag.pdftext import extract_text, minimal_pdf, render_first_page

ROWS = [("Item", "Quantity", "Price"), ("Apples", "10", "1,20"), ("Pears", "5", "2,00")]


def _price_list(tmp_path, name: str, apples: str, note: str = "") -> str:
    rows = [ROWS[0], ("Apples", "10", apples), ROWS[2]]
    items = [(72, 80, "Price list for the shop (2026).")]
    items += [(72 + c * 160, 130 + r * 22, cell) for r, row in enumerate(rows) for c, cell in enumerate(row)]
    pages = [items] + ([[(72, 80, note)]] if note else [])
    path = tmp_path / name
    path.write_bytes(minimal_pdf(pages))
    return str(path)


def _no_ocr(image):
    raise AssertionError("pages with text must not be OCR'd")


def test_text_layer_with_a_table(tmp_path):
    result = extract_text(_price_list(tmp_path, "a.pdf", "1,20", "Prices include VAT."), _no_ocr)
    assert (result.pages, result.ocr_pages) == (2, 0)
    assert result.text.split("\n") == [
        "Price list for the shop (2026).",
        "Item | Quantity | Price",
        "Apples | 10 | 1,20",
        "Pears | 5 | 2,00",
        "Prices include VAT.",
    ]


def test_changed_cell_is_diffed_row_by_row(tmp_path):
    a = extract_text(_price_list(tmp_path, "a.pdf", "1,20"), _no_ocr).text
    b = extract_text(_price_list(tmp_path, "b.pdf", "1,50"), _no_ocr).text
    result = diff_texts(a, b)
    assert result.structured
    assert (result.stats.changed, result.stats.added, result.stats.removed) == (1, 0, 0)


def test_scanned_page_is_read_with_ocr(tmp_path):
    path = tmp_path / "scan.pdf"
    Image.new("RGB", (600, 800), "white").save(path)  # an image-only PDF
    sizes = []
    result = extract_text(str(path), lambda image: sizes.append(image.size) or "from OCR")
    assert result.text == "from OCR" and result.ocr_pages == 1
    assert sizes and sizes[0][0] > 600  # rendered above 72 dpi


def test_render_first_page(tmp_path):
    image, pages = render_first_page(_price_list(tmp_path, "a.pdf", "1,20", "second page"))
    assert pages == 2
    assert image.mode == "RGB" and image.width > 595


def test_bad_file_is_rejected(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(ValueError):
        extract_text(str(path), _no_ocr)
