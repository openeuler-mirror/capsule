import base64
import mimetypes
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def finalize_svg_files(svg_paths: list[str | Path], save_dir: str | Path) -> list[str]:
    """Copy generated SVGs into svg_final and embed local image references."""
    final_dir = Path(save_dir) / "svg_final"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)

    final_paths = []
    for svg_path in svg_paths:
        source_path = Path(svg_path)
        target_path = final_dir / source_path.name
        shutil.copy2(source_path, target_path)
        _embed_local_images(target_path, source_path.parent)
        final_paths.append(str(target_path.resolve()))

    return final_paths


def _embed_local_images(svg_path: Path, source_dir: Path) -> None:
    try:
        tree = ET.parse(svg_path)
    except ET.ParseError:
        return

    root = tree.getroot()
    changed = False
    for elem in root.iter():
        if _local_name(elem.tag) != "image":
            continue
        attr_name = "href" if elem.get("href") is not None else f"{{{XLINK_NS}}}href"
        href = elem.get(attr_name)
        if not href or href.startswith("data:image/"):
            continue
        if urlparse(href).scheme in {"http", "https"}:
            continue

        image_path = _resolve_image_path(href, svg_path.parent, source_dir)
        if image_path is None:
            continue
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        elem.set(attr_name, f"data:{mime_type};base64,{encoded}")
        changed = True

    if changed:
        tree.write(svg_path, encoding="unicode", xml_declaration=False)


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
