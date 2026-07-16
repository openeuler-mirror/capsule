"""Inline local image references inside SVG files and SVG content.

Used by two callers:

- ``svgs_to_pptx`` (PPTX export): rewrites image hrefs in a temporary SVG copy
  so the PPTX converter receives self-contained input.
- VLM review's screenshot step: inlines images in memory before rasterizing
  via CairoSVG, so referenced images render correctly.

Public API:
- ``embed_local_images_in_file(svg_path, source_dir)`` — rewrite a file in place.
- ``embed_local_images_in_content(svg_content, source_dir)`` — rewrite a string in memory.
"""

import base64
import mimetypes
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def embed_local_images_in_file(svg_path: Path | str, source_dir: Path | str) -> bool:
    """Inline local image references in an SVG file in place.

    ``source_dir`` is the directory used to resolve relative image hrefs (typical
    value: the SVG's own parent directory, where ``images/<name>`` lives). HTTP(S)
    and ``data:`` hrefs are left untouched. Returns True if any image was embedded.
    """
    p = Path(svg_path)
    try:
        tree = ET.parse(p)
    except ET.ParseError:
        return False
    root = tree.getroot()
    if _embed_images_in_root(root, p.parent, Path(source_dir)):
        tree.write(p, encoding="unicode", xml_declaration=False)
        return True
    return False


def embed_local_images_in_content(svg_content: str, source_dir: Path | str) -> str:
    """Return ``svg_content`` with all local <image> hrefs resolved to data URIs.

    Memory-only variant of :func:`embed_local_images_in_file`, used when the SVG
    is being fed straight to a rasterizer (e.g. CairoSVG for VLM screenshots)
    and never lands on disk in its intermediate form.
    """
    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError:
        return svg_content

    source_dir = Path(source_dir)
    if not _embed_images_in_root(root, source_dir, source_dir):
        return svg_content
    return ET.tostring(root, encoding="unicode")


def _embed_images_in_root(root: ET.Element, search_dir: Path, source_dir: Path) -> bool:
    changed = False
    for elem in root.iter():
        if _local_name(elem.tag) != "image":
            continue
        xlink_attr = f"{{{XLINK_NS}}}href"
        href = elem.get("href") or elem.get(xlink_attr)
        if not href:
            continue
        if href.startswith("data:image/"):
            # Some SVG renderers still prefer xlink:href over SVG2 href. Keep
            # both declarations synchronized so screenshots and PPTX export
            # consume the same embedded image.
            if elem.get("href") != href:
                elem.set("href", href)
                changed = True
            if xlink_attr in elem.attrib and elem.get(xlink_attr) != href:
                elem.set(xlink_attr, href)
                changed = True
            continue
        if urlparse(href).scheme in {"http", "https"}:
            continue

        image_path = _resolve_image_path(href, search_dir, source_dir)
        if image_path is None:
            continue
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        data_uri = f"data:{mime_type};base64,{encoded}"
        elem.set("href", data_uri)
        if xlink_attr in elem.attrib:
            elem.set(xlink_attr, data_uri)
        changed = True
    return changed


def _resolve_image_path(href: str, final_dir: Path, source_dir: Path) -> Path | None:
    raw_path = Path(href)
    candidates = [
        raw_path,
        final_dir / href,
        final_dir.parent / href,
        source_dir / href,
        source_dir.parent / href,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
