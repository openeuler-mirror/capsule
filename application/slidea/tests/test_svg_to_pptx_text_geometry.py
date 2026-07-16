import unittest
import xml.etree.ElementTree as ET

from core.ppt_generator.utils.svg_to_pptx.drawingml_context import ConvertContext
from core.ppt_generator.utils.svg_to_pptx.drawingml_converter import parse_transform
from core.ppt_generator.utils.svg_to_pptx.drawingml_elements import convert_text
from core.ppt_generator.utils.svg_to_pptx.drawingml_utils import parse_font_family


class SVGToPPTXTextGeometryTests(unittest.TestCase):
    def test_matrix_group_transform_is_preserved(self):
        self.assertEqual(
            parse_transform("matrix(0.94 0 0 0.958 -192.013 11.573)"),
            (-192.013, 11.573, 0.94, 0.958, 0.0),
        )

    def test_transform_list_order_is_preserved(self):
        self.assertEqual(
            parse_transform("translate(309.15 0) scale(-1 1)"),
            (309.15, 0.0, -1.0, 1.0, 0.0),
        )
        self.assertEqual(
            parse_transform("scale(2) translate(10)"),
            (20.0, 0.0, 2.0, 2.0, 0.0),
        )

    def test_noto_cjk_alias_maps_to_yahei(self):
        fonts = parse_font_family("Noto Sans CJK SC, sans-serif")
        self.assertEqual(fonts["ea"], "Microsoft YaHei")
        self.assertEqual(fonts["latin"], "Microsoft YaHei")

    def test_explicit_ooxml_source_font_wins_for_latin_text(self):
        fonts = parse_font_family(
            "Noto Sans CJK SC, Arial, sans-serif", source_font="Arial"
        )
        self.assertEqual(fonts["latin"], "Arial")
        self.assertEqual(fonts["ea"], "Microsoft YaHei")

    def test_yahei_source_font_covers_latin_and_east_asian_text(self):
        fonts = parse_font_family(
            "Noto Sans CJK SC, Arial, sans-serif", source_font="Microsoft YaHei"
        )
        self.assertEqual(fonts, {"latin": "Microsoft YaHei", "ea": "Microsoft YaHei"})

    def test_source_font_is_honored_without_css_font_stack(self):
        fonts = parse_font_family("", source_font="Arial")
        self.assertEqual(fonts, {"latin": "Arial", "ea": "Microsoft YaHei"})

    def test_text_length_and_font_metrics_contract_are_consumed(self):
        elem = ET.fromstring(
            '<text xmlns="http://www.w3.org/2000/svg" x="100" y="80" '
            'font-family="Noto Sans CJK SC" font-size="20" '
            'textLength="123.5" data-font-ascent="19" '
            'data-font-descent="5" letter-spacing="1">中文ABC</text>'
        )
        result = convert_text(elem, ConvertContext())
        self.assertIsNotNone(result)
        self.assertIn('spc="75"', result.xml)
        self.assertIn('<a:noAutofit/>', result.xml)
        self.assertNotIn('<a:spAutoFit/>', result.xml)
        self.assertAlmostEqual(result.bounds_emu[1] / 9525, 64.6, places=3)
        self.assertGreater(result.bounds_emu[2] - result.bounds_emu[0], round(123.5 * 9525))

    def test_tiny_legal_text_keeps_existing_baseline(self):
        elem = ET.fromstring(
            '<text xmlns="http://www.w3.org/2000/svg" x="10" y="30" '
            'font-size="11.333" data-font-ascent="13">Legal text</text>'
        )
        result = convert_text(elem, ConvertContext())
        self.assertAlmostEqual(result.bounds_emu[1] / 9525, 17.0, places=3)

    def test_svg_font_size_is_clamped_to_drawingml_schema_range(self):
        tiny = ET.fromstring(
            '<text xmlns="http://www.w3.org/2000/svg" x="10" y="10" '
            'font-size="1">.</text>'
        )
        huge = ET.fromstring(
            '<text xmlns="http://www.w3.org/2000/svg" x="10" y="10" '
            'font-size="10000">.</text>'
        )
        self.assertIn(' sz="100"', convert_text(tiny, ConvertContext()).xml)
        self.assertIn(' sz="400000"', convert_text(huge, ConvertContext()).xml)


if __name__ == "__main__":
    unittest.main()
