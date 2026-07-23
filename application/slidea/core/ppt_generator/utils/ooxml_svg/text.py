from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from lxml import etree
from PIL import ImageFont

from .model import Paragraph, Rect, RunStyle, TextBody, TextRun
from .namespaces import A, NS
from .theme import Theme
from .util import is_cjk


def _first_attr(nodes: list[etree._Element | None], attr: str, default: str | None = None) -> str | None:
    for node in nodes:
        if node is not None and node.get(attr) is not None:
            return node.get(attr)
    return default


def _bool(value: str | None) -> bool:
    return value in {"1", "true", "on"}


class TextParser:
    def __init__(self, theme: Theme, master_root: etree._Element | None = None):
        self.theme = theme
        self.master_root = master_root

    def _master_level(self, ph_type: str | None, level: int) -> etree._Element | None:
        if self.master_root is None:
            return None
        style_name = (
            "titleStyle"
            if ph_type in {"title", "ctrTitle", "subTitle"}
            else "bodyStyle" if ph_type in {"body", "obj"} else "otherStyle"
        )
        return self.master_root.find(f"p:txStyles/p:{style_name}/a:lvl{min(level + 1, 9)}pPr", NS)

    @staticmethod
    def _source_level(shape: etree._Element, level: int) -> etree._Element | None:
        tx_body = shape.find("p:txBody", NS)
        if tx_body is None:
            return None
        return tx_body.find(f"a:lstStyle/a:lvl{min(level + 1, 9)}pPr", NS)

    @staticmethod
    def _body_level(tx_body: etree._Element, level: int) -> etree._Element | None:
        """Return the direct text body's list style for one paragraph level."""
        return tx_body.find(f"a:lstStyle/a:lvl{min(level + 1, 9)}pPr", NS)

    @staticmethod
    def _source_paragraph(shape: etree._Element, level: int) -> etree._Element | None:
        tx_body = shape.find("p:txBody", NS)
        if tx_body is None:
            return None
        for paragraph in tx_body.findall("a:p", NS):
            ppr = paragraph.find("a:pPr", NS)
            if ppr is not None and int(ppr.get("lvl", "0")) == level:
                return ppr
        return None

    def _paragraph_style_nodes(
        self,
        direct_ppr: etree._Element | None,
        tx_body: etree._Element,
        inherited_shapes: list[etree._Element],
        ph_type: str | None,
        level: int,
    ) -> list[etree._Element | None]:
        # A sibling paragraph in the same text body is not an inheritance
        # source.  Only the paragraph's own pPr, its direct lstStyle and the
        # matched layout/master placeholder chain may contribute properties.
        # Treating the first same-level sibling as a style source made later
        # paragraphs accidentally inherit its alignment (notably slide 7 of
        # the regression fixture).
        nodes: list[etree._Element | None] = [direct_ppr, self._body_level(tx_body, level)]
        for shape in inherited_shapes:
            nodes.append(self._source_paragraph(shape, level))
            nodes.append(self._source_level(shape, level))
        nodes.append(self._master_level(ph_type, level))
        return nodes

    def _run_style(
        self,
        run_props: etree._Element | None,
        ppr_nodes: list[etree._Element | None],
        font_ref_color: str | None,
    ) -> RunStyle:
        candidates: list[etree._Element | None] = [run_props]
        candidates.extend(p.find("a:defRPr", NS) if p is not None else None for p in ppr_nodes)
        size = float(_first_attr(candidates, "sz", "1800") or 1800) / 100
        bold = _bool(_first_attr(candidates, "b", "0"))
        italic = _bool(_first_attr(candidates, "i", "0"))
        underline = (_first_attr(candidates, "u", "none") or "none") != "none"
        strike = (_first_attr(candidates, "strike", "noStrike") or "noStrike") != "noStrike"
        spacing = float(_first_attr(candidates, "spc", "0") or 0) / 1000
        baseline = float(_first_attr(candidates, "baseline", "0") or 0) / 100000
        latin = ""
        east_asian = ""
        for candidate in candidates:
            if candidate is None:
                continue
            if not latin:
                node = candidate.find("a:latin", NS)
                if node is not None:
                    latin = node.get("typeface", "")
            if not east_asian:
                node = candidate.find("a:ea", NS)
                if node is not None:
                    east_asian = node.get("typeface", "")
        family = self.theme.resolve_typeface(east_asian or latin, east_asian=bool(east_asian))
        color, opacity = font_ref_color or "#000000", 1.0
        for candidate in candidates:
            if candidate is None:
                continue
            paint = self.theme.parse_paint(candidate)
            if paint is not None:
                color, opacity = paint.color, paint.opacity
                break
        return RunStyle(
            font_family=family,
            font_size_pt=size,
            color=color,
            opacity=opacity,
            bold=bold,
            italic=italic,
            underline=underline,
            strike=strike,
            spacing_pt=spacing,
            baseline=baseline,
        )

    @staticmethod
    def _spacing_pt(ppr_nodes: list[etree._Element | None], child: str) -> float:
        for ppr in ppr_nodes:
            if ppr is None:
                continue
            node = ppr.find(f"a:{child}", NS)
            if node is None:
                continue
            pts = node.find("a:spcPts", NS)
            if pts is not None:
                return float(pts.get("val", "0")) / 100
        return 0.0

    @staticmethod
    def _line_spacing(ppr_nodes: list[etree._Element | None]) -> float | None:
        for ppr in ppr_nodes:
            if ppr is None:
                continue
            node = ppr.find("a:lnSpc", NS)
            if node is None:
                continue
            pct = node.find("a:spcPct", NS)
            if pct is not None:
                return float(pct.get("val", "100000")) / 100000
            pts = node.find("a:spcPts", NS)
            if pts is not None:
                return -(float(pts.get("val", "0")) / 100)
        return None

    @staticmethod
    def _bullet(ppr_nodes: list[etree._Element | None]) -> str | None:
        for ppr in ppr_nodes:
            if ppr is None:
                continue
            if ppr.find("a:buNone", NS) is not None:
                return None
            char = ppr.find("a:buChar", NS)
            if char is not None:
                return char.get("char", "•")
            if ppr.find("a:buAutoNum", NS) is not None:
                return "•"
        return None

    @staticmethod
    def _bullet_font(ppr_nodes: list[etree._Element | None]) -> str | None:
        """Return the bullet's own typeface instead of inheriting body text.

        DrawingML stores ``buFont`` beside ``buChar``. Ignoring it makes a
        source Arial bullet inherit the first body run's CJK font, whose U+2022
        glyph can have a very different advance and outline.
        """
        for ppr in ppr_nodes:
            if ppr is None:
                continue
            if ppr.find("a:buNone", NS) is not None:
                return None
            font = ppr.find("a:buFont", NS)
            if font is not None:
                return font.get("typeface") or None
            if ppr.find("a:buFontTx", NS) is not None:
                return None
        return None

    def parse(
        self,
        tx_body: etree._Element | None,
        inherited_shapes: list[etree._Element],
        ph_type: str | None,
        page_number: int,
        font_ref_color: str | None = None,
    ) -> TextBody | None:
        if tx_body is None:
            return None
        body_pr = tx_body.find("a:bodyPr", NS)
        body = TextBody()
        body_pr_nodes = [body_pr]
        body_pr_nodes.extend(shape.find("p:txBody/a:bodyPr", NS) for shape in inherited_shapes)

        def body_attr(name: str, default: str) -> str:
            return _first_attr(body_pr_nodes, name, default) or default

        body.inset_left_emu = float(body_attr("lIns", "91440"))
        body.inset_right_emu = float(body_attr("rIns", "91440"))
        body.inset_top_emu = float(body_attr("tIns", "45720"))
        body.inset_bottom_emu = float(body_attr("bIns", "45720"))
        body.anchor = {"ctr": "middle", "b": "bottom", "t": "top", "just": "top", "dist": "top"}.get(
            body_attr("anchor", "t"), "top"
        )
        body.wrap = body_attr("wrap", "square") != "none"
        for candidate in body_pr_nodes:
            if candidate is None:
                continue
            norm = candidate.find("a:normAutofit", NS)
            if norm is not None:
                body.font_scale = float(norm.get("fontScale", "100000")) / 100000
                body.line_space_reduction = float(norm.get("lnSpcReduction", "0")) / 100000
                break
        for p in tx_body.findall("a:p", NS):
            direct_ppr = p.find("a:pPr", NS)
            level = int(direct_ppr.get("lvl", "0")) if direct_ppr is not None else 0
            ppr_nodes = self._paragraph_style_nodes(direct_ppr, tx_body, inherited_shapes, ph_type, level)
            align = _first_attr(ppr_nodes, "algn", "l") or "l"
            align = {"l": "left", "ctr": "center", "r": "right", "just": "justify", "dist": "justify"}.get(
                align, "left"
            )
            bullet_font = self._bullet_font(ppr_nodes)
            paragraph = Paragraph(
                align=align,
                level=level,
                margin_left_emu=float(_first_attr(ppr_nodes, "marL", "0") or 0),
                indent_emu=float(_first_attr(ppr_nodes, "indent", "0") or 0),
                space_before_pt=self._spacing_pt(ppr_nodes, "spcBef"),
                space_after_pt=self._spacing_pt(ppr_nodes, "spcAft"),
                line_spacing=self._line_spacing(ppr_nodes),
                bullet=self._bullet(ppr_nodes),
                bullet_font_family=self.theme.resolve_typeface(bullet_font) if bullet_font else None,
            )
            for child in p:
                local = etree.QName(child).localname
                if local in {"r", "fld"}:
                    props = child.find("a:rPr", NS)
                    if props is None:
                        props = child.find("a:endParaRPr", NS)
                    style = self._run_style(props, ppr_nodes, font_ref_color)
                    text_node = child.find("a:t", NS)
                    text = text_node.text if text_node is not None and text_node.text is not None else ""
                    if local == "fld" and child.get("type", "").lower() in {"slidenum", "slide number"}:
                        text = str(page_number)
                    paragraph.runs.append(TextRun(text, style))
                elif local == "br":
                    paragraph.runs.append(
                        TextRun("", self._run_style(child.find("a:rPr", NS), ppr_nodes, font_ref_color), is_break=True)
                    )
            if not paragraph.runs:
                end_props = p.find("a:endParaRPr", NS)
                paragraph.runs.append(TextRun("", self._run_style(end_props, ppr_nodes, font_ref_color)))
            body.paragraphs.append(paragraph)
        if not any(run.text for p in body.paragraphs for run in p.runs):
            return None
        return body


