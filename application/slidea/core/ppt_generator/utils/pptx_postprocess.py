"""Post-processing helpers shared by the HTML and SVG → PPTX export routes.

Both routes have the same problem: the source format expresses the slide
backdrop as the bottom-most full-slide solid-fill shape (LibreOffice produces
those when converting PDF→PPTX; the LLM-generated SVG files start with a
``<rect width="100%" height="100%" fill="#x">``). Either way the result is a
real shape on the slide instead of the slide's own background fill, which
hurts editability in PowerPoint.

``remove_full_slide_solid_backdrops`` walks the z-order from the bottom up,
drops every shape that is a full-slide solid fill, and lifts the last removed
color onto ``slide.background.fill`` so the page keeps its backdrop.

``flatten_all_groups`` recursively dissolves every ``p:grpSp`` on every slide,
hoisting each group's children onto the slide (or the parent group) with
absolute coordinates. Run this before backdrop detection on SVG-built PPTX so
group bounding boxes never falsely match "full-slide solid fill" and so a
backdrop rect that happens to be wrapped in a group still gets stripped.
"""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn

from core.utils.logger import logger


_DRAWABLE_TAGS = frozenset(qn(t) for t in ("p:sp", "p:cxnSp", "p:pic", "p:grpSp", "p:graphicFrame"))


def remove_full_slide_solid_backdrops(pptx_path) -> None:
    """Strip full-slide solid-fill backdrop shapes in-place and lift to slide background.

    python-pptx ``shapes[0]`` is the bottom-most layer. We pop shapes from the
    bottom while they are full-slide solid fills; the last removed color is
    promoted to the slide's background fill.
    """
    logger.info(f"Stripping full-slide solid backdrops in {pptx_path}")
    prs = Presentation(str(pptx_path))
    slide_w, slide_h = prs.slide_width, prs.slide_height

    for slide in prs.slides:
        last_rgb = None
        removed = 0
        while len(slide.shapes) > 0:
            shape = slide.shapes[0]
            rgb = _full_slide_solid_fill_rgb(shape, slide_w, slide_h)
            if rgb is None:
                break
            last_rgb = rgb
            sp = shape._element
            sp.getparent().remove(sp)
            removed += 1

        if last_rgb is not None:
            fill = slide.background.fill
            fill.solid()
            fill.fore_color.rgb = last_rgb
        logger.debug(f"removed {removed} backdrop shape(s) from slide")

    prs.save(str(pptx_path))
    logger.info(f"Successfully cleaned bottom backdrop shapes in '{pptx_path}'")


def _full_slide_solid_fill_rgb(shape, slide_w, slide_h, tol_ratio=0.1):
    """Return shape's solid-fill RGB iff it has one AND covers the whole slide
    within ``tol_ratio`` of slide dimensions. Otherwise None."""
    rgb = _extract_solid_fill_rgb(shape)
    if rgb is None:
        return None
    try:
        left, top, width, height = shape.left, shape.top, shape.width, shape.height
    except AttributeError:
        return None
    if None in (left, top, width, height):
        return None
    w_tol = slide_w * tol_ratio
    h_tol = slide_h * tol_ratio
    covers_slide = (
        left <= w_tol
        and top <= h_tol
        and left + width >= slide_w - w_tol
        and top + height >= slide_h - h_tol
    )
    return rgb if covers_slide else None


def _extract_solid_fill_rgb(shape):
    """Return the shape's solid-fill RGB color, or None if absent/unsupported."""
    srgb = shape._element.find(f".//{qn('a:solidFill')}/{qn('a:srgbClr')}")
    if srgb is None:
        return None
    val = srgb.get("val")
    if not val:
        return None
    try:
        return RGBColor.from_string(val)
    except ValueError:
        return None


def flatten_all_groups(pptx_path) -> None:
    """Recursively dissolve every ``p:grpSp`` on every slide.

    Each group's drawable children are spliced into the group's parent in the
    same z-order, with their offsets/extents transformed from the group's
    inner coordinate system into the parent's coordinate system. The group's
    rotation is honored only when zero — groups with non-zero rotation are
    left intact (rotating each child around the group's pivot is geometrically
    non-trivial and rare in LLM-generated SVG).

    Idempotent: running on a PPTX with no groups does nothing.
    """
    logger.info(f"Flattening grouped shapes in {pptx_path}")
    prs = Presentation(str(pptx_path))
    total = 0
    for slide in prs.slides:
        spTree = slide.shapes._spTree
        # Each pass dissolves immediate-child groups in spTree. Children of a
        # dissolved group that are themselves groups become new immediate
        # children, so loop until none remain.
        while True:
            n = _ungroup_one_pass(spTree)
            total += n
            if n == 0:
                break
    prs.save(str(pptx_path))
    logger.info(f"Flattened {total} group(s) in '{pptx_path}'")


