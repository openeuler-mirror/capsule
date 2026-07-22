import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.ppt_generator.thought_to_ppt.svg_page_generators import node as svg_node
from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType
from core.ppt_generator.utils.style_pack import apply_style_reference_shell


VALID_SVG = """```svg
<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="120" font-family="Microsoft YaHei, Arial, sans-serif" font-size="40" fill="#111111">Fixed</text>
</svg>
```"""


class DummyWriter:
    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)


class SVGQualityRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_quality_check_repairs_failed_svg_once(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_bad.svg"
            svg_path.write_text(
                '<svg width="1280" height="720" viewBox="0 0 1280 720" '
                'xmlns="http://www.w3.org/2000/svg"><style>.x{fill:red}</style></svg>',
                encoding="utf-8",
            )
            writer = DummyWriter()

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=AsyncMock(return_value=VALID_SVG),
            ):
                result = await svg_node.quality_check_node(
                    {"page_files": [str(svg_path)]},
                    writer,
                )

            repaired_content = svg_path.read_text(encoding="utf-8")

        self.assertIn("Fixed", repaired_content)
        self.assertTrue(result["svg_quality_report"][0]["passed"])
        self.assertTrue(any(payload.get("step", "").endswith("自动修复") for payload in writer.payloads))

    async def test_quality_check_raises_when_repair_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_bad.svg"
            svg_path.write_text("<svg><style></style></svg>", encoding="utf-8")
            writer = DummyWriter()

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=AsyncMock(return_value="not svg"),
            ):
                with self.assertRaisesRegex(ValueError, "quality check failed"):
                    await svg_node.quality_check_node(
                        {"page_files": [str(svg_path)]},
                        writer,
                    )

    async def test_style_pack_quality_repairs_only_dynamic_content_without_llm(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"><text x="40" y="690" font-family="Arial">fixed footer</text></g>
          <g id="layout-content"/>
          <g id="main-content"><g data-role="header"><text x="40" y="80" font-size="40" font-family="Arial">old title</text></g></g>
        </svg>"""
        dynamic_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="body"><image href="images/missing.png" x="100" y="180" width="200" height="100"/><text x="100" y="360" font-family="Arial">keep body</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            svg_path = root / "01_style.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
            )
            svg_path.write_text(apply_style_reference_shell(dynamic_svg, page), encoding="utf-8")
            writer = DummyWriter()
            repair_mock = AsyncMock(return_value=VALID_SVG)

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=repair_mock,
            ):
                result = await svg_node.quality_check_node(
                    {"page_files": [str(svg_path)], "outline": [page]},
                    writer,
                )

            final_content = svg_path.read_text(encoding="utf-8")

        repair_mock.assert_not_awaited()
        self.assertTrue(result["svg_quality_report"][0]["passed"])
        self.assertEqual(result["svg_quality_report"][0]["scope"], "dynamic-main-content")
        self.assertIn("keep body", final_content)
        self.assertIn("fixed footer", final_content)
        self.assertIn("slidea-style-background", final_content)
        self.assertNotIn("missing.png", final_content)
        self.assertTrue(any("动态内容质量检查" in payload.get("step", "") for payload in writer.payloads))

    async def test_partial_style_pack_checks_builtin_fallback_page_normally(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"/><g id="layout-content"/><g id="main-content"/>
        </svg>"""
        dynamic_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="body"><text x="100" y="200" font-family="Arial">styled body</text></g>
        </svg>"""
        fallback_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
          <text x="100" y="200" font-family="Arial" font-size="36">built-in thanks</text>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            styled_path = root / "01_style.svg"
            fallback_path = root / "02_fallback.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            styled_page = PPTPage(
                title="content", abstract="摘要", type=PageType.CONTENT, index=0,
                style_reference_svg=str(reference), style_reference_page_type="content",
            )
            fallback_page = PPTPage(
                title="thanks", abstract="摘要", type=PageType.COVER_THANKS, index=1,
            )
            styled_path.write_text(
                apply_style_reference_shell(dynamic_svg, styled_page), encoding="utf-8"
            )
            fallback_path.write_text(fallback_svg, encoding="utf-8")
            writer = DummyWriter()
            repair_mock = AsyncMock(return_value=VALID_SVG)

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=repair_mock,
            ):
                result = await svg_node.quality_check_node(
                    {
                        "page_files": [str(styled_path), str(fallback_path)],
                        "outline": [styled_page, fallback_page],
                    },
                    writer,
                )

        repair_mock.assert_not_awaited()
        self.assertEqual(
            [item["scope"] for item in result["svg_quality_report"]],
            ["dynamic-main-content", "full-svg-built-in-fallback"],
        )
        self.assertTrue(all(item["passed"] for item in result["svg_quality_report"]))

    async def test_style_pack_quality_removes_redundant_rect_clip_without_llm(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"/><g id="layout-content"/><g id="main-content"/>
        </svg>"""
        dynamic_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <defs><clipPath id="photo"><rect x="145" y="170" width="350" height="230" rx="18" ry="18"/></clipPath></defs>
          <g id="image-panel"><image href="images/photo.jpg" x="145" y="170" width="350" height="230" clip-path="url(#photo)"/><rect id="caption" x="145" y="342" width="350" height="58" clip-path="url(#photo)"/><text x="160" y="375" font-family="Arial">caption</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "images").mkdir()
            (root / "images" / "photo.jpg").write_bytes(b"image")
            reference = root / "reference.svg"
            svg_path = root / "01_style.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title", abstract="摘要", type=PageType.CONTENT, index=0,
                style_reference_svg=str(reference), style_reference_page_type="content",
            )
            svg_path.write_text(apply_style_reference_shell(dynamic_svg, page), encoding="utf-8")
            writer = DummyWriter()
            repair_mock = AsyncMock(return_value=VALID_SVG)

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=repair_mock,
            ):
                result = await svg_node.quality_check_node(
                    {"page_files": [str(svg_path)], "outline": [page]}, writer,
                )
            final_content = svg_path.read_text(encoding="utf-8")

        repair_mock.assert_not_awaited()
        self.assertTrue(result["svg_quality_report"][0]["passed"])
        self.assertIn('id="caption" x="145" y="342" width="350" height="58" rx="18" ry="18"', final_content)
        self.assertIn('<image href="images/photo.jpg"', final_content)
        self.assertIn('clip-path="url(#photo)"', final_content)

    async def test_style_pack_quality_uses_checked_dynamic_llm_fallback(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"/><g id="layout-content"/><g id="main-content"/>
        </svg>"""
        dynamic_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <style>.body{fill:red}</style><g id="body" class="body"><text x="100" y="360" font-family="Arial">keep original</text></g>
        </svg>"""
        repaired_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="body" fill="#FF0000"><text x="100" y="360" font-family="Arial">keep original</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            svg_path = root / "01_style.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title", abstract="摘要", type=PageType.CONTENT, index=0,
                style_reference_svg=str(reference), style_reference_page_type="content",
            )
            svg_path.write_text(apply_style_reference_shell(dynamic_svg, page), encoding="utf-8")
            writer = DummyWriter()
            repair_mock = AsyncMock(return_value=repaired_svg)

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=repair_mock,
            ):
                result = await svg_node.quality_check_node(
                    {"page_files": [str(svg_path)], "outline": [page]}, writer,
                )
            final_content = svg_path.read_text(encoding="utf-8")

        repair_mock.assert_awaited_once()
        self.assertTrue(result["svg_quality_report"][0]["passed"])
        self.assertEqual(result["svg_quality_report"][0]["repair_mode"], "dynamic-llm")
        self.assertIn("keep original", final_content)
        self.assertNotIn("<style", final_content)
        self.assertNotIn('class="body"', final_content)

    async def test_style_pack_quality_rejects_redesign_and_preserves_page(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"/><g id="layout-content"/><g id="main-content"/>
        </svg>"""
        dynamic_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <style>.body{fill:red}</style><g id="body" class="body"><text x="100" y="360" font-family="Arial">keep original</text></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            svg_path = root / "01_style.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
            )
            svg_path.write_text(apply_style_reference_shell(dynamic_svg, page), encoding="utf-8")
            before = svg_path.read_text(encoding="utf-8")
            writer = DummyWriter()
            repair_mock = AsyncMock(return_value=VALID_SVG)

            with patch(
                "core.ppt_generator.thought_to_ppt.svg_page_generators.node.llm_invoke",
                new=repair_mock,
            ):
                with self.assertRaisesRegex(ValueError, "dynamic SVG quality check failed"):
                    await svg_node.quality_check_node(
                        {"page_files": [str(svg_path)], "outline": [page]},
                        writer,
                    )

            after = svg_path.read_text(encoding="utf-8")
            report_path = svg_path.with_suffix(".quality-report.json")
            report = report_path.read_text(encoding="utf-8")

        repair_mock.assert_awaited_once()
        self.assertEqual(after, before)
        self.assertIn("keep original", after)
        self.assertIn("forbidden <style>", report.lower())
        self.assertIn("visible text content or order changed", report)


if __name__ == "__main__":
    unittest.main()
