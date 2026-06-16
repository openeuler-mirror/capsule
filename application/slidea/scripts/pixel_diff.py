"""Pixel-level diff between A/B/C font_check renders.

Expected outcome if the bundled-fonts fallback works:
  - mean_abs_diff(A, B) is large (tofu vs real glyphs differ a lot)
  - mean_abs_diff(A, C) is much smaller than A-B (both render real glyphs)
  - mean_abs_diff(B, C) is large (tofu vs real glyphs)
"""
import logging
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "output" / "font_check"


def load_gray(name):
    from PIL import Image
    import numpy as np

    img = Image.open(OUT / name).convert("L")
    return np.asarray(img, dtype=np.int16)


def mean_abs_diff(x, y):
    import numpy as np

    return float(np.abs(x - y).mean())


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger(__name__)

    try:
        import PIL  # noqa: F401
    except ImportError:
        log.error("Pillow not in this venv. Trying via numpy + cairosvg internals...")
        sys.exit(2)

    baseline = load_gray("A_baseline_system.png")
    no_fonts = load_gray("B_no_fonts_tofu.png")
    bundled = load_gray("C_bundled_fonts.png")

    log.info(f"shapes: A={baseline.shape} B={no_fonts.shape} C={bundled.shape}")
    log.info(
        f"mean_abs_diff(A, B) = {mean_abs_diff(baseline, no_fonts):.2f}   "
        "# baseline vs no-fonts (tofu expected)"
    )
    log.info(
        f"mean_abs_diff(A, C) = {mean_abs_diff(baseline, bundled):.2f}   "
        "# baseline vs bundled (should be small)"
    )
    log.info(
        f"mean_abs_diff(B, C) = {mean_abs_diff(no_fonts, bundled):.2f}   "
        "# no-fonts vs bundled (tofu expected)"
    )

    ab = mean_abs_diff(baseline, no_fonts)
    ac = mean_abs_diff(baseline, bundled)
    bc = mean_abs_diff(no_fonts, bundled)
    log.info("")
    if ac < ab and ac < bc:
        log.info("PASS: C matches A more than B does. Bundled-font fallback works.")
        if ac < 5.0:
            log.info(
                f"  (A vs C diff {ac:.2f} is small — glyph shapes are essentially identical)"
            )
        else:
            log.info(
                f"  (A vs C diff {ac:.2f} — both render real glyphs but "
                "metrics/metrics differ slightly, expected)"
            )
    else:
        log.info("FAIL: C does not match A better than B does. Fallback may not have triggered.")


if __name__ == "__main__":
    main()
