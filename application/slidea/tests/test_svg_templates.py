import unittest

from core.ppt_generator.svg_pipeline.templates import (
    format_svg_template_for_prompt,
    load_svg_templates,
    select_svg_template,
)


class SVGTemplateTests(unittest.TestCase):
    def test_load_svg_templates_contains_general_default(self):
        templates = load_svg_templates()
        names = {item["name"] for item in templates}

        self.assertIn("general_modern", names)

    def test_select_svg_template_by_keyword(self):
        template = select_svg_template("为 AI 运维平台架构做一份汇报", [])

        self.assertEqual(template["name"], "ai_ops")

    def test_select_svg_template_accepts_explicit_name(self):
        template = select_svg_template("普通汇报", [], "mckinsey")

        self.assertEqual(template["name"], "mckinsey")

    def test_format_svg_template_for_prompt_includes_colors_and_guidance(self):
        template = select_svg_template("战略咨询分析", [])
        formatted = format_svg_template_for_prompt(template)

        self.assertIn("SVG Template", formatted)
        self.assertIn("Template Colors", formatted)
        self.assertIn("Layout Guidance", formatted)


if __name__ == "__main__":
    unittest.main()
