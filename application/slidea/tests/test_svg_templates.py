import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.ppt_generator.utils.svg_pipeline.templates import (
    load_svg_template_content,
    load_svg_templates,
    select_svg_template,
)


class SVGTemplateTests(unittest.IsolatedAsyncioTestCase):
    def test_load_svg_templates_returns_name_description_pairs(self):
        templates = load_svg_templates()
        self.assertGreater(len(templates), 0)
        for item in templates:
            self.assertIn("name", item)
            self.assertIn("description", item)
        names = {item["name"] for item in templates}
        self.assertIn("common_light", names)

    def test_load_svg_template_content_returns_full_svg(self):
        content = load_svg_template_content("common_light")
        self.assertIn("<svg", content)
        self.assertIn("viewBox=\"0 0 1280 720\"", content)

    def test_load_svg_template_content_raises_when_missing(self):
        with self.assertRaises(FileNotFoundError):
            load_svg_template_content("nonexistent_template")

    async def test_select_svg_template_uses_llm_response(self):
        fake_response = SimpleNamespace(name="academic_blue", reason="academic")
        with patch(
            "core.ppt_generator.utils.svg_pipeline.templates.llm_invoke",
            new=AsyncMock(return_value=fake_response),
        ):
            chosen = await select_svg_template("学术报告", "outline placeholder")
        self.assertEqual(chosen, "academic_blue")

    async def test_select_svg_template_falls_back_when_llm_returns_unknown(self):
        fake_response = SimpleNamespace(name="not_a_real_template", reason="oops")
        with patch(
            "core.ppt_generator.utils.svg_pipeline.templates.llm_invoke",
            new=AsyncMock(return_value=fake_response),
        ):
            chosen = await select_svg_template("任意请求", "outline placeholder")

        valid_names = {item["name"] for item in load_svg_templates()}
        self.assertIn(chosen, valid_names)


if __name__ == "__main__":
    unittest.main()
