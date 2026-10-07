"""The shared replacement list: (text, replacement) pairs in one JSON file on
the server, used by every visitor."""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Iterable

from .flagger import normalise_term
from .paths import DEFAULT_REPLACEMENTS_FILE, data_dir
from .wordlist import quote, unquote

Pair = tuple[str, str]

_lock = threading.Lock()

# Separators accepted between a text and its replacement on one line.
SEPARATORS = ("=>", "→", "\t")
_LINE_RE = re.compile(r'^\s*("[^"]*"|.*?)\s*(?:=>|→|\t)\s*("[^"]*"|.*?)\s*$')


def normalise(pairs: Iterable[Pair]) -> list[Pair]:
    """Collapse whitespace in the texts (keeping a space at either end) and
    drop blank ones. A text already in the list keeps its place and first
    spelling but takes the latest replacement, so adding it again updates
    it. Replacements are kept exactly as given."""
    merged: dict[str, Pair] = {}
    for source, target in pairs:
        source = normalise_term(source)
        if not source.strip():
            continue
        key = source.casefold()
        merged[key] = (merged.get(key, (source,))[0], target)
    return list(merged.values())


def parse_line(line: str) -> Pair | None:
    """'τηλ. => τηλέφωνο' (or with → or a tab between) as a pair, or None
    if the line has no separator."""
    match = _LINE_RE.match(line)
    if match is None:
        return None
    return unquote(match.group(1)), unquote(match.group(2))


def parse_lines(text: str) -> tuple[list[Pair], int]:
    """One pair per line; blank lines and # comments are ignored. Returns
    the pairs and how many lines could not be read as one."""
    pairs = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pair = parse_line(line)
        if pair is None or not pair[0].strip():
            skipped += 1
        else:
            pairs.append(pair)
    return normalise(pairs), skipped


def format_lines(pairs: Iterable[Pair]) -> str:
    return "\n".join(
        f"{quote(source, SEPARATORS)} => {quote(target, SEPARATORS)}"
        for source, target in pairs
    )


def defaults() -> list[Pair]:
    return parse_lines(DEFAULT_REPLACEMENTS_FILE.read_text(encoding="utf-8"))[0]


def _path():
    return data_dir() / "replacements.json"


def _read() -> list[Pair]:
    try:
        with open(_path(), encoding="utf-8") as f:
            return normalise(
                (item["from"], item["to"]) for item in json.load(f)["replacements"]
            )
    except FileNotFoundError:
        return defaults()


def _write(pairs: Iterable[Pair]) -> list[Pair]:
    pairs = normalise(pairs)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"replacements": [{"from": s, "to": t} for s, t in pairs]},
            f, ensure_ascii=False, indent=2,
        )
    os.replace(tmp, path)
    return pairs


def load() -> list[Pair]:
    with _lock:
        return _read()


def save(pairs: Iterable[Pair]) -> list[Pair]:
    with _lock:
        return _write(pairs)


def add(pairs: Iterable[Pair]) -> list[Pair]:
    with _lock:
        return _write([*_read(), *pairs])


def remove(source: str) -> list[Pair]:
    with _lock:
        key = normalise_term(source).casefold()
        return _write(p for p in _read() if p[0].casefold() != key)


def reset() -> list[Pair]:
    with _lock:
        return _write(defaults())
