from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .model import LineStyle, Paint, Style
from .namespaces import A, NS
from .util import clamp, color_hex, hsl_to_srgb, srgb_to_hsl


PRESET_COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#008000",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "gray": "#808080",
    "grey": "#808080",
    "orange": "#FFA500",
    "purple": "#800080",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
}


DEFAULT_SCHEME = {
    "dk1": "#000000",
    "lt1": "#FFFFFF",
    "dk2": "#1F497D",
    "lt2": "#EEECE1",
    "accent1": "#4F81BD",
    "accent2": "#C0504D",
    "accent3": "#9BBB59",
    "accent4": "#8064A2",
    "accent5": "#4BACC6",
    "accent6": "#F79646",
    "hlink": "#0000FF",
    "folHlink": "#800080",
    "tx1": "#000000",
    "tx2": "#1F497D",
    "bg1": "#FFFFFF",
    "bg2": "#EEECE1",
}


@dataclass
class Theme:
    root: etree._Element | None
    color_map: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self.scheme = dict(DEFAULT_SCHEME)
        self.major_latin = "Arial"
        self.major_ea = ""
        self.minor_latin = "Arial"
        self.minor_ea = ""
        self.fill_styles: list[etree._Element] = []
        self.bg_fill_styles: list[etree._Element] = []
        self.line_styles: list[etree._Element] = []
        if self.root is None:
            return
        clr_scheme = self.root.find(".//a:themeElements/a:clrScheme", NS)
        base_scheme: dict[str, str] = {}
        if clr_scheme is not None:
            for slot in clr_scheme:
                name = etree.QName(slot).localname
                child = next(iter(slot), None)
                if child is not None:
                    base_scheme[name] = self.resolve_color(child)[0]
        self.scheme.update(base_scheme)
        mapping = {
            "bg1": "lt1",
            "tx1": "dk1",
            "bg2": "lt2",
            "tx2": "dk2",
            "accent1": "accent1",
            "accent2": "accent2",
            "accent3": "accent3",
            "accent4": "accent4",
            "accent5": "accent5",
            "accent6": "accent6",
            "hlink": "hlink",
            "folHlink": "folHlink",
        }
        if self.color_map:
            mapping.update(self.color_map)
        for alias, target in mapping.items():
            if target in base_scheme:
                self.scheme[alias] = base_scheme[target]
        font_scheme = self.root.find(".//a:themeElements/a:fontScheme", NS)
        if font_scheme is not None:
            self.major_latin = self._font(font_scheme, "majorFont", "latin") or self.major_latin
            self.major_ea = self._font(font_scheme, "majorFont", "ea")
            self.minor_latin = self._font(font_scheme, "minorFont", "latin") or self.minor_latin
            self.minor_ea = self._font(font_scheme, "minorFont", "ea")
        self.fill_styles = list(self.root.findall(".//a:themeElements/a:fmtScheme/a:fillStyleLst/*", NS))
        self.bg_fill_styles = list(self.root.findall(".//a:themeElements/a:fmtScheme/a:bgFillStyleLst/*", NS))
        self.line_styles = list(self.root.findall(".//a:themeElements/a:fmtScheme/a:lnStyleLst/a:ln", NS))

    @staticmethod
    def _font(root: etree._Element, group: str, kind: str) -> str:
        node = root.find(f"a:{group}/a:{kind}", NS)
        return node.get("typeface", "") if node is not None else ""

    def resolve_typeface(self, value: str | None, east_asian: bool = False) -> str:
        value = value or ""
        mapping = {
            "+mj-lt": self.major_latin,
            "+mj-ea": self.major_ea or self.major_latin,
            "+mn-lt": self.minor_latin,
            "+mn-ea": self.minor_ea or self.minor_latin,
        }
        if value in mapping:
            return mapping[value]
        if value:
            return value
        return (self.minor_ea if east_asian else self.minor_latin) or "Arial"

    def resolve_color(self, node: etree._Element | None, ph_color: str | None = None) -> tuple[str, float]:
        if node is None:
            return "#000000", 1.0
        kind = etree.QName(node).localname
        if kind == "srgbClr":
            color = color_hex(node.get("val", ""))
        elif kind == "schemeClr":
            key = node.get("val", "dk1")
            color = ph_color if key == "phClr" and ph_color else self.scheme.get(key, "#000000")
        elif kind == "sysClr":
            color = color_hex(node.get("lastClr", "000000"))
        elif kind == "prstClr":
            color = PRESET_COLORS.get(node.get("val", "black"), "#000000")
        elif kind == "scrgbClr":
            vals = [clamp(float(node.get(k, "0")) / 100000) for k in ("r", "g", "b")]
            color = f"#{round(vals[0]*255):02X}{round(vals[1]*255):02X}{round(vals[2]*255):02X}"
        else:
            child = next(iter(node), None)
            return self.resolve_color(child, ph_color)
        opacity = 1.0
        hue, saturation, lightness = srgb_to_hsl(color)
        for transform in node:
            name = etree.QName(transform).localname
            try:
                value = float(transform.get("val", "100000")) / 100000
            except ValueError:
                continue
            if name == "alpha":
                opacity *= value
            elif name == "alphaMod":
                opacity *= value
            elif name == "alphaOff":
                opacity = clamp(opacity + value)
            elif name == "tint":
                lightness = lightness + (1 - lightness) * value
            elif name == "shade":
                lightness *= value
            elif name == "lumMod":
                lightness *= value
            elif name == "lumOff":
                lightness += value
            elif name == "satMod":
                saturation *= value
            elif name == "satOff":
                saturation += value
        return hsl_to_srgb(hue, clamp(saturation), clamp(lightness)), clamp(opacity)

    def parse_paint(self, container: etree._Element | None, ph_color: str | None = None) -> Paint | None:
        if container is None:
            return None
        no_fill = container.find("a:noFill", NS)
        if no_fill is not None or etree.QName(container).localname == "noFill":
            return None
        solid = container if etree.QName(container).localname == "solidFill" else container.find("a:solidFill", NS)
        if solid is not None:
            color_node = next(iter(solid), None)
            color, opacity = self.resolve_color(color_node, ph_color)
            return Paint(color=color, opacity=opacity)
        grad = container if etree.QName(container).localname == "gradFill" else container.find("a:gradFill", NS)
        if grad is not None:
            stops: list[tuple[float, str, float]] = []
            for gs in grad.findall("a:gsLst/a:gs", NS):
                color, opacity = self.resolve_color(next(iter(gs), None), ph_color)
                stops.append((float(gs.get("pos", "0")) / 100000, color, opacity))
            lin = grad.find("a:lin", NS)
            angle = float(lin.get("ang", "0")) / 60000 if lin is not None else 0.0
            if stops:
                return Paint(color=stops[0][1], opacity=stops[0][2], kind="gradient", stops=stops, angle=angle)
        return None

    def _style_paint(self, ref: etree._Element | None, background: bool = False) -> Paint | None:
        if ref is None:
            return None
        color_node = next(iter(ref), None)
        ph_color = self.resolve_color(color_node)[0] if color_node is not None else None
        try:
            idx = int(ref.get("idx", "0"))
        except ValueError:
            return None
        styles = self.bg_fill_styles if background or idx >= 1000 else self.fill_styles
        if idx >= 1000:
            idx -= 1000
        if 1 <= idx <= len(styles):
            return self.parse_paint(styles[idx - 1], ph_color)
        return None

    def shape_style(self, sources: list[etree._Element]) -> Style:
        fill: Paint | None = None
        line: LineStyle | None = None
        direct_line: etree._Element | None = None
        direct_line_no_fill = False
        for source in sources:
            sp_pr = source.find("p:spPr", NS)
            if sp_pr is None:
                sp_pr = source.find("p:grpSpPr", NS)
            if fill is None and sp_pr is not None:
                if sp_pr.find("a:noFill", NS) is not None:
                    fill = Paint(color="#FFFFFF", opacity=0.0, kind="none")
                else:
                    fill = self.parse_paint(sp_pr)
            if direct_line is None and sp_pr is not None:
                ln = sp_pr.find("a:ln", NS)
                if ln is not None:
                    direct_line = ln
                    if ln.find("a:noFill", NS) is not None:
                        direct_line_no_fill = True
                        line = LineStyle(paint=None, width_emu=float(ln.get("w", "12700")))
                    else:
                        lp = self.parse_paint(ln)
                        if lp is not None:
                            line = LineStyle(paint=lp, width_emu=float(ln.get("w", "12700")))
        style_node = sources[0].find("p:style", NS) if sources else None
        if style_node is not None:
            if fill is None:
                fill = self._style_paint(style_node.find("a:fillRef", NS))
            if line is None:
                ref = style_node.find("a:lnRef", NS)
                if ref is not None:
                    color_node = next(iter(ref), None)
                    ph_color = self.resolve_color(color_node)[0] if color_node is not None else None
                    try:
                        idx = int(ref.get("idx", "0"))
                    except ValueError:
                        idx = 0
                    if 1 <= idx <= len(self.line_styles):
                        ln = self.line_styles[idx - 1]
                        line = LineStyle(
                            paint=self.parse_paint(ln, ph_color),
                            width_emu=float(ln.get("w", "12700")),
                        )
        if direct_line is not None:
            if line is None and not direct_line_no_fill:
                line = LineStyle(paint=Paint("#000000"))
            if line is not None:
                line.width_emu = float(direct_line.get("w", str(line.width_emu)))
                dash_node = direct_line.find("a:prstDash", NS)
                if dash_node is not None:
                    line.dash = dash_node.get("val")
                head = direct_line.find("a:headEnd", NS)
                tail = direct_line.find("a:tailEnd", NS)
                if head is not None:
                    line.head = head.get("type")
                if tail is not None:
                    line.tail = tail.get("type")
        if fill is not None and fill.kind == "none":
            fill = None
        return Style(fill=fill, line=line)

    def background_paint(self, sources: list[etree._Element]) -> Paint:
        for root in sources:
            bg = root.find("p:cSld/p:bg", NS)
            if bg is None:
                continue
            bg_pr = bg.find("p:bgPr", NS)
            paint = self.parse_paint(bg_pr)
            if paint is not None:
                return paint
            ref = bg.find("p:bgRef", NS)
            paint = self._style_paint(ref, background=True)
            if paint is not None:
                return paint
        return Paint("#FFFFFF")
