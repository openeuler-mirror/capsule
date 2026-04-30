import argparse
import asyncio
import os
from pathlib import Path
from datetime import datetime, timezone

from core.utils.logger import logger
from core.utils.config import output_files_dir
from core.utils.document_parser.parser import (
    DocumentParser, ParserConfig, MIME_TO_EXT
)


_SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown",
    ".pdf",
    ".doc", ".docx",
    ".xls", ".xlsx",
    ".ppt", ".pptx",
    ".html", ".htm",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".avif", ".tiff", ".tif",
}


def _collect_files(path: str) -> list[str]:
    """如果路径是目录，递归收集其中支持的文件；否则直接返回该路径"""
    if not os.path.isdir(path):
        return [path]
    result = []
    for root, _dirs, files in os.walk(path):
        for fname in sorted(files):
            file_path = os.path.join(root, fname)
            mime_type = DocumentParser.detect_mime_type(file_path)
            ext = MIME_TO_EXT.get(mime_type, Path(file_path).suffix.lower())
            if ext in _SUPPORTED_EXTENSIONS:
                result.append(os.path.join(root, fname))
    if not result:
        logger.warning("_collect_files 目录 {} 中未找到可解析的文件", path)
    return result


async def get_content(file_path: str, extract_images: bool = False, output_dir: str = None):
    """get content of document, supports file path and directory path"""
    file_paths = _collect_files(file_path)
    if not file_paths:
        return {"text": "", "images": [], "markdown_file": []}

    merged_text = []
    merged_images = []
    merged_markdown_files = []

    parser = DocumentParser(
        config=ParserConfig(extract_images=extract_images, output_dir=output_dir)
    )
    semaphore = asyncio.Semaphore(3)

    async def _parse_one(fp):
        async with semaphore:
            try:
                return await parser.parse(fp)
            except Exception as e:
                import traceback
                logger.error("get_content 解析文档 {} 失败: {}", fp, e)
                traceback.print_exc()
                return {"text": "", "images": [], "markdown_file": ""}

    results = await asyncio.gather(*[_parse_one(fp) for fp in file_paths])
    for result in results:
        merged_text.append(result.get("text", ""))
        merged_images.extend(result.get("images", []))
        if result.get("markdown_file"):
            merged_markdown_files.append(result.get("markdown_file"))

    return {
        "text": "\n\n".join(merged_text),
        "images": merged_images,
        "markdown_file": merged_markdown_files,
    }


async def get_contents(file_paths: list[str], extract_images: bool = False, output_dir: str = None):
    """解析多个文档，合并 markdown 和 images。支持传入目录路径，会自动遍历目录下的文件"""
    merged_text = []
    merged_images = []
    merged_markdown_files = []

    semaphore = asyncio.Semaphore(3)

    async def _get_one(file_path):
        async with semaphore:
            return await get_content(file_path, extract_images=extract_images, output_dir=output_dir)

    results = await asyncio.gather(*[_get_one(fp) for fp in file_paths])
    for result in results:
        merged_text.append(result.get("text", ""))
        merged_images.extend(result.get("images", []))
        merged_markdown_files.extend(result.get("markdown_file", []))

    return {
        "text": "\n\n".join(merged_text),
        "images": merged_images,
        "markdown_file": merged_markdown_files,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="解析文档并提取内容")
    parser.add_argument("--file_path", nargs="+", help="文件路径或 URL，支持多个")
    parser.add_argument("--extract-images", action="store_true", default=False, help="是否提取图片 (默认: False)")
    args = parser.parse_args()
    output_dir = os.path.join(output_files_dir, "documents")
    output_dir = os.path.join(output_dir, datetime.now(timezone.utc).strftime("%Y%m%d"))
    result = asyncio.run(get_contents(args.file_path, extract_images=args.extract_images, output_dir=output_dir))
    logger.info("解析完成，文本长度: {} 字符，图片链接: {}:  解析文本路径: {}",
                len(result["text"]), result["images"], result["markdown_file"])