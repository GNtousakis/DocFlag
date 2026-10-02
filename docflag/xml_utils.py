"""Shared OOXML namespace map and lxml helpers."""
from __future__ import annotations

from lxml import etree

NSMAP = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def qn(tag: str) -> str:
    """Convert a 'prefix:local' tag into a Clark-notation '{uri}local' qname."""
    prefix, local = tag.split(":", 1)
    return f"{{{NSMAP[prefix]}}}{local}"


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data)


def serialize_xml(element: etree._Element) -> bytes:
    return etree.tostring(
        element, xml_declaration=True, encoding="UTF-8", standalone=True
    )
