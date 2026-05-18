import os
import re
from pathlib import Path
from urllib.parse import urlparse

from core.utils.logger import logger
from core.utils.document_parser.models import ParseResult, ImageInfo
from core.utils.document_parser.config import MarkdownConfig


class MarkdownReader:
    def __init__(self, config: MarkdownConfig):
        self.config = config
        self.extract_images = config.extract_images
        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _is_url(path: str) -> bool:
        try:
            result = urlparse(path)
            return result.scheme in ("http", "https")
        except Exception:
            return False

    def _resolve_image_path(self, img_path: str, md_dir: str) -> str:
        if self._is_url(img_path):
            from core.utils.document_parser.parser import DocumentParser
            try:
                local_path = DocumentParser.download_from_url(img_path)
                logger.debug("网络图片已下载: {} -> {}", img_path, local_path)
                return local_path
            except Exception as e:
                logger.warning("下载网络图片失败: {}, 错误: {}", img_path, e)
                return img_path

        if os.path.isabs(img_path):
            abs_path = os.path.normpath(img_path)
        else:
            abs_path = os.path.normpath(os.path.join(md_dir, img_path))
        return abs_path

    def parse(self, file_path: str) -> ParseResult:
        logger.debug("MarkdownReader 开始解析: {}", file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        md_dir = os.path.dirname(os.path.abspath(file_path))
        images = []
        pattern = r"!\[.*?\]\((.*?)\)"
        for match in re.finditer(pattern, text):
            img_path = match.group(1)
            resolved = self._resolve_image_path(img_path, md_dir)
            images.append(ImageInfo(path=resolved, description=""))

        logger.debug(
            "MarkdownReader 解析完成，文本长度: {} 字符，图片数量: {}",
            len(text),
            len(images),
        )
        images = images if self.extract_images else []
        return ParseResult(text=text, images=images, markdown_file=file_path)
