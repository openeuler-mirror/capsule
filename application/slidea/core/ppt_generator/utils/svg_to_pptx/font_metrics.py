"""Portable text metrics for the SVG -> DrawingML boundary.

The SVG generator and Cairo preview use Slidea's bundled Noto Sans CJK SC
faces when a source font is unavailable.  This module measures the same font
files, so the editable PPTX converter does not fall back to character-class
width heuristics that vary from the rendered SVG.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


APP_ROOT = Path(__file__).resolve().parents[4]
BUNDLED_FONTS_DIR = APP_ROOT / "assets" / "fonts"
REGULAR_FONT = BUNDLED_FONTS_DIR / "NotoSansSC-Regular.otf"
BOLD_FONT = BUNDLED_FONTS_DIR / "NotoSansSC-Bold.otf"


def _is_bold(weight: str) -> bool:
    return str(weight).strip().lower() in {"bold", "600", "700", "800", "900"}


@lru_cache(maxsize=256)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = BOLD_FONT if bold and BOLD_FONT.is_file() else REGULAR_FONT
    if path.is_file():
        return ImageFont.truetype(str(path), max(1, size))
    return ImageFont.load_default(size=max(1, size))


def measure_text(
    text: str,
    font_size_px: float,
    font_weight: str = "400",
    letter_spacing_px: float = 0.0,
) -> float:
    """Measure one SVG run with the bundled rendering face."""
    if not text:
        return 0.0
    face = _font(max(1, round(font_size_px)), _is_bold(font_weight))
    try:
        width = float(face.getlength(text))
    except AttributeError:  # pragma: no cover - old Pillow compatibility
        box = face.getbbox(text)
        width = float(box[2] - box[0])
    return width + max(0, len(text) - 1) * letter_spacing_px


def measure_runs(runs: list[dict]) -> float:
    return sum(
        measure_text(
            str(run.get("text", "")),
            float(run.get("font_size", 16)),
            str(run.get("font_weight", "400")),
            float(run.get("letter_spacing", 0) or 0),
        )
        for run in runs
    )


def ascent_descent(font_size_px: float, font_weight: str = "400") -> tuple[float, float]:
    face = _font(max(1, round(font_size_px)), _is_bold(font_weight))
    try:
        ascent, descent = face.getmetrics()
        return float(ascent), float(descent)
    except AttributeError:  # pragma: no cover - bitmap fallback
        return font_size_px * 0.9, font_size_px * 0.3

