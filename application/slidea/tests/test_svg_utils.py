import unittest

from core.ppt_generator.utils.svg import extract_svg_content, repair_svg_content, validate_svg_content


class SVGUtilsTests(unittest.TestCase):
    def test_extract_svg_content_from_fenced_block(self):
        raw = "before\n```svg\n<svg viewBox=\"0 0 1280 720\"></svg>\n```\nafter"

        self.assertEqual(extract_svg_content(raw), '<svg viewBox="0 0 1280 720"></svg>')

    def test_repair_svg_content_replaces_named_entities_and_bare_ampersand(self):
        repaired = repair_svg_content(
            '<svg viewBox="0 0 1280 720"><text>R&D&nbsp;&mdash;</text></svg>'
        )

        self.assertIn("R&amp;D", repaired)
        self.assertIn(" —", repaired)
        validate_svg_content(repaired)

    def test_repair_svg_content_normalizes_rgba_fill_and_stroke(self):
        repaired = repair_svg_content(
            '<svg viewBox="0 0 1280 720"><rect fill="rgba(255, 0, 16, 0.5)" stroke="rgba(1,2,3,1)"/></svg>'
        )

        self.assertIn('fill="#FF0010" fill-opacity="0.5"', repaired)
        self.assertIn('stroke="#010203" stroke-opacity="1"', repaired)
        validate_svg_content(repaired)


if __name__ == "__main__":
    unittest.main()
