import hashlib
import os
import re
from collections import Counter
from contextlib import redirect_stderr
from pathlib import Path
from typing import List, Tuple

import fitz
from PIL import Image

from core.utils.logger import logger
from core.utils.document_parser.config import PDFEngineConfig
from core.utils.document_parser.models import ImageInfo, PageContent, ParseResult
from core.utils.document_parser.pdf_engines.base import PDFParserBase


class FitzPDFParser(PDFParserBase):
    priority: int = 1

    def __init__(self, config: PDFEngineConfig):
        super().__init__(config)

    @classmethod
    def is_available(cls, config=None) -> bool:
        return True

    async def parse(self, pdf_path: str) -> ParseResult:
        logger.info("Fitz 引擎开始解析: {}", pdf_path)
        with open(os.devnull, "w") as devnull, redirect_stderr(devnull):
            try:
                doc = fitz.open(pdf_path)
                pages = [
                    self._extract_page_content(doc[i], doc, i + 1)
                    for i in range(len(doc))
                ]
            finally:
                doc.close()
        all_images = self._deduplicate_images(pages)
        return self._assemble_result(pages, pdf_path, all_images)

    def _deduplicate_images(self, pages: list) -> list:
        all_images: List[ImageInfo] = []
        seen_hashes: set = set()
        for page in pages:
            remaining: List[ImageInfo] = []
            for img in page.images:
                img_hash = self._compute_image_hash(img.path)
                if img_hash not in seen_hashes:
                    seen_hashes.add(img_hash)
                    all_images.append(img)
                    remaining.append(img)
                else:
                    logger.debug("重复图片已跳过: {}", img.path)
                    os.remove(img.path)
            page.images = remaining
        return all_images

    def _assemble_result(
        self, pages: list, pdf_path: str, all_images: list
    ) -> ParseResult:
        markdown_lines: List[str] = []
        for page in pages:
            all_items = list(page.items)
            end_parts = []
            for img in page.images:
                img_name = os.path.basename(img.path)
                desc = img.description or "图片"
                md_text = f"![{desc}](images/{img_name})"
                if img.bbox:
                    y_center = (img.bbox[1] + img.bbox[3]) / 2
                    x_center = (img.bbox[0] + img.bbox[2]) / 2
                    width = img.bbox[2] - img.bbox[0]
                    all_items.append((y_center, x_center, width, md_text))
                else:
                    end_parts.append(md_text)

            sorted_items = self._sort_items(
                all_items, page.num_cols, page.col_boundary, page.page_width
            )
            parts = [item[3] for item in sorted_items] + end_parts
            markdown_lines.append("\n\n".join(parts))

        full_text = "\n\n".join(markdown_lines)
        md_path = os.path.join(self.output_dir, Path(pdf_path).stem + ".md")
        os.makedirs(self.output_dir, exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        logger.debug(
            "Fitz 解析完成，共 {} 页，提取 {} 张图片", len(pages), len(all_images)
        )
        return ParseResult(text=full_text, images=all_images, markdown_file=md_path)

    @staticmethod
    def _compute_image_hash(image_path: str) -> str:
        h = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _extract_page_content(self, page, doc, page_num: int) -> PageContent:
        table_bboxes, table_items = self._extract_tables(page)
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        heading_map = self._build_heading_map(text_dict)
        text_items = self._extract_text_items(text_dict, table_bboxes, heading_map)
        items = text_items + table_items
        images = self._extract_images(doc, page, page_num)
        num_cols, col_boundary = self._detect_columns(items, page.rect.width)
        items = self._sort_items(items, num_cols, col_boundary, page.rect.width)
        return PageContent(
            page_num=page_num,
            items=items,
            images=images,
            page_width=page.rect.width,
            num_cols=num_cols,
            col_boundary=col_boundary,
        )

    def _extract_tables(self, page) -> Tuple[list, list]:
        table_bboxes = []
        table_items = []
        try:
            for tab in page.find_tables().tables:
                tab_rect = fitz.Rect(tab.bbox)
                table_bboxes.append(tab_rect)
                table_data = tab.extract()
                table_md = self._table_to_markdown(table_data)
                if table_md:
                    y_center = (tab_rect.y0 + tab_rect.y1) / 2
                    x_center = (tab_rect.x0 + tab_rect.x1) / 2
                    table_items.append((y_center, x_center, tab_rect.width, table_md))
        except Exception:
            logger.debug("页面提取表格时出错")
        return table_bboxes, table_items

    def _table_to_markdown(self, table_data) -> str:
        if not table_data:
            return ""

        cleaned = []
        for row in table_data:
            cleaned_row = []
            for cell in row:
                cell_text = str(cell).strip() if cell else ""
                cell_text = cell_text.replace("\n", " ").replace("|", "\\|")
                cleaned_row.append(cell_text)
            cleaned.append(cleaned_row)

        if not cleaned:
            return ""

        num_cols = max(len(row) for row in cleaned)
        for row in cleaned:
            while len(row) < num_cols:
                row.append("")

        lines = []
        header = "| " + " | ".join(cleaned[0]) + " |"
        separator = "| " + " | ".join(["---"] * num_cols) + " |"
        lines.append(header)
        lines.append(separator)

        for row in cleaned[1:]:
            line = "| " + " | ".join(row) + " |"
            lines.append(line)

        return "\n".join(lines)

    def _build_heading_map(self, text_dict: dict) -> dict:
        font_sizes = []
        for block in text_dict["blocks"]:
            if block["type"] == 0:
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"].strip():
                            font_sizes.append(round(span["size"], 1))

        if not font_sizes:
            return {}

        base_size = Counter(font_sizes).most_common(1)[0][0]
        heading_sizes = sorted(
            [s for s in set(font_sizes) if s > base_size * 1.15], reverse=True
        )
        return {s: i + 1 for i, s in enumerate(heading_sizes)}

    def _extract_text_items(
        self, text_dict: dict, table_bboxes: list, heading_map: dict
    ) -> list:
        items = []

        for block in text_dict["blocks"]:
            if block["type"] == 1:
                continue

            block_rect = fitz.Rect(block["bbox"])
            if any(block_rect.intersects(tr) for tr in table_bboxes):
                continue

            text = self._format_text_block(block, heading_map)
            if text.strip():
                y_center = (block_rect.y0 + block_rect.y1) / 2
                x_center = (block_rect.x0 + block_rect.x1) / 2
                items.append((y_center, x_center, block_rect.width, text))

        return items

    def _format_text_block(self, block: dict, heading_map: dict) -> str:
        lines_text = []
        for line in block["lines"]:
            spans_text = []
            for span in line["spans"]:
                text = span["text"]
                if not text.strip():
                    spans_text.append(text)
                    continue

                flags = span["flags"]
                is_bold = bool(flags & 16)
                is_italic = bool(flags & 2)

                if is_bold and is_italic:
                    formatted = f"***{text}***"
                elif is_bold:
                    formatted = f"**{text}**"
                elif is_italic:
                    formatted = f"*{text}*"
                else:
                    formatted = text
                spans_text.append(formatted)

            lines_text.append("".join(spans_text))

        full_text = " ".join(lines_text).strip()
        if not full_text:
            return ""

        first_size = None
        for line in block["lines"]:
            for span in line["spans"]:
                if span["text"].strip():
                    first_size = round(span["size"], 1)
                    break
            if first_size:
                break

        if first_size and first_size in heading_map:
            level = heading_map[first_size]
            return f"{'#' * (level + 1)} {full_text}"

        stripped = full_text.lstrip()
        if re.match(r"^[•·▪▸►]\s", stripped):
            full_text = "- " + stripped[2:]

        return full_text

    def _extract_images(self, doc, page, page_num: int) -> List[ImageInfo]:
        if not self.config.extract_images:
            return []
        image_info_list = page.get_image_info(xrefs=True)
        seen_xrefs = set()
        extracted = []

        for img_index, info in enumerate(image_info_list):
            xref = info.get("xref", 0)
            if xref == 0 or xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]

                if len(image_bytes) < 10 * 1024:
                    continue

                image_dir = os.path.join(self.output_dir, "images")
                os.makedirs(image_dir, exist_ok=True)
                img_filename = f"page_{page_num}_fitz_img_{img_index}.{image_ext}"
                img_path = os.path.join(image_dir, img_filename)
                with open(img_path, "wb") as f:
                    f.write(image_bytes)

                extracted.append((img_path, len(image_bytes), info.get("bbox")))
            except Exception:
                logger.debug("页面 {} 提取图片 {} 时出错", page_num, img_index)

        extracted.sort(key=lambda x: x[1], reverse=True)
        for path, _, _ in extracted[4:]:
            os.remove(path)

        images = []
        for path, _, bbox in extracted[:4]:
            images.append(ImageInfo(path=path, description="", bbox=bbox))
        return images

    def _detect_columns(self, items: list, page_width: float) -> Tuple[int, float]:
        non_full_width_x = []
        for y_center, x_center, block_width, _ in items:
            if block_width < page_width * 0.7:
                non_full_width_x.append(x_center)

        if len(non_full_width_x) < 4:
            return 1, None

        mid = page_width / 2
        left_count = sum(1 for x in non_full_width_x if x < mid * 0.85)
        right_count = sum(1 for x in non_full_width_x if x > mid * 1.15)

        if left_count >= 2 and right_count >= 2:
            sorted_x = sorted(non_full_width_x)
            max_gap = 0
            gap_mid = mid
            for i in range(len(sorted_x) - 1):
                gap = sorted_x[i + 1] - sorted_x[i]
                if gap > max_gap:
                    max_gap = gap
                    gap_mid = (sorted_x[i] + sorted_x[i + 1]) / 2
            if max_gap > page_width * 0.05:
                return 2, gap_mid

        return 1, None

    def _sort_items(
        self, items: list, num_cols: int, col_boundary: float, page_width: float
    ) -> list:
        if num_cols == 2 and col_boundary:

            def column_sort_key(item):
                y_center, x_center, block_width, _ = item
                if block_width >= page_width * 0.7:
                    col = 0
                else:
                    col = 0 if x_center < col_boundary else 1
                return (col, y_center)

            return sorted(items, key=column_sort_key)
        return sorted(items, key=lambda x: x[0])

    def _extract_page_vectors(self, page, page_index, zoom=3.0, padding=20):
        paths = page.get_drawings()
        if not paths:
            return []

        rect_list = []
        for p in paths:
            r = p["rect"]
            if r.is_empty:
                continue
            rect_list.append(r)

        round_num = 0
        changed = True
        while changed:
            round_num += 1
            changed = False
            new_list = []
            for r in rect_list:
                merged_with = []
                for i, existing_rect in enumerate(new_list):
                    check_rect = existing_rect + (-padding, -padding, padding, padding)
                    if r.intersects(check_rect):
                        merged_with.append(i)
                if not merged_with:
                    new_list.append(r)
                else:
                    combined = r
                    for idx in reversed(merged_with):
                        combined = combined | new_list.pop(idx)
                    new_list.append(combined)
                    changed = True
            rect_list = new_list

        vector_metadata = []
        for i, rect in enumerate(rect_list):
            if (
                rect.width > page.rect.width * 0.9
                and rect.height > page.rect.height * 0.9
            ):
                continue
            if rect.width < 15 or rect.height < 15:
                continue

            mat = fitz.Matrix(zoom, zoom)
            clip_rect = rect + (-2, -2, 2, 2)
            pix = page.get_pixmap(matrix=mat, clip=clip_rect, alpha=False)

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            vector_metadata.append(
                {
                    "page": page_index,
                    "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                    "image": img,
                }
            )

        return vector_metadata
