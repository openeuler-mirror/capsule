from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePosixPath

from lxml import etree

from .model import (
    Chart, ChartSeries, Element, Matrix, Page, Paint, Rect, Table, TableCell, TextBody,
)
from .namespaces import A, C, NS, P, R, qn
from .package import OpcPackage, PackageError
from .text import TextParser
from .theme import Theme
from .util import flip_matrix, rotate_matrix, safe_id


@dataclass
class PartShape:
    node: etree._Element
    part: str


class PresentationParser:
    def __init__(self, package: OpcPackage):
        self.pkg = package
        self.presentation_part = "ppt/presentation.xml"
        self.presentation = package.xml(self.presentation_part)
        size = self.presentation.find("p:sldSz", NS)
        self.width_emu = float(size.get("cx", "12192000")) if size is not None else 12192000.0
        self.height_emu = float(size.get("cy", "6858000")) if size is not None else 6858000.0

    def slide_parts(self) -> list[str]:
        rels = self.pkg.relationships(self.presentation_part)
        result: list[str] = []
        for slide_id in self.presentation.findall("p:sldIdLst/p:sldId", NS):
            rel_id = slide_id.get(qn(R, "id"), "")
            rel = rels.get(rel_id)
            if rel and rel.rel_type == "slide" and not rel.external:
                result.append(rel.target)
        return result

    def parse(self) -> list[Page]:
        return [self.parse_slide(part, number) for number, part in enumerate(self.slide_parts(), 1)]

    def _first_related(self, part: str, rel_type: str) -> str | None:
        rels = self.pkg.related_by_type(part, rel_type)
        return rels[0].target if rels and not rels[0].external else None

    def parse_slide(self, slide_part: str, number: int) -> Page:
        slide = self.pkg.xml(slide_part)
        layout_part = self._first_related(slide_part, "slideLayout")
        layout = self.pkg.xml(layout_part) if layout_part else None
        master_part = self._first_related(layout_part, "slideMaster") if layout_part else None
        master = self.pkg.xml(master_part) if master_part else None
        theme_part = self._first_related(master_part, "theme") if master_part else None
        color_map: dict[str, str] = {}
        if master is not None:
            node = master.find("p:clrMap", NS)
            if node is not None:
                color_map.update(node.attrib)
        for root in (layout, slide):
            if root is None:
                continue
            override = root.find("p:clrMapOvr/a:overrideClrMapping", NS)
            if override is not None:
                color_map.update(override.attrib)
        theme = Theme(self.pkg.xml(theme_part) if theme_part else None, color_map)
        page = Page(number, slide_part, self.width_emu, self.height_emu)
        page.background = theme.background_paint([x for x in (slide, layout, master) if x is not None])

        layout_placeholders = self._placeholder_map(layout)
        master_placeholders = self._placeholder_map(master)
        show_master = slide.get("showMasterSp", "1") != "0"
        show_master = show_master and (layout is None or layout.get("showMasterSp", "1") != "0")
        if show_master and master is not None and master_part:
            page.master_elements = self._parse_static_tree(
                master, master_part, theme, master, number, "master"
            )
        if layout is not None and layout_part:
            page.layout_elements = self._parse_static_tree(
                layout, layout_part, theme, master, number, "layout"
            )
        sp_tree = slide.find("p:cSld/p:spTree", NS)
        if sp_tree is not None:
            for child in sp_tree:
                local = etree.QName(child).localname
                if local in {"nvGrpSpPr", "grpSpPr"}:
                    continue
                ph = child.find(".//p:ph", NS)
                inherited: list[PartShape] = []
                if ph is not None:
                    layout_match = self._match_placeholder(ph, layout_placeholders)
                    if layout_match is not None and layout_part:
                        inherited.append(PartShape(layout_match, layout_part))
                        layout_ph = layout_match.find(".//p:ph", NS)
                        master_match = self._match_placeholder(layout_ph if layout_ph is not None else ph, master_placeholders)
                    else:
                        master_match = self._match_placeholder(ph, master_placeholders)
                    if master_match is not None and master_part:
                        inherited.append(PartShape(master_match, master_part))
                element = self._parse_element(
                    PartShape(child, slide_part), inherited, theme, master, number, "slide"
                )
                if element is not None:
                    page.slide_elements.append(element)
        return page

    @staticmethod
    def _placeholder_map(root: etree._Element | None) -> list[etree._Element]:
        return root.xpath("p:cSld/p:spTree/*[.//p:ph]", namespaces=NS) if root is not None else []

    @staticmethod
    def _match_placeholder(ph: etree._Element | None, candidates: list[etree._Element]) -> etree._Element | None:
        if ph is None:
            return None
        idx = ph.get("idx")
        kind = ph.get("type", "body")
        if idx is not None:
            for candidate in candidates:
                cph = candidate.find(".//p:ph", NS)
                if cph is not None and cph.get("idx") == idx:
                    return candidate
        for candidate in candidates:
            cph = candidate.find(".//p:ph", NS)
            if cph is not None and cph.get("type", "body") == kind:
                return candidate
        return None

    def _parse_static_tree(
        self,
        root: etree._Element,
        part: str,
        theme: Theme,
        master: etree._Element | None,
        page_number: int,
        provenance: str,
    ) -> list[Element]:
        result: list[Element] = []
        sp_tree = root.find("p:cSld/p:spTree", NS)
        if sp_tree is None:
            return result
        for child in sp_tree:
            local = etree.QName(child).localname
            if local in {"nvGrpSpPr", "grpSpPr"} or child.find(".//p:ph", NS) is not None:
                continue
            element = self._parse_element(
                PartShape(child, part), [], theme, master, page_number, provenance
            )
            if element is not None:
                result.append(element)
        return result

    @staticmethod
    def _non_visual(node: etree._Element) -> etree._Element | None:
        local = etree.QName(node).localname
        paths = {
            "sp": "p:nvSpPr/p:cNvPr", "pic": "p:nvPicPr/p:cNvPr",
            "cxnSp": "p:nvCxnSpPr/p:cNvPr", "grpSp": "p:nvGrpSpPr/p:cNvPr",
            "graphicFrame": "p:nvGraphicFramePr/p:cNvPr",
        }
        return node.find(paths.get(local, ""), NS) if local in paths else None

    @staticmethod
    def _xfrm(sources: list[PartShape], local: str) -> etree._Element | None:
        for source in sources:
            if local == "graphicFrame":
                node = source.node.find("p:xfrm", NS)
            elif local == "grpSp":
                node = source.node.find("p:grpSpPr/a:xfrm", NS)
            else:
                node = source.node.find("p:spPr/a:xfrm", NS)
            if node is not None:
                return node
        return None

    @staticmethod
    def _rect(xfrm: etree._Element | None) -> Rect:
        if xfrm is None:
            return Rect(0, 0, 0, 0)
        off = xfrm.find("a:off", NS)
        ext = xfrm.find("a:ext", NS)
        return Rect(
            float(off.get("x", "0")) if off is not None else 0.0,
            float(off.get("y", "0")) if off is not None else 0.0,
            float(ext.get("cx", "0")) if ext is not None else 0.0,
            float(ext.get("cy", "0")) if ext is not None else 0.0,
            float(xfrm.get("rot", "0")) / 60000,
            xfrm.get("flipH", "0") == "1",
            xfrm.get("flipV", "0") == "1",
        )

    @staticmethod
    def _group_matrix(xfrm: etree._Element | None) -> Matrix:
        if xfrm is None:
            return Matrix()
        off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
        ch_off, ch_ext = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
        ox = float(off.get("x", "0")) if off is not None else 0
        oy = float(off.get("y", "0")) if off is not None else 0
        ew = float(ext.get("cx", "1")) if ext is not None else 1
        eh = float(ext.get("cy", "1")) if ext is not None else 1
        cx = float(ch_off.get("x", "0")) if ch_off is not None else 0
        cy = float(ch_off.get("y", "0")) if ch_off is not None else 0
        cw = float(ch_ext.get("cx", str(ew))) if ch_ext is not None else ew
        ch = float(ch_ext.get("cy", str(eh))) if ch_ext is not None else eh
        sx, sy = (ew / cw if cw else 1), (eh / ch if ch else 1)
        base = Matrix(sx, 0, 0, sy, ox - sx * cx, oy - sy * cy)
        center_x, center_y = ox + ew / 2, oy + eh / 2
        if xfrm.get("flipH", "0") == "1" or xfrm.get("flipV", "0") == "1":
            base = flip_matrix(xfrm.get("flipH", "0") == "1", xfrm.get("flipV", "0") == "1", center_x, center_y) @ base
        rotation = float(xfrm.get("rot", "0")) / 60000
        if rotation:
            base = rotate_matrix(rotation, center_x, center_y) @ base
        return base

    def _font_ref_color(self, nodes: list[etree._Element], theme: Theme) -> str | None:
        for node in nodes:
            ref = node.find("p:style/a:fontRef", NS)
            if ref is None:
                continue
            child = next(iter(ref), None)
            return theme.resolve_color(child)[0] if child is not None else None
        return None

    def _role(self, node: etree._Element, rect: Rect, ph_type: str | None, provenance: str) -> str:
        if ph_type in {"title", "ctrTitle", "subTitle"}:
            return "header"
        if ph_type in {"ftr", "dt"}:
            return "footer"
        if ph_type == "sldNum":
            return "page-number"
        name = (self._non_visual(node).get("name", "") if self._non_visual(node) is not None else "").lower()
        if "footer" in name or "页脚" in name:
            return "footer"
        if provenance == "master" and rect.y > self.height_emu * 0.88:
            return "footer"
        return "content"

    def _parse_element(
        self,
        own: PartShape,
        inherited: list[PartShape],
        theme: Theme,
        master: etree._Element | None,
        page_number: int,
        provenance: str,
    ) -> Element | None:
        node = own.node
        local = etree.QName(node).localname
        if local not in {"sp", "pic", "cxnSp", "grpSp", "graphicFrame"}:
            return None
        nv = self._non_visual(node)
        if nv is not None and nv.get("hidden", "0") == "1":
            return None
        source_id = nv.get("id", "0") if nv is not None else "0"
        name = nv.get("name", f"{local}-{source_id}") if nv is not None else f"{local}-{source_id}"
        element_id = safe_id(f"{PurePosixPath(own.part).stem}-{source_id}-{name}")
        sources = [own] + inherited
        xfrm = self._xfrm(sources, local)
        rect = self._rect(xfrm)
        ph = node.find(".//p:ph", NS)
        ph_type = ph.get("type", "body") if ph is not None else None
        role = self._role(node, rect, ph_type, provenance)
        if local == "grpSp":
            element = Element(element_id, name, "group", rect, source_part=own.part, source_id=source_id, role=role)
            element.parent_matrix = self._group_matrix(xfrm)
            for child in node:
                child_local = etree.QName(child).localname
                if child_local in {"nvGrpSpPr", "grpSpPr"}:
                    continue
                parsed = self._parse_element(
                    PartShape(child, own.part), [], theme, master, page_number, provenance
                )
                if parsed is not None:
                    element.children.append(parsed)
            return element
        if local == "graphicFrame":
            return self._parse_graphic_frame(own, rect, element_id, name, theme, master, page_number, role)

        shape_nodes = [s.node for s in sources]
        inherited_text_nodes = [s.node for s in inherited]
        style = theme.shape_style(shape_nodes)
        preset = "line" if local == "cxnSp" else "rect"
        geometry_node = None
        for source in shape_nodes:
            geom = source.find("p:spPr/a:prstGeom", NS)
            if geom is not None:
                preset = geom.get("prst", preset)
                geometry_node = geom
                break
        kind = {"sp": "shape", "pic": "image", "cxnSp": "connector"}[local]
        element = Element(
            element_id, name, kind, rect, style=style, preset=preset,
            source_part=own.part, source_id=source_id, role=role,
        )
        if geometry_node is not None:
            for guide in geometry_node.findall("a:avLst/a:gd", NS):
                formula = guide.get("fmla", "").split()
                if len(formula) == 2 and formula[0] == "val":
                    try:
                        element.adjustments[guide.get("name", "adj")] = float(formula[1]) / 100000
                    except ValueError:
                        pass
        if xfrm is None:
            element.warnings.append("missing-transform")
        if local == "pic":
            blip = node.find("p:blipFill/a:blip", NS)
            rel_id = blip.get(qn(R, "embed"), "") if blip is not None else ""
            rel = self.pkg.related(own.part, rel_id) if rel_id else None
            if rel and not rel.external:
                element.image_part = rel.target
            else:
                element.warnings.append("missing-image-relationship")
            src_rect = node.find("p:blipFill/a:srcRect", NS)
            if src_rect is not None:
                element.crop = tuple(float(src_rect.get(k, "0")) / 100000 for k in ("l", "t", "r", "b"))
            return element
        tx_body = node.find("p:txBody", NS)
        text_parser = TextParser(theme, master)
        element.text = text_parser.parse(
            tx_body,
            inherited_text_nodes,
            ph_type,
            page_number,
            self._font_ref_color(shape_nodes, theme),
        )
        if node.find("p:spPr/a:custGeom", NS) is not None:
            element.warnings.append("custom-geometry-fallback")
        return element

    def _parse_graphic_frame(
        self,
        own: PartShape,
        rect: Rect,
        element_id: str,
        name: str,
        theme: Theme,
        master: etree._Element | None,
        page_number: int,
        role: str,
    ) -> Element:
        data = own.node.find("a:graphic/a:graphicData", NS)
        uri = data.get("uri", "") if data is not None else ""
        if data is not None and data.find("a:tbl", NS) is not None:
            element = Element(element_id, name, "table", rect, source_part=own.part, role=role)
            element.table = self._parse_table(data.find("a:tbl", NS), theme, master, page_number)
            return element
        chart_ref = data.find("c:chart", NS) if data is not None else None
        if chart_ref is not None:
            element = Element(element_id, name, "chart", rect, source_part=own.part, role="chart")
            rel = self.pkg.related(own.part, chart_ref.get(qn(R, "id"), ""))
            if rel and not rel.external:
                element.chart = self._parse_chart(rel.target)
            else:
                element.warnings.append("missing-chart-relationship")
            return element
        element = Element(element_id, name, "unsupported", rect, source_part=own.part, role=role)
        element.warnings.append(f"unsupported-graphic-frame:{uri}")
        return element

    def _parse_table(
        self,
        table_node: etree._Element,
        theme: Theme,
        master: etree._Element | None,
        page_number: int,
    ) -> Table:
        widths = [float(col.get("w", "0")) for col in table_node.findall("a:tblGrid/a:gridCol", NS)]
        heights: list[float] = []
        rows: list[list[TableCell]] = []
        text_parser = TextParser(theme, master)
        for tr in table_node.findall("a:tr", NS):
            heights.append(float(tr.get("h", "0")))
            row: list[TableCell] = []
            for tc in tr.findall("a:tc", NS):
                tx_body = tc.find("a:txBody", NS)
                text = text_parser.parse(tx_body, [], None, page_number) or TextBody()
                tc_pr = tc.find("a:tcPr", NS)
                fill = theme.parse_paint(tc_pr)
                row.append(TableCell(
                    text=text,
                    fill=fill,
                    row_span=int(tc.get("rowSpan", "1")),
                    col_span=int(tc.get("gridSpan", "1")),
                ))
            rows.append(row)
        return Table(widths, heights, rows)

    def _parse_chart(self, chart_part: str) -> Chart:
        root = self.pkg.xml(chart_part)
        type_map = {
            "barChart": "bar", "lineChart": "line", "pieChart": "pie",
            "doughnutChart": "doughnut", "areaChart": "area", "scatterChart": "scatter",
        }
        chart_node = None
        chart_type = "unknown"
        for local, name in type_map.items():
            chart_node = root.find(f".//c:{local}", NS)
            if chart_node is not None:
                chart_type = name
                break
        series: list[ChartSeries] = []
        if chart_node is not None:
            for index, ser in enumerate(chart_node.findall("c:ser", NS), 1):
                name = self._chart_text(ser.find("c:tx", NS)) or f"Series {index}"
                categories = self._chart_cache(ser.find("c:cat", NS), strings=True)
                values_raw = self._chart_cache(ser.find("c:val", NS), strings=False)
                values: list[float] = []
                for value in values_raw:
                    try:
                        values.append(float(value))
                    except ValueError:
                        values.append(0.0)
                series.append(ChartSeries(name, categories, values))
        title = self._chart_text(root.find(".//c:title", NS))
        show_legend = root.find(".//c:legend", NS) is not None
        return Chart(chart_type, series, title, show_legend)

    @staticmethod
    def _chart_text(node: etree._Element | None) -> str:
        if node is None:
            return ""
        return "".join(node.xpath(".//a:t/text() | .//c:v/text()", namespaces=NS)).strip()

    @staticmethod
    def _chart_cache(node: etree._Element | None, strings: bool) -> list[str]:
        if node is None:
            return []
        paths = [".//c:strCache/c:pt", ".//c:numCache/c:pt", ".//c:strLit/c:pt", ".//c:numLit/c:pt"]
        points: list[etree._Element] = []
        for path in paths:
            points = node.xpath(path, namespaces=NS)
            if points:
                break
        points.sort(key=lambda p: int(p.get("idx", "0")))
        return [(p.findtext("c:v", default="", namespaces=NS)) for p in points]
