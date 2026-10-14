"""Windows built-in OCR via the pywinrt projections (no deprecated ``winsdk``)."""
from __future__ import annotations

import asyncio
import io
import sys
from typing import TYPE_CHECKING

from docflag.compare.ocr.base import OcrEngine, OcrError, OcrLine, OcrResult, OcrWord

if TYPE_CHECKING:  # pragma: no cover
    from PIL.Image import Image

GREEK_TAGS = ("el-GR", "el")
FALLBACK_TAGS = ("en-US", "en-GB", "en")


def is_supported() -> bool:
    """True when the winrt OCR projections can be imported (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.media.ocr  # noqa: F401
    except Exception:
        return False
    return True


def _import_winrt():
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
    from winrt.windows.media.ocr import OcrEngine as WinOcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    return Language, BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat, WinOcrEngine, DataWriter, InMemoryRandomAccessStream


def available_language_tags() -> list[str]:
    _, _, _, _, WinOcrEngine, _, _ = _import_winrt()
    return [lang.language_tag for lang in WinOcrEngine.available_recognizer_languages]


class WindowsOcrEngine(OcrEngine):
    """OCR with ``Windows.Media.Ocr``.

    Prefers Greek (which also reads Latin text, so mixed Greek/English works).  If the
    Greek OCR language is not installed, falls back to the user-profile languages and
    sets :attr:`language_fallback` so the UI can explain how to install Greek.
    """

    name = "windows"
    is_real = True

    def __init__(self, prefer_greek: bool = True):
        if not is_supported():
            raise OcrError("Windows OCR is not available on this system")
        self._mods = _import_winrt()
        self._engine = None
        self.language_tag: str | None = None
        self.language_fallback = False
        self._create_engine(prefer_greek)

    def _create_engine(self, prefer_greek: bool) -> None:
        Language, *_, WinOcrEngine, _, _ = self._mods
        if prefer_greek:
            for tag in GREEK_TAGS:
                try:
                    eng = WinOcrEngine.try_create_from_language(Language(tag))
                except Exception:
                    eng = None
                if eng is not None:
                    self._engine, self.language_tag = eng, eng.recognizer_language.language_tag
                    return
            self.language_fallback = True
        eng = WinOcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            for tag in FALLBACK_TAGS:
                try:
                    eng = WinOcrEngine.try_create_from_language(Language(tag))
                except Exception:
                    eng = None
                if eng is not None:
                    break
        if eng is None:
            raise OcrError("No Windows OCR language is installed")
        self._engine, self.language_tag = eng, eng.recognizer_language.language_tag

    @property
    def max_image_dimension(self) -> int:
        return int(self._mods[4].max_image_dimension)

    def recognize(self, image: "Image") -> OcrResult:
        # Own event loop per call: this runs on worker threads, never the UI thread.
        return asyncio.run(self._recognize_async(image))

    async def _recognize_async(self, image: "Image") -> OcrResult:
        _, BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat, _, DataWriter, Stream = self._mods
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        stream = Stream()
        writer = DataWriter(stream)
        writer.write_bytes(buf.getvalue())
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_converted_async(
            BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED
        )
        result = await self._engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            words = []
            for w in line.words:
                r = w.bounding_rect
                words.append(OcrWord(w.text, r.x, r.y, r.width, r.height))
            lines.append(OcrLine(text=line.text, words=words))
        return OcrResult(lines=lines, language=self.language_tag, language_fallback=self.language_fallback)
