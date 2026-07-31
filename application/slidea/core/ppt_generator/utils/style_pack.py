"""Style-pack validation, outline-time selection and prompt loading."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from langchain.messages import HumanMessage
from pydantic import BaseModel, Field, TypeAdapter

from core.ppt_generator.utils.svg_to_pptx.drawingml_transform import (
    AffineMatrix,
    parse_transform_info,
)
from core.utils.llm import InvokeOptions, llm_invoke
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
STYLE_SHELL_ONLY_ATTR = "data-slidea-style-shell-only"
STYLE_REUSABLE_ATTR = "data-slidea-style-reusable"
STYLE_REUSABLE_LAYER_ATTR = "data-slidea-style-layer"
STYLE_REFERENCE_ONLY_PREFIX = "style-reference-only/"
REUSABLE_LAYERS = {"back", "front"}
SPECIAL_SHELL_FALLBACK_TARGETS = {"cover", "thanks", "separator", "toc"}

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


def _is_local_file_reference(href: str) -> bool:
    if not href or href.startswith("data:"):
        return False
    parsed = urlparse(href)
    return not parsed.scheme and not parsed.netloc


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

    reusable_assets = manifest.get("reusable_assets", [])
    if not isinstance(reusable_assets, list):
        raise ValueError("reusable_assets must be an array when provided")
    reusable_paths: dict[Path, str] = {}
    reusable_ids: set[str] = set()
    for asset in reusable_assets:
        if not isinstance(asset, dict):
            raise ValueError("every reusable_assets entry must be an object")
        asset_id = _required_text(asset, "id")
        if asset_id in reusable_ids:
            raise ValueError(f"duplicate reusable asset id: {asset_id}")
        reusable_ids.add(asset_id)
        relative_path = _required_text(asset, "path")
        asset_path = _safe_child(root, relative_path)
        if not asset_path.is_file():
            raise ValueError(f"missing reusable asset: {relative_path}")
        if asset_path in reusable_paths:
            raise ValueError(
                f"reusable asset path {relative_path!r} is declared by both "
                f"{reusable_paths[asset_path]!r} and {asset_id!r}"
            )
        reusable_paths[asset_path] = asset_id
        _required_text(asset, "role")
        _required_text(asset, "reason")

    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("style pack has no selected reference pages")

    seen_ids: set[str] = set()
    used_reusable_paths: set[Path] = set()
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
        try:
            svg_root = ET.parse(svg_path).getroot()
        except (ET.ParseError, OSError) as error:
            raise ValueError(f"invalid reference SVG {svg}: {error}") from error
        if _local_name(svg_root.tag) != "svg":
            raise ValueError(f"reference file is not an SVG document: {svg}")

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
        layout_rules = page.get("layout_rules", [])
        if not isinstance(layout_rules, list) or any(
            not isinstance(rule, str) or not rule.strip() for rule in layout_rules
        ):
            raise ValueError(
                f"style-pack page {page_id} layout_rules must be an array of non-empty strings"
            )
        style_rules = page.get("style_rules", {})
        if not isinstance(style_rules, dict):
            raise ValueError(f"style-pack page {page_id} style_rules must be an object")
        _validate_machine_style_rules(style_rules, f"style-pack page {page_id}")

        fixed_elements = page.get("fixed_image_elements", [])
        if not isinstance(fixed_elements, list):
            raise ValueError(
                f"style-pack page {page_id} fixed_image_elements must be an array when provided"
            )
        main_content = _direct_child(svg_root, "main-content")
        seen_element_ids: set[str] = set()
        for fixed in fixed_elements:
            if not isinstance(fixed, dict):
                raise ValueError(
                    f"style-pack page {page_id} fixed_image_elements entries must be objects"
                )
            element_id = _required_text(fixed, "element_id")
            if element_id in seen_element_ids:
                raise ValueError(
                    f"style-pack page {page_id} has duplicate fixed image element {element_id!r}"
                )
            seen_element_ids.add(element_id)
            layer = _required_text(fixed, "layer")
            if layer not in REUSABLE_LAYERS:
                raise ValueError(
                    f"style-pack page {page_id} fixed image element {element_id!r} has invalid "
                    f"layer {layer!r}; expected one of {sorted(REUSABLE_LAYERS)}"
                )
            matches = (
                [child for child in main_content if child.get("id") == element_id]
                if main_content is not None else []
            )
            if len(matches) != 1:
                raise ValueError(
                    f"style-pack page {page_id} fixed image element {element_id!r} must identify "
                    "exactly one direct child of main-content"
                )
            element = matches[0]
            if any(_local_name(item.tag) == "text" for item in element.iter()):
                raise ValueError(
                    f"style-pack page {page_id} fixed image element {element_id!r} "
                    "must not contain text"
                )
            images = [item for item in element.iter() if _local_name(item.tag) == "image"]
            if not images:
                raise ValueError(
                    f"style-pack page {page_id} fixed image element {element_id!r} "
                    "must contain at least one image"
                )
            for image in images:
                href = _image_href(image)
                if not _is_local_file_reference(href):
                    raise ValueError(
                        f"style-pack page {page_id} fixed image element {element_id!r} "
                        "must use declared local image files"
                    )
                parsed = urlparse(href)
                image_path = (svg_path.parent / unquote(parsed.path)).resolve()
                if not _is_within(image_path, root) or not image_path.is_file():
                    raise ValueError(
                        f"style-pack page {page_id} fixed image element {element_id!r} "
                        f"references missing or unsafe image {href!r}"
                    )
                if image_path not in reusable_paths:
                    raise ValueError(
                        f"style-pack page {page_id} fixed image element {element_id!r} image "
                        f"{href!r} is not declared in reusable_assets"
                    )
                used_reusable_paths.add(image_path)

    unused_assets = set(reusable_paths) - used_reusable_paths
    if unused_assets:
        unused = ", ".join(sorted(reusable_paths[path] for path in unused_assets))
        raise ValueError(
            "every reusable asset must be used by at least one fixed_image_elements entry; "
            f"unused ids: {unused}"
        )

    global_style = manifest.get("global_style")
    if global_style is not None and not isinstance(global_style, (str, dict)):
        raise ValueError("global_style must be a string or object when provided")
    if isinstance(global_style, dict):
        geometry = global_style.get("geometry", {})
        if geometry is not None and not isinstance(geometry, dict):
            raise ValueError("global_style.geometry must be an object when provided")
        _validate_machine_style_rules(geometry or {}, "global_style.geometry")
        _validate_text_container_usage(global_style.get("text_container_usage"))
    return manifest


def _is_non_negative_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value >= 0


def _validate_machine_style_rules(rules: dict[str, Any], owner: str) -> None:
    for field in ("max_corner_radius_px", "max_rounded_rect_height_px"):
        value = rules.get(field)
        if value is not None and not _is_non_negative_number(value):
            raise ValueError(f"{owner}.{field} must be a non-negative number")


def _validate_text_container_usage(value: Any) -> None:
    """Validate the optional Agent-authored text/background design dimension."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("global_style.text_container_usage must be an object")
    preference = value.get("preference")
    if preference not in {"minimal", "selective", "frequent"}:
        raise ValueError(
            "global_style.text_container_usage.preference must be one of: "
            "minimal, selective, frequent"
        )
    rules = value.get("rules")
    if not isinstance(rules, list) or not rules or any(
        not isinstance(rule, str) or not rule.strip() for rule in rules
    ):
        raise ValueError(
            "global_style.text_container_usage.rules must be a non-empty array "
            "of non-empty strings"
        )


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
            "layout_rules": page.get("layout_rules", []),
        }
        for page in manifest["pages"]
    ]


