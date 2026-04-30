import hashlib
import mimetypes
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse
from datetime import datetime, timezone

import requests

from core.utils.logger import logger
from core.utils.document_parser.config import ParserConfig
from core.utils.document_parser.exceptions import EngineNotAvailableError
from core.utils.document_parser.models import ImageInfo
from core.utils.document_parser.pdf_engines import ENGINE_REGISTRY, PDFParserBase
from core.utils.document_parser.readers.text_reader import TextReader
from core.utils.document_parser.readers.markdown_reader import MarkdownReader
from core.utils.document_parser.readers.html_reader import HtmlReader
from core.utils.document_parser.readers.pptx_reader import PptxReader
from core.utils.document_parser.readers.docx_reader import DocxReader
from core.utils.document_parser.readers.image_reader import ImageReader


MIME_TO_EXT = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/html": ".html",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
    "image/tiff": ".tiff",
}


class DocumentParser:
    def __init__(self, config: Optional[Union[ParserConfig, dict]] = None):
        if config is None:
            self.config = ParserConfig()
        elif isinstance(config, dict):
            self.config = ParserConfig(**config)
        else:
            self.config = config

        self.text_reader = TextReader(self.config.text)
        self.markdown_reader = MarkdownReader(self.config.markdown)
        self.html_reader = HtmlReader(self.config.html)
        self.pptx_reader = PptxReader(self.config.pptx)
        self.docx_reader = DocxReader(self.config.docx)
        self.image_reader = ImageReader(self.config.image)


    @staticmethod
    def _compute_file_hash(file_path: str) -> Optional[str]:
        try:
            hasher = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            return None

    @staticmethod
    def filter_images(images: list) -> list:
        if not images:
            return images

        _min_dimension = 100

        seen_hashes = set()
        seen_paths = set()
        filtered = []

        for img in images:
            img_path = img.path if isinstance(img, ImageInfo) else img.get("path", "")
            if not img_path or not os.path.isfile(img_path):
                continue

            if img_path in seen_paths:
                logger.debug("去重: 跳过重复路径图片 {}", img_path)
                continue
            seen_paths.add(img_path)

            try:
                from PIL import Image as PILImage
                with PILImage.open(img_path) as pil_img:
                    w, h = pil_img.size
                if w < _min_dimension and h < _min_dimension:
                    logger.debug(
                        "过滤: 图片尺寸过小 ({}x{})，删除: {}",
                        w, h, img_path,
                    )
                    os.remove(img_path)
                    continue
            except Exception:
                logger.warning("处理图片 {} 时出错", img_path)
                continue

            file_hash = DocumentParser._compute_file_hash(img_path)
            if file_hash and file_hash in seen_hashes:
                logger.debug("去重: 跳过内容重复图片 {}", img_path)
                try:
                    os.remove(img_path)
                except OSError as e:
                    logger.warning("删除重复图片失败: {}, 错误: {}", img_path, e)
                continue
            if file_hash:
                seen_hashes.add(file_hash)

            filtered.append(img)

        return filtered

    async def native_parser(self, task_output_dir, file_path, ext):
        if ext in (".md", ".markdown"):
            self.markdown_reader.output_dir = task_output_dir
            result = self.markdown_reader.parse(file_path)
        elif ext in (".html", ".htm"):
            self.html_reader.output_dir = task_output_dir
            result = self.html_reader.parse(file_path)
        elif ext == ".pptx":
            self.pptx_reader.output_dir = task_output_dir
            result = self.pptx_reader.parse(file_path)
        elif ext in (".docx", ".doc"):
            self.docx_reader.output_dir = task_output_dir
            result = self.docx_reader.parse(file_path)
        elif ImageReader.is_image_file(file_path, ext):
            self.image_reader.output_dir = task_output_dir
            result = await self.image_reader.parse(file_path)
        else:
            result = self.text_reader.parse(file_path)
        return result


    async def parse(self, file_path: str) -> dict:
        logger.info("开始解析: {}", file_path)

        task_name = self._compute_task_name(file_path)
        task_output_dir = os.path.join(self.config.output_dir, task_name)
        os.makedirs(task_output_dir, exist_ok=True)
        logger.info("任务输出目录: {}", task_output_dir)

        if self._is_url(file_path):
            logger.info("检测到 URL，开始下载: {}", file_path)
            file_path = self.download_from_url(file_path)
            logger.info("下载完成，临时文件: {}", file_path)

        mime_type = self.detect_mime_type(file_path)
        ext = MIME_TO_EXT.get(mime_type, Path(file_path).suffix.lower())
        logger.info("文件类型: {} (MIME: {})", ext, mime_type)

        engine = self._resolve_engine()
        engine.output_dir = task_output_dir

        if ext in engine.support_file_type():
            logger.info("使用引擎: {}", engine.__class__.__name__)
            result = await engine.parse(file_path)
        else:
            result = await self.native_parser(task_output_dir, file_path, ext)

        original_count = len(result.images) if hasattr(result, 'images') else len(result.get('images', []))
        result.images = DocumentParser.filter_images(result.images)
        removed_count = original_count - len(result.images)
        logger.info(
            "解析完成，文本长度: {} 字符，图片数量: {}， 移除: {}",
            len(result.text),
            len(result.images),
            removed_count,
        )
        return result.to_dict()

    @staticmethod
    def detect_mime_type(file_path: str) -> str:
        mimetypes.add_type("text/markdown", ".md")
        mimetypes.add_type("text/markdown", ".markdown")

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            return mime_type

        try:
            with open(file_path, "rb") as f:
                header = f.read(8)

            if header.startswith(b"%PDF"):
                return "application/pdf"
            elif header.startswith(b"PK\x03\x04"):
                with zipfile.ZipFile(file_path) as zf:
                    names = zf.namelist()
                    if any("word/" in n for n in names):
                        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    elif any("xl/" in n for n in names):
                        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    elif any("ppt/" in n for n in names):
                        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                return "application/zip"
            elif header.startswith(b"\xd0\xcf\x11\xe0"):
                return "application/msword"
        except Exception:
            logger.warning("检测文件类型时出错: {}", file_path)

        return ""

    @staticmethod
    def _compute_task_name(file_path: str) -> str:
        if DocumentParser._is_url(file_path):
            parsed = urlparse(file_path)
            p = Path(parsed.path)
            stem = (p.stem + "_" + p.suffix.lstrip(".")) if p.suffix else (p.stem or "downloaded")
        else:
            p = Path(file_path)
            stem = p.stem + "_" + p.suffix.lstrip(".") if p.suffix else p.stem

        task_name = re.sub(r'[<>:"/\\|?*]', "_", stem)
        if not task_name:
            task_name = "untitled"
        return task_name + "_" + datetime.now(timezone.utc).strftime("%H%M%S")

    @staticmethod
    def _is_url(path: str) -> bool:
        try:
            result = urlparse(path)
            return result.scheme in ("http", "https")
        except Exception:
            return False

    _READ_TIMEOUT = 10

    @staticmethod
    def download_from_url(url: str) -> str:
        logger.info("开始下载 URL: {}", url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        try:
            response = requests.get(url, headers=headers, timeout=(30, 60), stream=True)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error("下载网页文件失败: {}", e)
            raise RuntimeError("下载网页文件失败") from e

        total_size = response.headers.get("Content-Length")
        total_size = int(total_size) if total_size else None
        if total_size:
            logger.info("文件总大小: {:.2f} MB", total_size / (1024 * 1024))

        suffix = ""
        logger.debug("尝试从 Content-Type 推断扩展名")
        content_type = response.headers.get("Content-Type", "")
        for mime, ext in MIME_TO_EXT.items():
            if mime in content_type:
                suffix = ext
                logger.debug("从 Content-Type 推断扩展名: {}", suffix)
                break

        if not suffix:
            suffix = ".html"
            logger.debug("无法推断扩展名，默认使用 .html")

        downloaded = 0
        chunk_size = 8192
        last_logged_percent = -1
        last_progress_time = time.monotonic()
        read_timeout = DocumentParser._READ_TIMEOUT

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            for chunk in response.iter_content(chunk_size=chunk_size):
                now = time.monotonic()
                if chunk:
                    last_progress_time = now
                elif now - last_progress_time > read_timeout:
                    logger.error("下载超时: {}秒内无数据接收", read_timeout)
                    raise RuntimeError(f"下载超时: {read_timeout}秒内无数据接收")

                tmp.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    percent = int(downloaded / total_size * 100)
                    if percent >= last_logged_percent + 10:
                        last_logged_percent = percent
                        logger.info(
                            "下载进度: {}% ({:.2f} MB / {:.2f} MB)",
                            percent,
                            downloaded / (1024 * 1024),
                            total_size / (1024 * 1024),
                        )
                elif downloaded % (5 * 1024 * 1024) < chunk_size:
                    logger.info("已下载: {:.2f} MB", downloaded / (1024 * 1024))

        logger.info("下载完成，总大小: {:.2f} MB", downloaded / (1024 * 1024))
        return tmp.name

    def _resolve_engine(self) -> PDFParserBase:
        pdf_config = self.config.pdf
        engine_name = pdf_config.engine

        if engine_name:
            engine_cls = ENGINE_REGISTRY.get(engine_name.lower())
            if engine_cls is None:
                logger.error("未知的 引擎: {}", engine_name)
                raise ValueError(f"未知的 引擎: {engine_name}")
            if not engine_cls.is_available(pdf_config):
                logger.warning("引擎 {} 不可用", engine_name)
                raise EngineNotAvailableError(f"引擎 {engine_name} 不可用")
            logger.info("初始化引擎: {}", engine_name)
            return engine_cls(pdf_config)

        sorted_engines = sorted(ENGINE_REGISTRY.values(), key=lambda cls: cls.priority)
        for engine_cls in sorted_engines:
            if engine_cls.is_available(pdf_config):
                logger.info(
                    "自动选择引擎: {} (优先级: {})",
                    engine_cls.__name__,
                    engine_cls.priority,
                )
                return engine_cls(pdf_config)

        raise EngineNotAvailableError("没有可用的 PDF 引擎")
