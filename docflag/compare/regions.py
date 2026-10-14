"""Pure helpers for the per-side list of captured regions and the joined text."""
from __future__ import annotations


def append_text(box: str, new: str) -> str:
    """Join an appended capture after the existing text, in order."""
    new = new.strip("\n")
    if not box.strip():
        return new
    return box.rstrip("\n") + "\n" + new


def remove_region_text(box: str, region_text: str) -> tuple[str, bool]:
    """Remove a region's original text from the (possibly edited) box. Returns (text, removed)."""
    region_text = region_text.strip("\n")
    if not region_text:
        return box, True
    idx = box.find(region_text)
    if idx < 0:
        return box, False
    before, after = box[:idx], box[idx + len(region_text):]
    out = before.rstrip("\n") + ("\n" if before.strip() and after.strip() else "") + after.lstrip("\n")
    return out, True
