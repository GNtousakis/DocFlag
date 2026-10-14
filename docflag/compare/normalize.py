"""Text normalisation for comparison (display text is never altered)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class CompareOptions:
    ignore_case: bool = True
    ignore_punctuation: bool = True


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def strip_accents(text: str) -> str:
    """Remove combining marks (Greek tonos/dialytika/breathings, Latin accents)."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))


def is_punct_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    # P* punctuation, S* symbols (|, +, =, currency kept as symbols is fine to drop),
    # plus the Greek ano teleia / question mark which are P* already.
    return cat[0] in ("P", "S") and cat != "Sc"


def normalize_word(word: str, options: CompareOptions = CompareOptions()) -> str:
    """Comparison key for one whitespace-delimited word. Empty string means 'nothing to compare'."""
    w = strip_accents(nfc(word))
    w = w.replace("ς", "σ")  # final sigma is the same letter as sigma
    if options.ignore_case:
        w = w.casefold().replace("ς", "σ")
    if options.ignore_punctuation:
        w = _drop_punct(w)
    return w


def _drop_punct(w: str) -> str:
    out = []
    for i, c in enumerate(w):
        if is_punct_char(c):
            # keep decimal/thousands separators inside numbers: 1,20 must differ from 12,0
            if c in ".," and 0 < i < len(w) - 1 and w[i - 1].isdigit() and w[i + 1].isdigit():
                out.append(c)
            continue
        out.append(c)
    return "".join(out)


_WS = re.compile(r"\s+")


def normalize_text(text: str, options: CompareOptions = CompareOptions()) -> str:
    """Whole-text normalisation: words joined by single spaces."""
    keys = (normalize_word(w, options) for w in _WS.split(text.strip()))
    return " ".join(k for k in keys if k)
