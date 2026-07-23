import unittest

from lxml import etree

from core.ppt_generator.utils.ooxml_svg.model import Element, Page, Rect
from core.ppt_generator.utils.ooxml_svg.renderer import SvgRenderer
from core.ppt_generator.utils.ooxml_svg.text import TextParser
from core.ppt_generator.utils.ooxml_svg.theme import Theme


class OoxmlSvgBulletTests(unittest.TestCase):
    def test_bullet_keeps_its_font_without_scaling_the_single_glyph(self):
        tx_body = etree.fromstring(
            b'''<p:txBody xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:bodyPr/><a:lstStyle/>
              <a:p>
                <a:pPr marL="285750" indent="-285750">
                  <a:buFont typeface="Arial"/><a:buChar char="&#x2022;"/>
                </a:pPr>
                <a:r>
                  <a:rPr sz="1400"><a:ea typeface="Microsoft YaHei"/></a:rPr>
                  <a:t>body</a:t>
                </a:r>
              </a:p>
            </p:txBody>'''
        )
        body = TextParser(Theme(None)).parse(tx_body, [], None, 1)
        self.assertEqual(body.paragraphs[0].bullet, "•")
        self.assertEqual(body.paragraphs[0].bullet_font_family, "Arial")

        page = Page(1, "synthetic", 12192000, 6858000)
        page.slide_elements.append(
            Element("bullet-shape", "Text", "shape", Rect(100000, 100000, 3000000, 800000), text=body)
        )
        root = etree.fromstring(SvgRenderer().tostring(page))
        texts = root.findall(".//svg:text", {"svg": "http://www.w3.org/2000/svg"})
        bullet = next(text for text in texts if text.text == "•")
        body_text = next(text for text in texts if text.text == "body")

        self.assertEqual(bullet.get("data-source-font"), "Arial")
        self.assertGreater(float(bullet.get("data-measured-width")), 0)
        self.assertIsNone(bullet.get("textLength"))
        self.assertIsNone(bullet.get("lengthAdjust"))
        self.assertEqual(body_text.get("lengthAdjust"), "spacingAndGlyphs")


if __name__ == "__main__":
    unittest.main()
