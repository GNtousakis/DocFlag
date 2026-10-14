"""OCR interface and result types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PIL.Image import Image


@dataclass(frozen=True)
class OcrWord:
    """A recognised word and its bounding box (pixels of the OCR'd image)."""

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass
class OcrLine:
    text: str
    words: list[OcrWord] = field(default_factory=list)


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    language: str | None = None
    #: True when the preferred (Greek) language was unavailable and a fallback was used.
    language_fallback: bool = False

    @property
    def words(self) -> list[OcrWord]:
        return [w for line in self.lines for w in line.words]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class OcrError(RuntimeError):
    """Raised when OCR cannot be performed."""


class OcrEngine(ABC):
    """Anything that can turn an image into text with word boxes."""

    #: Human readable engine name, shown in diagnostics.
    name: str = "base"
    #: True when this engine produces real OCR output (False for the stub).
    is_real: bool = True
    #: True when Greek is not available and a fallback language is in use.
    language_fallback: bool = False

    @abstractmethod
    def recognize(self, image: "Image") -> OcrResult:
        """Recognise text in ``image``. Safe to call from a worker thread."""
