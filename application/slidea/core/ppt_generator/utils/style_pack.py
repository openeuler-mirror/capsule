"""Style-pack validation, outline-time selection and prompt loading."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from langchain.messages import HumanMessage
from pydantic import BaseModel, Field, TypeAdapter

from core.ppt_generator.utils.svg_to_pptx.drawingml_transform import parse_transform_info
from core.utils.llm import InvokeOptions, ModelRoute, llm_invoke
from core.utils.logger import logger


STYLE_PACK_FILENAME = "style-pack.json"
STYLE_PACK_VERSION = 1
STYLE_ASSIGNMENT_BATCH_SIZE = 12
STYLE_ASSIGNMENT_MAX_ATTEMPTS = 3
PAGE_TYPES = {"cover", "toc", "separator", "content", "thanks"}
DENSITIES = {"sparse", "medium", "dense"}
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
STYLE_SHELL_ATTR = "data-slidea-style-shell"
STYLE_SHELL_DEF_ATTR = "data-slidea-style-shell-def"
STYLE_REFERENCE_ONLY_PREFIX = "style-reference-only/"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class StyleReferenceAssignment(BaseModel):
    page_index: int = Field(description="目标 PPT 页的 index")
    style_reference_id: str = Field(description="style-pack.json 中存在的参考页 id")


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"style pack path escapes root: {relative}")
    return candidate


def _required_text(page: dict[str, Any], field: str) -> str:
    value = page.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"every style-pack page requires non-empty {field}")
    return value.strip()


def validate_style_pack(style_pack_dir: str | Path) -> dict[str, Any]:
    """Validate the Agent-authored manifest and every selected SVG path."""
    root = Path(style_pack_dir).resolve()
    manifest_path = root / STYLE_PACK_FILENAME
    if not root.is_dir() or not manifest_path.is_file():
        raise ValueError(f"style pack must contain Agent-authored {STYLE_PACK_FILENAME}: {root}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid {STYLE_PACK_FILENAME} JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("style-pack.json root must be an object")
    if manifest.get("version") != STYLE_PACK_VERSION:
        raise ValueError(f"unsupported style pack version: {manifest.get('version')!r}")

    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("style pack has no selected reference pages")

    seen_ids: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("every style-pack page must be an object")
        page_id = _required_text(page, "id")
        if page_id in seen_ids:
            raise ValueError(f"duplicate style-pack page id: {page_id}")
        seen_ids.add(page_id)

        svg = _required_text(page, "svg")
        svg_path = _safe_child(root, svg)
        if not svg_path.is_file() or svg_path.suffix.lower() != ".svg":
            raise ValueError(f"missing reference SVG: {svg}")

        source_slide = page.get("source_slide")
        if not isinstance(source_slide, int) or isinstance(source_slide, bool) or source_slide < 1:
            raise ValueError(f"style-pack page {page_id} requires a positive integer source_slide")

        page_type = _required_text(page, "page_type")
        if page_type not in PAGE_TYPES:
            raise ValueError(
                f"style-pack page {page_id} has invalid page_type {page_type!r}; "
                f"expected one of {sorted(PAGE_TYPES)}"
            )
        density = _required_text(page, "density")
        if density not in DENSITIES:
            raise ValueError(
                f"style-pack page {page_id} has invalid density {density!r}; "
                f"expected one of {sorted(DENSITIES)}"
            )
        _required_text(page, "structure")
        _required_text(page, "description")

    global_style = manifest.get("global_style")
    if global_style is not None and not isinstance(global_style, (str, dict)):
        raise ValueError("global_style must be a string or object when provided")
    return manifest


def copy_style_pack_into_run(source_dir: str | Path, run_dir: str | Path) -> str:
    """Validate and snapshot a style pack under one run directory."""
    source = Path(source_dir).resolve()
    validate_style_pack(source)
    target = Path(run_dir).resolve() / "style_pack"
    if source == target:
        return str(target)
    if target.exists():
        # A resumed run keeps the original immutable snapshot.
        validate_style_pack(target)
        return str(target)
    shutil.copytree(source, target)
    validate_style_pack(target)
    return str(target)


def style_reference_catalog(style_pack_dir: str | Path) -> list[dict[str, Any]]:
    """Return only the Agent-authored descriptions needed by outline selection."""
    manifest = validate_style_pack(style_pack_dir)
    return [
        {
            "id": page["id"],
            "page_type": page["page_type"],
            "density": page["density"],
            "structure": page["structure"],
            "description": page["description"],
        }
        for page in manifest["pages"]
    ]


def bind_style_reference_paths(outline: list[Any], style_pack_dir: str | Path) -> None:
    """Resolve model-selected ids to SVG paths without making a selection decision."""
    root = Path(style_pack_dir).resolve()
    manifest = validate_style_pack(root)
    pages_by_id = {str(page["id"]): page for page in manifest["pages"]}
    for page in outline:
        reference_id = str(getattr(page, "style_reference_id", "") or "")
        if not reference_id:
            raise ValueError(f"outline page {page.index} has no style_reference_id")
        reference = pages_by_id.get(reference_id)
        if reference is None:
            raise ValueError(
                f"outline page {page.index} selected unknown style reference {reference_id!r}"
            )
        page.style_reference_svg = str(_safe_child(root, str(reference["svg"])))
        page.style_reference_page_type = str(reference["page_type"])


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child(root: ET.Element, element_id: str) -> ET.Element | None:
    return next((child for child in root if child.get("id") == element_id), None)


def _image_href(element: ET.Element) -> str:
    return element.get("href") or element.get(f"{{{XLINK_NS}}}href") or ""


def _set_image_href(element: ET.Element, href: str) -> None:
    element.set("href", href)
    if f"{{{XLINK_NS}}}href" in element.attrib:
        element.set(f"{{{XLINK_NS}}}href", href)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _copy_fixed_style_asset(
    href: str,
    *,
    reference_svg: Path,
    style_pack_root: Path,
    slides_root: Path,
    asset_dir: Path,
) -> str:
    """Copy one master/layout image into the generated slides image tree."""
    if href.startswith("data:image/"):
        return href

    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"fixed style asset must be a local file or data URI: {href}")
    source = (reference_svg.parent / unquote(parsed.path)).resolve()
    if not _is_within(source, style_pack_root):
        raise ValueError(f"fixed style asset escapes style pack: {href}")
    if not source.is_file():
        raise ValueError(f"missing fixed style asset: {source}")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    suffix = source.suffix.lower() or ".bin"
    target = asset_dir / f"{digest}{suffix}"
    if not target.exists():
        shutil.copy2(source, target)
    return target.relative_to(slides_root).as_posix()


def _prepare_runtime_reference(
    source: Path,
    target: Path,
    *,
    page: Any,
    style_pack_root: Path,
    slides_root: Path,
    asset_dir: Path,
) -> None:
    try:
        tree = ET.parse(source)
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"invalid style reference SVG {source}: {error}") from error
    root = tree.getroot()
    if _local_name(root.tag) != "svg":
        raise ValueError(f"style reference is not an SVG document: {source}")

    fixed_nodes: set[int] = set()
    for group_id in ("background", "master-content", "layout-content"):
        group = _direct_child(root, group_id)
        if group is not None:
            fixed_nodes.update(id(item) for item in group.iter())
    # Title text and its visual backdrop are sometimes separate slide-level
    # OOXML shapes. They are part of the deterministic shell as well, so their
    # texture/logo images must be published with the inherited fixed assets.
    for group in _reference_title_shell_nodes(root, page):
        fixed_nodes.update(id(item) for item in group.iter())

    for image in (item for item in root.iter() if _local_name(item.tag) == "image"):
        href = _image_href(image)
        if id(image) in fixed_nodes:
            if not href:
                raise ValueError(f"fixed style image has no href in {source}")
            runtime_href = _copy_fixed_style_asset(
                href,
                reference_svg=source,
                style_pack_root=style_pack_root,
                slides_root=slides_root,
                asset_dir=asset_dir,
            )
        else:
            # Slide-body images usually contain the source deck's business content.
            # Keep only an explicit unavailable marker in the prompt copy so the
            # model can understand the image slot without being able to reuse it.
            basename = Path(unquote(urlparse(href).path)).name or "reference-image"
            runtime_href = STYLE_REFERENCE_ONLY_PREFIX + basename
        _set_image_href(image, runtime_href)

    target.parent.mkdir(parents=True, exist_ok=True)
    tree.write(target, encoding="unicode", xml_declaration=False)


def prepare_style_runtime_references(
    outline: list[Any],
    style_pack_dir: str | Path,
    slides_dir: str | Path,
) -> None:
    """Prepare prompt-safe references and copy fixed style assets before fan-out.

    The immutable style-pack snapshot remains untouched. Each outline page is
    rebound to a runtime SVG under ``slides/style_references``. Only images in
    ``master-content`` or ``layout-content`` are copied into
    ``slides/images/style-pack``; slide-body images are never published to the
    generated slide asset directory.
    """
    style_pack_root = Path(style_pack_dir).resolve()
    slides_root = Path(slides_dir).resolve()
    reference_dir = slides_root / "style_references"
    asset_dir = slides_root / "images" / "style-pack"
    reference_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    prepared: dict[Path, Path] = {}
    for page in outline:
        source_value = str(getattr(page, "style_reference_svg", "") or "")
        if not source_value:
            raise ValueError(f"outline page {page.index} has no resolved style reference SVG")
        source = Path(source_value).resolve()
        if not _is_within(source, style_pack_root):
            raise ValueError(f"style reference escapes style pack: {source}")
        if source not in prepared:
            relative = source.relative_to(style_pack_root).as_posix()
            suffix = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]
            target = reference_dir / f"{source.stem}-{suffix}.svg"
            _prepare_runtime_reference(
                source,
                target,
                page=page,
                style_pack_root=style_pack_root,
                slides_root=slides_root,
                asset_dir=asset_dir,
            )
            prepared[source] = target
        page.style_reference_svg = str(prepared[source])


def _float_attr(element: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(element.get(name, default))
    except (TypeError, ValueError):
        return default


def _text_elements(element: ET.Element) -> list[ET.Element]:
    return [item for item in element.iter() if _local_name(item.tag) == "text"]


def _max_font_size(element: ET.Element) -> float:
    return max((_float_attr(text, "font-size") for text in _text_elements(element)), default=0.0)


def _min_text_y(element: ET.Element) -> float:
    return min((_float_attr(text, "y", 10_000.0) for text in _text_elements(element)), default=10_000.0)


def _multiply_matrix(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_point(
    matrix: tuple[float, float, float, float, float, float],
    x: float,
    y: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _number_list(value: str | None) -> list[float]:
    return [
        float(item)
        for item in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", value or "")
    ]


def _local_geometry_points(element: ET.Element) -> list[tuple[float, float]]:
    tag = _local_name(element.tag)
    if tag in {"rect", "image"}:
        x, y = _float_attr(element, "x"), _float_attr(element, "y")
        width, height = _float_attr(element, "width"), _float_attr(element, "height")
        return [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]
    if tag == "text":
        x, y = _float_attr(element, "x"), _float_attr(element, "y")
        size = max(1.0, _float_attr(element, "font-size", 16.0))
        width = max(
            _float_attr(element, "textLength"),
            _float_attr(element, "data-measured-width"),
            len(element.text or "") * size * 0.55,
        )
        return [(x, y - size), (x + width, y)]
    if tag == "line":
        return [
            (_float_attr(element, "x1"), _float_attr(element, "y1")),
            (_float_attr(element, "x2"), _float_attr(element, "y2")),
        ]
    if tag == "circle":
        cx, cy, radius = (
            _float_attr(element, "cx"),
            _float_attr(element, "cy"),
            abs(_float_attr(element, "r")),
        )
        return [(cx - radius, cy - radius), (cx + radius, cy + radius)]
    if tag == "ellipse":
        cx, cy = _float_attr(element, "cx"), _float_attr(element, "cy")
        rx, ry = abs(_float_attr(element, "rx")), abs(_float_attr(element, "ry"))
        return [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    if tag in {"polygon", "polyline"}:
        values = _number_list(element.get("points"))
        return list(zip(values[0::2], values[1::2]))
    if tag == "path":
        # ooxml-svg emits absolute M/L/C/Q paths for custom DrawingML geometry.
        values = _number_list(element.get("d"))
        return list(zip(values[0::2], values[1::2]))
    return []


def _visual_bounds(element: ET.Element) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []

    def visit(
        node: ET.Element,
        parent_matrix: tuple[float, float, float, float, float, float],
    ) -> None:
        matrix = _multiply_matrix(
            parent_matrix,
            parse_transform_info(node.get("transform", "")).matrix,
        )
        points.extend(_transform_point(matrix, x, y) for x, y in _local_geometry_points(node))
        for child in node:
            visit(child, matrix)

    visit(element, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _substantially_overlaps(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> bool:
    if first is None or second is None:
        return False
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    smaller = min(first_area, second_area)
    return smaller > 0 and intersection / smaller >= 0.35


def _reference_title_group(reference_root: ET.Element, page: Any) -> ET.Element | None:
    # Prefer an explicit main-content title for every page role. Some template
    # producers serialize TOC/thanks titles on the slide itself rather than in
    # layout-content; assuming those titles are inherited silently drops them.
    reference_page_type = str(getattr(page, "style_reference_page_type", "") or "")
    main = _direct_child(reference_root, "main-content")
    if main is None:
        return None
    groups = [child for child in main if _local_name(child.tag) == "g" and _text_elements(child)]
    explicit = next((group for group in groups if group.get("data-role") == "header"), None)
    if explicit is not None:
        return explicit
    if reference_page_type in {"content", "toc"} or (
        not reference_page_type and int(getattr(page, "type", 1)) in {1, 2}
    ):
        candidates = [
            group for group in groups
            if _min_text_y(group) <= 120.0 and _max_font_size(group) >= 24.0
        ]
        return max(candidates, key=_max_font_size, default=None)
    # Separator references are often centered rather than top-aligned. Cover
    # references normally carry data-role=header, but largest-text is a safe
    # fallback for converters that omitted placeholder metadata.
    return max(groups, key=_max_font_size, default=None)


def _reference_title_shell_nodes(reference_root: ET.Element, page: Any) -> list[ET.Element]:
    """Return the title text box plus separate overlapping visual backdrops."""
    title = _reference_title_group(reference_root, page)
    main = _direct_child(reference_root, "main-content")
    if title is None or main is None:
        return []
    title_bounds = _visual_bounds(title)
    result: list[ET.Element] = []
    for child in main:
        if child is title:
            result.append(child)
            continue
        if _text_elements(child):
            continue
        if _substantially_overlaps(_visual_bounds(child), title_bounds):
            result.append(child)
    return result


def _replace_group_text(group: ET.Element, value: str) -> None:
    texts = _text_elements(group)
    if not texts:
        return
    first = texts[0]
    first.text = value
    for attr in ("textLength", "lengthAdjust", "data-measured-width"):
        first.attrib.pop(attr, None)
    parent_by_child = {child: parent for parent in group.iter() for child in parent}
    for extra in texts[1:]:
        parent = parent_by_child.get(extra)
        if parent is not None:
            parent.remove(extra)


def _update_page_number(element: ET.Element, page_number: int, slide_height: float) -> None:
    for text in _text_elements(element):
        value = (text.text or "").strip()
        if not re.fullmatch(r"\d{1,3}", value) or _float_attr(text, "y") < slide_height * 0.8:
            continue
        replacement = str(page_number)
        if len(value) > 1 and value.startswith("0"):
            replacement = replacement.zfill(len(value))
        text.text = replacement
        for attr in ("textLength", "lengthAdjust", "data-measured-width"):
            text.attrib.pop(attr, None)


def _referenced_definition_ids(nodes: list[ET.Element], defs: ET.Element | None) -> set[str]:
    if defs is None:
        return set()
    by_id = {item.get("id"): item for item in defs.iter() if item.get("id")}
    pending: list[str] = []
    pattern = re.compile(r"url\(#([^)]+)\)")

    def collect(node: ET.Element) -> None:
        for item in node.iter():
            for value in item.attrib.values():
                pending.extend(pattern.findall(value))
                if value.startswith("#"):
                    pending.append(value[1:])

    for node in nodes:
        collect(node)
    result: set[str] = set()
    while pending:
        definition_id = pending.pop()
        if definition_id in result or definition_id not in by_id:
            continue
        result.add(definition_id)
        collect(by_id[definition_id])
    return result


def _clone_reference_nodes(
    reference_root: ET.Element,
    nodes: list[ET.Element],
) -> tuple[list[ET.Element], list[ET.Element]]:
    clones = [copy.deepcopy(node) for node in nodes]
    reference_defs = next(
        (child for child in reference_root if _local_name(child.tag) == "defs"),
        None,
    )
    definition_ids = _referenced_definition_ids(nodes, reference_defs)
    definition_clones = []
    if reference_defs is not None:
        for child in reference_defs:
            if child.get("id") in definition_ids:
                definition_clones.append(copy.deepcopy(child))

    all_roots = [*clones, *definition_clones]
    id_map: dict[str, str] = {}
    for root in all_roots:
        for item in root.iter():
            old_id = item.get("id")
            if old_id:
                id_map[old_id] = f"slidea-style-{old_id}"
    pattern = re.compile(r"url\(#([^)]+)\)")
    for root in all_roots:
        for item in root.iter():
            old_id = item.get("id")
            if old_id in id_map:
                item.set("id", id_map[old_id])
            for attr, value in list(item.attrib.items()):
                value = pattern.sub(lambda match: f"url(#{id_map.get(match.group(1), match.group(1))})", value)
                if value.startswith("#") and value[1:] in id_map:
                    value = "#" + id_map[value[1:]]
                item.set(attr, value)
    return clones, definition_clones


def _is_generated_shell_group(group: ET.Element, *, remove_top_title: bool) -> bool:
    element_id = (group.get("id") or "").lower().replace("_", "-")
    role = (group.get("data-role") or "").lower()
    if role in {"header", "footer", "page-number"}:
        return True
    if element_id in {"background", "slide-background", "master-content", "layout-content"}:
        return True
    if "footer" in element_id or "page-number" in element_id:
        return True
    title_tokens = (
        "header", "page-title", "main-title", "cover-title", "toc-title",
        "section-title", "closing-title", "thanks-title",
    )
    if any(token in element_id for token in title_tokens):
        return True
    return remove_top_title and _min_text_y(group) <= 120.0 and _max_font_size(group) >= 24.0


def _has_vector_graphics(group: ET.Element) -> bool:
    graphic_tags = {"circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect"}
    return any(_local_name(item.tag) in graphic_tags for item in group.iter())


def _reference_body_top(reference_root: ET.Element) -> float:
    main = _direct_child(reference_root, "main-content")
    if main is None:
        return 0.0
    shell_ids = {id(item) for item in _reference_title_shell_nodes(reference_root, object())}
    candidates = []
    for child in main:
        if id(child) in shell_ids:
            continue
        bounds = _visual_bounds(child)
        if bounds is not None and bounds[1] >= 120.0 and bounds[3] - bounds[1] > 40.0:
            candidates.append(bounds[1])
    return min(candidates, default=0.0)


def _remove_special_page_redesigns(generated_root: ET.Element, reference_root: ET.Element, page: Any) -> None:
    """Keep only the dynamic portion allowed by special-page style mode."""
    reference_page_type = str(getattr(page, "style_reference_page_type", "") or "")
    if reference_page_type not in {"cover", "toc", "separator", "thanks"}:
        return
    toc_body_top = _reference_body_top(reference_root) if reference_page_type == "toc" else 0.0
    for child in list(generated_root):
        if _local_name(child.tag) != "g" or child.get(STYLE_SHELL_ATTR) == "true":
            continue
        remove = False
        if reference_page_type == "separator":
            remove = True
        elif reference_page_type in {"cover", "thanks"}:
            # Cover/thanks mode may keep simple subtitle/closing text, but
            # never a model-invented illustration, card system or concept
            # diagram.
            remove = not _text_elements(child) or _has_vector_graphics(child)
        elif reference_page_type == "toc":
            # Keep directory entries inside the source placeholder; discard
            # model-invented headings and free-floating decorative groups.
            remove = not _text_elements(child) or (
                toc_body_top > 0 and _min_text_y(child) < toc_body_top
            )
        if remove:
            generated_root.remove(child)


def apply_style_reference_shell(svg_content: str, page: Any) -> str:
    """Deterministically restore fixed style layers after model generation.

    This is deliberately a no-op for pages without ``style_reference_svg`` so
    the original non-style-pack route remains byte-for-byte unchanged.
    """
    reference_value = str(getattr(page, "style_reference_svg", "") or "")
    if not reference_value:
        return svg_content
    try:
        generated_root = ET.fromstring(svg_content)
        reference_root = ET.parse(reference_value).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"cannot compose style reference shell: {error}") from error

    generated_defs = next(
        (child for child in generated_root if _local_name(child.tag) == "defs"),
        None,
    )
    if generated_defs is None:
        generated_defs = ET.Element(f"{{{SVG_NS}}}defs")
        generated_root.insert(0, generated_defs)
    for child in list(generated_defs):
        if child.get(STYLE_SHELL_DEF_ATTR) == "true":
            generated_defs.remove(child)
    for child in list(generated_root):
        if child is generated_defs:
            continue
        if child.get(STYLE_SHELL_ATTR) == "true":
            generated_root.remove(child)

    title = _reference_title_group(reference_root, page)
    title_shell_nodes = _reference_title_shell_nodes(reference_root, page)
    for child in list(generated_root):
        if child is generated_defs or _local_name(child.tag) != "g":
            continue
        if _is_generated_shell_group(child, remove_top_title=title is not None):
            generated_root.remove(child)
    _remove_special_page_redesigns(generated_root, reference_root, page)

    fixed_pairs: list[tuple[str, ET.Element]] = []
    for name, group_id in (
        ("background", "background"),
        ("master", "master-content"),
        ("layout", "layout-content"),
    ):
        group = _direct_child(reference_root, group_id)
        if group is not None:
            fixed_pairs.append((name, group))
    reference_nodes = [group for _, group in fixed_pairs] + title_shell_nodes
    clones, definition_clones = _clone_reference_nodes(reference_root, reference_nodes)
    for definition in definition_clones:
        definition.set(STYLE_SHELL_DEF_ATTR, "true")
        generated_defs.append(definition)

    slide_height = _float_attr(reference_root, "height", 720.0)
    if slide_height <= 0:
        slide_height = 720.0
    insertion_index = list(generated_root).index(generated_defs) + 1
    fixed_count = len(fixed_pairs)
    for index, clone in enumerate(clones):
        clone.set(STYLE_SHELL_ATTR, "true")
        if index < fixed_count:
            clone.set("id", f"slidea-style-{fixed_pairs[index][0]}")
            _update_page_number(clone, int(page.index) + 1, slide_height)
        else:
            shell_source = title_shell_nodes[index - fixed_count]
            if shell_source is not title:
                clone.set("id", f"slidea-style-title-shell-{index - fixed_count + 1}")
                generated_root.insert(insertion_index, clone)
                insertion_index += 1
                continue
            clone.set("id", "slidea-style-page-title")
            # A selected thanks page already carries the template's deliberate
            # closing phrase. Keep that generic fixed title verbatim; replacing
            # it with an outline label such as “致谢页” degrades the result.
            if str(getattr(page, "style_reference_page_type", "") or "") != "thanks":
                _replace_group_text(clone, str(getattr(page, "title", "")))
        generated_root.insert(insertion_index, clone)
        insertion_index += 1

    return ET.tostring(generated_root, encoding="unicode", xml_declaration=False)


def extract_style_dynamic_content(svg_content: str) -> str:
    """Return a standalone SVG containing only model-authored dynamic nodes.

    ``apply_style_reference_shell`` marks every injected top-level shell group
    and copied definition.  Removing only those marked nodes is therefore
    deterministic and does not depend on model-chosen ids such as
    ``main-content``.  Unstyled SVGs are returned byte-for-byte unchanged.
    """
    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError as error:
        raise ValueError(f"cannot extract style dynamic content: {error}") from error

    has_shell = any(
        item.get(STYLE_SHELL_ATTR) == "true"
        or item.get(STYLE_SHELL_DEF_ATTR) == "true"
        for item in root.iter()
    )
    if not has_shell:
        return svg_content

    dynamic_root = ET.Element(root.tag, dict(root.attrib))
    dynamic_root.text = root.text

    def clone_without_shell_nodes(element: ET.Element) -> ET.Element | None:
        if (
            element.get(STYLE_SHELL_ATTR) == "true"
            or element.get(STYLE_SHELL_DEF_ATTR) == "true"
        ):
            return None
        clone = copy.copy(element)
        clone[:] = []
        for child in element:
            child_clone = clone_without_shell_nodes(child)
            if child_clone is not None:
                clone.append(child_clone)
        return clone

    for child in root:
        clone = clone_without_shell_nodes(child)
        if clone is not None:
            dynamic_root.append(clone)

    return ET.tostring(dynamic_root, encoding="unicode", xml_declaration=False)


def apply_style_reference_shell_file(svg_path: str | Path, page: Any) -> None:
    path = Path(svg_path)
    content = path.read_text(encoding="utf-8")
    composed = apply_style_reference_shell(content, page)
    if composed != content:
        path.write_text(composed, encoding="utf-8")


def _target_page_type(page: Any, position: int, total: int) -> str:
    value = int(page.type)
    if value == 4:
        return "cover" if position == 0 else "thanks" if position == total - 1 else "cover"
    return {1: "content", 2: "toc", 3: "separator"}.get(value, "content")


def _outline_batches(outline: list[Any]) -> list[list[Any]]:
    """Keep chapter groups together, then bound prompt size inside large chapters."""
    groups: OrderedDict[int, list[Any]] = OrderedDict()
    for page in outline:
        groups.setdefault(int(getattr(page, "source", -1)), []).append(page)
    batches: list[list[Any]] = []
    for pages in groups.values():
        for start in range(0, len(pages), STYLE_ASSIGNMENT_BATCH_SIZE):
            batches.append(pages[start:start + STYLE_ASSIGNMENT_BATCH_SIZE])
    return batches


def _valid_assignments(
    assignments: Any,
    expected_indices: set[int],
    valid_reference_ids: set[str],
) -> dict[int, str] | None:
    if not isinstance(assignments, list):
        return None
    result: dict[int, str] = {}
    for item in assignments:
        if not isinstance(item, dict):
            return None
        try:
            page_index = int(item["page_index"])
            reference_id = str(item["style_reference_id"])
        except (KeyError, TypeError, ValueError):
            return None
        if page_index in result or page_index not in expected_indices:
            return None
        if reference_id not in valid_reference_ids:
            return None
        result[page_index] = reference_id
    return result if set(result) == expected_indices else None


async def assign_style_references_for_outline(
    outline: list[Any],
    style_pack_dir: str | Path,
) -> None:
    """Ask the outline-stage model to select layouts by type, density and structure."""
    catalog = style_reference_catalog(style_pack_dir)
    catalog_text = json.dumps(catalog, ensure_ascii=False, indent=2)
    valid_reference_ids = {str(item["id"]) for item in catalog}
    schema = TypeAdapter(list[StyleReferenceAssignment]).json_schema()

    for batch_number, batch in enumerate(_outline_batches(outline), 1):
        targets = [
            {
                "page_index": page.index,
                "page_type": _target_page_type(page, page.index, len(outline)),
                "title": page.title,
                "abstract": page.abstract,
                "has_reference_images": bool(getattr(page, "reference_images", None)),
            }
            for page in batch
        ]
        expected_indices = {int(page.index) for page in batch}
        correction = ""
        selected: dict[int, str] | None = None
        for attempt in range(STYLE_ASSIGNMENT_MAX_ATTEMPTS):
            prompt = f"""
