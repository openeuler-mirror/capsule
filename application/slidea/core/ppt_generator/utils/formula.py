"""LaTeX formula rendering via matplotlib mathtext.

Renders display formulas to transparent PNGs for embedding in SVG slides.
Offline; no network or LaTeX system installation required.

Configuration is via module-level constants (no env vars). Edit this file to tune.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime

from PIL import Image

from core.utils.logger import logger


# Module-level configuration. Edit these constants to tune behavior.
FORMULA_RENDER_ENABLED = True
FORMULA_RENDER_COLOR = "#000000"
FORMULA_RENDER_DPI = 300
FORMULA_RENDER_FONT_SIZE = 14  # matplotlib points; final pixel size scales with DPI
MAX_UPSCALE = 1.3  # display size relative to natural size; DPI=300 keeps ~231 DPI effective at 1.3x

# SVG user units are treated as 96-DPI equivalents on the 1280x720 slide canvas
# (1280 units ≈ 13.33 inches). PNGs rendered at FORMULA_RENDER_DPI must be
# scaled down to 96-DPI equivalents before being used as SVG width/height,
# otherwise the displayed formula is DPI/96 times too large.
_SVG_USER_UNITS_PER_INCH = 96

# Formula PNGs land directly under <save_dir>/images/ alongside other images,
# so existing "images/<filename>" references in SVG/PPTX pipelines work
# unchanged. The sha1 filename makes collisions essentially impossible.


def is_cjk_contaminated(latex: str) -> bool:
    """Return True if the LaTeX source contains CJK characters that mathtext cannot render."""
    if not latex:
        return False
    for ch in latex:
        code = ord(ch)
        if (
            0x3040 <= code <= 0x30FF  # Hiragana, Katakana
            or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
            or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
            or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
            or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
            or 0xFF00 <= code <= 0xFFEF  # Halfwidth/Fullwidth (covers fullwidth Latin)
        ):
            return True
    return False


def measure_png(path: str) -> tuple[int, int]:
    """Return (width, height) in raw PNG pixels. Returns (0, 0) on failure.

    Callers that embed the PNG in SVG should use svg_size_for_pixels() to
    convert these into SVG user units first.
    """
    try:
        with Image.open(path) as img:
            return img.width, img.height
    except (FileNotFoundError, OSError, ValueError) as e:
        logger.warning(f"measure_png failed for {path}: {e}")
        return 0, 0


def svg_size_for_pixels(pixel_w: int, pixel_h: int, dpi: int) -> tuple[int, int]:
    """Convert raw PNG pixel dimensions into SVG user units (96-DPI equivalent).

    The slide canvas is 1280x720 SVG user units mapped to 13.33"x7.5", so
    1 SVG unit ≈ 1/96 inch. A PNG rendered at `dpi` DPI representing a
    physical size of (pixel_w/dpi) × (pixel_h/dpi) inches therefore occupies
    (pixel_w * 96 / dpi) × (pixel_h * 96 / dpi) SVG units when displayed at
    its natural physical size.
    """
    if dpi <= 0:
        dpi = FORMULA_RENDER_DPI
    svg_w = round(pixel_w * _SVG_USER_UNITS_PER_INCH / dpi)
    svg_h = round(pixel_h * _SVG_USER_UNITS_PER_INCH / dpi)
    return svg_w, svg_h


def _cache_key(latex: str, color: str, dpi: int, display: bool) -> str:
    raw = f"{latex}|{color}|{dpi}|{display}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_dir(save_dir: str) -> str:
    """Return the images directory formula PNGs live in (same as other images)."""
    return os.path.join(save_dir, "images")


def _normalize_latex(latex: str) -> str:
    """Strip surrounding $...$ or $$...$$ if the LLM added them; we re-wrap internally."""
    s = latex.strip()
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        return s[2:-2].strip()
    if s.startswith("$") and s.endswith("$") and len(s) >= 2:
        return s[1:-1].strip()
    return s


def _render_to_png_sync(
    latex: str,
    out_path: str,
    *,
    color: str,
    dpi: int,
    font_size: int,
) -> bool:
    """Synchronous matplotlib rendering. Returns True on success.

    Uses Figure + FigureCanvasAgg directly (no pyplot) so it is safe to call
    from worker threads.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = Figure()
    FigureCanvasAgg(fig)
    # Anchor text at center; bbox_inches='tight' crops to actual extent on save.
    fig.text(
        0.5, 0.5, f"${latex}$",
        fontsize=font_size, color=color,
        ha='center', va='center',
    )
    try:
        fig.savefig(
            out_path,
            dpi=dpi,
            transparent=True,
            bbox_inches='tight',
            pad_inches=0,
        )
        return True
    except Exception as e:
        logger.warning(f"matplotlib savefig failed for latex={latex!r}: {e}")
        return False
    finally:
        fig.clear()


