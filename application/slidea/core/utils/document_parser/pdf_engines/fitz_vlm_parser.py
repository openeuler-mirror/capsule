import asyncio
import base64
import io
import os
import re
from typing import List

from PIL import Image
from json_repair import repair_json

from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.document_parser.config import PDFEngineConfig
from core.utils.document_parser.models import ImageInfo, PageContent, ParseResult
from core.utils.document_parser.pdf_engines.fitz_parser import FitzPDFParser

from core.utils.llm import ModelRoute, can_vlm_invoke_route, vlm_raw_invoke


class FitzVLMPDFParser(FitzPDFParser):
    priority: int = 2

    def __init__(self, config: PDFEngineConfig):
        super().__init__(config)

    @classmethod
    def is_available(cls, config=None) -> bool:
        if not super().is_available(config):
            return False

        if not can_vlm_invoke_route(ModelRoute.PREMIUM):
            return False

        return True

    async def parse(self, pdf_path: str) -> ParseResult:
        doc = self._open_pdf(pdf_path)
        pages = [
            self._extract_page_content(doc[i], doc, i + 1) for i in range(len(doc))
        ]
        doc.close()
        all_images = await self._process_images_vlm(pages)
        return self._assemble_result(pages, pdf_path, all_images)

    def _extract_page_content(self, page, doc, page_num: int) -> PageContent:
        content = super()._extract_page_content(page, doc, page_num)
        if not self.config.extract_images:
            return content

        try:
            content.vector_metadata = self._extract_page_vectors(page, page_num)
            logger.debug(
                "页面 {}: 共 {} 个矢量路径矩形", page_num, len(content.vector_metadata)
            )
        except Exception:
            logger.debug("页面 {} 提取矢量路径矩形时出错", page_num)
        return content

    async def _process_images_vlm(self, pages: list) -> List[ImageInfo]:
        all_images = self._deduplicate_images(pages)
        await self._describe_images(all_images)
        vector_images = await self._process_vector_charts(pages)
        logger.debug("共 {} 张矢量图表", len(vector_images))
        all_images.extend(vector_images)
        return all_images

    async def _describe_images(self, images: List[ImageInfo]):
        semaphore = asyncio.Semaphore(3)

        async def describe(img: ImageInfo):
            async with semaphore:
                img.description = await self._describe_image_via_vlm(img.path)
                logger.debug("Fitz-VLM 引擎描述图片 {}: {}", img.path, img.description)

        await asyncio.gather(*[describe(img) for img in images])

    async def _process_vector_charts(self, pages: list) -> List[ImageInfo]:
        chart_images: List[ImageInfo] = []
        for page in pages:
            for vi, vmeta in enumerate(page.vector_metadata):
                try:
                    vector_image = vmeta["image"]
                    rtype, description = await self._classify_vector_image(
                        vector_image, page.page_num
                    )
                    logger.debug(
                        "页面 {} 矢量图 {} 分类结果: {}", page.page_num, vi, description
                    )
                    if rtype in ["NO", "no"]:
                        continue
                    elif rtype in ["SPLIT", "split"]:
                        cropped = await self._extract_sub_charts(
                            vector_image, description, page.page_num, f"v{vi}_"
                        )
                        page.images.extend(cropped)
                        chart_images.extend(cropped)
                        continue

                    dest_filename = f"page_{page.page_num}_vector_chart_v{vi}.png"
                    image_dir = os.path.join(self.output_dir, "images")
                    os.makedirs(image_dir, exist_ok=True)
                    dest_path = os.path.join(image_dir, dest_filename)
                    vector_image.save(dest_path)

                    img_info = ImageInfo(
                        path=dest_path,
                        description=description,
                        bbox=tuple(vmeta.get("bbox", ())),
                    )
                    page.images.append(img_info)
                    chart_images.append(img_info)
                except Exception as e:
                    logger.warning(
                        "矢量图处理失败 page_{}_vector_{}: {}", page.page_num, vi, e
                    )
        return chart_images

    async def _describe_image_via_vlm(self, img_path: str) -> str:
        try:
            with Image.open(img_path) as img:
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

            prompt = "请简要描述这张图片的内容，用一句话概括。"
            response = await vlm_raw_invoke(
                ModelRoute.PREMIUM,
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
                work_node="document_image_description",
            )
            return response.content
        except Exception as e:
            logger.warning("VLM 描述失败 {}: {}", img_path, e)
            return ""

    async def _classify_vector_image(self, image, page_num: int) -> str:
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

        prompt = f"""下面的图片是一张PDF页面的矢量截图，你需要判断该图片是否是一个可用的流程图/架构图/示意图或统计图表。
返回json格式如下：
{{
    type: 判断类型
        - YES：如果只包含流程图/架构图/示意图或统计图表，可以直接使用该图片
        - NO：没有明显的流程图/架构图/示意图或统计图表，无法作为图片使用
        - SPLIT：如果除了流程图/架构图/示意图或统计图表之外还包含有其他的无关信息，需要裁剪才能使用
    description: 根据type的值返回不同的内容
        - YES：对图片的详细描述
        - NO：无法作为图片使用的理由
        - SPLIT：使用以下格式标注可用的图片部分：[FIGURE: 内容描述 | bbox: ymin,xmin,ymax,xmax]，
        其中坐标为归一化坐标（0-1000），表示在原图中的位置。ymin,xmin 为左上角坐标，ymax,xmax 为右下角坐标。
        可以标注最多4个图片，每个标注之间用换行符分隔。
}}
"""
        try:
            response = await vlm_raw_invoke(
                ModelRoute.PREMIUM,
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
                work_node="document_vector_chart_classification",
            )
            content = response.content
            logger.debug("Fitz-VLM 引擎分类矢量图 {}: {}", page_num, content)
            result = repair_json(content, ensure_ascii=False, return_objects=True)
            return result.get("type", "NO"), result.get("description", "")
        except Exception as e:
            logger.warning(
                "Fitz-VLM 引擎分类矢量图 {} 失败: {}", page_num, e
            )
            return "NO", ""

    async def _extract_sub_charts(
        self, image, description: str, page_num: int, suffix: str
    ) -> List[ImageInfo]:
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")

        pattern = r"\[FIGURE:\s*(.*?)\s*\|\s*bbox:\s*\[?([\d,\s]+)\]?\s*\]"
        matches = re.findall(pattern, description)

        width, height = image.size
        charts = []
        for i, (desc, bbox_str) in enumerate(matches):
            coords = [int(c.strip()) for c in bbox_str.split(",")]
            ymin, xmin, ymax, xmax = coords

            left = xmin * width / 1000
            top = ymin * height / 1000
            right = xmax * width / 1000
            bottom = ymax * height / 1000

            crop_img = image.crop((left, top, right, bottom))
            crop_filename = f"page_{page_num}_vlm_img_{suffix}{i}.png"
            crop_path = os.path.join(self.output_dir, crop_filename)
            crop_img.save(crop_path)

            if os.path.getsize(crop_path) < 10 * 1024:
                os.remove(crop_path)
                continue

            charts.append(ImageInfo(path=crop_path, description=desc))

        charts.sort(key=lambda x: os.path.getsize(x.path), reverse=True)
        for img in charts[4:]:
            os.remove(img.path)

        return charts[:4]
