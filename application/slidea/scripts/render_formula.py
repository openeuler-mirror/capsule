#!/usr/bin/env python3
"""Render a single LaTeX formula to a transparent PNG for an existing slidea run.

Used by Phase 3 (agent-led page editing) to add or replace a formula on a page
without re-running the generation pipeline. The output is a PNG plus a record
appended to <run_id_dir>/formulas.json so the agent can later look up LaTeX
source by image path.

Usage:
    .venv/bin/python scripts/render_formula.py "<latex>" \
        --out <run_id_dir>/slides [--color #000000] [--dpi 300]

stdout is a single JSON object: {"path", "width", "height", "latex", "color",
"dpi", "relative_href"}. Exit code is non-zero on render failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.ppt_generator.utils.formula import (
    FORMULA_RENDER_COLOR,
    FORMULA_RENDER_DPI,
    append_formula_record_sync,
    render_formula,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one LaTeX formula to a transparent PNG for an existing slidea run.",
    )
    parser.add_argument(
        "latex",
        help="LaTeX source, e.g. '\\\\frac{a}{b}'. Surrounding $...$ is optional and stripped.",
    )
    parser.add_argument(
        "--out",
        required=True,
        help=(
            "Slides directory of the target run (the parent of images/). "
            "The PNG lands at <out>/images/<sha1>.png."
        ),
    )
    parser.add_argument(
        "--color",
        default=FORMULA_RENDER_COLOR,
        help=f"Foreground HEX color (default: {FORMULA_RENDER_COLOR}).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=FORMULA_RENDER_DPI,
        help=f"Render DPI (default: {FORMULA_RENDER_DPI}).",
    )
    args = parser.parse_args()

    slides_dir = str(Path(args.out).expanduser().resolve())
    if not os.path.isdir(slides_dir):
        sys.stderr.write(f"--out must be an existing directory: {slides_dir}\n")
        return 2

    run_dir = os.path.dirname(slides_dir) or os.getcwd()

    path, dims = asyncio.run(
        render_formula(
            args.latex,
            slides_dir,
            color=args.color,
            dpi=args.dpi,
        )
    )
    if path is None or dims is None:
        sys.stderr.write(
            f"formula render failed for: {args.latex[:80]}\n"
            "Common causes: unsupported LaTeX syntax, CJK characters in source, "
            "or write permission on the output directory.\n"
        )
        return 1

    width, height = dims
    record = {
        "latex": args.latex,
        "path": path,
        "color": args.color,
        "dpi": args.dpi,
        "display": True,
        "width": width,
        "height": height,
        "first_used_page": None,
    }
    append_formula_record_sync(run_dir, record)

    payload = {
        "path": path,
        "width": width,
        "height": height,
        "latex": args.latex,
        "color": args.color,
        "dpi": args.dpi,
        "relative_href": f"images/{Path(path).name}",
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
