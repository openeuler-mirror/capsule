import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
EXPECTED_VIEWBOX = "0 0 1280 720"
SAFE_FONT_TOKENS = {
    "microsoft yahei",
    "simhei",
    "simsun",
    "arial",
    "calibri",
    "times new roman",
    "georgia",
    "consolas",
    "sans-serif",
    "serif",
    "monospace",
}
FORBIDDEN_TAGS = {
    "foreignobject",
    "mask",
    "script",
    "iframe",
    "style",
    "textpath",
    "animate",
    "animatetransform",
    "animatemotion",
    "set",
}


def check_svg_file(svg_path: str | Path, expected_viewbox: str = EXPECTED_VIEWBOX) -> dict:
    """Return structured SVG quality results for one file."""
    path = Path(svg_path)
    result = {
        "file": path.name,
        "path": str(path),
        "exists": path.exists(),
        "errors": [],
        "warnings": [],
        "passed": False,
    }

    if not path.exists():
        result["errors"].append("File does not exist")
        return result

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as error:
        result["errors"].append(f"Failed to read file: {error}")
        return result

    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        result["errors"].append(
            "Invalid XML: "
            f"{error}. Escape XML reserved characters as &amp;, &lt;, &gt;, &quot;, &apos;."
        )
        return result

    if _local_name(root.tag) != "svg":
        result["errors"].append("Root element must be <svg>")
    _check_canvas(root, expected_viewbox, result)
    _check_forbidden_content(content, root, result)
    _check_fonts(root, result)
    _check_images(root, path, result)

    result["passed"] = not result["errors"]
    return result


def check_svg_files(svg_paths: list[str | Path]) -> list[dict]:
    return [check_svg_file(path) for path in svg_paths]


def format_quality_issues(results: list[dict]) -> str:
    """Format checker results for logs and exceptions."""
    lines = []
    for item in results:
        if item.get("passed"):
            continue
        lines.append(f"{item.get('file')}:")
        for error in item.get("errors") or []:
            lines.append(f"  ERROR: {error}")
        for warning in item.get("warnings") or []:
            lines.append(f"  WARN: {warning}")
    return "\n".join(lines)


def raise_for_svg_quality(svg_paths: list[str | Path]) -> list[dict]:
    results = check_svg_files(svg_paths)
    failed = [item for item in results if not item.get("passed")]
    if failed:
        raise ValueError("SVG quality check failed:\n" + format_quality_issues(results))
    return results


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _check_canvas(root: ET.Element, expected_viewbox: str, result: dict) -> None:
    viewbox = root.get("viewBox")
    if not viewbox:
        result["errors"].append("Missing viewBox attribute")
    elif viewbox != expected_viewbox:
        result["errors"].append(f"viewBox mismatch: expected '{expected_viewbox}', got '{viewbox}'")

    width = root.get("width")
    height = root.get("height")
    if width and _dimension_to_float(width) != 1280:
        result["warnings"].append(f"Unexpected width: {width}")
    if height and _dimension_to_float(height) != 720:
        result["warnings"].append(f"Unexpected height: {height}")


def _dimension_to_float(value: str) -> float | None:
    match = re.match(r"^\s*([0-9.]+)", value or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _check_forbidden_content(content: str, root: ET.Element, result: dict) -> None:
    lower = content.lower()
    if "<?xml-stylesheet" in lower:
        result["errors"].append("Detected forbidden xml-stylesheet")
    if re.search(r"<link[^>]*rel\s*=\s*['\"]stylesheet['\"]", lower):
        result["errors"].append("Detected forbidden stylesheet link")
    if "@font-face" in lower:
        result["errors"].append("Detected forbidden @font-face")
    if "@import" in lower:
        result["errors"].append("Detected forbidden @import")
    if re.search(r"\brgba\s*\(", lower):
        result["errors"].append("Detected rgba(); use HEX plus fill-opacity/stroke-opacity")

    for elem in root.iter():
        tag = _local_name(elem.tag)
        if tag in FORBIDDEN_TAGS:
            result["errors"].append(f"Detected forbidden <{tag}> element")
        if "class" in elem.attrib:
            result["errors"].append("Detected forbidden class attribute")
        if any(name.lower().startswith("on") for name in elem.attrib):
            result["errors"].append("Detected forbidden event handler attribute")
        if tag != "image" and any(_local_name(name) == "clip-path" for name in elem.attrib):
            result["errors"].append("clip-path is only allowed on <image> elements")


def _check_fonts(root: ET.Element, result: dict) -> None:
    for elem in root.iter():
        if _local_name(elem.tag) != "text":
            continue
        family = elem.get("font-family")
        if not family:
            result["warnings"].append("A <text> element is missing font-family")
            continue
        normalized = family.lower().replace('"', "").replace("'", "")
        if not any(token in normalized for token in SAFE_FONT_TOKENS):
            result["warnings"].append(f"Font family may not be PowerPoint-safe: {family}")


def _check_images(root: ET.Element, svg_path: Path, result: dict) -> None:
    for elem in root.iter():
        if _local_name(elem.tag) != "image":
            continue
        href = elem.get("href") or elem.get(f"{{{XLINK_NS}}}href")
        if not href:
            result["errors"].append("<image> is missing href")
            continue
        if href.startswith("data:image/"):
            continue
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"}:
            result["warnings"].append(f"Remote image href may not export reliably: {href}")
            continue
        image_path = Path(href)
        candidates = [
            image_path,
            svg_path.parent / href,
            svg_path.parent.parent / href,
        ]
        if not any(path.exists() for path in candidates):
            result["errors"].append(f"Image file not found: {href}")