async def render_formula(
    latex: str,
    save_dir: str,
    *,
    color: str | None = None,
    dpi: int | None = None,
    display: bool = True,
    font_size: int = FORMULA_RENDER_FONT_SIZE,
) -> tuple[str | None, tuple[int, int] | None]:
    """Render a single LaTeX formula to a transparent PNG.

    Returns (absolute_path, (svg_width, svg_height)) on success, (None, None) on
    failure. The returned dimensions are **SVG user units** (already scaled from
    raw PNG pixels by 96/dpi), suitable for direct use as the `<image width=...
    height=...>` values in a 1280x720 SVG canvas.

    Failures include: feature disabled, empty source, CJK-contaminated source,
    invalid LaTeX syntax, matplotlib errors.

    Cached by sha1(latex|color|dpi|display) under <save_dir>/images/.
    """
    if not FORMULA_RENDER_ENABLED:
        return None, None

    latex = _normalize_latex(latex or "")
    if not latex:
        return None, None
    if is_cjk_contaminated(latex):
        logger.info(f"formula skipped (CJK in source): {latex[:60]}")
        return None, None

    color = color or FORMULA_RENDER_COLOR
    dpi = dpi or FORMULA_RENDER_DPI

    key = _cache_key(latex, color, dpi, display)
    cache_dir = _cache_dir(save_dir)
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, f"{key}.png")

    if os.path.exists(out_path):
        pw, ph = measure_png(out_path)
        if pw > 0 and ph > 0:
            return out_path, svg_size_for_pixels(pw, ph, dpi)
        # Corrupt cache entry; fall through and re-render.
        try:
            os.remove(out_path)
        except OSError:
            pass

    try:
        ok = await asyncio.to_thread(
            _render_to_png_sync, latex, out_path,
            color=color, dpi=dpi, font_size=font_size,
        )
    except ValueError as e:
        # mathtext raises ValueError for unsupported syntax.
        logger.info(f"formula mathtext parse failed for {latex!r}: {e}")
        return None, None
    except Exception as e:
        logger.warning(f"formula render unexpected error for {latex!r}: {e}")
        return None, None

    if not ok:
        return None, None

    pw, ph = measure_png(out_path)
    if pw == 0 or ph == 0:
        try:
            os.remove(out_path)
        except OSError:
            pass
        return None, None

    return out_path, svg_size_for_pixels(pw, ph, dpi)


def append_formula_record_sync(run_dir: str, record: dict) -> None:
    """Best-effort append a formula record to <run_dir>/formulas.json.

    Keeps a running ledger of every formula rendered in this run, so Phase 3
    edits can look up LaTeX source by image path. Failures are logged and
    swallowed — a missing ledger entry doesn't break the pipeline. Sync; no
    locking. Callers that may write concurrently should wrap this in a lock.
    """
    if not run_dir:
        return
    log_path = os.path.join(run_dir, "formulas.json")
    try:
        existing: list = []
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                try:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        existing = loaded
                except (json.JSONDecodeError, ValueError):
                    existing = []
        entry = {**record, "rendered_at": datetime.now().isoformat()}
        existing.append(entry)
        tmp_path = log_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, log_path)
    except Exception as e:
        logger.warning(f"failed to update formulas.json at {log_path}: {e}")


__all__ = [
    "FORMULA_RENDER_ENABLED",
    "FORMULA_RENDER_COLOR",
    "FORMULA_RENDER_DPI",
    "FORMULA_RENDER_FONT_SIZE",
    "MAX_UPSCALE",
    "is_cjk_contaminated",
    "measure_png",
    "svg_size_for_pixels",
    "render_formula",
    "append_formula_record_sync",
]
