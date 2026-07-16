from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .namespaces import SVG


HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
FORBIDDEN = {"style", "foreignObject", "mask", "script", "iframe", "animate", "animateMotion", "animateTransform", "set", "tspan"}


@dataclass
class ValidationResult:
    path: str
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)


def validate_svg(path: str | Path) -> ValidationResult:
    path = Path(path)
    result = ValidationResult(str(path))
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
        root = etree.parse(path, parser).getroot()
    except (OSError, etree.XMLSyntaxError) as exc:
        result.error(f"invalid-xml:{exc}")
        return result
    if etree.QName(root).namespace != SVG or etree.QName(root).localname != "svg":
        result.error("root-is-not-svg")
    expected = {"width": "1280", "height": "720", "viewBox": "0 0 1280 720"}
    for key, value in expected.items():
        if root.get(key) != value:
            result.error(f"root-{key}-must-be-{value}")
    top_visible = [e for e in root if etree.QName(e).localname != "defs"]
    top_groups = [e for e in top_visible if etree.QName(e).localname == "g"]
    if len(top_groups) != len(top_visible):
        result.error("visible-top-level-elements-must-be-groups")
    if not 3 <= len(top_groups) <= 8:
        result.error(f"semantic-top-group-count:{len(top_groups)}")
    if not top_groups or top_groups[0].get("id") != "background":
        result.error("first-visible-group-must-be-background")
    else:
        rect = top_groups[0].find(f"{{{SVG}}}rect")
        if rect is None or any(rect.get(k) != v for k, v in {"x":"0","y":"0","width":"1280","height":"720"}.items()):
            result.error("background-must-cover-canvas")
    ids: set[str] = set()
    for element in root.iter():
        local = etree.QName(element).localname
        if local in FORBIDDEN:
            result.error(f"forbidden-element:{local}")
        if element.get("class") is not None:
            result.error("class-attribute-forbidden")
        if local == "g" and element.get("opacity") is not None:
            result.error("group-opacity-forbidden")
        if local == "image" and element.get("opacity") is not None:
            result.error("image-opacity-forbidden")
        if local != "image" and element.get("clip-path") is not None:
            result.error(f"clip-path-only-supported-on-image:{local}")
        element_id = element.get("id")
        if element_id:
            if element_id in ids:
                result.error(f"duplicate-id:{element_id}")
            ids.add(element_id)
        for key, value in element.attrib.items():
            if "rgba(" in value.lower():
                result.error(f"rgba-forbidden:{local}:{etree.QName(key).localname}")
            attr = etree.QName(key).localname
            if attr in {"fill", "stroke", "stop-color"} and value not in {"none"} and not value.startswith("url(") and not HEX_RE.fullmatch(value):
                result.error(f"non-hex-color:{local}:{attr}:{value}")
        if local == "image":
            href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
            if not href:
                result.error("image-without-href")
            elif href.startswith(("data:", "/", "file:", "http:", "https:")):
                result.error(f"image-href-must-be-relative:{href[:40]}")
            elif not (path.parent / href).is_file():
                result.error(f"image-target-missing:{href}")
        if local == "text":
            family = element.get("font-family", "")
            if not any(name in family for name in ("Microsoft YaHei", "SimSun", "Arial", "sans-serif")):
                result.error(f"font-fallback-missing:{family}")
    return result
