import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.ppt_generator.thought_to_ppt.svg_page_generators import node as svg_node


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
                '<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg"><style>.x{fill:red}</style></svg>',
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
        self.assertEqual(result["svg_quality_report"][0]["passed"], True)
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


if __name__ == "__main__":
    unittest.main()
