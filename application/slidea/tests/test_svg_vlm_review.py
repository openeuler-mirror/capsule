import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator import node as svg_page_node


ORIGINAL_SVG = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="120" font-family="Arial" font-size="40" fill="#111111">Original</text>
</svg>"""

FIXED_SVG = """```svg
<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="120" font-family="Arial" font-size="40" fill="#111111">Fixed</text>
</svg>
```"""


class SVGVLMReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_skips_when_vlm_route_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_page.svg"
            svg_path.write_text(ORIGINAL_SVG, encoding="utf-8")

            with patch.object(svg_page_node, "can_vlm_invoke_route", return_value=False), \
                 patch.object(svg_page_node, "screenshot_svg", new=AsyncMock(side_effect=AssertionError("unused"))):
                result = await svg_page_node._maybe_review_svg_with_vlm(str(svg_path), ORIGINAL_SVG)

        self.assertEqual(result, ORIGINAL_SVG)

    async def test_review_repairs_critical_svg_with_vlm(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_page.svg"
            svg_path.write_text(ORIGINAL_SVG, encoding="utf-8")
            judge_response = SimpleNamespace(
                content='{"has_issue": true, "severity": "critical", "issues": [{"type": "overlap", "location": "title", "desc": "text overlaps", "repair_instruction": "move title"}]}'
            )
            fix_response = SimpleNamespace(content=FIXED_SVG)

            with patch.object(svg_page_node, "can_vlm_invoke_route", return_value=True), \
                 patch.object(svg_page_node, "screenshot_svg", new=AsyncMock(return_value=str(Path(tmp_dir) / "shot.png"))), \
                 patch.object(svg_page_node, "build_image_url", return_value="data:image/png;base64,abc"), \
                 patch.object(svg_page_node, "vlm_raw_invoke", new=AsyncMock(side_effect=[judge_response, fix_response])):
                result = await svg_page_node._maybe_review_svg_with_vlm(str(svg_path), ORIGINAL_SVG)

            written = svg_path.read_text(encoding="utf-8")

        self.assertIn("Fixed", result)
        self.assertIn("Fixed", written)


if __name__ == "__main__":
    unittest.main()