def bind_style_reference_paths(outline: list[Any], style_pack_dir: str | Path) -> None:
    """Resolve selected ids and preserve per-page built-in fallbacks.

    A blank id means that the pack has no reference with the target page role.
    That page intentionally stays on the built-in template route while the
    remaining pages continue to use the pack.  Persisted assignments from an
    older run are also type-checked here. The only cross-role exception is a
    content reference reused as a shell-only cover/thanks fallback when the
    pack genuinely has no reference for that special role.
    """
    root = Path(style_pack_dir).resolve()
    manifest = validate_style_pack(root)
    pages_by_id = {str(page["id"]): page for page in manifest["pages"]}
    ordered_pages = sorted(outline, key=lambda item: int(item.index))
    total = len(ordered_pages)
    for position, page in enumerate(ordered_pages):
        reference_id = str(getattr(page, "style_reference_id", "") or "")
        if not reference_id:
            _clear_style_reference_runtime(page)
            continue
        reference = pages_by_id.get(reference_id)
        if reference is None:
            raise ValueError(
                f"outline page {page.index} selected unknown style reference {reference_id!r}"
            )
        target_page_type = _target_page_type(page, position, total)
        reference_page_type = str(reference["page_type"])
        shell_only_fallback = (
            target_page_type in SPECIAL_SHELL_FALLBACK_TARGETS
            and reference_page_type == "content"
            and not any(
                str(candidate["page_type"]) == target_page_type
                for candidate in manifest["pages"]
            )
        )
        if reference_page_type != target_page_type and not shell_only_fallback:
            logger.warning(
                f"outline page {page.index} targets {target_page_type!r} but selected "
                f"{reference_page_type!r} reference {reference_id!r}; using the built-in "
                "template for this page"
            )
            page.style_reference_id = ""
            _clear_style_reference_runtime(page)
            continue
        page.style_reference_svg = str(_safe_child(root, str(reference["svg"])))
        page.style_reference_page_type = (
            target_page_type if shell_only_fallback else reference_page_type
        )
        global_style = manifest.get("global_style", "")
        effective_rules: dict[str, Any] = {}
        if isinstance(global_style, dict):
            effective_rules.update(global_style.get("geometry") or {})
        effective_rules.update(reference.get("style_rules") or {})
        selected_layout = {
            "structure": reference["structure"],
            "description": reference["description"],
            "layout_rules": reference.get("layout_rules", []),
        }
        if shell_only_fallback:
            selected_layout = {
                "structure": "仅复用背景、母版、版式、页眉页脚和已授权装饰",
                "description": "内容页正文和标题已移除；特殊页只使用少量独立文字",
                "layout_rules": [],
            }
            logger.info(
                f"outline page {page.index} uses content reference {reference_id!r} "
                f"as a shell-only {target_page_type} fallback"
            )
        guidance = {
            "reference_id": reference_id,
            "global_style": global_style,
            "selected_layout": selected_layout,
            "effective_machine_rules": effective_rules,
        }
        page.style_reference_guidance = json.dumps(
            guidance,
            ensure_ascii=False,
            indent=2,
        )
        page.style_reference_rules = effective_rules


def _clear_style_reference_runtime(page: Any) -> None:
    """Clear only runtime-bound fields for one built-in fallback page."""
    page.style_reference_svg = ""
    page.style_reference_page_type = ""
    page.style_reference_guidance = ""
    page.style_reference_rules = {}


def style_guidance_for_page(page: Any) -> str:
    """Return Agent-authored style guidance already bound to one outline page."""
    return str(getattr(page, "style_reference_guidance", "") or "").strip()


