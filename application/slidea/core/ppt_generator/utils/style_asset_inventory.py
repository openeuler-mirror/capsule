"""Build an advisory inventory of image assets found in converted style SVGs.

The inventory deliberately does not authorize an image for reuse.  It exposes
repeatable, deterministic signals so an Agent can inspect a small set of likely
template decorations and explicitly opt them into ``style-pack.json``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PIL import Image

from core.ppt_generator.utils.svg_to_pptx.drawingml_transform import (
    AffineMatrix,
    parse_transform_info,
)


XLINK_NS = "http://www.w3.org/1999/xlink"
FIXED_GROUP_IDS = {"background", "master-content", "layout-content"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _natural_key(path: Path) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _number(value: str | None, default: float = 0.0) -> float:
    match = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", value or "")
    return float(match.group(0)) if match else default


def _image_href(element: ET.Element) -> str:
    return element.get("href") or element.get(f"{{{XLINK_NS}}}href") or ""


def _multiply(
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


def _point(
    matrix: AffineMatrix,
    x: float,
    y: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def _image_bounds(
    image: ET.Element,
    matrix: AffineMatrix,
) -> tuple[float, float, float, float]:
    x = _number(image.get("x"))
    y = _number(image.get("y"))
    width = _number(image.get("width"))
    height = _number(image.get("height"))
    points = [
        _point(matrix, x, y),
        _point(matrix, x + width, y),
        _point(matrix, x, y + height),
        _point(matrix, x + width, y + height),
    ]
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _image_metadata(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            return {
                "format": image.format or path.suffix.lstrip(".").upper(),
                "pixel_width": image.width,
                "pixel_height": image.height,
                "mode": image.mode,
                "has_alpha": has_alpha,
            }
    except (OSError, ValueError):
        return {
            "format": path.suffix.lstrip(".").upper(),
            "pixel_width": None,
            "pixel_height": None,
            "mode": None,
            "has_alpha": False,
        }


def _position_signature(bounds: tuple[float, float, float, float], width: float, height: float) -> str:
    left, top, right, bottom = bounds
    normalized = (
        left / width,
        top / height,
        max(0.0, right - left) / width,
        max(0.0, bottom - top) / height,
    )
    # A 2% grid absorbs insignificant converter rounding while still separating
    # genuinely different placements.
    return ",".join(f"{round(value / 0.02) * 0.02:.2f}" for value in normalized)


def _asset_path(href: str, svg_path: Path, style_pack_root: Path) -> Path | None:
    parsed = urlparse(href)
    is_external = href.startswith("data:") or bool(parsed.scheme) or bool(parsed.netloc)
    if not href or is_external:
        return None
    path = (svg_path.parent / unquote(parsed.path)).resolve()
    root = style_pack_root.resolve()
    if path != root and root not in path.parents:
        return None
    return path if path.is_file() else None


@dataclass(frozen=True)
class _SvgVisitContext:
    svg_root: ET.Element
    svg_path: Path
    pack_root: Path
    slide_number: int
    slide_width: float
    slide_height: float
    node_parent: dict[ET.Element, ET.Element]
    occurrences_by_asset: dict[Path, list[dict[str, Any]]]


def _visit_svg_node(
    node: ET.Element,
    matrix: AffineMatrix,
    top_group: str,
    main_element_id: str,
    context: _SvgVisitContext,
) -> None:
    current = _multiply(matrix, parse_transform_info(node.get("transform", "")).matrix)
    current_top = top_group
    current_element = main_element_id
    parent = context.node_parent.get(node)
    if parent is context.svg_root:
        current_top = node.get("id") or ""
    if parent is not None and parent.get("id") == "main-content":
        current_element = node.get("id") or ""
    if _local_name(node.tag) == "image":
        href = _image_href(node)
        asset = _asset_path(href, context.svg_path, context.pack_root)
        if asset is not None:
            bounds = _image_bounds(node, current)
            left, top, right, bottom = bounds
            normalized_width = max(0.0, right - left) / context.slide_width
            normalized_height = max(0.0, bottom - top) / context.slide_height
            context.occurrences_by_asset[asset].append(
                {
                    "slide": context.slide_number,
                    "svg": context.svg_path.relative_to(context.pack_root).as_posix(),
                    "top_group": current_top,
                    "element_id": current_element or node.get("id") or "",
                    "bounds": [round(value, 3) for value in bounds],
                    "normalized_bounds": [
                        round(left / context.slide_width, 4),
                        round(top / context.slide_height, 4),
                        round(right / context.slide_width, 4),
                        round(bottom / context.slide_height, 4),
                    ],
                    "position_signature": _position_signature(
                        bounds,
                        context.slide_width,
                        context.slide_height,
                    ),
                    "wide_strip": normalized_width >= 0.65 and normalized_height <= 0.35,
                    "edge_or_corner": (
                        left / context.slide_width <= 0.12
                        or top / context.slide_height <= 0.12
                        or right / context.slide_width >= 0.88
                        or bottom / context.slide_height >= 0.88
                    ),
                    "small_ornament": normalized_width * normalized_height <= 0.08,
                }
            )
    for child in node:
        _visit_svg_node(child, current, current_top, current_element, context)


def build_style_asset_inventory(
    reference_dir: str | Path,
    style_pack_root: str | Path,
) -> dict[str, Any]:
    """Return deterministic image reuse candidates for Agent review."""
    reference_root = Path(reference_dir).resolve()
    pack_root = Path(style_pack_root).resolve()
    occurrences_by_asset: dict[Path, list[dict[str, Any]]] = defaultdict(list)

    for ordinal, svg_path in enumerate(
        sorted(reference_root.glob("*.svg"), key=_natural_key),
        start=1,
    ):
        slide_match = re.search(r"slide(\d+)", svg_path.stem, re.IGNORECASE)
        slide_number = int(slide_match.group(1)) if slide_match else ordinal
        try:
            svg_root = ET.parse(svg_path).getroot()
        except (ET.ParseError, OSError):
            continue
        view_box: list[float] = []
        view_box_values = re.findall(
            r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?",
            svg_root.get("viewBox", ""),
        )
        for item in view_box_values:
            view_box.append(_number(item))
        slide_width = _number(svg_root.get("width"), view_box[2] if len(view_box) == 4 else 1280.0)
        slide_height = _number(svg_root.get("height"), view_box[3] if len(view_box) == 4 else 720.0)
        if slide_width <= 0 or slide_height <= 0:
            slide_width, slide_height = 1280.0, 720.0

        node_parent = {child: parent for parent in svg_root.iter() for child in parent}
        visit_context = _SvgVisitContext(
            svg_root=svg_root,
            svg_path=svg_path,
            pack_root=pack_root,
            slide_number=slide_number,
            slide_width=slide_width,
            slide_height=slide_height,
            node_parent=node_parent,
            occurrences_by_asset=occurrences_by_asset,
        )
        _visit_svg_node(
            svg_root,
            AffineMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            "",
            "",
            visit_context,
        )

    assets: list[dict[str, Any]] = []
    for asset_path, occurrences in sorted(
        occurrences_by_asset.items(),
        key=lambda item: item[0].relative_to(pack_root).as_posix(),
    ):
        metadata = _image_metadata(asset_path)
        pages = {item["slide"] for item in occurrences}
        main_occurrences = [item for item in occurrences if item["top_group"] == "main-content"]
        main_pages = {item["slide"] for item in main_occurrences}
        signatures: dict[str, set[int]] = defaultdict(set)
        for item in main_occurrences:
            signatures[item["position_signature"]].add(item["slide"])
        repeated = len(main_pages) >= 2
        stable = repeated and max((len(value) for value in signatures.values()), default=0) >= 2
        signals = {
            "automatic_fixed_layer": any(item["top_group"] in FIXED_GROUP_IDS for item in occurrences),
            "repeated_across_pages": repeated,
            "stable_placement": stable,
            "has_alpha": bool(metadata["has_alpha"]),
            "wide_strip": any(item["wide_strip"] for item in main_occurrences),
            "edge_or_corner": any(item["edge_or_corner"] for item in main_occurrences),
            "small_ornament": any(item["small_ornament"] for item in main_occurrences),
        }
        needs_authorization = bool(main_occurrences)
        candidate_signal_names = (
            "stable_placement",
            "has_alpha",
            "wide_strip",
            "edge_or_corner",
            "small_ornament",
        )
        has_candidate_signal = any(signals.get(name, False) for name in candidate_signal_names)
        candidate = needs_authorization and repeated and has_candidate_signal
        assets.append(
            {
                "path": asset_path.relative_to(pack_root).as_posix(),
                "page_count": len(pages),
                "main_content_page_count": len(main_pages),
                "usage_count": len(occurrences),
                "metadata": metadata,
                "signals": signals,
                "needs_explicit_authorization": needs_authorization,
                "candidate": candidate,
                "candidate_reasons": [name for name, enabled in signals.items() if enabled],
                "occurrences": occurrences,
            }
        )

    return {
        "version": 1,
        "advisory_only": True,
        "guidance": (
            "Candidate signals are hints only. An Agent must inspect the referenced SVG elements "
            "and explicitly authorize reusable_assets and fixed_image_elements in style-pack.json."
        ),
        "assets": assets,
    }


def write_style_asset_inventory(
    reference_dir: str | Path,
    style_pack_root: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    root = Path(style_pack_root).resolve()
    target = Path(output_path).resolve() if output_path else root / "asset-inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    inventory = build_style_asset_inventory(reference_dir, root)
    target.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
