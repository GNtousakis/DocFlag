"""Text of a PDF for comparison. Pages with a text layer are read exactly:
their words and positions go through the same layout reconstruction as OCR
results (compare/layout.py), so tables come out as 'a | b | c' rows. Pages
without one (scans) are rendered and read with OCR.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import pypdfium2 as pdfium
from PIL import Image

from .compare.layout import reconstruct_text
from .compare.ocr.base import OcrLine, OcrResult, OcrWord

# A page with fewer characters than this is treated as a scan (some scans
# carry a stray word or page number as text).
MIN_TEXT_CHARS = 10
# 144 dpi: enough for OCR (which upscales further) and for image comparison.
RENDER_SCALE = 2
# pdfium is not thread-safe, and every user of the page shares this process.
_pdfium_lock = threading.Lock()


@dataclass
class PdfText:
    text: str
    pages: int
    ocr_pages: int = 0


def _open(path: str) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        raise ValueError(
            "Αυτό δεν είναι έγκυρο αρχείο PDF ή προστατεύεται με κωδικό."
        ) from exc


def page_words(page: pdfium.PdfPage) -> list[OcrWord]:
    """The page's words with their boxes, top-left origin as in OCR results.
    Loose character boxes (font ascent to descent) give every word on a line
    the same height, which the layout reconstruction relies on."""
    textpage = page.get_textpage()
    page_height = page.get_height()
    words: list[OcrWord] = []
    chars: list[str] = []
    box: list[float] = []  # left, bottom, right, top of the current word

    def flush() -> None:
        if chars:
            left, bottom, right, top = box
            words.append(OcrWord("".join(chars), left, page_height - top, right - left, top - bottom))
            chars.clear()

    try:
        for i in range(textpage.count_chars()):
            char = textpage.get_text_range(i, 1)
            if not char or char.isspace():
                flush()
                continue
            left, bottom, right, top = textpage.get_charbox(i, loose=True)
            if chars:
                height = max(box[3] - box[1], top - bottom, 1.0)
                new_line = abs(top - box[3]) > height / 2 or left < box[0] - 1
                if new_line or left > box[2] + height / 4:  # no space char between words
                    flush()
            if chars:
                box = [min(box[0], left), min(box[1], bottom), max(box[2], right), max(box[3], top)]
            else:
                box = [left, bottom, right, top]
            chars.append(char)
        flush()
    finally:
        textpage.close()
    return words


def extract_text(path: str, ocr: Callable[[Image.Image], str]) -> PdfText:
    """Text of every page; `ocr` reads the pages that have no text layer."""
    with _pdfium_lock:
        pdf = _open(path)
    try:
        texts = []
        ocr_pages = 0
        for index in range(len(pdf)):
            with _pdfium_lock:
                page = pdf[index]
                try:
                    words = page_words(page)
                    scanned = sum(len(w.text) for w in words) < MIN_TEXT_CHARS
                    image = page.render(scale=RENDER_SCALE).to_pil() if scanned else None
                finally:
                    page.close()
            if image is not None:
                # OCR outside the lock: other users' PDFs needn't wait for it.
                text = ocr(image)
                if text.strip():
                    ocr_pages += 1
                else:  # a blank page, or one with very little text
                    text = reconstruct_text(OcrResult(lines=[OcrLine("", words)]))
            else:
                text = reconstruct_text(OcrResult(lines=[OcrLine("", words)]))
            if text.strip():
                texts.append(text.strip())
        return PdfText("\n".join(texts), len(pdf), ocr_pages)
    finally:
        with _pdfium_lock:
            pdf.close()


def render_first_page(path: str) -> tuple[Image.Image, int]:
    """The first page as an image, and the number of pages."""
    with _pdfium_lock:
        pdf = _open(path)
        try:
            if len(pdf) == 0:
                raise ValueError("Το PDF δεν έχει σελίδες.")
            page = pdf[0]
            try:
                return page.render(scale=RENDER_SCALE).to_pil().convert("RGB"), len(pdf)
            finally:
                page.close()
        finally:
            pdf.close()


def minimal_pdf(pages: list[list[tuple[float, float, str]]]) -> bytes:
    """A small A4 PDF with Helvetica text at (x, y from the top, text) on each
    page, for tests and the self-test. Latin-1 text only."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for items in pages:
        stream = b"".join(
            b"BT /F1 12 Tf %.1f %.1f Td (%s) Tj ET\n"
            % (x, 842 - y, text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1"))
            for x, y, text in items
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (len(objects))
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
