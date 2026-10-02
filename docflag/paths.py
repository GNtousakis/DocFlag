"""Where DocFlag keeps its files, both from source and as a packaged .exe."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller keeps __file__ pointing inside the bundle, so this works frozen
# as long as the .spec ships default_words.txt into a "docflag" folder.
DEFAULT_WORDS_FILE = Path(__file__).parent / "default_words.txt"


def data_dir() -> Path:
    """Folder for the shared word list: next to the .exe when packaged, in
    the repo root otherwise. DOCFLAG_DATA_DIR overrides both."""
    override = os.environ.get("DOCFLAG_DATA_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return Path(__file__).parent.parent / "data"
