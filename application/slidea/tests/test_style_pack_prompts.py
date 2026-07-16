import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator import node as content_node
from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator import node as cover_node
from core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator import node as toc_node

# Prompt builders are intentionally internal workflow seams; these contract
# tests call them directly so failures identify the exact prompt layer.
# pylint: disable=protected-access


SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"></svg>'


class StylePackPromptTests(unittest.TestCase):
    def _reference_page(self, root: Path, page_type: PageType) -> PPTPage:
        reference = root / "reference.svg"
        reference.write_text(SVG, encoding="utf-8")
        return PPTPage(
            title="目标标题",
            abstract="目标摘要",
            type=page_type,
            index=0,
            style_reference_svg=str(reference),
        )

    def test_content_style_prompt_locks_shell_and_assets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            content_node, "_svg_prompt_header", return_value="BASE"
        ):
            page = self._reference_page(Path(tmp), PageType.CONTENT)
            prompt = content_node._build_content_prompt(
                query="主题",
                outline=[page],
                ppt_prompt="",
                template=SVG,
                language="中文",
                relevant_material="材料",
                page=page,
            )
        self.assertIn("精确注入参考页的 background、master-content、layout-content", prompt)
        self.assertIn("显式授权的前后层可复用装饰", prompt)
        self.assertIn("style-reference-only/", prompt)
        self.assertIn("images/style-pack/", prompt)
        self.assertIn("代码管理的继承外壳或已授权可复用装饰", prompt)
        self.assertIn("不得输出、重画、移动或覆盖这些固定元素", prompt)

    def test_special_page_style_prompts_remove_creative_redesign_permission(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            cover_node, "_svg_prompt_header", return_value="BASE"
        ), patch.object(toc_node, "_svg_prompt_header", return_value="BASE"):
            root = Path(tmp)
            cover = self._reference_page(root, PageType.COVER_THANKS)
            cover_prompt = cover_node._build_cover_prompt(
                query="主题", outline=[cover], save_dir="", ppt_prompt="",
                template=SVG, language="中文", page=cover,
            )
            thanks_prompt = cover_node._build_thanks_prompt(
                query="主题", outline=[cover], save_dir="", ppt_prompt="",
                template=SVG, language="中文", page=cover,
            )
            toc = self._reference_page(root, PageType.TOC)
            toc_prompt = toc_node._build_toc_prompt(
                ppt_prompt="", template=SVG, language="中文", page=toc,
            )

        for prompt in (cover_prompt, thanks_prompt, toc_prompt):
            self.assertNotIn("可以发挥创造力", prompt)
            self.assertIn("代码会精确注入", prompt)
            self.assertRegex(prompt, r"基本(?:一致|不变)")

    def test_builtin_special_page_prompt_keeps_original_creative_route(self):
        page = PPTPage(
            title="普通封面",
            abstract="摘要",
            type=PageType.COVER_THANKS,
            index=0,
        )
        with patch.object(cover_node, "_svg_prompt_header", return_value="BASE"):
            prompt = cover_node._build_cover_prompt(
                query="主题", outline=[page], save_dir="", ppt_prompt="",
                template=SVG, language="中文", page=page,
            )
        self.assertIn("这是内置模板示意", prompt)
        self.assertIn("可以发挥创造力", prompt)


if __name__ == "__main__":
    unittest.main()
