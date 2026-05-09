import os
import uuid
from io import StringIO
from pathlib import Path

import xml.etree.ElementTree as ElementTree
from docx import Document

from core.utils.logger import logger
from core.utils.document_parser.models import ParseResult, ImageInfo
from core.utils.document_parser.config import DocxConfig


class DocxReader:
    def __init__(self, config: DocxConfig):
        self.config = config
        self.extract_images = config.extract_images
        self.output_dir = config.output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def parse(self, file_path: str) -> ParseResult:
        logger.info("DocxReader 开始解析: {}", file_path)
        document = self._load_document(file_path)
        if document is None:
            logger.warning("DocxReader 无法加载文档: {}", file_path)
            return ParseResult(text="", images=[], markdown_file="")

        name = Path(file_path).stem
        md_lines, image_list = self._convert_to_markdown(document)
        md_text = "\n".join(md_lines)

        md_filename = name + ".md"
        md_path = os.path.join(self.output_dir, md_filename)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        images = image_list if self.extract_images else []
        logger.info(
            "DocxReader 解析完成，文本长度: {} 字符，图片数量: {}",
            len(md_text),
            len(images),
        )
        return ParseResult(text=md_text, images=images, markdown_file=md_path)

    @staticmethod
    def _load_document(file_path: str):
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".docx", ".doc"):
            return None
        real_path = os.path.realpath(file_path)
        if not os.path.exists(real_path):
            return None
        return Document(real_path)

    def _convert_to_markdown(self, document):
        md_lines = []
        image_list = []
        for paragraph in document.paragraphs:
            style_name = paragraph.style.name
            text = paragraph.text.strip()

            if style_name == "Title":
                if text:
                    md_lines.append(f"# {text}")
                    md_lines.append("")
            elif style_name.startswith("Heading "):
                try:
                    level = int(style_name[len("Heading "):])
                except ValueError:
                    level = 1
                if text:
                    md_lines.append(f"{'#' * (level + 1)} {text}")
                    md_lines.append("")
            else:
                if self.extract_images:
                    imgs = self._extract_images(document, paragraph)
                    image_list.extend(imgs)
                    if text:
                        md_lines.append(text)
                        md_lines.append("")
                    for img_info in imgs:
                        img_rel = os.path.basename(img_info.path)
                        md_lines.append(f"![image](images/{img_rel})")
                        md_lines.append("")
                else:
                    if text:
                        md_lines.append(text)
                        md_lines.append("")

        return md_lines, image_list

    def _extract_images(self, document, paragraph):
        images = []
        for run in paragraph.runs:
            xmlstr = run.element.xml
            if "pic:pic" not in xmlstr:
                continue
            namespaces = dict(
                node for _, node in ElementTree.iterparse(StringIO(xmlstr), events=["start-ns"])
            )
            root = ElementTree.fromstring(xmlstr)
            for pic in root.findall(".//pic:pic", namespaces):
                blip_elem = pic.find("pic:blipFill/a:blip", namespaces)
                if blip_elem is None:
                    continue
                embed_attr = blip_elem.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                )
                if embed_attr is None:
                    continue
                image_part = document.part.related_parts.get(embed_attr)
                if image_part is None:
                    continue

                image_dir = os.path.join(self.output_dir, "images")
                os.makedirs(image_dir, exist_ok=True)
                image_file = os.path.join(image_dir, f"image_{uuid.uuid4().hex[:4]}.png")
                with open(image_file, "wb") as f:
                    f.write(image_part.blob)
                images.append(ImageInfo(path=os.path.realpath(image_file)))
        return images
