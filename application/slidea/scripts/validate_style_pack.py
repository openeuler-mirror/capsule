#!/usr/bin/env python3
"""Validate a Slidea style-pack manifest and referenced files."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from core.ppt_generator.utils.style_pack import validate_style_pack


logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Validate a Slidea style pack")
    parser.add_argument("style_pack")
    args = parser.parse_args()
    manifest = validate_style_pack(args.style_pack)
    logger.info(json.dumps({
        "valid": True,
        "style_pack": str(Path(args.style_pack).resolve()),
        "pages": len(manifest["pages"]),
        "reusable_assets": len(manifest.get("reusable_assets", [])),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
