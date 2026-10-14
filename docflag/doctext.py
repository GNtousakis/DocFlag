"""Plain text of a .docx for comparison: one line per paragraph, and table
rows as 'a | b | c' so the comparison can pair rows and diff them cell by
cell (see compare/diffing.py).
"""
from __future__ import annotations

import zipfile

from lxml import etree

from .compare.diffing import CELL_SEP
from .flagger import DOCUMENT_PART, MC_FALLBACK, W_P, _paragraph_text
from .package_io import Package
from .xml_utils import qn

W_BODY = qn("w:body")
W_TBL = qn("w:tbl")
W_TR = qn("w:tr")
W_TC = qn("w:tc")
W_TXBX = qn("w:txbxContent")
W_TYPE = qn("w:type")
NOTE_PARTS = (("word/footnotes.xml", qn("w:footnote")), ("word/endnotes.xml", qn("w:endnote")))

# Block-level wrappers whose paragraphs and tables belong to the document.
# Deliberately excludes w:del / w:moveFrom (tracked-deleted text).
_BLOCK_CONTAINERS = {
    qn(t) for t in ("w:sdt", "w:sdtContent", "w:customXml", "w:ins", "w:moveTo")
}


def extract_text(path: str) -> str:
    """The text of the body, then footnotes and endnotes. Headers and
    footers are left out: they repeat on every page and only add noise."""
    try:
        pkg = Package.load(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.") from exc
    if not pkg.has_part(DOCUMENT_PART):
        raise ValueError("Αυτό δεν είναι έγκυρο αρχείο .docx.")

    lines = _block_lines(pkg.get_xml(DOCUMENT_PART).find(W_BODY))
    for part_name, note_tag in NOTE_PARTS:
        if pkg.has_part(part_name):
            for note in pkg.get_xml(part_name).iter(note_tag):
                # Separator "notes" hold Word's footnote line, not text.
                if note.get(W_TYPE) in (None, "normal"):
                    lines += _block_lines(note)
    return "\n".join(lines)


def _block_lines(container: etree._Element | None) -> list[str]:
    lines: list[str] = []
    if container is None:
        return lines
    for child in container:
        if child.tag == W_P:
            text, _ = _paragraph_text(child)
            lines += [line.strip() for line in text.split("\n") if line.strip()]
            for box in _text_boxes(child):
                lines += _block_lines(box)
        elif child.tag == W_TBL:
            lines += _table_lines(child)
        elif child.tag in _BLOCK_CONTAINERS:
            lines += _block_lines(child)
    return lines


def _table_lines(table: etree._Element) -> list[str]:
    lines = []
    for row in _children(table, W_TR):
        # A nested table is flattened into its cell.
        cells = [" ".join(_block_lines(cell)) for cell in _children(row, W_TC)]
        cells = [" ".join(cell.replace("|", " ").split()) for cell in cells]
        if any(cells):
            lines.append(CELL_SEP.join(cells))
    return lines


def _children(element: etree._Element, tag: str):
    """`tag` children of `element`, looking through content controls."""
    for child in element:
        if child.tag == tag:
            yield child
        elif child.tag in _BLOCK_CONTAINERS:
            yield from _children(child, tag)


def _text_boxes(paragraph: etree._Element):
    """Text boxes anchored in this paragraph (not in a nested one). Each is
    stored twice, as DrawingML and as a VML fallback: only the first counts."""
    for box in paragraph.iter(W_TXBX):
        if next(box.iterancestors(W_P), None) is not paragraph:
            continue
        for ancestor in box.iterancestors():
            if ancestor is paragraph:
                yield box
                break
            if ancestor.tag == MC_FALLBACK:
                break
