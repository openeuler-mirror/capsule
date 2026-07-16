from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .converter import Converter
from .validator import validate_svg


logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ooxml-svg", description="Convert OOXML PresentationML directly to editable semantic SVG"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert", help="Convert a PPTX or extracted OOXML directory")
    convert.add_argument("source")
    convert.add_argument("output")
    convert.add_argument("--no-strict", action="store_true", help="Write output even when policy validation fails")
    convert.add_argument(
        "--font-dir",
        action="append",
        default=[],
        help="Additional directory searched for exact source fonts (repeatable)",
    )
    convert.add_argument(
        "--fallback-font-regular", help="Portable regular font file used when the source family is unavailable"
    )
    convert.add_argument(
        "--fallback-font-bold", help="Portable bold font file used when the source family is unavailable"
    )
    validate = sub.add_parser("validate", help="Validate one or more generated SVG files")
    validate.add_argument("svg", nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    args = build_parser().parse_args(argv)
    if args.command == "convert":
        result = Converter(
            font_dirs=args.font_dir,
            fallback_font_regular=args.fallback_font_regular,
            fallback_font_bold=args.fallback_font_bold,
        ).convert(args.source, args.output, strict=not args.no_strict)
        logger.info(
            json.dumps(
                {
                    "output": str(result.output_dir),
                    "slides": len(result.svg_files),
                    "report": str(result.report_file),
                    "valid": all(v.valid for v in result.validations),
                },
                ensure_ascii=False,
            )
        )
        return 0
    results = [validate_svg(path) for path in args.svg]
    logger.info(
        json.dumps(
            [{"path": r.path, "valid": r.valid, "errors": r.errors, "warnings": r.warnings} for r in results],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(r.valid for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
