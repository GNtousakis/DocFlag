"""The shared word list: one JSON file on the server, used by every visitor."""
from __future__ import annotations

import json
import os
import threading
from typing import Iterable

from .paths import DEFAULT_WORDS_FILE, data_dir

_lock = threading.Lock()


def normalise(words: Iterable[str]) -> list[str]:
    """Trim, collapse inner whitespace, drop blanks and case-insensitive
    duplicates (first spelling wins), keeping the original order."""
    seen = set()
    cleaned = []
    for word in words:
        word = " ".join(word.split())
        key = word.casefold()
        if word and key not in seen:
            seen.add(key)
            cleaned.append(word)
    return cleaned


def parse_lines(text: str) -> list[str]:
    """One word or phrase per line; blank lines and # comments are ignored."""
    return normalise(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def decode_text(data: bytes) -> str:
    """Decode an imported .txt: UTF-8 or UTF-16 if it is one, otherwise the
    Greek Windows code page that Notepad's "ANSI" uses on a Greek PC."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1253", errors="replace")


def defaults() -> list[str]:
    return parse_lines(DEFAULT_WORDS_FILE.read_text(encoding="utf-8"))


def _path():
    return data_dir() / "wordlist.json"


def _read() -> list[str]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return normalise(json.load(f)["words"])
    except FileNotFoundError:
        return defaults()


def _write(words: Iterable[str]) -> list[str]:
    words = normalise(words)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"words": words}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return words


def load() -> list[str]:
    with _lock:
        return _read()


def save(words: Iterable[str]) -> list[str]:
    with _lock:
        return _write(words)


def add(words: Iterable[str]) -> list[str]:
    with _lock:
        return _write([*_read(), *words])


def remove(word: str) -> list[str]:
    with _lock:
        key = " ".join(word.split()).casefold()
        return _write(w for w in _read() if w.casefold() != key)


def reset() -> list[str]:
    with _lock:
        return _write(defaults())
