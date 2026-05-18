import hashlib
import os
import uuid
from pathlib import Path

from core.utils.logger import logger
from core.utils.document_parser.config import PDFEngineConfig
from core.utils.document_parser.models import ImageInfo, ParseResult
from core.utils.document_parser.pdf_engines.base import PDFParserBase


class DoclingPDFParser(PDFParserBase):
    priority: int = 10

    def __init__(self, config: PDFEngineConfig):
        super().__init__(config)

    @classmethod
    def support_file_type(cls) -> set:
        return {".pdf", ".docx", ".pptx", ".html"}

    @classmethod
    def is_available(cls, config=None) -> bool:
        try:
            import docling  # noqa: F401
            return True
        except ImportError:
            return False

    async def parse(self, pdf_path: str) -> ParseResult:
        logger.debug("Docling 引擎开始解析: {} {}", pdf_path, self.config.extract_images)
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        pipeline_options = PdfPipelineOptions()
        pipeline_options.generate_picture_images = self.config.extract_images
        pipeline_options.images_scale = 2

        converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
        )

        conv_res = converter.convert(pdf_path)
        doc = conv_res.document

        markdown_lines = []
        images = []
        seen_img_hashes = set()

        heading_labels = {"title", "section_header"}
        skip_labels = {"page_header", "page_footer"}

        for item, level in doc.iterate_items():
            if item.label in skip_labels:
                continue

            if (
                item.label in ["picture", "figure", "chart"]
                and self.config.extract_images
            ):
                img_pil = item.get_image(doc)
                if img_pil:
                    img_hash = hashlib.md5(img_pil.tobytes()).hexdigest()
                    if img_hash in seen_img_hashes:
                        continue
                    seen_img_hashes.add(img_hash)
                    img_filename = f"{uuid.uuid4().hex[:4]}.png"
                    image_dir = os.path.join(self.output_dir, "images")
                    os.makedirs(image_dir, exist_ok=True)
                    img_path = os.path.join(image_dir, img_filename)
                    img_pil.save(img_path, format="PNG")
                    if os.path.getsize(img_path) < 10 * 1024:
                        os.remove(img_path)
                        continue
                    images.append(ImageInfo(path=img_path, description=""))
                    markdown_lines.append(f"![Image](images/{img_filename})")

            elif item.label == "table":
                table_md = getattr(item, "export_to_markdown", lambda doc: None)(doc)
                if table_md and table_md.strip():
                    markdown_lines.append(table_md)

            else:
                text_md = getattr(item, "export_to_markdown", lambda doc: None)(
                    doc
                ) or getattr(item, "text", None)
                if text_md and text_md.strip():
                    if item.label in heading_labels:
                        heading_level = (
                            level + 1 if item.label == "title" else level + 2
                        )
                        heading_level = min(heading_level, 6)
                        text_md = f"{'#' * heading_level} {text_md}"
                    markdown_lines.append(text_md)

        text = "\n\n".join(markdown_lines)
        md_path = os.path.join(self.output_dir, Path(pdf_path).stem + ".md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(text)

        logger.debug("Docling 解析完成，提取 {} 张图片", len(images))
        return ParseResult(text=text, images=images, markdown_file=md_path)