def style_reference_is_shell_only(page: Any) -> bool:
    """Return whether the runtime reference intentionally contains only a shell."""
    path_value = str(getattr(page, "style_reference_svg", "") or "")
    if not path_value:
        return False
    try:
        root = ET.parse(path_value).getroot()
    except (ET.ParseError, OSError):
        return False
    return root.get(STYLE_SHELL_ONLY_ATTR) == "true"


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
    reference_page: dict[str, Any],
    style_pack_root: Path,
    slides_root: Path,
    asset_dir: Path,
    shell_only: bool = False,
) -> None:
    try:
        tree = ET.parse(source)
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"invalid style reference SVG {source}: {error}") from error
    root = tree.getroot()
    if _local_name(root.tag) != "svg":
        raise ValueError(f"style reference is not an SVG document: {source}")

    if shell_only:
        root.set(STYLE_SHELL_ONLY_ATTR, "true")

    fixed_nodes: set[int] = set()
    for group_id in ("background", "master-content", "layout-content"):
        group = _direct_child(root, group_id)
        if group is not None:
            fixed_nodes.update(id(item) for item in group.iter())
    # Title text and its visual backdrop are sometimes separate slide-level
    # OOXML shapes. They are part of the deterministic shell as well, so their
    # texture/logo images must be published with the inherited fixed assets.
    if not shell_only:
        for group in _reference_title_shell_nodes(root, page):
            fixed_nodes.update(id(item) for item in group.iter())

    reusable_by_id = {
        str(item["element_id"]): str(item["layer"])
        for item in reference_page.get("fixed_image_elements", [])
    }
    main_content = _direct_child(root, "main-content")
    if main_content is not None:
        for child in main_content:
            layer = reusable_by_id.get(child.get("id") or "")
            if layer is None:
                continue
            child.set(STYLE_REUSABLE_ATTR, "true")
            child.set(STYLE_REUSABLE_LAYER_ATTR, layer)
            fixed_nodes.update(id(item) for item in child.iter())

    if shell_only and main_content is not None:
        for child in list(main_content):
            if child.get(STYLE_REUSABLE_ATTR) != "true":
                main_content.remove(child)
        defs = next(
            (child for child in root if _local_name(child.tag) == "defs"),
            None,
        )
        if defs is not None:
            retained_nodes = [
                child
                for child in root
                if child is not defs
            ]
            retained_definition_ids = _referenced_definition_ids(retained_nodes, defs)
            for definition in list(defs):
                if definition.get("id") not in retained_definition_ids:
                    defs.remove(definition)

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
    the inherited shell or in Agent-authorized ``fixed_image_elements`` are
    copied into ``slides/images/style-pack``. Other slide-body images remain
    unavailable content references.
    """
    style_pack_root = Path(style_pack_dir).resolve()
    slides_root = Path(slides_dir).resolve()
    reference_dir = slides_root / "style_references"
    asset_dir = slides_root / "images" / "style-pack"
    reference_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    manifest = validate_style_pack(style_pack_root)
    pages_by_id = {str(item["id"]): item for item in manifest["pages"]}
    pages_by_source: dict[Path, list[dict[str, Any]]] = {}
    for item in manifest["pages"]:
        source_path = _safe_child(style_pack_root, str(item["svg"]))
        pages_by_source.setdefault(source_path, []).append(item)

    prepared: dict[tuple[Path, str, bool], Path] = {}
    for page in outline:
        source_value = str(getattr(page, "style_reference_svg", "") or "")
        if not source_value:
            # No exact page-role candidate exists for this page.  It uses the
            # unchanged built-in template route and needs no runtime reference.
            continue
        source = Path(source_value).resolve()
        if not _is_within(source, style_pack_root):
            raise ValueError(f"style reference escapes style pack: {source}")
        reference_id = str(getattr(page, "style_reference_id", "") or "")
        reference_page = pages_by_id.get(reference_id)
        if reference_page is None:
            source_matches = pages_by_source.get(source, [])
            if len(source_matches) != 1:
                raise ValueError(
                    f"cannot resolve style manifest entry for outline page {page.index}: {source}"
                )
            reference_page = source_matches[0]
        fixed_signature = json.dumps(
            reference_page.get("fixed_image_elements", []),
            ensure_ascii=False,
            sort_keys=True,
        )
        shell_only = (
            str(getattr(page, "style_reference_page_type", "") or "")
            in SPECIAL_SHELL_FALLBACK_TARGETS
            and str(reference_page["page_type"]) == "content"
        )
        prepared_key = (source, fixed_signature, shell_only)
        if prepared_key not in prepared:
            relative = source.relative_to(style_pack_root).as_posix()
            suffix = hashlib.sha1(
                f"{relative}\n{fixed_signature}\nshell_only={shell_only}".encode("utf-8")
            ).hexdigest()[:10]
            target = reference_dir / f"{source.stem}-{suffix}.svg"
            _prepare_runtime_reference(
                source,
                target,
                page=page,
                reference_page=reference_page,
                style_pack_root=style_pack_root,
                slides_root=slides_root,
                asset_dir=asset_dir,
                shell_only=shell_only,
            )
            prepared[prepared_key] = target
        page.style_reference_svg = str(prepared[prepared_key])


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
    left: AffineMatrix,
    right: AffineMatrix,
) -> AffineMatrix:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return AffineMatrix(
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_point(
    matrix: AffineMatrix,
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
        parent_matrix: AffineMatrix,
    ) -> None:
        matrix = _multiply_matrix(
            parent_matrix,
            parse_transform_info(node.get("transform", "")).matrix,
        )
        points.extend(_transform_point(matrix, x, y) for x, y in _local_geometry_points(node))
        for child in node:
            visit(child, matrix)

    visit(element, AffineMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
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


def _text_bounds_by_element(
    root: ET.Element,
) -> dict[ET.Element, tuple[float, float, float, float]]:
    """Return absolute text bounds, including every ancestor transform."""
    result: dict[ET.Element, tuple[float, float, float, float]] = {}

    def visit(node: ET.Element, parent_matrix: AffineMatrix) -> None:
        matrix = _multiply_matrix(
            parent_matrix,
            parse_transform_info(node.get("transform", "")).matrix,
        )
        if _local_name(node.tag) == "text":
            local_points = _local_geometry_points(node)
            if local_points:
                width = max(x for x, _ in local_points) - min(x for x, _ in local_points)
                anchor = (node.get("text-anchor") or "start").strip().lower()
                anchor_shift = (
                    -width / 2.0
                    if anchor == "middle"
                    else (-width if anchor == "end" else 0.0)
                )
                local_points = [(x + anchor_shift, y) for x, y in local_points]
            points = [
                _transform_point(matrix, x, y)
                for x, y in local_points
            ]
            if points:
                xs, ys = zip(*points)
                result[node] = min(xs), min(ys), max(xs), max(ys)
        for child in node:
            visit(child, matrix)

    visit(root, AffineMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    return result


def _same_text_region(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Detect independent text boxes occupying effectively the same baseline."""
    first_width, first_height = first[2] - first[0], first[3] - first[1]
    second_width, second_height = second[2] - second[0], second[3] - second[1]
    if min(first_width, second_width, first_height, second_height) <= 0:
        return False
    height_ratio = min(first_height, second_height) / max(first_height, second_height)
    if height_ratio < 0.7:
        return False
    baseline_tolerance = max(3.0, max(first_height, second_height) * 0.15)
    if abs(first[3] - second[3]) > baseline_tolerance:
        return False
    horizontal_overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    vertical_overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return (
        horizontal_overlap / min(first_width, second_width) >= 0.75
        and vertical_overlap / min(first_height, second_height) >= 0.75
    )


