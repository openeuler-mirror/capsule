"""Convert SVG slides into a native editable PPTX file.

Image inlining happens at export time: each SVG is copied into a temporary
directory, its local ``<image href="images/...">`` references are rewritten as
``data:`` URIs (resolved against the SVG's original parent directory), and the
inlined copies are passed to the DrawingML converter. This keeps the on-disk
SVGs editable (relative paths, small files) while still feeding self-contained
input to the PPTX converter.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from core.utils.logger import logger
from core.ppt_generator.utils.svg_pipeline.finalize_svg import embed_local_images_in_file
from core.ppt_generator.utils.svg_to_pptx import create_pptx_with_native_svg
from core.ppt_generator.utils.pptx_postprocess import remove_full_slide_solid_backdrops


async def svgs_to_pptx(
    svg_paths: list[str],
    output_dir: str,
    filename: str = "output",
) -> tuple[str, str]:
    """Convert SVG files into a native editable PPTX written to ``<output_dir>/<filename>.pptx``.

    ``svg_paths`` are the editable on-disk SVGs (with relative image hrefs).
    ``output_dir`` is where the final PPTX lands — typically the run's cache
    directory so the deliverable sits alongside its source metadata.
    """
    svg_files = [Path(path) for path in svg_paths if Path(path).exists()]
    if not svg_files:
        raise Exception("没有生成任何 SVG 文件，请检查 SVG 路径是否正确。")

    output_path = Path(output_dir) / f"{filename}.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _convert() -> bool:
        with tempfile.TemporaryDirectory(prefix="slidea_export_") as tmp:
            inlined: list[Path] = []
            for src in svg_files:
                tmp_svg = Path(tmp) / src.name
                shutil.copy2(src, tmp_svg)
                embed_local_images_in_file(tmp_svg, src.parent)
                inlined.append(tmp_svg)
            return create_pptx_with_native_svg(
                svg_files=inlined,
                output_path=output_path,
                canvas_format="ppt169",
                verbose=False,
                transition=None,
                use_compat_mode=False,
                enable_notes=False,
                use_native_shapes=True,
                animation=None,
            )

    logger.info(f"正在转换 SVG 到原生 PPTX: {output_path}")
    ok = await asyncio.to_thread(_convert)
    if not ok or not output_path.exists():
        raise Exception("SVG 转 PPTX 失败，请检查 SVG 内容和转换日志。")

    await asyncio.to_thread(remove_full_slide_solid_backdrops, output_path)

    return "", str(output_path)
