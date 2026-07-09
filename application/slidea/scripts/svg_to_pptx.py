#!/usr/bin/env python3
"""Convert SVG file(s) or a directory of SVG files into a single PPTX.

Reuses the existing `svgs_to_pptx` helper from
`core.ppt_generator.utils.svg_export`, which converts each SVG into native
editable DrawingML and assembles them into a single PPTX (no PDF round-trip).

Inputs:
- A single directory: every ``.svg`` file inside is collected
  (natural-sorted by filename) and merged into one PPTX.
- A single SVG file: converted into a one-page PPTX.
- Multiple SVG file paths: merged into one PPTX in the order given.

Usage:
    python svg_to_pptx.py <input_dir> [-o <output_dir>] [-n <filename>]
    python svg_to_pptx.py <page.svg> [-o <output_dir>] [-n <filename>]
    python svg_to_pptx.py <p1.svg> <p2.svg> ... [-o <output_dir>] [-n <filename>]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from core.ppt_generator.utils.common import sanitize_filename
from core.utils.logger import logger


SVG_SUFFIXES = {".svg"}


def _natural_key(path: Path):
    """Sort 01_a.svg, 02_b.svg, ..., 10_z.svg naturally instead of lexicographically."""
    parts = re.split(r"(\d+)", path.stem)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _collect_svg_files_from_dir(input_dir: Path) -> list[str]:
    svg_paths = sorted(
        (p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SVG_SUFFIXES),
        key=_natural_key,
    )
    return [str(p) for p in svg_paths]


def _resolve_inputs(inputs: list[Path]) -> tuple[list[str], Path]:
    """Resolve the user-supplied paths into (ordered svg file list, default_output_dir).

    - 1 directory  → enumerate its SVG files, default output dir = the directory
    - 1+ svg files → use as-is, default output dir = parent of the first file
    """
    if len(inputs) == 1 and inputs[0].is_dir():
        input_dir = inputs[0]
        svg_paths = _collect_svg_files_from_dir(input_dir)
        if not svg_paths:
            raise ValueError(f"在目录中未找到 SVG 文件: {input_dir}")
        return svg_paths, input_dir

    svg_paths: list[str] = []
    for path in inputs:
        if not path.exists():
            raise ValueError(f"输入路径不存在: {path}")
        if path.is_dir():
            raise ValueError(
                f"混合输入不支持：当提供多个路径时每个都必须是 SVG 文件，但 {path} 是目录"
            )
        if path.suffix.lower() not in SVG_SUFFIXES:
            raise ValueError(f"不是 SVG 文件: {path}")
        svg_paths.append(str(path))
    return svg_paths, inputs[0].parent


async def _convert(svg_paths: list[str], output_dir: Path, filename: str) -> None:
    from core.ppt_generator.utils.svg_export import svgs_to_pptx

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f">>> 共 {len(svg_paths)} 个 SVG 文件，按以下顺序合成：")
    for idx, path in enumerate(svg_paths):
        logger.info(f"    [{idx + 1:02d}] {path}")
    logger.info(f">>> 输出目录: {output_dir}")
    logger.info(f">>> 输出文件名: {filename}")

    _, pptx_path = await svgs_to_pptx(svg_paths, str(output_dir), filename)

    if pptx_path:
        logger.info(f">>> 生成 PPTX: {pptx_path}")
    else:
        raise RuntimeError("SVG 转 PPTX 失败，请检查转换日志。")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 SVG 文件或目录合成为单个 PPTX 文件（原生 DrawingML，可编辑）。"
    )
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="一个 SVG 目录，或一个/多个 SVG 文件路径",
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
        help="PPTX 文件名（不含扩展名，默认 output）；会按生成流水线的规则自动 sanitize（空格→下划线等），传 ppt.json 的 topic 原文即可",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    inputs = [p.expanduser().resolve() for p in args.inputs]
    svg_paths, default_output_dir = _resolve_inputs(inputs)

    output_dir: Path = (args.output_dir or default_output_dir).expanduser().resolve()

    # Sanitize the filename the same way run_ppt_pipeline.py does, so Phase 3
    # re-exports land on exactly the same path the original pipeline wrote.
    # Without this, "-n <raw topic>" would produce a differently-named PPTX
    # (spaces preserved) and the prior file would silently remain.
    filename = sanitize_filename(args.filename)

    asyncio.run(_convert(svg_paths, output_dir, filename))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error(str(exc))
        raise
