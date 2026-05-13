#!/usr/bin/env python3
"""Convert all HTML files in a directory to a single PPTX.

Reuses the existing `htmls_to_pptx` helper from
`core.ppt_generator.utils.common`, which renders each HTML with a headless
browser, merges the PDFs, then converts via LibreOffice into a PPTX.

Usage:
    python html_dir_to_pptx.py <input_dir> [-o <output_dir>] [-n <filename>]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _natural_key(path: Path):
    """Sort 0.html, 1.html, ..., 10.html naturally instead of lexicographically."""
    parts = re.split(r"(\d+)", path.stem)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _collect_html_files(input_dir: Path) -> list[str]:
    html_paths = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".html", ".htm"}),
        key=_natural_key,
    )
    return [str(p) for p in html_paths]


async def _convert(input_dir: Path, output_dir: Path, filename: str) -> None:
    from core.ppt_generator.utils.common import htmls_to_pptx

    html_paths = _collect_html_files(input_dir)
    if not html_paths:
        raise SystemExit(f"在目录中未找到 HTML 文件: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f">>> 输入目录: {input_dir}")
    print(f">>> 共 {len(html_paths)} 个 HTML 文件，按以下顺序合成：")
    for idx, path in enumerate(html_paths):
        print(f"    [{idx + 1:02d}] {Path(path).name}")
    print(f">>> 输出目录: {output_dir}")
    print(f">>> 输出文件名: {filename}")

    pdf_path, pptx_path = await htmls_to_pptx(html_paths, str(output_dir), filename)

    print(f"\n>>> 合并 PDF: {pdf_path}")
    if pptx_path:
        print(f">>> 生成 PPTX: {pptx_path}")
    else:
        print(">>> PDF 转 PPTX 失败，请检查日志中的 LibreOffice 报错信息。")
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将目录中的 HTML 文件合成为单个 PPTX 文件。"
    )
    parser.add_argument("input_dir", type=Path, help="包含 HTML 文件的目录")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="PPTX 输出目录（默认为输入目录）",
    )
    parser.add_argument(
        "-n",
        "--filename",
        default="output",
        help="PPTX 文件名（不含扩展名，默认 output）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    input_dir: Path = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"输入目录不存在或不是目录: {input_dir}")

    output_dir: Path = (args.output_dir or input_dir).expanduser().resolve()

    asyncio.run(_convert(input_dir, output_dir, args.filename))


if __name__ == "__main__":
    main()
