import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType


class VlmFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_modify_ppt_page_skips_vlm_when_vlm_is_not_configured(self):
        from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator import node as base_node

        state = {
            "final_file_path": "/tmp/demo.html",
            "iteration": 1,
            "html_content": "<html>existing</html>",
        }

        with patch.object(base_node.settings, "has_default_vlm_config", return_value=False), patch.object(
            base_node.BrowserManager,
            "get_browser_context",
            side_effect=AssertionError("browser should not be used without VLM"),
        ):
            result = await base_node.modify_ppt_page_node(state)

        self.assertEqual(result["html_content"], "<html>existing</html>")

    async def test_distribute_images_via_vlm_keeps_outline_unchanged_without_vlm(self):
        from core.ppt_generator.thought_to_ppt.page_generators import node as page_node

        outline = [
            PPTPage(
                title="Page A",
                abstract="A",
                type=PageType.CONTENT,
                index=0,
                reference_images=["a.png", "b.png"],
            ),
            PPTPage(
                title="Page B",
                abstract="B",
                type=PageType.CONTENT,
                index=1,
                reference_images=["a.png", "b.png"],
            ),
        ]

        with patch.object(page_node.settings, "has_default_vlm_config", return_value=False):
            result = await page_node.distribute_images_via_vlm(outline)

        self.assertEqual(result[0].reference_images, ["a.png", "b.png"])
        self.assertEqual(result[1].reference_images, ["a.png", "b.png"])
        self.assertIsNot(result[0], outline[0])

    async def test_get_img_score_uses_fallback_without_vlm(self):
        pil_module = types.ModuleType("PIL")

        class FakeImageFile:
            width = 20
            height = 10

            def verify(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        image_module = types.SimpleNamespace(open=lambda _path: FakeImageFile())
        pil_module.Image = image_module

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "demo.png"
            image_path.write_bytes(b"not-a-real-image")

            with patch.dict(sys.modules, {"PIL": pil_module}):
                content_node = importlib.import_module(
                    "core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.node"
                )
                content_node = importlib.reload(content_node)

                with patch.object(content_node.settings, "has_default_vlm_config", return_value=False):
                    result = await content_node.get_img_score_node(
                        {
                            "relevant_material": "demo material",
                            "image_path": str(image_path),
                            "image_description": "search result description",
                        }
                    )

        score = result["img_scores"][0]
        self.assertIsNotNone(score)
        self.assertEqual(score["img_description"], "search result description")
        self.assertEqual(score["score"], 5.0)
        self.assertEqual(score["size"], "图片高度为10，宽度为20")
        self.assertEqual(score["image_path"], str(image_path))


if __name__ == "__main__":
    unittest.main()