@dataclass(frozen=True)
class ResolvedFont:
    requested_family: str
    family: str
    style: str
    path: str | None
    index: int = 0
    substituted: bool = False


class FontMetrics:
    def __init__(
        self,
        *,
        font_dirs: list[str | Path] | tuple[str | Path, ...] | None = None,
        fallback_font_regular: str | Path | None = None,
        fallback_font_bold: str | Path | None = None,
    ):
        self._font_cache: dict[tuple[str, int, bool, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._resolved_cache: dict[tuple[str, bool, bool], ResolvedFont] = {}
        configured = os.environ.get("OOXML_SVG_FONT_DIRS", "")
        directories = [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
        directories.extend(Path(p).expanduser() for p in (font_dirs or ()))
        windir = os.environ.get("WINDIR")
        if windir:
            directories.append(Path(windir) / "Fonts")
        if sys.platform == "darwin":
            directories.extend(
                [
                    Path("/System/Library/Fonts"),
                    Path("/Library/Fonts"),
                    Path.home() / "Library" / "Fonts",
                ]
            )
        # These are discovery locations, not fallback policy. A font found
        # here is only used when its internal family name exactly matches the
        # requested OOXML family. Non-native mounts (for example WSL's
        # /mnt/c/Windows/Fonts) are deliberately not scanned implicitly: a
        # downstream SVG renderer usually cannot see those faces through
        # fontconfig, which would recreate a measurement/render mismatch.
        self._font_dirs = tuple(dict.fromkeys(str(path) for path in directories))
        regular = fallback_font_regular or os.environ.get("OOXML_SVG_FALLBACK_FONT_REGULAR")
        bold = fallback_font_bold or os.environ.get("OOXML_SVG_FALLBACK_FONT_BOLD")
        self._fallback_regular = Path(regular).expanduser() if regular else None
        self._fallback_bold = Path(bold).expanduser() if bold else self._fallback_regular

    @staticmethod
    def _normalized_family(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.casefold())

    @staticmethod
    def _face(path: str, index: int = 0, size: int = 16) -> ImageFont.FreeTypeFont | None:
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            return None

    @classmethod
    def _font_entry(cls, path: str, index: int = 0) -> tuple[str, str, str, int] | None:
        face = cls._face(path, index)
        if face is None:
            return None
        family, style = face.getname()
        return family, style, path, index

    @staticmethod
    @lru_cache(maxsize=8)
    def _font_entries_for_dirs(
        directory_names: tuple[str, ...],
    ) -> tuple[tuple[str, str, str, int], ...]:
        """Index fonts not necessarily visible to fontconfig.

        The directory list is supplied by the caller so an integrating
        application can expose its bundled fonts without hard-coding an
        application path in this standalone package.
        """
        directories = [Path(name) for name in directory_names]
        entries: list[tuple[str, str, str, int]] = []
        seen_paths: set[Path] = set()
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.suffix.lower() not in {".ttf", ".otf", ".ttc", ".otc"}:
                    continue
                try:
                    resolved_path = path.resolve()
                except OSError:
                    continue
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                max_faces = 32 if path.suffix.lower() in {".ttc", ".otc"} else 1
                for index in range(max_faces):
                    entry = FontMetrics._font_entry(str(resolved_path), index)
                    if entry is None:
                        break
                    entries.append(entry)
        return tuple(entries)

    @classmethod
    def _style_score(cls, style_name: str, bold: bool, italic: bool) -> tuple[int, str]:
        style = style_name.casefold()
        actual_bold = any(token in style for token in ("bold", "demi", "semibold", "black"))
        actual_italic = any(token in style for token in ("italic", "oblique"))
        mismatch = int(actual_bold != bold) + int(actual_italic != italic)
        # When regular is requested, prefer the actual Regular/Book face over
        # Light, Narrow or Condensed variants that happen to sort earlier by
        # filename (for example ARIALN.TTF or msyhl.ttc on Windows).
        unwanted_variant = int(
            not bold and any(token in style for token in ("light", "thin", "narrow", "condensed", "black"))
        )
        regular_bonus = 0 if any(token in style for token in ("regular", "book", "normal")) else 1
        return (mismatch * 10 + unwanted_variant * 2 + regular_bonus, style)

    def _find_extra_font(self, family: str, bold: bool, italic: bool) -> tuple[str, str, str, int] | None:
        normalized = self._normalized_family(family)
        matches = [
            entry
            for entry in self._font_entries_for_dirs(self._font_dirs)
            if self._normalized_family(entry[0]) == normalized
        ]
        if not matches:
            return None
        return min(matches, key=lambda entry: self._style_score(entry[1], bold, italic))

    def _fallback_font(self, bold: bool) -> tuple[str, str, str, int] | None:
        path = self._fallback_bold if bold else self._fallback_regular
        if path is None or not path.is_file():
            return None
        return self._font_entry(str(path.resolve()))

    @classmethod
    def _fc_match(cls, family: str, bold: bool, italic: bool) -> tuple[str, str, str, int] | None:
        pattern = family
        if bold:
            pattern += ":style=Bold"
        if italic:
            pattern += ":slant=italic"
        executable = shutil.which("fc-match")
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "-f", "%{file}\n%{index}\n", pattern],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.splitlines()
            if not result or not Path(result[0]).is_file():
                return None
            try:
                index = int(result[1]) if len(result) > 1 else 0
            except ValueError:
                index = 0
            return cls._font_entry(result[0], index)
        except (OSError, subprocess.SubprocessError):
            return None

    def resolve(self, style: RunStyle) -> ResolvedFont:
        key = (style.font_family, style.bold, style.italic)
        cached = self._resolved_cache.get(key)
        if cached is not None:
            return cached

        requested = style.font_family or "Arial"
        requested_normalized = self._normalized_family(requested)
        match = self._fc_match(requested, style.bold, style.italic)
        if match is not None and self._normalized_family(match[0]) != requested_normalized:
            match = None
        if match is None:
            match = self._find_extra_font(requested, style.bold, style.italic)

        substituted = False
        if match is None:
            # The host application may provide a portable fallback face. This
            # is the key consistency contract: the same file is used for SVG
            # measurement and made available to the SVG renderer downstream.
            match = self._fallback_font(style.bold)
            substituted = True
        if match is None:
            # Standalone compatibility fallback. Integrations that require
            # deterministic output should always pass explicit fallback files.
            match = self._fc_match("sans-serif", style.bold, style.italic)
            substituted = True

        if match is None:
            resolved = ResolvedFont(requested, "sans-serif", "Regular", None, 0, True)
        else:
            family, style_name, path, index = match
            substituted = substituted or self._normalized_family(family) != requested_normalized
            resolved = ResolvedFont(requested, family, style_name, path, index, substituted)
        self._resolved_cache[key] = resolved
        return resolved

    def font(self, style: RunStyle, px_size: float) -> ImageFont.ImageFont:
        size = max(1, round(px_size))
        key = (style.font_family, size, style.bold, style.italic)
        if key not in self._font_cache:
            resolved = self.resolve(style)
            try:
                self._font_cache[key] = (
                    ImageFont.truetype(resolved.path, size, index=resolved.index)
                    if resolved.path
                    else ImageFont.load_default(size=size)
                )
            except OSError:
                self._font_cache[key] = ImageFont.load_default(size=size)
        return self._font_cache[key]

    def audit(self) -> list[dict[str, str | int | bool | None]]:
        audit_rows: list[dict[str, str | int | bool | None]] = []
        resolved_items = sorted(
            self._resolved_cache.items(),
            key=lambda pair: (pair[0][0], pair[0][1], pair[0][2], pair[1].family),
        )
        for key, item in resolved_items:
            audit_rows.append(
                {
                    "requested": item.requested_family,
                    "requested_bold": key[1],
                    "requested_italic": key[2],
                    "resolved": item.family,
                    "style": item.style,
                    "file": Path(item.path).name if item.path else None,
                    "face_index": item.index,
                    "substituted": item.substituted,
                }
            )
        return audit_rows

    def width(self, text: str, style: RunStyle, px_size: float) -> float:
        if not text:
            return 0.0
        font = self.font(style, px_size)
        try:
            base = float(font.getlength(text))
        except AttributeError:
            box = font.getbbox(text)
            base = float(box[2] - box[0])
        return base + max(0, len(text) - 1) * style.spacing_pt * 96 / 72

    def ascent_descent(self, style: RunStyle, px_size: float) -> tuple[float, float]:
        font = self.font(style, px_size)
        try:
            a, d = font.getmetrics()
            return float(a), float(d)
        except AttributeError:
            return px_size * 0.8, px_size * 0.2


@dataclass
class PositionedRun:
    text: str
    x: float
    baseline_y: float
    width: float
    style: RunStyle
    px_size: float
    ascent: float
    descent: float
    resolved_font_family: str
    font_substituted: bool = False
    is_bullet: bool = False


class TextLayouter:
    TOKEN_RE = re.compile(r"\s+|[A-Za-z0-9_./:+%#@&-]+|.", re.DOTALL)

    def __init__(self, metrics: FontMetrics | None = None):
        self.metrics = metrics or FontMetrics()

    def _tokens(self, runs: list[TextRun], scale: float) -> list[tuple[str, RunStyle, float, bool]]:
        out: list[tuple[str, RunStyle, float, bool]] = []
        for run in runs:
            px_size = run.style.font_size_pt * 96 / 72 * scale
            if run.is_break:
                out.append(("", run.style, px_size, True))
                continue
            for token in self.TOKEN_RE.findall(run.text):
                out.append((token, run.style, px_size, False))
        return out

    @staticmethod
    def _line_metrics(
        line: list[tuple[str, RunStyle, float, float]],
    ) -> tuple[float, float, float]:
        if not line:
            px_size = RunStyle().font_size_pt * 96 / 72
            return px_size * 0.9, px_size * 0.3, px_size * 1.2
        # FreeType's ascent/descent values differ substantially across fonts
        # (YaHei reports a much taller EM box than many Latin fonts). Office's
        # percentage line spacing is based on a single-line text unit, not the
        # raw font file EM box. A normalized 1.2em line box therefore produces
        # stable OOXML baselines across hosts while horizontal advances still
        # use the exact resolved font metrics.
        ascent = max(item[2] * 0.9 for item in line)
        descent = max(item[2] * 0.3 for item in line)
        return ascent, descent, max(item[2] * 1.2 for item in line)

    def _line_height(
        self,
        paragraph: Paragraph,
        line: list[tuple[str, RunStyle, float, float]],
        body: TextBody,
    ) -> float:
        _, _, natural = self._line_metrics(line)
        if paragraph.line_spacing is None:
            return natural * (1 - body.line_space_reduction)
        if paragraph.line_spacing >= 0:
            return natural * paragraph.line_spacing * (1 - body.line_space_reduction)
        return -paragraph.line_spacing * 96 / 72

    def layout(self, body: TextBody, rect: Rect, sx: float, sy: float) -> list[PositionedRun]:
        left = rect.x * sx + body.inset_left_emu * sx
        right = (rect.x + rect.width) * sx - body.inset_right_emu * sx
        top = rect.y * sy + body.inset_top_emu * sy
        bottom = (rect.y + rect.height) * sy - body.inset_bottom_emu * sy
        box_width = max(0.0, right - left)
        paragraph_lines: list[tuple[Paragraph, list[list[tuple[str, RunStyle, float, float]]]]] = []
        total_height = 0.0
        for paragraph in body.paragraphs:
            tokens = self._tokens(paragraph.runs, body.font_scale)
            bullet_style = next((t[1] for t in tokens if not t[3]), RunStyle())
            if paragraph.bullet_font_family:
                bullet_style = replace(bullet_style, font_family=paragraph.bullet_font_family)
            bullet_px = bullet_style.font_size_pt * 96 / 72 * body.font_scale
            available = max(
                0.0,
                box_width
                - paragraph.margin_left_emu * sx
                - (max(0.0, paragraph.indent_emu * sx) if not paragraph.bullet else 0.0),
            )
            lines: list[list[tuple[str, RunStyle, float, float]]] = [[]]
            used = 0.0
            for token, style, px_size, forced in tokens:
                if forced:
                    lines.append([])
                    used = 0.0
                    continue
                width = self.metrics.width(token, style, px_size)
                should_wrap = body.wrap and not token.isspace()
                if should_wrap and used > 0 and used + width > available:
                    lines.append([])
                    used = 0.0
                if should_wrap and width > available and len(token) > 1:
                    for ch in token:
                        ch_w = self.metrics.width(ch, style, px_size)
                        if used > 0 and used + ch_w > available:
                            lines.append([])
                            used = 0.0
                        lines[-1].append((ch, style, px_size, ch_w))
                        used += ch_w
                else:
                    if used == 0 and token.isspace():
                        continue
                    lines[-1].append((token, style, px_size, width))
                    used += width
            if not lines:
                lines = [[]]
            paragraph_lines.append((paragraph, lines))
            total_height += paragraph.space_before_pt * 96 / 72
            for line in lines:
                total_height += self._line_height(paragraph, line, body)
            total_height += paragraph.space_after_pt * 96 / 72
        if body.anchor == "middle":
            cursor_y = top + max(0.0, (bottom - top - total_height) / 2)
        elif body.anchor == "bottom":
            cursor_y = max(top, bottom - total_height)
        else:
            cursor_y = top
        positioned: list[PositionedRun] = []
        for paragraph, lines in paragraph_lines:
            cursor_y += paragraph.space_before_pt * 96 / 72
            for line_index, line in enumerate(lines):
                line_width = sum(item[3] for item in line)
                base_left = left + paragraph.margin_left_emu * sx
                if paragraph.align == "center":
                    cursor_x = base_left + max(0.0, (box_width - paragraph.margin_left_emu * sx - line_width) / 2)
                elif paragraph.align == "right":
                    cursor_x = right - line_width
                else:
                    cursor_x = base_left
                    if line_index == 0 and not paragraph.bullet:
                        cursor_x += paragraph.indent_emu * sx
                max_ascent, _, _ = self._line_metrics(line)
                line_height = self._line_height(paragraph, line, body)
                baseline = cursor_y + max_ascent
                if paragraph.bullet and line_index == 0:
                    bullet_width = self.metrics.width(paragraph.bullet, bullet_style, bullet_px)
                    bullet_font = self.metrics.resolve(bullet_style)
                    bullet_ascent, bullet_descent = self.metrics.ascent_descent(bullet_style, bullet_px)
                    positioned.append(
                        PositionedRun(
                            text=paragraph.bullet,
                            x=base_left + paragraph.indent_emu * sx,
                            baseline_y=baseline - bullet_px * bullet_style.baseline,
                            width=bullet_width,
                            style=bullet_style,
                            px_size=bullet_px,
                            ascent=bullet_ascent,
                            descent=bullet_descent,
                            resolved_font_family=bullet_font.family,
                            font_substituted=bullet_font.substituted,
                            is_bullet=True,
                        )
                    )
                merged: list[tuple[str, RunStyle, float, float]] = []
                for text, style, px_size, width in line:
                    if merged and merged[-1][1] == style and abs(merged[-1][2] - px_size) < 0.01:
                        old = merged[-1]
                        merged[-1] = (old[0] + text, style, px_size, old[3] + width)
                    else:
                        merged.append((text, style, px_size, width))
                for text, style, px_size, width in merged:
                    resolved_font = self.metrics.resolve(style)
                    ascent, descent = self.metrics.ascent_descent(style, px_size)
                    positioned.append(
                        PositionedRun(
                            text=text,
                            x=cursor_x,
                            baseline_y=baseline - px_size * style.baseline,
                            width=width,
                            style=style,
                            px_size=px_size,
                            ascent=ascent,
                            descent=descent,
                            resolved_font_family=resolved_font.family,
                            font_substituted=resolved_font.substituted,
                        )
                    )
                    cursor_x += width
                cursor_y += line_height
            cursor_y += paragraph.space_after_pt * 96 / 72
        return positioned
