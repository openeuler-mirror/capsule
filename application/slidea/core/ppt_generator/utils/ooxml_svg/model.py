from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True)
class Matrix:
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def __matmul__(self, other: "Matrix") -> "Matrix":
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.e + self.c * other.f + self.e,
            self.b * other.e + self.d * other.f + self.f,
        )


@dataclass
class Paint:
    color: str = "#FFFFFF"
    opacity: float = 1.0
    kind: str = "solid"
    stops: list[tuple[float, str, float]] = field(default_factory=list)
    angle: float = 0.0


@dataclass
class LineStyle:
    paint: Paint | None = None
    width_emu: float = 12700.0
    dash: str | None = None
    head: str | None = None
    tail: str | None = None


@dataclass
class Style:
    fill: Paint | None = None
    line: LineStyle | None = None


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float
    rotation: float = 0.0
    flip_h: bool = False
    flip_v: bool = False


@dataclass
class RunStyle:
    font_family: str = "Arial"
    font_size_pt: float = 18.0
    color: str = "#000000"
    opacity: float = 1.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    spacing_pt: float = 0.0
    baseline: float = 0.0


@dataclass
class TextRun:
    text: str
    style: RunStyle
    is_break: bool = False


@dataclass
class Paragraph:
    runs: list[TextRun] = field(default_factory=list)
    align: str = "left"
    level: int = 0
    margin_left_emu: float = 0.0
    indent_emu: float = 0.0
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    line_spacing: float | None = None
    bullet: str | None = None


@dataclass
class TextBody:
    paragraphs: list[Paragraph] = field(default_factory=list)
    inset_left_emu: float = 91440.0
    inset_right_emu: float = 91440.0
    inset_top_emu: float = 45720.0
    inset_bottom_emu: float = 45720.0
    anchor: str = "top"
    wrap: bool = True
    font_scale: float = 1.0
    line_space_reduction: float = 0.0


@dataclass
class Element:
    element_id: str
    name: str
    kind: str
    rect: Rect
    style: Style = field(default_factory=Style)
    text: TextBody | None = None
    preset: str = "rect"
    adjustments: dict[str, float] = field(default_factory=dict)
    source_part: str = ""
    source_id: str = ""
    role: str = "content"
    parent_matrix: Matrix = field(default_factory=Matrix)
    children: list["Element"] = field(default_factory=list)
    image_part: str | None = None
    image_href: str | None = None
    crop: tuple[float, float, float, float] | None = None
    table: Any = None
    chart: Any = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TableCell:
    text: TextBody
    fill: Paint | None = None
    row_span: int = 1
    col_span: int = 1


@dataclass
class Table:
    column_widths_emu: list[float]
    row_heights_emu: list[float]
    rows: list[list[TableCell]]


@dataclass
class ChartSeries:
    name: str
    categories: list[str]
    values: list[float]
    color: str | None = None


@dataclass
class Chart:
    chart_type: str
    series: list[ChartSeries]
    title: str = ""
    show_legend: bool = True


@dataclass
class Page:
    number: int
    source_part: str
    width_emu: float
    height_emu: float
    background: Paint = field(default_factory=Paint)
    master_elements: list[Element] = field(default_factory=list)
    layout_elements: list[Element] = field(default_factory=list)
    slide_elements: list[Element] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_elements(self) -> list[Element]:
        return self.master_elements + self.layout_elements + self.slide_elements


@dataclass
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    external: bool = False


@dataclass
class ConversionStats:
    slides: int = 0
    shapes: int = 0
    pictures: int = 0
    groups: int = 0
    connectors: int = 0
    tables: int = 0
    charts: int = 0
    text_runs: int = 0
    unsupported: int = 0
    warnings: list[dict[str, Any]] = field(default_factory=list)
