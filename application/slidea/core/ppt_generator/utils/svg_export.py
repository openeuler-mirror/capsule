import asyncio
from pathlib import Path

from core.utils.logger import logger
from core.ppt_generator.utils.svg_to_pptx import create_pptx_with_native_svg


async def svgs_to_pptx(svg_paths: list[str], save_dir: str, filename: str = "output") -> tuple[str, str]:
    """Convert SVG files into a native editable PPTX."""
    svg_files = [Path(path) for path in svg_paths if Path(path).exists()]
    if not svg_files:
        raise Exception("没有生成任何 SVG 文件，请检查 SVG 路径是否正确。")

    output_path = Path(save_dir) / f"{filename}.pptx"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _convert() -> bool:
        return create_pptx_with_native_svg(
            svg_files=svg_files,
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

    return "", str(output_path)
