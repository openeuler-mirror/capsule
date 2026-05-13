import os
import uuid
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.utils.logger import logger
from core.utils.document_parser.models import ParseResult, ImageInfo
from core.utils.document_parser.config import HtmlConfig


class HtmlReader:
    def __init__(self, config: HtmlConfig):
        self.config = config
        self.extract_images = config.extract_images
        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def parse(self, file_path: str, base_url: str = "") -> ParseResult:
        logger.debug("HtmlReader 开始解析: {}", file_path)
        if not os.path.exists(file_path):
            logger.warning("HtmlReader 文件不存在: {}", file_path)
            return ParseResult(text="", images=[], markdown_file="")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, "html.parser")
        md_lines, image_list = self._convert_to_markdown(soup, base_url)
        md_text = "\n".join(md_lines)

        name = Path(file_path).stem
        md_filename = name + ".md"
        md_path = os.path.join(self.output_dir, md_filename)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        images = image_list if self.extract_images else []
        logger.debug(
            "HtmlReader 解析完成，文本长度: {} 字符，图片数量: {}",
            len(md_text),
            len(images),
        )
        return ParseResult(text=md_text, images=images, markdown_file=md_path)

    def _convert_to_markdown(self, soup, base_url):
        md_lines = []
        image_list = []

        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            md_lines.append(f"# {title_tag.string.strip()}")
            md_lines.append("")

        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = tag.get_text(strip=True)
            if not text:
                continue
            level = int(tag.name[1])
            md_lines.append(f"{'#' * level} {text}")
            md_lines.append("")

        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if not text:
                continue
            md_lines.append(text)
            md_lines.append("")

            if self.extract_images:
                imgs = self._extract_images_from_tag(p, base_url)
                image_list.extend(imgs)
                for img_info in imgs:
                    md_lines.append(f"![image](images/{img_info.path})")
                    md_lines.append("")

        for li in soup.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                md_lines.append(f"- {text}")
        if soup.find_all("li"):
            md_lines.append("")

        if self.extract_images:
            standalone_imgs = soup.find_all("img", recursive=True)
            processed_srcs = {img.path for img in image_list}
            for img_tag in standalone_imgs:
                src = img_tag.get("src", "")
                if not src or src in processed_srcs:
                    continue
                img_url = self._resolve_url(src, base_url)
                image_list.append(ImageInfo(path=img_url))
                md_lines.append(f"![image]({img_url})")
                md_lines.append("")

        return md_lines, image_list

    def _extract_images_from_tag(self, tag, base_url):
        images = []
        for img_tag in tag.find_all("img"):
            src = img_tag.get("src", "")
            if not src:
                continue
            img_url = self._resolve_url(src, base_url)
            images.append(ImageInfo(path=img_url))
        return images

    @staticmethod
    def _resolve_url(src, base_url):
        if src.startswith("http://") or src.startswith("https://"):
            return src
        if base_url:
            return urljoin(base_url, src)
        return src
