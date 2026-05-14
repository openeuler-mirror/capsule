#!/usr/bin/env python3
"""Convert HTML file(s) or a directory of HTML files into a single PPTX.

Reuses the existing `htmls_to_pptx` helper from
`core.ppt_generator.utils.common`, which renders each HTML with a headless
browser, merges the PDFs, then converts via LibreOffice into a PPTX.

Inputs:
- A single directory: every ``.html`` / ``.htm`` file inside is collected
  (natural-sorted by filename) and merged into one PPTX.
- A single HTML file: converted into a one-page PPTX.
- Multiple HTML file paths: merged into one PPTX in the order given.

Usage:
    python html_to_pptx.py <input_dir> [-o <output_dir>] [-n <filename>]
    python html_to_pptx.py <page.html> [-o <output_dir>] [-n <filename>]
    python html_to_pptx.py <p1.html> <p2.html> ... [-o <output_dir>] [-n <filename>]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from core.utils.logger import logger


HTML_SUFFIXES = {".html", ".htm"}


def _natural_key(path: Path):
    """Sort 0.html, 1.html, ..., 10.html naturally instead of lexicographically."""
    parts = re.split(r"(\d+)", path.stem)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _collect_html_files_from_dir(input_dir: Path) -> list[str]:
    html_paths = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in HTML_SUFFIXES),
        key=_natural_key,
    )
    return [str(p) for p in html_paths]


def _resolve_inputs(inputs: list[Path]) -> tuple[list[str], Path]:
    """Resolve the user-supplied paths into (ordered html file list, default_output_dir).

    - 1 directory  → enumerate its HTML files, default output dir = the directory
    - 1+ html files → use as-is, default output dir = parent of the first file
    """
    if len(inputs) == 1 and inputs[0].is_dir():
        input_dir = inputs[0]
        html_paths = _collect_html_files_from_dir(input_dir)
        if not html_paths:
            raise ValueError(f"在目录中未找到 HTML 文件: {input_dir}")
        return html_paths, input_dir

    html_paths: list[str] = []
    for path in inputs:
        if not path.exists():
            raise ValueError(f"输入路径不存在: {path}")
        if path.is_dir():
            raise ValueError(
                f"混合输入不支持：当提供多个路径时每个都必须是 HTML 文件，但 {path} 是目录"
            )
        if path.suffix.lower() not in HTML_SUFFIXES:
            raise ValueError(f"不是 HTML 文件: {path}")
        html_paths.append(str(path))
    return html_paths, inputs[0].parent


async def _convert(html_paths: list[str], output_dir: Path, filename: str) -> None:
    from core.ppt_generator.utils.common import htmls_to_pptx

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f">>> 共 {len(html_paths)} 个 HTML 文件，按以下顺序合成：")
    for idx, path in enumerate(html_paths):
        logger.info(f"    [{idx + 1:02d}] {path}")
    logger.info(f">>> 输出目录: {output_dir}")
    logger.info(f">>> 输出文件名: {filename}")

    pdf_path, pptx_path = await htmls_to_pptx(html_paths, str(output_dir), filename)

    logger.info(f">>> 合并 PDF: {pdf_path}")
    if pptx_path:
        logger.info(f">>> 生成 PPTX: {pptx_path}")
    else:
        raise RuntimeError("PDF 转 PPTX 失败，请检查日志中的 LibreOffice 报错信息。")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 HTML 文件或目录合成为单个 PPTX 文件。"
    )
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="一个 HTML 目录，或一个/多个 HTML 文件路径",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="PPTX 输出目录（默认：目录输入时为该目录本身，文件输入时为第一个文件所在目录）",
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

    inputs = [p.expanduser().resolve() for p in args.inputs]
    html_paths, default_output_dir = _resolve_inputs(inputs)

    output_dir: Path = (args.output_dir or default_output_dir).expanduser().resolve()

    asyncio.run(_convert(html_paths, output_dir, args.filename))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error(str(exc))
        raise
