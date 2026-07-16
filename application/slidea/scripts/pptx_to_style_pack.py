#!/usr/bin/env python3
"""Convert a reference PPTX into SVG material for an Agent-authored style pack."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.ppt_generator.utils.ooxml_svg.converter import Converter
from core.ppt_generator.utils.style_asset_inventory import write_style_asset_inventory


STYLE_PACK_TEMP_ROOT = Path("/tmp/slidea/style-packs")
logger = logging.getLogger(__name__)


def _style_pack_dir(session_id: str) -> Path:
    value = session_id.strip()
    if not value or value in {".", ".."}:
        raise ValueError("session-id must be non-empty")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise ValueError("session-id may contain only letters, digits, '.', '_' and '-'")
    output = (STYLE_PACK_TEMP_ROOT / value).resolve()
    root = STYLE_PACK_TEMP_ROOT.resolve()
    if output == root or root not in output.parents:
        raise ValueError(f"invalid session-id for temporary style-pack path: {session_id!r}")
    return output


def _build(args: argparse.Namespace) -> dict:
    source = Path(args.pptx).resolve()
    output = _style_pack_dir(args.session_id)
    if not source.is_file() or source.suffix.lower() != ".pptx":
        raise ValueError(f"reference PPTX not found: {source}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    reference_dir = output / "reference"
    fonts_dir = ROOT / "assets" / "fonts"
    result = Converter(
        font_dirs=[fonts_dir],
        fallback_font_regular=fonts_dir / "NotoSansSC-Regular.otf",
        fallback_font_bold=fonts_dir / "NotoSansSC-Bold.otf",
    ).convert(source, reference_dir, strict=True)
    inventory_path = write_style_asset_inventory(reference_dir, output)

    return {
        "style_pack": str(output),
        "reference_dir": str(reference_dir),
        "asset_inventory": str(inventory_path),
        "converted_pages": len(result.svg_files),
        "font_resolution": str(result.report_file),
        "next_step": (
            "Agent must read asset-inventory.json and selected SVG source files, choose a small "
            "diverse subset, explicitly authorize any reusable template assets in style-pack.json, "
            "then run scripts/validate_style_pack.py"
        ),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(
        description="Convert a reference PPTX into SVG material for an Agent-authored Slidea style pack"
    )
    parser.add_argument("pptx", help="User-supplied reference .pptx")
    parser.add_argument(
        "--session-id",
        required=True,
        help="Session id shared with run_ppt_pipeline.py; output is fixed at /tmp/slidea/style-packs/<session-id>",
    )
    args = parser.parse_args()
    logger.info(json.dumps(_build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
