import tempfile
import unittest
import zipfile
from pathlib import Path

from core.ppt_generator.utils.svg_export import svgs_to_pptx


SVG_PAGE = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="120" font-family="Microsoft YaHei, Arial, sans-serif" font-size="44" fill="#111111">SVG export smoke</text>
<rect x="80" y="180" width="420" height="120" fill="#E8F1FF" stroke="#2563EB" stroke-width="3"/>
<text x="110" y="250" font-family="Microsoft YaHei, Arial, sans-serif" font-size="28" fill="#2563EB">native editable shapes</text>
</svg>"""

GROUPED_SVG_PAGE = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<g id="background"><rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/></g>
<g id="main-message">
  <rect x="80" y="120" width="420" height="160" rx="12" fill="#E8F1FF"/>
  <text x="110" y="200" font-family="Microsoft YaHei, Arial, sans-serif" font-size="28">grouped content</text>
</g>
</svg>"""


class SVGExportSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_svgs_to_pptx_creates_openable_pptx(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            svg_path = base / "01_demo.svg"
            svg_path.write_text(SVG_PAGE, encoding="utf-8")

            pdf_path, pptx_path = await svgs_to_pptx([str(svg_path)], str(base), "smoke")
            pptx = Path(pptx_path)

            self.assertEqual(pdf_path, "")
            self.assertTrue(pptx.exists())
            self.assertGreater(pptx.stat().st_size, 0)

            with zipfile.ZipFile(pptx) as archive:
                names = set(archive.namelist())
                self.assertIn("ppt/presentation.xml", names)
                self.assertIn("ppt/slides/slide1.xml", names)
                slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")

        self.assertIn("SVG export smoke", slide_xml)
        self.assertIn("native editable shapes", slide_xml)

    async def test_svgs_to_pptx_rejects_empty_input(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(Exception, "SVG"):
                await svgs_to_pptx([], tmp_dir, "empty")

    async def test_svgs_to_pptx_preserves_semantic_groups(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            svg_path = base / "01_grouped.svg"
            svg_path.write_text(GROUPED_SVG_PAGE, encoding="utf-8")

            _, pptx_path = await svgs_to_pptx([str(svg_path)], str(base), "grouped")
            with zipfile.ZipFile(pptx_path) as archive:
                slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")

        self.assertIn("<p:grpSp>", slide_xml)
        self.assertIn('prst="roundRect"', slide_xml)


if __name__ == "__main__":
    unittest.main()
