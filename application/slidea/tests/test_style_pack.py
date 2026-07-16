import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.ppt_generator.thought_to_ppt.node import _outline_page_dump
from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType
from core.ppt_generator.utils.style_pack import (
    apply_style_reference_shell,
    assign_style_references_for_outline,
    bind_style_reference_paths,
    copy_style_pack_into_run,
    extract_style_dynamic_content,
    prepare_style_runtime_references,
    style_reference_catalog,
    validate_style_pack,
)


SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"></svg>'


class StylePackTests(unittest.IsolatedAsyncioTestCase):
    def _pack(self, root: Path) -> Path:
        pack = root / "pack"
        reference = pack / "reference"
        reference.mkdir(parents=True)
        (reference / "cover.svg").write_text(SVG, encoding="utf-8")
        (reference / "content.svg").write_text(SVG, encoding="utf-8")
        (pack / "style-pack.json").write_text(json.dumps({
            "version": 1,
            "source": "reference.pptx",
            "global_style": "深蓝背景、青色强调线、微软雅黑",
            "pages": [
                {
                    "id": "cover",
                    "source_slide": 1,
                    "svg": "reference/cover.svg",
                    "page_type": "cover",
                    "density": "sparse",
                    "structure": "左侧大标题，右侧几何装饰",
                    "description": "适合标题较短的正式封面",
                },
                {
                    "id": "content",
                    "source_slide": 7,
                    "svg": "reference/content.svg",
                    "page_type": "content",
                    "density": "medium",
                    "structure": "标题栏下方三列并列卡片",
                    "description": "适合三个并列观点或方案比较",
                },
            ],
        }), encoding="utf-8")
        return pack

    def test_validate_copy_catalog_and_bind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._pack(root)
            manifest = validate_style_pack(source)
            copied = copy_style_pack_into_run(source, root / "run")
            catalog = style_reference_catalog(copied)
            outline = [
                PPTPage(
                    title="方案对比",
                    abstract="比较三套架构",
                    type=PageType.CONTENT,
                    index=0,
                    style_reference_id="content",
                ),
            ]
            bind_style_reference_paths(outline, copied)

            self.assertEqual(len(manifest["pages"]), 2)
            self.assertEqual(catalog[1]["structure"], "标题栏下方三列并列卡片")
            self.assertTrue(Path(outline[0].style_reference_svg).is_file())
            self.assertEqual(outline[0].style_reference_page_type, "content")

    def test_validator_requires_agent_descriptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self._pack(Path(tmp))
            manifest_path = pack / "style-pack.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["pages"][0]["description"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "description"):
                validate_style_pack(pack)

    def test_saved_outline_schema_differs_by_style_mode(self):
        page = PPTPage(
            title="页面",
            abstract="摘要",
            type=PageType.CONTENT,
            index=0,
            style_reference_id="content",
            style_reference_svg="/run/style_pack/reference/content.svg",
        )
        plain = _outline_page_dump(page, include_style_reference=False)
        styled = _outline_page_dump(page, include_style_reference=True)

        self.assertNotIn("style_reference_id", plain)
        self.assertNotIn("style_reference_svg", plain)
        self.assertNotIn("style_reference_page_type", plain)
        self.assertEqual(styled["style_reference_id"], "content")
        self.assertNotIn("style_reference_svg", styled)
        self.assertNotIn("style_reference_page_type", styled)

    async def test_outline_model_selects_references_by_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self._pack(Path(tmp))
            outline = [
                PPTPage(
                    title="项目封面",
                    abstract="主题",
                    type=PageType.COVER_THANKS,
                    index=0,
                    source=-1,
                ),
                PPTPage(
                    title="方案对比",
                    abstract="三种实现路径与优缺点",
                    type=PageType.CONTENT,
                    index=1,
                    source=0,
                ),
            ]
            responses = [
                [{"page_index": 0, "style_reference_id": "cover"}],
                [{"page_index": 1, "style_reference_id": "content"}],
            ]
            with patch(
                "core.ppt_generator.utils.style_pack.llm_invoke",
                new=AsyncMock(side_effect=responses),
            ) as invoke:
                await assign_style_references_for_outline(outline, pack)

            self.assertEqual([page.style_reference_id for page in outline], ["cover", "content"])
            self.assertTrue(all(Path(page.style_reference_svg).is_file() for page in outline))
            self.assertEqual(invoke.await_count, 2)
            prompt = invoke.await_args_list[1].args[1][0].content
            self.assertIn("只根据页面类型、信息密度和版式结构选择", prompt)
            self.assertIn("三列并列卡片", prompt)

    async def test_invalid_model_assignments_fail_after_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self._pack(Path(tmp))
            outline = [
                PPTPage(title="封面", abstract="主题", type=PageType.COVER_THANKS, index=0),
            ]
            invalid = [{"page_index": 0, "style_reference_id": "unknown"}]
            with patch(
                "core.ppt_generator.utils.style_pack.llm_invoke",
                new=AsyncMock(return_value=invalid),
            ) as invoke:
                with self.assertRaisesRegex(ValueError, "failed to assign"):
                    await assign_style_references_for_outline(outline, pack)
            self.assertEqual(invoke.await_count, 3)

    async def test_large_chapter_is_split_into_bounded_outline_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = self._pack(Path(tmp))
            outline = [
                PPTPage(
                    title=f"内容 {index}",
                    abstract="结构化说明",
                    type=PageType.CONTENT,
                    index=index,
                    source=2,
                )
                for index in range(25)
            ]
            responses = [
                [{"page_index": index, "style_reference_id": "content"} for index in range(0, 12)],
                [{"page_index": index, "style_reference_id": "content"} for index in range(12, 24)],
                [{"page_index": 24, "style_reference_id": "content"}],
            ]
            with patch(
                "core.ppt_generator.utils.style_pack.llm_invoke",
                new=AsyncMock(side_effect=responses),
            ) as invoke:
                await assign_style_references_for_outline(outline, pack)

            self.assertEqual(invoke.await_count, 3)
            self.assertTrue(all(page.style_reference_id == "content" for page in outline))

    def test_runtime_reference_copies_only_fixed_master_layout_images(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720" fill="#fff"/></g>
          <g id="master-content"><image href="images/logo.png" x="10" y="680"/></g>
          <g id="layout-content"/>
          <g id="main-content"><image href="images/business.png" x="100" y="100"/></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = self._pack(root)
            reference = pack / "reference"
            (reference / "images").mkdir(exist_ok=True)
            (reference / "images" / "logo.png").write_bytes(b"fixed-logo")
            (reference / "images" / "business.png").write_bytes(b"business-content")
            (reference / "content.svg").write_text(reference_svg, encoding="utf-8")
            slides = root / "run" / "slides"
            page = PPTPage(
                title="新标题",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_id="content",
            )
            bind_style_reference_paths([page], pack)
            prepare_style_runtime_references([page], pack, slides)

            runtime = Path(page.style_reference_svg)
            runtime_text = runtime.read_text(encoding="utf-8")
            assets = list((slides / "images" / "style-pack").iterdir())
            self.assertTrue(runtime.is_file())
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].read_bytes(), b"fixed-logo")
            self.assertIn("images/style-pack/", runtime_text)
            self.assertIn("style-reference-only/business.png", runtime_text)
            self.assertNotIn("business-content", runtime_text)

    def test_runtime_reference_publishes_separate_title_backdrop_assets(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720" fill="#fff"/></g>
          <g id="master-content"/>
          <g id="layout-content"/>
          <g id="main-content">
            <g id="title-backdrop"><rect x="40" y="40" width="1200" height="100"/><image href="images/title-texture.png" x="40" y="40" width="1200" height="100"/></g>
            <g id="source-title" data-role="header"><text x="60" y="110" font-size="48">Source title</text></g>
            <g id="source-body"><image href="images/business.png" x="100" y="220" width="400" height="300"/></g>
          </g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = self._pack(root)
            reference = pack / "reference"
            (reference / "images").mkdir(exist_ok=True)
            (reference / "images" / "title-texture.png").write_bytes(b"fixed-title-texture")
            (reference / "images" / "business.png").write_bytes(b"business-content")
            (reference / "content.svg").write_text(reference_svg, encoding="utf-8")
            slides = root / "run" / "slides"
            page = PPTPage(
                title="New title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_id="content",
            )
            bind_style_reference_paths([page], pack)
            prepare_style_runtime_references([page], pack, slides)

            runtime_text = Path(page.style_reference_svg).read_text(encoding="utf-8")
            assets = list((slides / "images" / "style-pack").iterdir())
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].read_bytes(), b"fixed-title-texture")
            self.assertIn("images/style-pack/", runtime_text)
            self.assertIn("style-reference-only/business.png", runtime_text)

    def test_composer_restores_fixed_shell_title_and_page_number(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <defs/>
          <g id="background"><rect width="1280" height="720" fill="#ffffff"/></g>
          <g id="master-content">
            <g data-role="footer"><text x="77" y="684" font-size="13">9</text></g>
            <image href="images/style-pack/logo.png" x="1100" y="670"/>
          </g>
          <g id="layout-content"><line x1="20" y1="90" x2="1260" y2="90"/></g>
          <g id="main-content">
            <g id="source-title" data-role="content"><rect x="27" y="32" width="1225" height="49"/><text x="37" y="66" font-size="32" textLength="100">原始业务标题</text></g>
            <g id="source-body"><text x="100" y="200">不得复制</text></g>
          </g>
        </svg>"""
        generated_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720" fill="#000"/></g>
          <g id="header"><text x="30" y="60" font-size="32">漂移标题</text></g>
          <g id="body"><text x="100" y="200">保留的新正文</text></g>
          <g id="footer"><text x="30" y="690">漂移页脚</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="确定性新标题",
                abstract="摘要",
                type=PageType.CONTENT,
                index=4,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
            )
            composed = apply_style_reference_shell(generated_svg, page)
            root = ET.fromstring(composed)
            groups = {child.get("id"): child for child in root if child.get("id")}
            all_text = [text.text for text in root.iter() if text.tag.rsplit("}", 1)[-1] == "text"]

            self.assertIn("slidea-style-background", groups)
            self.assertIn("slidea-style-master", groups)
            self.assertIn("slidea-style-layout", groups)
            self.assertIn("slidea-style-page-title", groups)
            self.assertIn("body", groups)
            self.assertNotIn("header", groups)
            self.assertNotIn("footer", groups)
            self.assertIn("确定性新标题", all_text)
            self.assertIn("保留的新正文", all_text)
            self.assertIn("5", all_text)
            self.assertNotIn("原始业务标题", all_text)
            self.assertNotIn("不得复制", all_text)
            title_text = next(
                item for item in groups["slidea-style-page-title"].iter()
                if item.tag.rsplit("}", 1)[-1] == "text"
            )
            self.assertNotIn("textLength", title_text.attrib)

    def test_composer_is_exact_noop_without_style_reference(self):
        page = PPTPage(title="普通页", abstract="摘要", type=PageType.CONTENT, index=0)
        content = '<svg xmlns="http://www.w3.org/2000/svg"><g id="header"/></svg>'
        self.assertIs(apply_style_reference_shell(content, page), content)

    def test_dynamic_extraction_removes_only_injected_style_shell(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
          <defs><linearGradient id="fixed-gradient"><stop offset="0" stop-color="#fff"/></linearGradient></defs>
          <g id="background"><rect width="1280" height="720" fill="url(#fixed-gradient)"/></g>
          <g id="master-content"><text x="40" y="690">fixed footer</text></g>
          <g id="layout-content"/>
          <g id="main-content"><g data-role="header"><text x="40" y="80" font-size="40">old title</text></g></g>
        </svg>"""
        generated_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
          <defs><linearGradient id="dynamic-gradient"><stop offset="0" stop-color="#f00"/></linearGradient></defs>
          <g id="body"><rect x="100" y="180" width="400" height="200" fill="url(#dynamic-gradient)"/><text x="120" y="240">dynamic body</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "reference.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
            )
            composed = apply_style_reference_shell(generated_svg, page)
            dynamic = extract_style_dynamic_content(composed)
            restored = apply_style_reference_shell(dynamic, page)

        self.assertIn("dynamic body", dynamic)
        self.assertIn("dynamic-gradient", dynamic)
        self.assertNotIn("data-slidea-style-shell", dynamic)
        self.assertNotIn("fixed-gradient", dynamic)
        self.assertNotIn("fixed footer", dynamic)
        self.assertIn("dynamic body", restored)
        self.assertIn("fixed footer", restored)
        self.assertIn("slidea-style-background", restored)

    def test_special_page_composer_rejects_model_invented_redesign_groups(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720"/></g>
          <g id="master-content"/>
          <g id="layout-content"><text x="60" y="120" font-size="48">目录</text></g>
          <g id="main-content"><g><rect x="100" y="190" width="1000" height="350"/></g></g>
        </svg>"""
        generated_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="main-title"><text x="100" y="160" font-size="28">模型新增副标题</text></g>
          <g id="agenda"><rect x="100" y="220" width="300" height="100"/><text x="120" y="260">01 第一章</text></g>
          <g id="decor"><circle cx="900" cy="200" r="40"/></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "toc.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="目录页",
                abstract="章节",
                type=PageType.TOC,
                index=1,
                style_reference_svg=str(reference),
                style_reference_page_type="toc",
            )
            root = ET.fromstring(apply_style_reference_shell(generated_svg, page))
            group_ids = {child.get("id") for child in root if child.get("id")}
        self.assertIn("agenda", group_ids)
        self.assertNotIn("main-title", group_ids)
        self.assertNotIn("decor", group_ids)

    def test_toc_composer_restores_slide_level_title(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720"/></g>
          <g id="master-content"/>
          <g id="layout-content"><rect x="0" y="0" width="50" height="720"/></g>
          <g id="main-content">
            <g id="source-title-backdrop"><rect x="45" y="44" width="1193" height="98" fill="#58C1DD"/></g>
            <g id="source-toc-title" data-role="header"><text x="70" y="108" font-size="48">Table of contents.</text></g>
            <g id="source-toc-body"><rect x="134" y="287" width="321" height="133"/><text x="160" y="340">Original item</text></g>
          </g>
        </svg>"""
        generated_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="main-title"><text x="60" y="100" font-size="48">模型标题</text></g>
          <g id="agenda"><rect x="134" y="287" width="321" height="133"/><text x="160" y="340">01 新目录项</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "toc.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="新目录标题",
                abstract="章节",
                type=PageType.TOC,
                index=1,
                style_reference_svg=str(reference),
                style_reference_page_type="toc",
            )
            root = ET.fromstring(apply_style_reference_shell(generated_svg, page))
            groups = {child.get("id"): child for child in root if child.get("id")}
            all_text = [text.text for text in root.iter() if text.tag.rsplit("}", 1)[-1] == "text"]

        self.assertIn("slidea-style-page-title", groups)
        self.assertIn("slidea-style-title-shell-1", groups)
        self.assertIn("agenda", groups)
        self.assertNotIn("main-title", groups)
        self.assertIn("新目录标题", all_text)
        self.assertNotIn("Table of contents.", all_text)
        self.assertNotIn("Original item", all_text)

    def test_thanks_composer_keeps_reference_title_and_simple_closing_copy(self):
        reference_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="background"><rect width="1280" height="720"/></g>
          <g id="master-content"/>
          <g id="layout-content"><rect x="0" y="0" width="50" height="720"/></g>
          <g id="main-content">
            <g id="source-thanks-title" data-role="header"><text x="100" y="300" font-size="80">Thank you!</text></g>
            <g id="source-contact"><text x="100" y="500" font-size="24">private@example.com</text></g>
          </g>
        </svg>"""
        generated_svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
          <g id="thanks-title"><text x="100" y="300" font-size="80">模型另画标题</text></g>
          <g id="closing-copy"><text x="100" y="500" font-size="24">谢谢观看</text></g>
          <g id="invented-cards"><rect x="100" y="550" width="300" height="100"/><text x="120" y="600">不要保留</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "thanks.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="致谢页",
                abstract="收束",
                type=PageType.COVER_THANKS,
                index=10,
                style_reference_svg=str(reference),
                style_reference_page_type="thanks",
            )
            root = ET.fromstring(apply_style_reference_shell(generated_svg, page))
            groups = {child.get("id"): child for child in root if child.get("id")}
            all_text = [text.text for text in root.iter() if text.tag.rsplit("}", 1)[-1] == "text"]

        self.assertIn("slidea-style-page-title", groups)
        self.assertIn("closing-copy", groups)
        self.assertNotIn("thanks-title", groups)
        self.assertNotIn("invented-cards", groups)
        self.assertIn("Thank you!", all_text)
        self.assertIn("谢谢观看", all_text)
        self.assertNotIn("private@example.com", all_text)
        self.assertNotIn("致谢页", all_text)


if __name__ == "__main__":
    unittest.main()
