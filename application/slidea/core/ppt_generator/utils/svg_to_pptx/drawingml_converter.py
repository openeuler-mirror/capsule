"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""
# 以下代码源自 PPT Master (https://github.com/hugohe3/ppt-master)
# 原始项目采用 MIT 许可证，版权所有 (c) 2025-2026 Hugo He


from __future__ import annotations

import re
import copy
from pathlib import Path
from xml.etree import ElementTree as ET

from core.utils.logger import logger

from .drawingml_context import ConvertContext, ShapeResult
from .drawingml_utils import (
    EMU_PER_PX, SVG_NS,
    _extract_inheritable_styles, resolve_url_id, ctx_x, ctx_y, px_to_emu,
)
from .drawingml_styles import build_effect_xml
from .drawingml_transform import parse_transform_info
from .drawingml_elements import (
    convert_rect, convert_circle, convert_ellipse,
    convert_line, convert_path,
    convert_polygon, convert_polyline,
    convert_text, convert_image,
)


# ---------------------------------------------------------------------------
# Animation anchor selection
# ---------------------------------------------------------------------------

# Tokens that mark a top-level <g id="..."> as page chrome rather than animated
# content. When any token (after splitting id on '-' and '_') matches, the group
# is excluded from the per-element entrance animation cascade so background,
# header/footer, decorations etc. appear together with the slide instead of
# requiring presenter clicks.
_CHROME_ID_TOKENS = frozenset({
    'background', 'bg',
    'decoration', 'decorations', 'decor',
    'header', 'footer',
    'chrome', 'watermark',
    'pagenumber', 'pagenum',
})


def _is_chrome_id(elem_id: str | None) -> bool:
    if not elem_id:
        return False
    lower = elem_id.lower()
    if lower.replace('-', '').replace('_', '') in _CHROME_ID_TOKENS:
        return True
    tokens = re.split(r'[-_]', lower)
    return any(t in _CHROME_ID_TOKENS for t in tokens if t)


# ---------------------------------------------------------------------------
# Transform & layout helpers
# ---------------------------------------------------------------------------

def parse_transform(transform_str: str) -> tuple[float, float, float, float, float]:
    """Parse SVG transform string, extract translate, scale, and rotate.

    Returns:
        (dx, dy, sx, sy, angle_deg) tuple.
    """
    info = parse_transform_info(transform_str)
    if info.has_skew:
        logger.warning(
            f"SVG transform contains skew; using scale/rotation decomposition: {transform_str}"
        )
    return info.dx, info.dy, info.sx, info.sy, info.angle_deg


# ---------------------------------------------------------------------------
# Group handling
# ---------------------------------------------------------------------------

