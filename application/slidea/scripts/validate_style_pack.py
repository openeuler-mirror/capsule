#!/usr/bin/env python3
"""Validate a Slidea style-pack manifest and referenced files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ppt_generator.utils.style_pack import validate_style_pack


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Slidea style pack")
    parser.add_argument("style_pack")
    args = parser.parse_args()
    manifest = validate_style_pack(args.style_pack)
    print(json.dumps({
        "valid": True,
        "style_pack": str(Path(args.style_pack).resolve()),
        "pages": len(manifest["pages"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
