"""Convert legacy Word 97-2003 .doc files to .docx so they can be flagged.

.doc is a binary format nothing else in DocFlag can read, so a real word
processor on the server does the conversion: Word itself through COM
automation on Windows, otherwise LibreOffice in headless mode. If neither
is installed the upload is refused with a message saying so.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

_WORD_FORMAT_DOCX = 12  # wdFormatXMLDocument
_MACROS_DISABLED = 3    # msoAutomationSecurityForceDisable
_NO_ALERTS = 0          # wdAlertsNone

# One conversion at a time: Word and LibreOffice both misbehave when several
# instances are started at once by the same user.
_lock = threading.Lock()


def is_legacy_doc(name: str) -> bool:
    return os.path.splitext(name)[1].lower() == ".doc"


def convert_doc(path: str) -> str:
    """Convert `path` (a .doc) to a .docx next to it and return its path."""
    out_path = os.path.splitext(path)[0] + ".docx"
    errors: list[str] = []

    with _lock:
        if sys.platform == "win32":
            try:
                return _convert_via_word_com(path, out_path)
            except Exception as exc:  # noqa: BLE001 - fall through to next converter
                errors.append(f"Word: {exc}")

        soffice = _find_soffice()
        if soffice:
            try:
                return _convert_via_libreoffice(soffice, path, out_path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"LibreOffice: {exc}")

    detail = " | ".join(errors) if errors else "no converter found"
    raise RuntimeError(
        "Converting .doc files needs Microsoft Word or LibreOffice on the PC "
        f"that runs DocFlag ({detail}). Alternatively open the file in Word "
        "and use Save As > Word Document (.docx)."
    )


def _convert_via_word_com(path: str, out_path: str) -> str:
    """Drives a fresh, invisible Word instance (DispatchEx, so it can't
    attach to a Word window someone has open on the server)."""
    import pythoncom  # noqa: PLC0415 - only importable on Windows
    import win32com.client  # noqa: PLC0415

    # Uploads are handled on worker threads; COM must be set up on each.
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        try:
            word.Visible = False
            word.DisplayAlerts = _NO_ALERTS
            # The file comes from whoever is on the network: never run its macros.
            word.AutomationSecurity = _MACROS_DISABLED
            doc = word.Documents.Open(
                os.path.abspath(path), ReadOnly=True, AddToRecentFiles=False,
                ConfirmConversions=False,
            )
            try:
                doc.SaveAs2(os.path.abspath(out_path), FileFormat=_WORD_FORMAT_DOCX)
            finally:
                doc.Close(SaveChanges=False)
        finally:
            word.Quit()
    finally:
        pythoncom.CoUninitialize()
    return out_path


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _convert_via_libreoffice(soffice: str, path: str, out_path: str) -> str:
    # LibreOffice names its output <input stem>.docx inside --outdir, which
    # is exactly out_path.
    result = subprocess.run(
        [soffice, "--headless", "--convert-to", "docx",
         "--outdir", os.path.dirname(out_path), path],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0 or not os.path.isfile(out_path):
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"exit code {result.returncode}")
    return out_path