def _same_title_region(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Detect two large title runs painted on effectively the same baseline.

    Title replacements can have different measured widths and alignment modes,
    so the stricter generic text-box overlap rule is insufficient.  This rule
    deliberately remains baseline- and size-sensitive and is used only against
    a title that the deterministic style shell is about to inject.
    """
    first_width, first_height = first[2] - first[0], first[3] - first[1]
    second_width, second_height = second[2] - second[0], second[3] - second[1]
    if min(first_width, second_width, first_height, second_height) <= 0:
        return False
    height_ratio = min(first_height, second_height) / max(first_height, second_height)
    if height_ratio < 0.7:
        return False
    baseline_tolerance = max(4.0, max(first_height, second_height) * 0.18)
    if abs(first[3] - second[3]) > baseline_tolerance:
        return False
    horizontal_overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    vertical_overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return (
        horizontal_overlap / min(first_width, second_width) >= 0.35
        and vertical_overlap / min(first_height, second_height) >= 0.7
    )


def _remove_empty_groups(root: ET.Element) -> None:
    """Remove wrapper chains left empty by deterministic element cleanup."""
    changed = True
    while changed:
        changed = False
        for parent in root.iter():
            for child in list(parent):
                if _local_name(child.tag) == "g" and len(child) == 0:
                    parent.remove(child)
                    changed = True


def _remove_duplicate_fixed_text(
    generated_root: ET.Element,
    reference_root: ET.Element,
    title: ET.Element | None,
    title_shell_nodes: list[ET.Element],
    page: Any,
) -> None:
    """Remove model text that would overprint an injected fixed title.

    Geometry, not wording or model-chosen group ids, is the source of truth.
    This covers cover/TOC/content titles as well as bilingual thanks phrases.
    Fixed layout/master text participates only on cover and thanks pages,
    where the prompt contract allows no arbitrary body copy in those regions.
    """
    reference_page_type = str(getattr(page, "style_reference_page_type", "") or "")
    reference_bounds = _text_bounds_by_element(reference_root)
    title_bounds: list[tuple[float, float, float, float]] = []
    for node in title_shell_nodes:
        if node is title and reference_page_type not in {"thanks", "toc"}:
            # Compare against the title that will actually be injected, not the
            # source deck's placeholder wording, whose measured width may be
            # completely different from the outline title.
            replacement = copy.deepcopy(node)
            _replace_group_text(replacement, str(getattr(page, "title", "")))
            title_bounds.extend(_text_bounds_by_element(replacement).values())
            continue
        title_bounds.extend(
            reference_bounds[text]
            for text in _text_elements(node)
            if text in reference_bounds
        )

    exact_texts: set[ET.Element] = set()
    if reference_page_type in {"cover", "thanks"}:
        for group_id in ("master-content", "layout-content"):
            group = _direct_child(reference_root, group_id)
            if group is not None:
                exact_texts.update(_text_elements(group))
    exact_bounds = [reference_bounds[text] for text in exact_texts if text in reference_bounds]
    if not title_bounds and not exact_bounds:
        return

    generated_bounds = _text_bounds_by_element(generated_root)
    parent_by_child = {
        child: parent
        for parent in generated_root.iter()
        for child in parent
    }
    for text, bounds in generated_bounds.items():
        matches_title = any(_same_title_region(bounds, fixed) for fixed in title_bounds)
        matches_exact = any(_same_text_region(bounds, fixed) for fixed in exact_bounds)
        if not matches_title and not matches_exact:
            continue
        parent = parent_by_child.get(text)
        if parent is not None:
            parent.remove(text)

    _remove_empty_groups(generated_root)


def _reference_title_group(reference_root: ET.Element, page: Any) -> ET.Element | None:
    # Prefer an explicit main-content title for every page role. Some template
    # producers serialize TOC/thanks titles on the slide itself rather than in
    # layout-content; assuming those titles are inherited silently drops them.
    reference_page_type = str(getattr(page, "style_reference_page_type", "") or "")
    main = _direct_child(reference_root, "main-content")
    if main is None:
        return None
    groups = [child for child in main if _local_name(child.tag) == "g" and _text_elements(child)]
    explicit = [group for group in groups if group.get("data-role") == "header"]
    if explicit:
        slide_height = _float_attr(reference_root, "height", 720.0) or 720.0

        def title_rank(group: ET.Element) -> tuple[float, float, float]:
            bounds = _visual_bounds(group)
            width = max(0.0, bounds[2] - bounds[0]) if bounds is not None else 0.0
            top = bounds[1] if bounds is not None else _min_text_y(group)
            return _max_font_size(group), width, -top

        if reference_page_type in {"content", "toc"} or (
            not reference_page_type and int(getattr(page, "type", 1)) in {1, 2}
        ):
            # Google Slides and similar producers may serialize every card
            # heading as a title placeholder.  Prefer a header in the actual
            # top title band, then use font size and visual width to distinguish
            # the page title from small labels in that band.
            top_band: list[ET.Element] = []
            for group in explicit:
                bounds = _visual_bounds(group)
                group_top = bounds[1] if bounds is not None else _min_text_y(group)
                if group_top <= slide_height * 0.25:
                    top_band.append(group)
            if top_band:
                return max(top_band, key=title_rank)
        return max(explicit, key=title_rank)
    if reference_page_type in {"content", "toc"} or (
        not reference_page_type and int(getattr(page, "type", 1)) in {1, 2}
    ):
        candidates = [
            group for group in groups
            if _min_text_y(group) <= 120.0 and _max_font_size(group) >= 24.0
        ]
        if candidates:
            return max(candidates, key=_max_font_size)
        if reference_page_type == "toc":
            # Some TOC designs use a large vertical label in a side column
            # instead of a conventional top title.  Select it only when its
            # typography clearly dominates the entry labels, avoiding a
            # template-specific dependency on words such as 目录 or CONTENTS.
            ranked = sorted(groups, key=_max_font_size, reverse=True)
            if ranked:
                largest = ranked[0]
                largest_size = _max_font_size(largest)
                next_size = _max_font_size(ranked[1]) if len(ranked) > 1 else 0.0
                bounds = _visual_bounds(largest)
                slide_width = _float_attr(reference_root, "width", 1280.0) or 1280.0
                peripheral = bounds is not None and (
                    (bounds[0] + bounds[2]) / 2.0 <= slide_width * 0.35
                    or (bounds[0] + bounds[2]) / 2.0 >= slide_width * 0.65
                )
                dominant = largest_size >= 24.0 and (
                    next_size <= 0.0 or largest_size >= next_size * 1.25
                )
                if peripheral and dominant:
                    return largest
        return None
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

    def is_toc_marker_companion(bounds: tuple[float, float, float, float] | None) -> bool:
        if bounds is None or title_bounds is None:
            return False
        title_height = title_bounds[3] - title_bounds[1]
        height = bounds[3] - bounds[1]
        height_ratio = (
            min(title_height, height) / max(title_height, height)
            if max(title_height, height) > 0
            else 0.0
        )
        if min(title_height, height) <= 0 or height_ratio < 0.6:
            return False
        vertical_overlap = max(
            0.0,
            min(title_bounds[3], bounds[3]) - max(title_bounds[1], bounds[1]),
        )
        if vertical_overlap / min(title_height, height) < 0.7:
            return False
        horizontal_gap = max(
            0.0,
            max(title_bounds[0], bounds[0]) - min(title_bounds[2], bounds[2]),
        )
        return horizontal_gap <= 40.0

    result: list[ET.Element] = []
    for child in main:
        if child is title:
            result.append(child)
            continue
        overlaps_title = _substantially_overlaps(_visual_bounds(child), title_bounds)
        if _text_elements(child):
            # A vertical TOC marker is often split into two adjacent text boxes
            # (for example a local-language label plus a stacked Latin label).
            # Keep the companion when it occupies the same marker region and
            # has comparable typography; ordinary directory entries are not
            # spatially overlapping and remain dynamic.
            if (
                str(getattr(page, "style_reference_page_type", "") or "") == "toc"
                and is_toc_marker_companion(_visual_bounds(child))
                and _max_font_size(child) >= _max_font_size(title) * 0.45
            ):
                result.append(child)
            continue
        if overlaps_title:
            result.append(child)
    return result


def _replace_group_text(group: ET.Element, value: str) -> None:
    texts = _text_elements(group)
    if not texts:
        return
    first = texts[0]
    # ooxml-svg stores centered text as a measured left edge rather than an
    # SVG text-anchor.  Preserve that visual center before removing the old
    # measured-width contract, otherwise a longer replacement title drifts
    # right and can leave the slide. Only infer centering when the title group
    # also contains an explicit visual container; for a text-only group its
    # own measured bounds trivially have the same center and provide no
    # evidence that the source paragraph was centered.
    bounds = _visual_bounds(group)
    measured_width = _float_attr(first, "textLength", _float_attr(first, "data-measured-width"))

    def has_visible_paint(item: ET.Element) -> bool:
        if _local_name(item.tag) not in {"rect", "path", "polygon", "polyline", "circle", "ellipse"}:
            return False
        opacity = _float_attr(item, "opacity", 1.0)
        fill = (item.get("fill") or "#000000").strip().lower()
        stroke = (item.get("stroke") or "none").strip().lower()
        fill_visible = fill != "none" and _float_attr(item, "fill-opacity", 1.0) > 0
        stroke_visible = stroke != "none" and _float_attr(item, "stroke-opacity", 1.0) > 0
        return opacity > 0 and (fill_visible or stroke_visible)

    has_visual_container = any(has_visible_paint(item) for item in group.iter())
    if has_visual_container and bounds is not None and measured_width > 0:
        original_center = _float_attr(first, "x") + measured_width / 2.0
        bounds_center = (bounds[0] + bounds[2]) / 2.0
        tolerance = max(2.0, (bounds[2] - bounds[0]) * 0.02)
        if abs(original_center - bounds_center) <= tolerance:
            first.set("x", f"{bounds_center:g}")
            first.set("text-anchor", "middle")
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


def _is_generated_shell_group(
    group: ET.Element,
    *,
    remove_top_title: bool,
    preserve_dynamic_title: bool,
) -> bool:
    element_id = (group.get("id") or "").lower().replace("_", "-")
    role = (group.get("data-role") or "").lower()
    if role in {"footer", "page-number"}:
        return True
    if role == "header" and not preserve_dynamic_title:
        return True
    if element_id in {"background", "slide-background", "master-content", "layout-content"}:
        return True
    if "footer" in element_id or "page-number" in element_id:
        return True
    title_tokens = (
        "header", "page-title", "main-title", "cover-title", "toc-title",
        "section-title", "closing-title", "thanks-title",
    )
    if any(token in element_id for token in title_tokens) and not preserve_dynamic_title:
        return True
    if not remove_top_title or _min_text_y(group) > 120.0 or _max_font_size(group) < 24.0:
        return False
    bounds = _visual_bounds(group)
    # A model may wrap the whole page in one generic main-content group.  The
    # presence of a title inside that wrapper must not classify all body nodes
    # as a duplicate title.  The heuristic applies only to a group whose full
    # visible extent stays inside the top title band.
    return bounds is not None and bounds[3] <= 150.0


def _strip_generated_shell_groups(
    parent: ET.Element,
    *,
    remove_top_title: bool,
    preserve_dynamic_title: bool = False,
) -> None:
    """Recursively remove duplicate shell groups while retaining body wrappers."""
    for child in list(parent):
        if _local_name(child.tag) != "g":
            continue
        if _is_generated_shell_group(
            child,
            remove_top_title=remove_top_title,
            preserve_dynamic_title=preserve_dynamic_title,
        ):
            parent.remove(child)
            continue
        _strip_generated_shell_groups(
            child,
            remove_top_title=remove_top_title,
            preserve_dynamic_title=preserve_dynamic_title,
        )


def _has_vector_graphics(group: ET.Element) -> bool:
    graphic_tags = {"circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect"}
    return any(_local_name(item.tag) in graphic_tags for item in group.iter())


def _reference_body_top(reference_root: ET.Element, page: Any) -> float:
    main = _direct_child(reference_root, "main-content")
    if main is None:
        return 0.0
    shell_ids = {id(item) for item in _reference_title_shell_nodes(reference_root, page)}
    candidates = []
    for child in main:
        if id(child) in shell_ids or child.get(STYLE_REUSABLE_ATTR) == "true":
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
    toc_body_top = _reference_body_top(reference_root, page) if reference_page_type == "toc" else 0.0
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
            # A model frequently wraps the page heading and all directory rows
            # in one generic group.  Never discard that entire wrapper merely
            # because one child sits above the body boundary.  Keep groups that
            # contain at least one entry baseline or visual row in the source
            # body region; fixed-title geometry cleanup runs separately.
            text_bounds = _text_bounds_by_element(child)
            has_body_text = any(
                bounds[3] >= toc_body_top - 4.0
                for bounds in text_bounds.values()
            ) if toc_body_top > 0 else bool(text_bounds)
            bounds = _visual_bounds(child)
            has_body_graphics = (
                bounds is not None
                and (toc_body_top <= 0 or bounds[1] >= toc_body_top - 4.0)
                and _has_vector_graphics(child)
            )
            remove = not has_body_text and not has_body_graphics
        if remove:
            generated_root.remove(child)


def _visible_fill_opacity(element: ET.Element) -> float:
    fill = (element.get("fill") or "#000000").strip().lower()
    if fill in {"none", "transparent"}:
        return 0.0
    return (
        max(0.0, min(1.0, _float_attr(element, "opacity", 1.0)))
        * max(0.0, min(1.0, _float_attr(element, "fill-opacity", 1.0)))
    )


def _visible_stroke_opacity(element: ET.Element) -> float:
    stroke = (element.get("stroke") or "none").strip().lower()
    if stroke in {"none", "transparent"}:
        return 0.0
    return (
        max(0.0, min(1.0, _float_attr(element, "opacity", 1.0)))
        * max(0.0, min(1.0, _float_attr(element, "stroke-opacity", 1.0)))
    )


def _is_full_canvas_opaque_rect(element: ET.Element, generated_root: ET.Element) -> bool:
    if _local_name(element.tag) != "rect" or _visible_fill_opacity(element) < 0.98:
        return False
    bounds = _visual_bounds(element)
    if bounds is None:
        return False
    width = _float_attr(generated_root, "width", 1280.0) or 1280.0
    height = _float_attr(generated_root, "height", 720.0) or 720.0
    covered_width = max(0.0, min(bounds[2], width) - max(bounds[0], 0.0))
    covered_height = max(0.0, min(bounds[3], height) - max(bounds[1], 0.0))
    return covered_width / width >= 0.98 and covered_height / height >= 0.98


def _remove_dynamic_full_canvas_backdrops(generated_root: ET.Element) -> int:
    """Remove opaque model backgrounds that would paint over the fixed shell."""
    removed = 0

    def visit(parent: ET.Element) -> None:
        nonlocal removed
        for child in list(parent):
            if _local_name(child.tag) == "defs":
                continue
            if _is_full_canvas_opaque_rect(child, generated_root):
                parent.remove(child)
                removed += 1
                continue
            visit(child)

    visit(generated_root)
    if removed:
        _remove_empty_groups(generated_root)
    return removed


def _is_visible_text_node(text: ET.Element) -> bool:
    return (
        bool((text.text or "").strip())
        and _float_attr(text, "opacity", 1.0) > 0
        and _float_attr(text, "fill-opacity", 1.0) > 0
    )


def _has_meaningful_dynamic_body(
    generated_root: ET.Element,
    reference_root: ET.Element,
    page: Any,
) -> bool:
    """Return whether a style page still has model-authored visible body data."""
    page_type = str(getattr(page, "style_reference_page_type", "") or "")
    if page_type not in {"content", "toc"}:
        return True

    text_bounds = _text_bounds_by_element(generated_root)
    nonempty_texts = [
        (text, bounds)
        for text, bounds in text_bounds.items()
        if _is_visible_text_node(text)
    ]
    if page_type == "toc":
        body_top = _reference_body_top(reference_root, page)
        return any(body_top <= 0 or bounds[3] >= body_top - 4.0 for _, bounds in nonempty_texts)
    if nonempty_texts:
        return True

    # Image-led and vector-led content pages are valid.  Full-canvas opaque
    # backgrounds have already been removed, so any remaining positive-area
    # visible graphic represents intentional body content rather than the
    # silent-blank failure mode.
    for element in generated_root.iter():
        if _local_name(element.tag) not in {
            "circle", "ellipse", "image", "line", "path", "polygon", "polyline", "rect"
        }:
            continue
        bounds = _visual_bounds(element)
        if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            continue
        if (
            _local_name(element.tag) == "image"
            or _visible_fill_opacity(element) > 0
            or _visible_stroke_opacity(element) > 0
        ):
            return True
    return False


def _apply_dynamic_style_rules(generated_root: ET.Element, page: Any) -> None:
    """Apply only safe, explicitly authorized style rules to model-authored nodes."""
    rules = getattr(page, "style_reference_rules", {}) or {}
    max_corner_radius = rules.get("max_corner_radius_px")
    max_rounded_height = rules.get("max_rounded_rect_height_px")
    if max_corner_radius is None and max_rounded_height is None:
        return
    radius_limit = None if max_corner_radius is None else max(0.0, float(max_corner_radius))
    height_limit = None if max_rounded_height is None else max(0.0, float(max_rounded_height))
    for element in generated_root.iter():
        if _local_name(element.tag) != "rect":
            continue
        effective_radius_limit = radius_limit
        if height_limit is not None and _float_attr(element, "height") > height_limit:
            effective_radius_limit = 0.0
        if effective_radius_limit is None:
            continue
        for attribute in ("rx", "ry"):
            value = element.get(attribute)
            if value is None:
                continue
            try:
                radius = float(value)
            except ValueError:
                continue
            if radius > effective_radius_limit:
                if effective_radius_limit == 0:
                    element.attrib.pop(attribute, None)
                else:
                    element.set(attribute, f"{effective_radius_limit:g}")


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
    shell_only = reference_root.get(STYLE_SHELL_ONLY_ATTR) == "true"
    _remove_dynamic_full_canvas_backdrops(generated_root)
    _strip_generated_shell_groups(
        generated_root,
        remove_top_title=title is not None,
        preserve_dynamic_title=shell_only,
    )
    _remove_special_page_redesigns(generated_root, reference_root, page)
    _remove_duplicate_fixed_text(
        generated_root,
        reference_root,
        title,
        title_shell_nodes,
        page,
    )
    _apply_dynamic_style_rules(generated_root, page)
    if not _has_meaningful_dynamic_body(generated_root, reference_root, page):
        page_type = str(getattr(page, "style_reference_page_type", "") or "content")
        raise ValueError(
            f"style-pack {page_type} page has no meaningful dynamic body content after composition"
        )

    fixed_pairs: list[tuple[str, ET.Element]] = []
    for name, group_id in (
        ("background", "background"),
        ("master", "master-content"),
        ("layout", "layout-content"),
    ):
        group = _direct_child(reference_root, group_id)
        if group is not None:
            fixed_pairs.append((name, group))
    reference_main = _direct_child(reference_root, "main-content")
    reusable_back: list[ET.Element] = []
    reusable_front: list[ET.Element] = []
    if reference_main is not None:
        for child in reference_main:
            if child.get(STYLE_REUSABLE_ATTR) != "true":
                continue
            if child.get(STYLE_REUSABLE_LAYER_ATTR) == "front":
                reusable_front.append(child)
            else:
                reusable_back.append(child)

    front_ids = {id(item) for item in reusable_front}
    before_dynamic_by_id = {
        id(item): item
        for item in [*title_shell_nodes, *reusable_back]
        if id(item) not in front_ids
    }
    if reference_main is not None:
        before_dynamic = [
            item for item in reference_main
            if id(item) in before_dynamic_by_id
        ]
    else:
        before_dynamic = list(before_dynamic_by_id.values())

    fixed_sources = [group for _, group in fixed_pairs]
    reference_nodes = [*fixed_sources, *before_dynamic, *reusable_front]
    clones, definition_clones = _clone_reference_nodes(reference_root, reference_nodes)
    for definition in definition_clones:
        definition.set(STYLE_SHELL_DEF_ATTR, "true")
        generated_defs.append(definition)

    slide_height = _float_attr(reference_root, "height", 720.0)
    if slide_height <= 0:
        slide_height = 720.0
    insertion_index = list(generated_root).index(generated_defs) + 1
    fixed_count = len(fixed_pairs)
    before_count = len(before_dynamic)
    for index, (source_node, clone) in enumerate(zip(reference_nodes, clones)):
        clone.set(STYLE_SHELL_ATTR, "true")
        if index < fixed_count:
            clone.set("id", f"slidea-style-{fixed_pairs[index][0]}")
            _update_page_number(clone, int(page.index) + 1, slide_height)
        else:
            reusable = source_node.get(STYLE_REUSABLE_ATTR) == "true"
            if reusable:
                layer = source_node.get(STYLE_REUSABLE_LAYER_ATTR) or "back"
                source_id = re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "-",
                    source_node.get("id") or str(index + 1),
                ).strip("-") or str(index + 1)
                clone.set("id", f"slidea-style-reusable-{layer}-{source_id}")
            elif source_node is title:
                clone.set("id", "slidea-style-page-title")
                # A selected thanks page already carries the template's deliberate
                # closing phrase, while a TOC marker such as 目录/CONTENTS is part
                # of the layout rather than the outline page title. Keep both
                # generic fixed labels verbatim.
                if str(getattr(page, "style_reference_page_type", "") or "") not in {"thanks", "toc"}:
                    _replace_group_text(clone, str(getattr(page, "title", "")))
            else:
                clone.set("id", f"slidea-style-title-shell-{index - fixed_count + 1}")

        is_front = index >= fixed_count + before_count
        if is_front:
            generated_root.append(clone)
        else:
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
    allowed_reference_ids: dict[int, set[str]],
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
        if reference_id not in allowed_reference_ids.get(page_index, set()):
            return None
        result[page_index] = reference_id
    return result if set(result) == expected_indices else None


async def assign_style_references_for_outline(
    outline: list[Any],
    style_pack_dir: str | Path,
) -> None:
    """Ask the outline-stage model to select layouts by type, density and structure."""
    catalog = style_reference_catalog(style_pack_dir)
    schema = TypeAdapter(list[StyleReferenceAssignment]).json_schema()
    catalog_by_type: dict[str, list[dict[str, Any]]] = {
        page_type: [item for item in catalog if str(item["page_type"]) == page_type]
        for page_type in PAGE_TYPES
    }
    ordered_pages = sorted(outline, key=lambda page: int(page.index))
    target_types = {
        int(page.index): _target_page_type(page, position, len(ordered_pages))
        for position, page in enumerate(ordered_pages)
    }
    needs_special_shell_fallback = any(
        target_page_type in SPECIAL_SHELL_FALLBACK_TARGETS
        and not catalog_by_type[target_page_type]
        for target_page_type in target_types.values()
    )
    special_shell_reference_id = ""
    if needs_special_shell_fallback and catalog_by_type["content"]:
        special_shell_reference_id = str(
            random.choice(catalog_by_type["content"])["id"]
        )

    for batch_number, batch in enumerate(_outline_batches(outline), 1):
        assignable_batch: list[Any] = []
        for page in batch:
            page_index = int(page.index)
            target_page_type = target_types[page_index]
            if catalog_by_type[target_page_type]:
                assignable_batch.append(page)
                continue
            if (
                target_page_type in SPECIAL_SHELL_FALLBACK_TARGETS
                and special_shell_reference_id
            ):
                page.style_reference_id = special_shell_reference_id
                _clear_style_reference_runtime(page)
                logger.info(
                    f"style pack has no {target_page_type!r} reference for outline page "
                    f"{page_index}; selected content reference "
                    f"{special_shell_reference_id!r} as a shell-only fallback"
                )
                continue
            page.style_reference_id = ""
            _clear_style_reference_runtime(page)
            logger.warning(
                f"style pack has no {target_page_type!r} reference for outline page "
                f"{page_index}; using the built-in template for this page"
            )

        if not assignable_batch:
            continue

        allowed_reference_ids = {
            int(page.index): {
                str(item["id"])
                for item in catalog_by_type[target_types[int(page.index)]]
            }
            for page in assignable_batch
        }
        batch_reference_ids = set().union(*allowed_reference_ids.values())
        catalog_text = json.dumps(
            [item for item in catalog if str(item["id"]) in batch_reference_ids],
            ensure_ascii=False,
            indent=2,
        )
        targets = [
            {
                "page_index": page.index,
                "page_type": target_types[int(page.index)],
                "title": page.title,
                "abstract": page.abstract,
                "has_reference_images": bool(getattr(page, "reference_images", None)),
            }
            for page in assignable_batch
        ]
        expected_indices = {int(page.index) for page in assignable_batch}
        correction = ""
        selected: dict[int, str] | None = None
        for attempt in range(STYLE_ASSIGNMENT_MAX_ATTEMPTS):
            prompt = f"""
你是 PPT 大纲阶段的版式参考分配器。请为下面每一页目标大纲选择一个 style pack 参考页。

# 选择原则
1. 只根据页面类型、信息密度和版式结构选择，不按主题词、行业词或语义相似度选择。
2. 页面类型是硬约束：cover、toc、separator、content、thanks 必须精确匹配，禁止跨类型选择。
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
                    [HumanMessage(content=prompt)],
                    InvokeOptions(json_schema=schema),
                )
            except Exception as error:
                logger.warning(
                    f"outline style assignment batch {batch_number} attempt "
                    f"{attempt + 1}/{STYLE_ASSIGNMENT_MAX_ATTEMPTS} failed: {error}"
                )
                assignments = None
            selected = _valid_assignments(
                assignments,
                expected_indices,
                allowed_reference_ids,
            )
            if selected is not None:
                break
            correction = (
                "\n上一次输出缺页、重复 page_index、包含未知/错误类型的 id 或格式错误。"
                "请重新为本批全部页面输出严格同类型的一一对应关系。"
            )
        if selected is None:
            raise ValueError(
                f"model failed to assign valid style references for outline batch {batch_number}"
            )
        for page in assignable_batch:
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
