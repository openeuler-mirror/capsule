from __future__ import annotations

import math
from collections import defaultdict

from lxml import etree

from .model import Chart, Element, LineStyle, Matrix, Page, Paint, Rect, Style, Table
from .namespaces import SVG, XLINK, qn
from .text import FontMetrics, TextLayouter
from .util import fmt, safe_id


PALETTE = ["#C7000B", "#3B82F6", "#22A06B", "#F59E0B", "#7C3AED", "#0891B2", "#64748B"]


class SvgRenderer:
    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        *,
        font_metrics: FontMetrics | None = None,
    ):
        self.width = width
        self.height = height
        self.text_layouter = TextLayouter(font_metrics)
        self.root: etree._Element
        self.defs: etree._Element
        self._resource_ids: dict[str, int] = defaultdict(int)

    def render(self, page: Page) -> etree._Element:
        self.sx = self.width / page.width_emu
        self.sy = self.height / page.height_emu
        self.root = etree.Element(
            qn(SVG, "svg"),
            nsmap={None: SVG, "xlink": XLINK},
            width=str(self.width), height=str(self.height),
            viewBox=f"0 0 {self.width} {self.height}",
        )
        self.defs = etree.SubElement(self.root, qn(SVG, "defs"))
        background = etree.SubElement(self.root, qn(SVG, "g"), id="background")
        bg_attrs = {"x": "0", "y": "0", "width": str(self.width), "height": str(self.height)}
        bg_attrs.update(self._paint_attrs(page.background, "fill", "background"))
        bg_attrs.update({"stroke": "#000000", "stroke-opacity": "0"})
        etree.SubElement(background, qn(SVG, "rect"), **bg_attrs)

        groups = [
            ("master-content", page.master_elements),
            ("layout-content", page.layout_elements),
            ("main-content", page.slide_elements),
        ]
        for group_id, elements in groups:
            group = etree.SubElement(self.root, qn(SVG, "g"), id=group_id)
            for element in elements:
                if not self._fully_outside(element.rect):
                    self._render_element(group, element)
        return self.root

    def tostring(self, page: Page) -> bytes:
        return etree.tostring(
            self.render(page), xml_declaration=True, encoding="UTF-8", pretty_print=True
        )

    def _fully_outside(self, rect: Rect) -> bool:
        x, y, w, h = rect.x * self.sx, rect.y * self.sy, rect.width * self.sx, rect.height * self.sy
        return w > 0 and h > 0 and (x + w < 0 or y + h < 0 or x > self.width or y > self.height)

    def _next_id(self, prefix: str) -> str:
        prefix = safe_id(prefix)
        self._resource_ids[prefix] += 1
        return f"{prefix}-{self._resource_ids[prefix]}"

    def _paint_attrs(self, paint: Paint | None, attr: str, prefix: str) -> dict[str, str]:
        opacity_attr = f"{attr}-opacity"
        if paint is None:
            return {attr: "#000000", opacity_attr: "0"}
        if paint.kind == "gradient" and paint.stops:
            gradient_id = self._next_id(f"gradient-{prefix}")
            angle = math.radians(paint.angle)
            x2 = 0.5 + math.cos(angle) / 2
            y2 = 0.5 + math.sin(angle) / 2
            grad = etree.SubElement(
                self.defs, qn(SVG, "linearGradient"), id=gradient_id,
                x1=fmt(1 - x2), y1=fmt(1 - y2), x2=fmt(x2), y2=fmt(y2),
            )
            for offset, color, opacity in paint.stops:
                etree.SubElement(
                    grad, qn(SVG, "stop"), offset=f"{fmt(offset * 100)}%",
                    **{"stop-color": color, "stop-opacity": fmt(opacity)},
                )
            return {attr: f"url(#{gradient_id})"}
        return {attr: paint.color, opacity_attr: fmt(paint.opacity)}

    def _style_attrs(self, style: Style, prefix: str) -> dict[str, str]:
        attrs = self._paint_attrs(style.fill, "fill", prefix)
        line = style.line
        if line is None or line.paint is None:
            attrs.update({"stroke": "#000000", "stroke-opacity": "0", "stroke-width": "0"})
            return attrs
        attrs.update(self._paint_attrs(line.paint, "stroke", prefix))
        attrs["stroke-width"] = fmt(max(0.1, line.width_emu * (self.sx + self.sy) / 2))
        attrs["stroke-linecap"] = "round"
        attrs["stroke-linejoin"] = "round"
        dash_map = {
            "dash": "6 4", "dashDot": "6 3 1 3", "dot": "1 3",
            "lgDash": "10 4", "lgDashDot": "10 3 1 3", "sysDot": "1 2",
            "sysDash": "4 2", "sysDashDot": "4 2 1 2",
        }
        if line.dash in dash_map:
            attrs["stroke-dasharray"] = dash_map[line.dash]
        if line.head and line.head != "none":
            attrs["marker-start"] = f"url(#{self._marker(line, prefix + '-head')})"
        if line.tail and line.tail != "none":
            attrs["marker-end"] = f"url(#{self._marker(line, prefix + '-tail')})"
        return attrs

    def _marker(self, line: LineStyle, prefix: str) -> str:
        marker_id = self._next_id("arrow-" + prefix)
        color = line.paint.color if line.paint else "#000000"
        opacity = fmt(line.paint.opacity if line.paint else 0)
        marker = etree.SubElement(
            self.defs, qn(SVG, "marker"), id=marker_id,
            viewBox="0 0 10 10", refX="9", refY="5",
            markerWidth="6", markerHeight="6", orient="auto-start-reverse",
        )
        etree.SubElement(
            marker, qn(SVG, "path"), d="M 0 0 L 10 5 L 0 10 z",
            fill=color, **{"fill-opacity": opacity, "stroke": "#000000", "stroke-opacity": "0"},
        )
        return marker_id

    def _element_group(self, parent: etree._Element, element: Element) -> etree._Element:
        attrs = {
            "id": element.element_id,
            "data-name": element.name,
            "data-kind": element.kind,
            "data-role": element.role,
            "data-ooxml-part": element.source_part,
            "data-ooxml-id": element.source_id,
        }
        transform = self._shape_transform(element.rect)
        if transform:
            attrs["transform"] = transform
        return etree.SubElement(parent, qn(SVG, "g"), **attrs)

    def _shape_transform(self, rect: Rect) -> str | None:
        transforms: list[str] = []
        cx = (rect.x + rect.width / 2) * self.sx
        cy = (rect.y + rect.height / 2) * self.sy
        if rect.flip_h:
            transforms.append(f"translate({fmt(2*cx)} 0) scale(-1 1)")
        if rect.flip_v:
            transforms.append(f"translate(0 {fmt(2*cy)}) scale(1 -1)")
        if abs(rect.rotation) > 0.0001:
            transforms.append(f"rotate({fmt(rect.rotation)} {fmt(cx)} {fmt(cy)})")
        return " ".join(transforms) if transforms else None

    def _matrix_px(self, m: Matrix) -> str:
        a = m.a
        b = m.b * self.sy / self.sx
        c = m.c * self.sx / self.sy
        d = m.d
        e = m.e * self.sx
        f = m.f * self.sy
        return f"matrix({fmt(a)} {fmt(b)} {fmt(c)} {fmt(d)} {fmt(e)} {fmt(f)})"

    def _render_element(self, parent: etree._Element, element: Element) -> None:
        group = self._element_group(parent, element)
        if element.kind == "group":
            group.set("transform", self._matrix_px(element.parent_matrix))
            for child in element.children:
                self._render_element(group, child)
            return
        if element.kind in {"shape", "connector"}:
            self._render_shape(group, element)
            if element.text is not None:
                self._render_text(group, element)
        elif element.kind == "image":
            self._render_image(group, element)
        elif element.kind == "table" and element.table is not None:
            self._render_table(group, element.rect, element.table, element.element_id)
        elif element.kind == "chart" and element.chart is not None:
            self._render_chart(group, element.rect, element.chart, element.element_id)

    def _render_shape(self, group: etree._Element, element: Element) -> None:
        r = element.rect
        x, y, w, h = r.x * self.sx, r.y * self.sy, r.width * self.sx, r.height * self.sy
        style = self._style_attrs(element.style, element.element_id)
        preset = element.preset
        common = dict(style)
        if preset in {"line", "straightConnector1", "bentConnector2", "bentConnector3", "curvedConnector2", "curvedConnector3"} or element.kind == "connector":
            etree.SubElement(group, qn(SVG, "line"), x1=fmt(x), y1=fmt(y), x2=fmt(x + w), y2=fmt(y + h), **common)
            return
        if preset in {"ellipse", "oval"}:
            etree.SubElement(group, qn(SVG, "ellipse"), cx=fmt(x + w/2), cy=fmt(y + h/2), rx=fmt(abs(w)/2), ry=fmt(abs(h)/2), **common)
            return
        if preset == "roundRect":
            etree.SubElement(group, qn(SVG, "rect"), x=fmt(x), y=fmt(y), width=fmt(w), height=fmt(h), rx=fmt(min(abs(w), abs(h)) * 0.12), ry=fmt(min(abs(w), abs(h)) * 0.12), **common)
            return
        points = self._preset_points(preset, x, y, w, h, element.adjustments)
        if points is not None:
            etree.SubElement(group, qn(SVG, "polygon"), points=" ".join(f"{fmt(px)},{fmt(py)}" for px, py in points), **common)
            return
        etree.SubElement(group, qn(SVG, "rect"), x=fmt(x), y=fmt(y), width=fmt(w), height=fmt(h), **common)
        if preset != "rect":
            group.set("data-geometry-fallback", preset)

    @staticmethod
    def _preset_points(preset: str, x: float, y: float, w: float, h: float, adjustments: dict[str, float] | None = None) -> list[tuple[float, float]] | None:
        adjustments = adjustments or {}
        if preset == "triangle": return [(x+w/2,y),(x+w,y+h),(x,y+h)]
        if preset == "rtTriangle": return [(x,y),(x+w,y+h),(x,y+h)]
        if preset == "diamond": return [(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)]
        if preset == "parallelogram": return [(x+w*.2,y),(x+w,y),(x+w*.8,y+h),(x,y+h)]
        if preset == "trapezoid": return [(x+w*.2,y),(x+w*.8,y),(x+w,y+h),(x,y+h)]
        if preset == "hexagon": return [(x+w*.25,y),(x+w*.75,y),(x+w,y+h/2),(x+w*.75,y+h),(x+w*.25,y+h),(x,y+h/2)]
        if preset == "pentagon": return [(x+w/2,y),(x+w,y+h*.38),(x+w*.81,y+h),(x+w*.19,y+h),(x,y+h*.38)]
        if preset == "rightArrow": return [(x,y+h*.25),(x+w*.62,y+h*.25),(x+w*.62,y),(x+w,y+h/2),(x+w*.62,y+h),(x+w*.62,y+h*.75),(x,y+h*.75)]
        if preset == "leftArrow": return [(x+w,y+h*.25),(x+w*.38,y+h*.25),(x+w*.38,y),(x,y+h/2),(x+w*.38,y+h),(x+w*.38,y+h*.75),(x+w,y+h*.75)]
        if preset == "upArrow": return [(x+w*.25,y+h),(x+w*.25,y+h*.38),(x,y+h*.38),(x+w/2,y),(x+w,y+h*.38),(x+w*.75,y+h*.38),(x+w*.75,y+h)]
        if preset == "downArrow": return [(x+w*.25,y),(x+w*.25,y+h*.62),(x,y+h*.62),(x+w/2,y+h),(x+w,y+h*.62),(x+w*.75,y+h*.62),(x+w*.75,y)]
        if preset == "leftRightArrow": return [(x,y+h/2),(x+w*.25,y),(x+w*.25,y+h*.25),(x+w*.75,y+h*.25),(x+w*.75,y),(x+w,y+h/2),(x+w*.75,y+h),(x+w*.75,y+h*.75),(x+w*.25,y+h*.75),(x+w*.25,y+h)]
        if preset == "chevron": return [(x,y),(x+w*.65,y),(x+w,y+h/2),(x+w*.65,y+h),(x,y+h),(x+w*.35,y+h/2)]
        if preset == "homePlate": return [(x,y),(x+w*.75,y),(x+w,y+h/2),(x+w*.75,y+h),(x,y+h)]
        if preset == "corner":
            tx = max(0.005, min(0.5, adjustments.get("adj1", .15)))
            ty = max(0.005, min(0.5, adjustments.get("adj2", .15)))
            return [(x,y),(x+w,y),(x+w,y+h*ty),(x+w*tx,y+h*ty),(x+w*tx,y+h),(x,y+h)]
        return None

    def _render_text(self, group: etree._Element, element: Element) -> None:
        text_group = etree.SubElement(group, qn(SVG, "g"), id=f"{element.element_id}-text")
        for index, run in enumerate(self.text_layouter.layout(element.text, element.rect, self.sx, self.sy), 1):
            requested = [item.strip().strip('"\'') for item in run.style.font_family.split(",") if item.strip()]
            fallback = ["Microsoft YaHei", "Noto Sans SC", "SimSun", "Arial", "sans-serif"]
            families: list[str] = []
            for item in [run.resolved_font_family] + requested + fallback:
                if item and item not in families:
                    families.append(item)
            family = ", ".join(families)
            attrs = {
                "x": fmt(run.x), "y": fmt(run.baseline_y),
                "fill": run.style.color, "fill-opacity": fmt(run.style.opacity),
                "font-family": family, "font-size": fmt(run.px_size),
                "font-weight": "700" if run.style.bold else "400",
                "font-style": "italic" if run.style.italic else "normal",
                "data-run": str(index),
                "data-source-font": run.style.font_family,
                "data-resolved-font": run.resolved_font_family,
                "data-font-ascent": fmt(run.ascent),
                "data-font-descent": fmt(run.descent),
            }
            if run.width > 0:
                # textLength preserves the measured horizontal advance when
                # the SVG is opened on a host that lacks the source font. It
                # also gives the downstream DrawingML converter an exact width
                # contract to consume instead of re-estimating it.
                attrs["textLength"] = fmt(run.width)
                attrs["lengthAdjust"] = "spacingAndGlyphs"
                attrs["data-measured-width"] = fmt(run.width)
            if run.font_substituted:
                attrs["data-font-substituted"] = "true"
            decorations = []
            if run.style.underline: decorations.append("underline")
            if run.style.strike: decorations.append("line-through")
            if decorations: attrs["text-decoration"] = " ".join(decorations)
            text = etree.SubElement(text_group, qn(SVG, "text"), **attrs)
            if run.text.startswith(" ") or run.text.endswith(" ") or "  " in run.text:
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            text.text = run.text

    def _render_image(self, group: etree._Element, element: Element) -> None:
        if not element.image_href:
            group.set("data-missing-image", element.image_part or "unknown")
            return
        r = element.rect
        x, y, w, h = r.x*self.sx, r.y*self.sy, r.width*self.sx, r.height*self.sy
        image_parent = group
        image_x, image_y, image_w, image_h = x, y, w, h
        if element.crop and any(element.crop):
            left, top, right, bottom = element.crop
            visible_w, visible_h = max(0.0001, 1-left-right), max(0.0001, 1-top-bottom)
            image_w, image_h = w/visible_w, h/visible_h
            image_x, image_y = x-image_w*left, y-image_h*top
            clip_id = self._next_id("clip-" + element.element_id)
            clip = etree.SubElement(self.defs, qn(SVG, "clipPath"), id=clip_id)
            etree.SubElement(clip, qn(SVG, "rect"), x=fmt(x), y=fmt(y), width=fmt(w), height=fmt(h))
            image_parent = etree.SubElement(group, qn(SVG, "g"), **{"clip-path": f"url(#{clip_id})"})
        etree.SubElement(
            image_parent, qn(SVG, "image"), href=element.image_href,
            x=fmt(image_x), y=fmt(image_y), width=fmt(image_w), height=fmt(image_h),
            preserveAspectRatio="none",
        ).set(qn(XLINK, "href"), element.image_href)
        if element.style.line is not None and element.style.line.paint is not None:
            outline = self._style_attrs(Style(fill=None, line=element.style.line), element.element_id + "-outline")
            etree.SubElement(group, qn(SVG, "rect"), x=fmt(x), y=fmt(y), width=fmt(w), height=fmt(h), **outline)

    def _render_table(self, group: etree._Element, rect: Rect, table: Table, prefix: str) -> None:
        total_w = sum(table.column_widths_emu) or rect.width
        total_h = sum(table.row_heights_emu) or rect.height
        scale_x, scale_y = rect.width / total_w, rect.height / total_h
        y = rect.y
        for row_idx, row in enumerate(table.rows):
            h = (table.row_heights_emu[row_idx] if row_idx < len(table.row_heights_emu) else total_h/max(1,len(table.rows))) * scale_y
            x = rect.x
            col_idx = 0
            for cell_idx, cell in enumerate(row):
                span = max(1, cell.col_span)
                raw_w = sum(table.column_widths_emu[col_idx:col_idx+span])
                w = raw_w * scale_x
                cell_group = etree.SubElement(group, qn(SVG, "g"), id=f"{prefix}-r{row_idx+1}c{cell_idx+1}")
                attrs = {"x":fmt(x*self.sx),"y":fmt(y*self.sy),"width":fmt(w*self.sx),"height":fmt(h*self.sy)}
                attrs.update(self._paint_attrs(cell.fill or Paint("#FFFFFF"), "fill", f"{prefix}-cell"))
                attrs.update({"stroke":"#808080","stroke-opacity":"1","stroke-width":"1"})
                etree.SubElement(cell_group, qn(SVG, "rect"), **attrs)
                temp = Element(f"{prefix}-r{row_idx+1}c{cell_idx+1}", "cell", "shape", Rect(x,y,w,h), text=cell.text)
                self._render_text(cell_group, temp)
                x += w; col_idx += span
            y += h

    def _render_chart(self, group: etree._Element, rect: Rect, chart: Chart, prefix: str) -> None:
        x, y, w, h = rect.x*self.sx, rect.y*self.sy, rect.width*self.sx, rect.height*self.sy
        etree.SubElement(group, qn(SVG, "rect"), x=fmt(x), y=fmt(y), width=fmt(w), height=fmt(h), fill="#FFFFFF", **{"fill-opacity":"1","stroke":"#D0D0D0","stroke-width":"1"})
        if chart.title:
            title = etree.SubElement(group, qn(SVG, "text"), x=fmt(x+w/2), y=fmt(y+28), fill="#222222", **{"fill-opacity":"1","font-family":"Microsoft YaHei, Arial, sans-serif","font-size":"18","font-weight":"700","text-anchor":"middle"})
            title.text = chart.title
        top = y + (42 if chart.title else 20)
        plot_x, plot_y, plot_w, plot_h = x+48, top, max(10,w-80), max(10,h-(top-y)-50)
        if chart.chart_type in {"pie", "doughnut"}:
            values = chart.series[0].values if chart.series else []
            total = sum(max(0,v) for v in values) or 1
            angle = -math.pi/2
            cx, cy, radius = plot_x+plot_w/2, plot_y+plot_h/2, min(plot_w,plot_h)*.38
            for i, value in enumerate(values):
                sweep = max(0,value)/total*2*math.pi
                x1,y1=cx+radius*math.cos(angle),cy+radius*math.sin(angle)
                x2,y2=cx+radius*math.cos(angle+sweep),cy+radius*math.sin(angle+sweep)
                large="1" if sweep>math.pi else "0"
                if chart.chart_type == "doughnut":
                    inner=radius*.55
                    ix1,iy1=cx+inner*math.cos(angle),cy+inner*math.sin(angle)
                    ix2,iy2=cx+inner*math.cos(angle+sweep),cy+inner*math.sin(angle+sweep)
                    d=f"M {fmt(x1)} {fmt(y1)} A {fmt(radius)} {fmt(radius)} 0 {large} 1 {fmt(x2)} {fmt(y2)} L {fmt(ix2)} {fmt(iy2)} A {fmt(inner)} {fmt(inner)} 0 {large} 0 {fmt(ix1)} {fmt(iy1)} Z"
                else:
                    d=f"M {fmt(cx)} {fmt(cy)} L {fmt(x1)} {fmt(y1)} A {fmt(radius)} {fmt(radius)} 0 {large} 1 {fmt(x2)} {fmt(y2)} Z"
                etree.SubElement(group, qn(SVG,"path"), d=d, fill=PALETTE[i%len(PALETTE)], **{"fill-opacity":"1","stroke":"#FFFFFF","stroke-width":"1"})
                angle += sweep
            return
        all_values=[v for s in chart.series for v in s.values]
        maximum=max(all_values,default=1); minimum=min(0,min(all_values,default=0)); span=maximum-minimum or 1
        etree.SubElement(group, qn(SVG,"line"), x1=fmt(plot_x),y1=fmt(plot_y+plot_h),x2=fmt(plot_x+plot_w),y2=fmt(plot_y+plot_h),stroke="#666666",**{"stroke-opacity":"1","stroke-width":"1"})
        if chart.chart_type == "line":
            for si, series in enumerate(chart.series):
                count=max(1,len(series.values)-1); pts=[]
                for i,value in enumerate(series.values):
                    px=plot_x+plot_w*i/count; py=plot_y+plot_h-(value-minimum)/span*plot_h; pts.append((px,py))
                etree.SubElement(group, qn(SVG,"polyline"), points=" ".join(f"{fmt(px)},{fmt(py)}" for px,py in pts), fill="#000000", **{"fill-opacity":"0","stroke":PALETTE[si%len(PALETTE)],"stroke-opacity":"1","stroke-width":"2"})
        else:
            category_count=max((len(s.values) for s in chart.series),default=1); series_count=max(1,len(chart.series)); band=plot_w/category_count; bar_w=band*.75/series_count
            for si, series in enumerate(chart.series):
                for i,value in enumerate(series.values):
                    bh=(value-minimum)/span*plot_h; bx=plot_x+i*band+band*.125+si*bar_w; by=plot_y+plot_h-bh
                    etree.SubElement(group, qn(SVG,"rect"), x=fmt(bx),y=fmt(by),width=fmt(bar_w),height=fmt(bh),fill=PALETTE[si%len(PALETTE)],**{"fill-opacity":"1","stroke":"#000000","stroke-opacity":"0"})