def _ungroup_one_pass(parent) -> int:
    """Dissolve every immediate-child ``p:grpSp`` of ``parent``. Returns count."""
    grpSp_tag = qn("p:grpSp")
    flattened = 0
    for grp in [c for c in list(parent) if c.tag == grpSp_tag]:
        if _flatten_group(grp):
            flattened += 1
    return flattened


def _flatten_group(grp) -> bool:
    """Replace ``grp`` with its drawable children in ``grp``'s parent.

    Returns True iff the group was successfully dissolved. False if the group
    has rotation (skipped to avoid wrong child positions) or if the group has
    no parent (already detached).
    """
    parent = grp.getparent()
    if parent is None:
        return False

    grp_xfrm = grp.find(qn("p:grpSpPr") + "/" + qn("a:xfrm"))
    rot = grp_xfrm.get("rot") if grp_xfrm is not None else None
    if rot and int(rot) != 0:
        logger.debug("Skip flattening rotated group (rot=%s)", rot)
        return False

    if grp_xfrm is None:
        ox = oy = 0
        cox = coy = 0
        sx = sy = 1.0
    else:
        off = grp_xfrm.find(qn("a:off"))
        ext = grp_xfrm.find(qn("a:ext"))
        chOff = grp_xfrm.find(qn("a:chOff"))
        chExt = grp_xfrm.find(qn("a:chExt"))
        ox = int(off.get("x", "0")) if off is not None else 0
        oy = int(off.get("y", "0")) if off is not None else 0
        ex = int(ext.get("cx", "0")) if ext is not None else 0
        ey = int(ext.get("cy", "0")) if ext is not None else 0
        cox = int(chOff.get("x", "0")) if chOff is not None else 0
        coy = int(chOff.get("y", "0")) if chOff is not None else 0
        cex = int(chExt.get("cx", str(ex or 1))) if chExt is not None else (ex or 1)
        cey = int(chExt.get("cy", str(ey or 1))) if chExt is not None else (ey or 1)
        sx = (ex / cex) if cex else 1.0
        sy = (ey / cey) if cey else 1.0

    children = [c for c in list(grp) if c.tag in _DRAWABLE_TAGS]
    insert_idx = list(parent).index(grp)
    for child in children:
        _apply_group_transform(child, ox, oy, cox, coy, sx, sy)
        parent.insert(insert_idx, child)
        insert_idx += 1
    parent.remove(grp)
    return True


def _apply_group_transform(child, ox, oy, cox, coy, sx, sy) -> None:
    """Map child's xfrm offset/extent from group's inner coords into parent coords.

    Formula: ``slide_x = group.off.x + (child.x - group.chOff.x) * (group.ext / group.chExt)``.
    For nested ``p:grpSp`` children we transform their own off/ext only — chOff /
    chExt define an internal coordinate system independent of how the nested
    group is placed in its parent and stay untouched.
    """
    xfrm = _find_xfrm(child)
    if xfrm is None:
        return
    off = xfrm.find(qn("a:off"))
    ext = xfrm.find(qn("a:ext"))
    if off is not None:
        x = int(off.get("x", "0"))
        y = int(off.get("y", "0"))
        off.set("x", str(round(ox + (x - cox) * sx)))
        off.set("y", str(round(oy + (y - coy) * sy)))
    if ext is not None:
        cx = int(ext.get("cx", "0"))
        cy = int(ext.get("cy", "0"))
        ext.set("cx", str(max(round(cx * sx), 1)))
        ext.set("cy", str(max(round(cy * sy), 1)))


def _find_xfrm(child):
    """Return the child element's xfrm element, or None if absent.

    Drawable XML location varies by tag::

        p:sp / p:cxnSp / p:pic → p:spPr/a:xfrm
        p:grpSp                → p:grpSpPr/a:xfrm
        p:graphicFrame         → p:xfrm
    """
    if child.tag in (qn("p:sp"), qn("p:cxnSp"), qn("p:pic")):
        return child.find(qn("p:spPr") + "/" + qn("a:xfrm"))
    if child.tag == qn("p:grpSp"):
        return child.find(qn("p:grpSpPr") + "/" + qn("a:xfrm"))
    if child.tag == qn("p:graphicFrame"):
        return child.find(qn("p:xfrm"))
    return None
