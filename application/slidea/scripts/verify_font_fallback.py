"""Verify the bundled-fonts fallback renders CJK glyphs instead of tofu.

Three renders of the same SVG, saved under output/font_check/:

  A) baseline       — system fontconfig as-is (this dev host has Noto Sans CJK,
                      so this proves the SVG text is well-formed)
  B) no_fonts       — FONTCONFIG_FILE points at a conf with NO <dir>, simulating
                      an intranet box without CJK fonts. Expected: tofu squares.
  C) bundled_fonts  — same empty conf, plus our _bundled_fonts_context on top,
                      pointing at assets/fonts. Expected: real glyphs.

Pango caches its font map per-process, so each variant is rendered in a fresh
subprocess with the right env pre-set. If C looks like A and not like B,
the fallback works.

Usage:
    .venv/bin/python scripts/verify_font_fallback.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "font_check"
OUT.mkdir(parents=True, exist_ok=True)
WORKER = Path(__file__).resolve()

SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" viewBox="0 0 600 200">\n'
    '  <rect width="600" height="200" fill="#fafafa"/>\n'
    '  <text x="40" y="80" font-family="Arial, sans-serif" font-size="48" fill="#111">Hello ABC 123</text>\n'
    '  <text x="40" y="150" font-family="Microsoft YaHei, Arial, sans-serif" font-size="48" fill="#111">中文测试幻灯片</text>\n'
    '</svg>\n'
).encode("utf-8")


def write_empty_conf(empty_dir: Path) -> Path:
    conf = empty_dir / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        '<fontconfig>\n'
        f'  <cachedir>{empty_dir}/cache</cachedir>\n'
        '</fontconfig>\n',
        encoding="utf-8",
    )
    return conf


def render_in_subprocess(mode: str, output: Path) -> None:
    env = dict(os.environ)
    if mode in ("no_fonts", "bundled_fonts"):
        empty_dir = output.parent / f"_empty_fc_{mode}"
        if empty_dir.exists():
            import shutil
            shutil.rmtree(empty_dir)
        empty_dir.mkdir()
        env["SLIDEA_FC_EMPTY_DIR"] = str(empty_dir)
        env["FONTCONFIG_FILE"] = str(write_empty_conf(empty_dir))
    subprocess.run(
        [sys.executable, str(WORKER), "--worker", mode, str(output)],
        check=True,
        env=env,
        cwd=str(ROOT),
    )


def worker_main(mode: str, output: str) -> None:
    sys.path.insert(0, str(ROOT))
    import cairosvg
    from core.ppt_generator.utils.screenshot import (
        _bundled_fonts_context,
        _detect_system_cjk_fonts,
    )

    detected = _detect_system_cjk_fonts(False)
    print(f"[{mode}] detected CJK fonts:", detected)

    def render() -> None:
        cairosvg.svg2png(bytestring=SVG, write_to=output, output_width=600, output_height=200)

    if mode == "bundled_fonts":
        with _bundled_fonts_context():
            render()
    else:
        render()


def main() -> None:
    targets = {
        "A_baseline_system.png": "baseline",
        "B_no_fonts_tofu.png": "no_fonts",
        "C_bundled_fonts.png": "bundled_fonts",
    }
    for filename, mode in targets.items():
        out = OUT / filename
        print(f"=== rendering {filename} (mode={mode}) ===")
        render_in_subprocess(mode, out)
        print(f"  -> {out}")
    print("\nDone. Compare:")
    for filename in targets:
        print(f"  {OUT}/{filename}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--worker":
        worker_main(sys.argv[2], sys.argv[3])
    else:
        main()
