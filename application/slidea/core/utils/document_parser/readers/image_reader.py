import base64
import io
import os
import shutil
from pathlib import Path
from typing import Optional

from core.utils.logger import logger
from core.utils.document_parser.models import ParseResult, ImageInfo
from core.utils.document_parser.config import ImageConfig
from core.utils.llm import can_vlm_invoke, vlm_raw_invoke



_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".avif", ".tiff", ".tif"}


class ImageReader:
    def __init__(self, config: ImageConfig):
        self.config = config
        self.output_dir = config.output_dir

    @staticmethod
    def is_image_file(file_path: str, ext: Optional[str] = None) -> bool:
        ext = ext or Path(file_path).suffix.lower()
        return ext in _IMAGE_EXTENSIONS

    async def parse(self, file_path: str) -> ParseResult:
        logger.debug("ImageReader 开始解析: {}", file_path)

        if not os.path.exists(file_path):
            logger.warning("ImageReader 文件不存在: {}", file_path)
            return ParseResult(text="", images=[], markdown_file="")

        description = ""
        if can_vlm_invoke():
            description = await self._describe_image_via_vlm(file_path)
            logger.debug("ImageReader VLM 描述: {}", description)
        else:
            logger.debug("ImageReader VLM 不可用，跳过图片内容识别")

        name = Path(file_path).stem
        md_lines = []

        if description:
            md_lines.append(f"# {name}")
            md_lines.append("")
            md_lines.append(description)
            md_lines.append("")

        md_text = "\n".join(md_lines)

        md_filename = name + ".md"
        md_path = os.path.join(self.output_dir, md_filename)
        os.makedirs(self.output_dir, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        img_ext = Path(file_path).suffix
        copied_img_path = os.path.join(self.output_dir, name + img_ext)
        shutil.copy2(file_path, copied_img_path)

        img_info = ImageInfo(path=copied_img_path, description=description)

        logger.debug(
            "ImageReader 解析完成，文本长度: {} 字符",
            len(md_text),
        )
        return ParseResult(text=md_text, images=[img_info], markdown_file=md_path)

    async def _describe_image_via_vlm(self, img_path: str) -> str:
        try:
            from PIL import Image
            from langchain.messages import HumanMessage

            with Image.open(img_path) as img:
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

            prompt = "请详细描述这张图片的内容。"
            logger.debug("ImageReader VLM 描述图片: {}", img_path)
            response = await vlm_raw_invoke(
                [
                    HumanMessage(
                        content=[
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}"
                                },
                            },
                        ]
                    )
                ],
            )
            return response.content
        except Exception as e:
            logger.warning("ImageReader VLM 描述失败 {}: {}", img_path, e)
            return ""
