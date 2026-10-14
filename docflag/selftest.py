"""`DocFlag.exe --selftest out.txt`: checks that the packaged program can do
what the build alone doesn't prove -- real Windows OCR (the winrt modules are
bundled), image comparison (numpy/opencv are bundled), Word -> text and
PDF -> text (pdfium is bundled).
Exit code 0 = OK. CI runs it against the frozen .exe.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor

from .compare.selftest import check_image, check_ocr

_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    "<w:p><w:r><w:t>Τιμοκατάλογος</w:t></w:r></w:p>"
    "<w:tbl><w:tr>"
    "<w:tc><w:p><w:r><w:t>Καφές</w:t></w:r></w:p></w:tc>"
    "<w:tc><w:p><w:r><w:t>{price}</w:t></w:r></w:p></w:tc>"
    "</w:tr></w:tbl>"
    "</w:body></w:document>"
)


def check_word(log) -> bool:
    """Two minimal .docx files that differ in one table cell."""
    from .compare.service import compare_text
    from .doctext import extract_text

    texts = []
    with tempfile.TemporaryDirectory() as folder:
        for name, price in (("a.docx", "1,20"), ("b.docx", "1,50")):
            path = os.path.join(folder, name)
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("word/document.xml", _DOCUMENT.format(price=price))
            texts.append(extract_text(path))
    result = compare_text(*texts)
    stats = result.stats
    log(f"Word text: {texts!r} structured={result.structured} "
        f"added={stats.added} removed={stats.removed} changed={stats.changed}")
    return result.structured and (stats.added, stats.removed, stats.changed) == (0, 0, 1)


def check_pdf(log) -> bool:
    """A text PDF read exactly, and its first page rendered for image mode."""
    from .pdftext import extract_text, minimal_pdf, render_first_page

    rows = [("Item", "Price"), ("Coffee", "1,20"), ("Tea", "1,00")]
    items = [(72 + c * 160, 100 + r * 22, cell) for r, row in enumerate(rows) for c, cell in enumerate(row)]
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "a.pdf")
        with open(path, "wb") as f:
            f.write(minimal_pdf([items]))
        result = extract_text(path, ocr=lambda image: "")
        image, pages = render_first_page(path)
    log(f"PDF text: {result.text!r} pages={pages} rendered={image.size}")
    return result.text == "Item | Price\nCoffee | 1,20\nTea | 1,00" and image.width > 595


def run_selftest(out_path: str | None = None) -> int:
    import faulthandler

    out = open(out_path, "w", encoding="utf-8", buffering=1) if out_path else None
    if out:
        faulthandler.enable(out)

    def log(msg: str) -> None:
        if out:
            out.write(msg + "\n")
            out.flush()
        if sys.stdout:
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:  # legacy Windows console code page
                print(msg.encode("ascii", "backslashreplace").decode(), flush=True)

    ok = True
    for name, check in (("ocr", check_ocr), ("image", check_image), ("word", check_word), ("pdf", check_pdf)):
        try:
            if name == "ocr":  # as in the app: OCR on a worker thread
                with ThreadPoolExecutor(1) as executor:
                    passed = executor.submit(check, log).result()
            else:
                passed = check(log)
        except Exception:  # noqa: BLE001
            log(f"EXCEPTION in {name}:\n{traceback.format_exc()}")
            passed = False
        log(f"{name}: {'OK' if passed else 'FAILED'}")
        ok &= bool(passed)
    if out:
        out.close()
    return 0 if ok else 1
