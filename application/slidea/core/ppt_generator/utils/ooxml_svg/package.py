from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree

from .model import Relationship
from .namespaces import PR


class PackageError(RuntimeError):
    pass


class OpcPackage:
    """Read-only OPC package backed by a PPTX zip or extracted directory."""

    def __init__(self, source: str | Path):
        self.source = Path(source)
        self._zip = zipfile.ZipFile(self.source) if self.source.is_file() else None
        if not self.source.exists():
            raise PackageError(f"Input does not exist: {self.source}")
        self._xml_cache: dict[str, etree._Element] = {}
        self._rels_cache: dict[str, dict[str, Relationship]] = {}

    def close(self) -> None:
        if self._zip:
            self._zip.close()

    def __enter__(self) -> "OpcPackage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def normalize(part: str) -> str:
        value = posixpath.normpath(part.lstrip("/"))
        if value == ".." or value.startswith("../"):
            raise PackageError(f"Unsafe OPC path: {part}")
        return value

    def exists(self, part: str) -> bool:
        part = self.normalize(part)
        if self._zip:
            return part in self._zip.namelist()
        return (self.source / part).is_file()

    def read(self, part: str) -> bytes:
        part = self.normalize(part)
        if self._zip:
            try:
                return self._zip.read(part)
            except KeyError as exc:
                raise PackageError(f"Missing package part: {part}") from exc
        path = (self.source / part).resolve()
        base = self.source.resolve()
        if base not in path.parents and path != base:
            raise PackageError(f"Unsafe package part: {part}")
        return path.read_bytes()

    def xml(self, part: str) -> etree._Element:
        part = self.normalize(part)
        if part not in self._xml_cache:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
            self._xml_cache[part] = etree.fromstring(self.read(part), parser)
        return self._xml_cache[part]

    @staticmethod
    def rels_part(part: str) -> str:
        p = PurePosixPath(part)
        return str(p.parent / "_rels" / f"{p.name}.rels")

    def relationships(self, part: str) -> dict[str, Relationship]:
        part = self.normalize(part)
        if part in self._rels_cache:
            return self._rels_cache[part]
        rels_part = self.rels_part(part)
        result: dict[str, Relationship] = {}
        if self.exists(rels_part):
            root = self.xml(rels_part)
            for rel in root.findall(f"{{{PR}}}Relationship"):
                rel_id = rel.get("Id", "")
                target_mode = rel.get("TargetMode", "")
                external = target_mode.lower() == "external"
                raw_target = rel.get("Target", "")
                target = raw_target if external else self.normalize(
                    posixpath.join(posixpath.dirname(part), raw_target)
                )
                result[rel_id] = Relationship(
                    rel_id=rel_id,
                    rel_type=rel.get("Type", "").rsplit("/", 1)[-1],
                    target=target,
                    external=external,
                )
        self._rels_cache[part] = result
        return result

    def related(self, part: str, rel_id: str) -> Relationship | None:
        return self.relationships(part).get(rel_id)

    def related_by_type(self, part: str, rel_type: str) -> list[Relationship]:
        return [r for r in self.relationships(part).values() if r.rel_type == rel_type]