def convert_g(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <g> to DrawingML group shape <p:grpSp>.

    Preserves group structure so elements can be selected and moved together
    in PowerPoint. Single-child groups are flattened to avoid unnecessary nesting.

    Uses identity coordinate mapping (chOff/chExt == off/ext) so child shapes
    keep their absolute slide coordinates unchanged.
    """
    transform = elem.get('transform', '')
    transform_info = parse_transform_info(transform)
    dx, dy = transform_info.dx, transform_info.dy
    sx, sy = transform_info.sx, transform_info.sy
    angle_deg = transform_info.angle_deg

    # Axis-aligned translate/scale can be flattened directly into primitive
    # coordinates. Rotation (including a 90-degree matrix) must stay on a
    # DrawingML group; otherwise a nested non-uniform parent scale changes its
    # pivot and turns a full-width texture into a misplaced rectangle.
    a, b, c, d, _, _ = transform_info.matrix
    has_linear_rotation = abs(b) > 1e-9 or abs(c) > 1e-9

    filter_id = resolve_url_id(elem.get('filter', ''))
    style_overrides = _extract_inheritable_styles(elem)
    if has_linear_rotation:
        child_ctx = ctx.child(0, 0, 1, 1, filter_id, style_overrides)
    else:
        child_ctx = ctx.child(dx, dy, sx, sy, filter_id, style_overrides)

    child_results: list[ShapeResult] = []
    for child in elem:
        result = convert_element(child, child_ctx)
        if result:
            child_results.append(result)

    ctx.sync_from_child(child_ctx)

    if not child_results:
        return None

    elem_id = elem.get('id')
    should_animate_group = ctx.depth == 0 and elem_id and not _is_chrome_id(elem_id)

    # Single-child non-semantic groups are flattened to reduce nesting. Top-level
    # semantic groups are preserved so animations target the group, not its
    # individual child shapes.
    preserves_group_semantics = should_animate_group or has_linear_rotation or bool(filter_id)
    if len(child_results) == 1 and not preserves_group_semantics:
        return child_results[0]

    # Multiple children, or a top-level semantic one-child group: wrap in
    # <p:grpSp> so PowerPoint can animate the group as one unit.
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for child_result in child_results:
        bounds = child_result.bounds_emu
        if bounds is None:
            continue
        min_x = min(min_x, bounds[0])
        min_y = min(min_y, bounds[1])
        max_x = max(max_x, bounds[2])
        max_y = max(max_y, bounds[3])

    if min_x == float('inf'):
        return ShapeResult(xml='\n'.join(result.xml for result in child_results))

    group_bounds = (int(min_x), int(min_y), int(max_x), int(max_y))
    ch_off_x, ch_off_y = int(min_x), int(min_y)
    ch_ext_w = max(int(max_x - min_x), 1)
    ch_ext_h = max(int(max_y - min_y), 1)
    flip_h = flip_v = False

    if has_linear_rotation:
        parent_sx = ctx.scale_x or 1.0
        parent_sy = ctx.scale_y or 1.0
        # Conjugate the SVG linear transform into the already-flattened parent
        # coordinate system: S_parent * M * inverse(S_parent).
        effective_a = a
        effective_b = parent_sy * b / parent_sx
        effective_c = parent_sx * c / parent_sy
        effective_d = d
        effective_info = parse_transform_info(
            f'matrix({effective_a} {effective_b} {effective_c} {effective_d} 0 0)'
        )
        if effective_info.has_skew:
            logger.warning(
                f"SVG affine transform contains skew not representable by DrawingML; "
                f"using the closest scale/rotation group: {transform}"
            )

        min_px_x, min_px_y = min_x / EMU_PER_PX, min_y / EMU_PER_PX
        max_px_x, max_px_y = max_x / EMU_PER_PX, max_y / EMU_PER_PX
        center_parent_x = (min_px_x + max_px_x) / 2
        center_parent_y = (min_px_y + max_px_y) / 2
        center_local_x = (center_parent_x - ctx.translate_x) / parent_sx
        center_local_y = (center_parent_y - ctx.translate_y) / parent_sy
        ma, mb, mc, md, me, mf = transform_info.matrix
        transformed_center_x = ma * center_local_x + mc * center_local_y + me
        transformed_center_y = mb * center_local_x + md * center_local_y + mf
        center_x = px_to_emu(ctx_x(transformed_center_x, ctx))
        center_y = px_to_emu(ctx_y(transformed_center_y, ctx))

        group_w = max(round((max_x - min_x) * abs(effective_info.sx)), 1)
        group_h = max(round((max_y - min_y) * abs(effective_info.sy)), 1)
        group_x = round(center_x - group_w / 2)
        group_y = round(center_y - group_h / 2)
        angle_deg = effective_info.angle_deg
        flip_h = effective_info.sx < 0
        flip_v = effective_info.sy < 0

        transformed_corners = []
        for parent_x, parent_y in (
            (min_px_x, min_px_y), (max_px_x, min_px_y),
            (max_px_x, max_px_y), (min_px_x, max_px_y),
        ):
            local_x = (parent_x - ctx.translate_x) / parent_sx
            local_y = (parent_y - ctx.translate_y) / parent_sy
            target_x = ma * local_x + mc * local_y + me
            target_y = mb * local_x + md * local_y + mf
            transformed_corners.append((ctx_x(target_x, ctx), ctx_y(target_y, ctx)))
        xs, ys = zip(*transformed_corners)
        group_bounds = (
            px_to_emu(min(xs)), px_to_emu(min(ys)),
            px_to_emu(max(xs)), px_to_emu(max(ys)),
        )
    elif angle_deg and transform_info.pivot_x is not None:
        pivot_x = px_to_emu(ctx_x(transform_info.pivot_x, ctx))
        pivot_y = px_to_emu(ctx_y(transform_info.pivot_y, ctx))
        half_w = max(abs(min_x - pivot_x), abs(max_x - pivot_x), 0.5)
        half_h = max(abs(min_y - pivot_y), abs(max_y - pivot_y), 0.5)
        group_x = round(pivot_x - half_w)
        group_y = round(pivot_y - half_h)
        group_w = max(round(half_w * 2), 1)
        group_h = max(round(half_h * 2), 1)
    else:
        group_x = int(min_x)
        group_y = int(min_y)
        group_w = max(int(max_x - min_x), 1)
        group_h = max(int(max_y - min_y), 1)

    shapes_xml = '\n'.join(result.xml for result in child_results)
    group_id = ctx.next_id()

    # Record top-level semantic groups (e.g. <g id="p02-title">) so the
    # PPTX builder can emit per-element entrance timing. Only the outermost
    # multi-child wrapper qualifies — flattened single-child groups have no
    # <p:grpSp> to anchor a timing target on, and nested groups are
    # ignored to keep the animation budget at ~per-section granularity.
    if should_animate_group:
        ctx.anim_targets.append((group_id, elem_id))

    group_effect = ''
    if filter_id and filter_id in ctx.defs:
        group_effect = build_effect_xml(ctx.defs[filter_id])

    rot_emu = int(angle_deg * 60000)
    rot_attr = f' rot="{rot_emu}"' if rot_emu else ''
    flip_h_attr = ' flipH="1"' if flip_h else ''
    flip_v_attr = ' flipV="1"' if flip_v else ''

    return ShapeResult(xml=f'''<p:grpSp>
<p:nvGrpSpPr>
<p:cNvPr id="{group_id}" name="Group {group_id}"/>
<p:cNvGrpSpPr/>
<p:nvPr/>
</p:nvGrpSpPr>
<p:grpSpPr>
<a:xfrm{rot_attr}{flip_h_attr}{flip_v_attr}>
<a:off x="{group_x}" y="{group_y}"/>
<a:ext cx="{group_w}" cy="{group_h}"/>
<a:chOff x="{ch_off_x}" y="{ch_off_y}"/>
<a:chExt cx="{ch_ext_w}" cy="{ch_ext_h}"/>
</a:xfrm>
{group_effect}
</p:grpSpPr>
{shapes_xml}
</p:grpSp>''', bounds_emu=group_bounds)


# ---------------------------------------------------------------------------
# Defs collection & element dispatch
# ---------------------------------------------------------------------------

_NON_VISUAL_TAGS = frozenset(('defs', 'title', 'desc', 'metadata', 'style'))

_CONVERTERS = {
    'rect': convert_rect,
    'circle': convert_circle,
    'ellipse': convert_ellipse,
    'line': convert_line,
    'path': convert_path,
    'polygon': convert_polygon,
    'polyline': convert_polyline,
    'text': convert_text,
    'image': convert_image,
    'g': convert_g,
}


def collect_defs(root: ET.Element) -> dict[str, ET.Element]:
    """Collect all <defs> children into an {id: element} dictionary."""
    defs: dict[str, ET.Element] = {}
    for defs_elem in root.iter(f'{{{SVG_NS}}}defs'):
        for child in defs_elem:
            elem_id = child.get('id')
            if elem_id:
                defs[elem_id] = child
    # Also check for defs without namespace
    for defs_elem in root.iter('defs'):
        for child in defs_elem:
            elem_id = child.get('id')
            if elem_id:
                defs[elem_id] = child
    return defs


def convert_element(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Dispatch an SVG element to the appropriate converter."""
    tag = elem.tag.replace(f'{{{SVG_NS}}}', '')

    # Treat an element-level transform exactly like a one-child SVG group.
    # This centralizes ordered transform handling and, importantly, preserves
    # non-center rotation pivots instead of every primitive parsing only the
    # first rotate() angle independently.
    transform = elem.get('transform')
    if transform and tag != 'g':
        wrapper = ET.Element(f'{{{SVG_NS}}}g', {'transform': transform})
        child = copy.deepcopy(elem)
        child.attrib.pop('transform', None)
        wrapper.append(child)
        return convert_g(wrapper, ctx)

    converter = _CONVERTERS.get(tag)
    if converter:
        try:
            return converter(elem, ctx)
        except Exception as e:
            logger.warning(f'Failed to convert <{tag}>: {e}')
            return None

    if tag in _NON_VISUAL_TAGS:
        return None

    return None


def convert_svg_to_slide_shapes(
    svg_path: Path,
    slide_num: int = 1,
    verbose: bool = False,
) -> tuple[str, dict[str, bytes], list[dict[str, str]], list]:
    """Convert an SVG file to a complete DrawingML slide XML.

    Args:
        svg_path: Path to the SVG file.
        slide_num: Slide number (for naming).
        verbose: Print progress info.

    Returns:
        (slide_xml, media_files, rel_entries, anim_targets) where:
        - slide_xml: Complete slide XML string.
        - media_files: Dict of {filename: bytes} for media to write.
        - rel_entries: List of relationship entries to add.
        - anim_targets: List of (shape_id, svg_id) tuples for top-level
          semantic groups, in z-order; consumed by the builder's optional
          per-element entrance timing emitter.
    """
    tree = ET.parse(str(svg_path))
    root = tree.getroot()

    defs = collect_defs(root)
    ctx = ConvertContext(defs=defs, slide_num=slide_num, svg_dir=Path(svg_path).parent)

    shapes: list[str] = []
    converted = 0
    skipped = 0
    # Per-element shape ids of every top-level child, used as an animation
    # fallback when no <g id="..."> groups are present at the root.
    fallback_targets: list = []

    for child in root:
        tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if tag == 'defs':
            continue
        result = convert_element(child, ctx)
        if result:
            shapes.append(result.xml)
            converted += 1
            m = re.search(r'<p:cNvPr id="(\d+)"', result.xml)
            if m:
                fallback_targets.append((int(m.group(1)), tag))
        else:
            if tag not in _NON_VISUAL_TAGS:
                skipped += 1

    # Animation target fallback. Semantic <g id="..."> groups are the
    # preferred anchors (set inside convert_g). When the SVG has none
    # at the root we fall back to top-level primitives, but only when
    # the count is reasonable. Presenter-click animation should reveal
    # semantic blocks, not atomized drawing primitives, so fallback is
    # intentionally capped at a low count.
    anim_fallback_cap = 8
    if not ctx.anim_targets and 0 < len(fallback_targets) <= anim_fallback_cap:
        ctx.anim_targets = fallback_targets

    if verbose:
        logger.info(f'Converted {converted} elements, skipped {skipped}')

    shapes_xml = '\n'.join(shapes)

    slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld>
<p:spTree>
<p:nvGrpSpPr>
<p:cNvPr id="1" name=""/>
<p:cNvGrpSpPr/><p:nvPr/>
</p:nvGrpSpPr>
<p:grpSpPr>
<a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>
<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm>
</p:grpSpPr>
{shapes_xml}
</p:spTree>
</p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>'''

    return slide_xml, ctx.media_files, ctx.rel_entries, ctx.anim_targets
