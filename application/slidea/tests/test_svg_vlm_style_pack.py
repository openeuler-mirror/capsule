import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator import node
from core.ppt_generator.utils.style_pack import apply_style_reference_shell


def _dynamic_svg(text: str, *, include_header: bool = False) -> str:
    header = '<g id="header"><text x="40" y="80" font-family="Arial">model header</text></g>' if include_header else ""
    return f'''<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
      {header}<g id="body"><text x="100" y="240" font-family="Arial">{text}</text></g>
    </svg>'''


def _write_test_png(path: Path) -> None:
    Image.new("RGB", (2, 2), "white").save(path, "PNG")


class SVGStylePackVLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_style_pack_generation_retries_bodyless_composition_once(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"/><g id="layout-content"/>
          <g id="main-content"><g data-role="header"><text x="40" y="80" font-size="40" font-family="Arial">old title</text></g></g>
        </svg>"""
        invalid = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
        </svg>"""
        valid = _dynamic_svg("retried body")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            reference.write_text(reference_svg, encoding="utf-8")
            page = PPTPage(
                title="new title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
            )
            invoke = AsyncMock(side_effect=[invalid, valid])
            state = {
                "index": 0,
                "save_dir": str(root),
                "generate_ppt_prompt": "original generation prompt",
                "ppt_prompt": "",
                "page": page,
            }

            with patch.object(node, "llm_invoke", new=invoke):
                update = await node.generate_ppt_page_node(state)

            rejected = list((root / "style_generation_candidates").glob("*.svg"))

        self.assertEqual(invoke.await_count, 2)
        retry_prompt = invoke.await_args_list[1].args[1][0].content
        self.assertIn("Style-pack 合成门禁", retry_prompt)
        self.assertIn("retried body", update["content"])
        self.assertNotIn('<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>', update["content"])
        self.assertEqual(len(rejected), 1)

    async def test_no_style_pack_generation_keeps_original_single_attempt_route(self):
        original = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            page = PPTPage(
                title="plain",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
            )
            invoke = AsyncMock(return_value=original)
            state = {
                "index": 0,
                "save_dir": tmp_dir,
                "generate_ppt_prompt": "original generation prompt",
                "ppt_prompt": "",
                "page": page,
            }

            with patch.object(node, "llm_invoke", new=invoke):
                update = await node.generate_ppt_page_node(state)

        self.assertEqual(invoke.await_count, 1)
        self.assertEqual(update["content"], original)

    async def test_style_pack_vlm_receives_dynamic_content_and_restores_shell(self):
        reference_svg = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
          <g id="background"><rect width="1280" height="720" fill="#FFFFFF"/></g>
          <g id="master-content"><text x="40" y="690" font-family="Arial">fixed footer</text></g>
          <g id="layout-content"/>
          <g id="main-content"><g data-role="header"><text x="40" y="80" font-size="40" font-family="Arial">old title</text></g></g>
        </svg>"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            reference = root / "reference.svg"
            screenshot = root / "review.png"
            reference.write_text(reference_svg, encoding="utf-8")
            _write_test_png(screenshot)
            page = PPTPage(
                title="new title",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
                style_reference_svg=str(reference),
                style_reference_page_type="content",
                style_reference_guidance='{"geometry":"square corners"}',
            )
            composed = apply_style_reference_shell(_dynamic_svg("old body"), page)
            invoke = AsyncMock(return_value=SimpleNamespace(content=_dynamic_svg("new body")))
            state = {
                "index": 0,
                "content": composed,
                "page": page,
                "screenshot_path": str(screenshot),
                "judge_result": {"severity": "critical", "issues": []},
                "vlm_iteration": 0,
                "ppt_prompt": "",
            }

            with patch.object(node, "vlm_raw_invoke", new=invoke):
                update = await node.vlm_modify_node(state)

        prompt_text = invoke.await_args.args[1][0].content[0]["text"]
        self.assertIn("old body", prompt_text)
        self.assertNotIn("fixed footer", prompt_text)
        self.assertNotIn("data-slidea-style-shell", prompt_text)
        self.assertIn("square corners", prompt_text)
        self.assertIn("new body", update["content"])
        self.assertIn("fixed footer", update["content"])
        self.assertIn("slidea-style-background", update["content"])

    async def test_no_style_pack_vlm_still_receives_original_full_svg(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            screenshot = Path(tmp_dir) / "review.png"
            _write_test_png(screenshot)
            page = PPTPage(
                title="plain",
                abstract="摘要",
                type=PageType.CONTENT,
                index=0,
            )
            original = _dynamic_svg("old body", include_header=True)
            invoke = AsyncMock(return_value=SimpleNamespace(content=_dynamic_svg("new body", include_header=True)))
            state = {
                "index": 0,
                "content": original,
                "page": page,
                "screenshot_path": str(screenshot),
                "judge_result": {"severity": "critical", "issues": []},
                "vlm_iteration": 0,
                "ppt_prompt": "",
            }

            with patch.object(node, "vlm_raw_invoke", new=invoke):
                update = await node.vlm_modify_node(state)

        prompt_text = invoke.await_args.args[1][0].content[0]["text"]
        self.assertIn('<g id="header">', prompt_text)
        self.assertIn("old body", prompt_text)
        self.assertNotIn("固定外壳边界", prompt_text)
        self.assertIn("new body", update["content"])
        self.assertNotIn("data-slidea-style-shell", update["content"])


if __name__ == "__main__":
    unittest.main()
