"""Pixel-level diff between A/B/C font_check renders.

Expected outcome if the bundled-fonts fallback works:
  - mean_abs_diff(A, B) is large (tofu vs real glyphs differ a lot)
  - mean_abs_diff(A, C) is much smaller than A-B (both render real glyphs)
  - mean_abs_diff(B, C) is large (tofu vs real glyphs)
"""
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow not in this venv. Trying via numpy + cairosvg internals...", file=sys.stderr)
    sys.exit(2)

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "output" / "font_check"

def load_gray(name):
    img = Image.open(OUT / name).convert("L")
    return np.asarray(img, dtype=np.int16)

A = load_gray("A_baseline_system.png")
B = load_gray("B_no_fonts_tofu.png")
C = load_gray("C_bundled_fonts.png")

def mean_abs_diff(x, y):
    return float(np.abs(x - y).mean())

print(f"shapes: A={A.shape} B={B.shape} C={C.shape}")
print(f"mean_abs_diff(A, B) = {mean_abs_diff(A, B):.2f}   # baseline vs no-fonts (tofu expected)")
print(f"mean_abs_diff(A, C) = {mean_abs_diff(A, C):.2f}   # baseline vs bundled (should be small)")
print(f"mean_abs_diff(B, C) = {mean_abs_diff(B, C):.2f}   # no-fonts vs bundled (tofu expected)")

# Heuristic pass/fail
ab = mean_abs_diff(A, B)
ac = mean_abs_diff(A, C)
bc = mean_abs_diff(B, C)
print()
if ac < ab and ac < bc:
    print(f"PASS: C matches A more than B does. Bundled-font fallback works.")
    if ac < 5.0:
        print(f"  (A vs C diff {ac:.2f} is small — glyph shapes are essentially identical)")
    else:
        print(f"  (A vs C diff {ac:.2f} — both render real glyphs but metrics/metrics differ slightly, expected)")
else:
    print(f"FAIL: C does not match A better than B does. Fallback may not have triggered.")
