"""docx-as-zip helpers: part storage, cached XML trees, final save."""
from __future__ import annotations

import zipfile

from lxml import etree

from .xml_utils import parse_xml, serialize_xml


class Package:
    """In-memory representation of a .docx package (a zip of XML/binary parts)."""

    def __init__(self):
        self.parts: dict[str, bytes] = {}
        self._xml_cache: dict[str, etree._Element] = {}

    @classmethod
    def load(cls, path: str) -> "Package":
        pkg = cls()
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                pkg.parts[info.filename] = zf.read(info.filename)
        return pkg

    def has_part(self, part_name: str) -> bool:
        return part_name in self.parts

    def get_xml(self, part_name: str) -> etree._Element:
        if part_name not in self._xml_cache:
            self._xml_cache[part_name] = parse_xml(self.parts[part_name])
        return self._xml_cache[part_name]

    def flush_xml_cache(self) -> None:
        """Re-serialize every cached (possibly mutated in-place) XML tree back
        into self.parts. Call before save()."""
        for part_name, element in self._xml_cache.items():
            self.parts[part_name] = serialize_xml(element)

    def save(self, path: str) -> None:
        self.flush_xml_cache()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for part_name, data in self.parts.items():
                zf.writestr(part_name, data)