你是 PPT 大纲阶段的版式参考分配器。请为下面每一页目标大纲选择一个 style pack 参考页。

# 选择原则
1. 只根据页面类型、信息密度和版式结构选择，不按主题词、行业词或语义相似度选择。
2. 页面类型优先：cover、toc、separator、content、thanks 应优先精确匹配。
3. 再根据 title、abstract 和 has_reference_images 判断目标页适合稀疏/中等/密集布局，以及分栏、卡片、流程、对比、图文、时间线等结构。
4. 参考页的结构和效果以 Agent 在 style-pack.json 中写的 structure、description 为准。
5. 必须为输入中的每个 page_index 返回且只返回一条分配；只能使用给定参考页 id。

# Agent 编写的参考页目录
{catalog_text}

# 当前章节或分批的大纲页
{json.dumps(targets, ensure_ascii=False, indent=2)}

# 输出要求
只输出 JSON list。每个对象必须且只能包含：
- page_index: int
- style_reference_id: string
{correction}
"""
            try:
                assignments = await llm_invoke(
                    ModelRoute.PREMIUM,
                    [HumanMessage(content=prompt)],
                    InvokeOptions(
                        json_schema=schema,
                        work_node=f"outline_style_assignment[{batch_number}]",
                    ),
                )
            except Exception as error:
                logger.warning(
                    f"outline style assignment batch {batch_number} attempt "
                    f"{attempt + 1}/{STYLE_ASSIGNMENT_MAX_ATTEMPTS} failed: {error}"
                )
                assignments = None
            selected = _valid_assignments(assignments, expected_indices, valid_reference_ids)
            if selected is not None:
                break
            correction = (
                "\n上一次输出缺页、重复 page_index、包含未知 id 或格式错误。"
                "请重新为本批全部页面输出严格的一一对应关系。"
            )
        if selected is None:
            raise ValueError(
                f"model failed to assign valid style references for outline batch {batch_number}"
            )
        for page in batch:
            page.style_reference_id = selected[int(page.index)]

    bind_style_reference_paths(outline, style_pack_dir)


def reference_svg_for_page(
    page: Any,
    default_template: str,
    max_chars: int = 120_000,
) -> tuple[str, bool]:
    path_value = getattr(page, "style_reference_svg", "")
    if not path_value:
        return default_template, False
    try:
        content = Path(path_value).read_text(encoding="utf-8")
        if len(content) > max_chars:
            logger.warning(f"style reference {path_value} is large; truncating prompt copy")
            content = content[:max_chars]
        return content, True
    except OSError as error:
        logger.warning(f"failed to load per-page style reference {path_value}: {error}")
        return default_template, False
