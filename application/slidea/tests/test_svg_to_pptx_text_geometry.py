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

    def test_noto_cjk_alias_maps_to_yahei(self):
        fonts = parse_font_family("Noto Sans CJK SC, sans-serif")
        self.assertEqual(fonts["ea"], "Microsoft YaHei")

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
        self.assertAlmostEqual(result.bounds_emu[1] / 9525, 61.0, places=3)
        self.assertGreater(result.bounds_emu[2] - result.bounds_emu[0], round(123.5 * 9525))


if __name__ == "__main__":
    unittest.main()
