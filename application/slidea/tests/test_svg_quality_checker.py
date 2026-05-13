import tempfile
import unittest
from pathlib import Path

from core.ppt_generator.utils.svg_pipeline.quality_checker import (
    check_svg_file,
    format_quality_issues,
    raise_for_svg_quality,
)


VALID_SVG = """<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<text x="80" y="120" font-family="Microsoft YaHei, Arial, sans-serif" font-size="40" fill="#111111">Demo</text>
</svg>"""


class SVGQualityCheckerTests(unittest.TestCase):
    def test_valid_svg_passes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_demo.svg"
            svg_path.write_text(VALID_SVG, encoding="utf-8")

            result = check_svg_file(svg_path)

        self.assertTrue(result["passed"])
        self.assertEqual(result["errors"], [])

    def test_forbidden_style_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_bad.svg"
            svg_path.write_text(
                VALID_SVG.replace("<rect", "<style>.x{fill:red}</style><rect"),
                encoding="utf-8",
            )

            result = check_svg_file(svg_path)

        self.assertFalse(result["passed"])
        self.assertTrue(any("style" in error.lower() for error in result["errors"]))

    def test_missing_image_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_image.svg"
            svg_path.write_text(
                VALID_SVG.replace(
                    "</svg>",
                    '<image href="missing.png" x="10" y="10" width="100" height="80"/></svg>',
                ),
                encoding="utf-8",
            )

            result = check_svg_file(svg_path)

        self.assertFalse(result["passed"])
        self.assertTrue(any("image file not found" in error.lower() for error in result["errors"]))

    def test_raise_for_svg_quality_formats_failures(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            svg_path = Path(tmp_dir) / "01_bad.svg"
            svg_path.write_text("<svg><style></style></svg>", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SVG quality check failed") as ctx:
                raise_for_svg_quality([svg_path])
            formatted = format_quality_issues([check_svg_file(svg_path)])

        self.assertIn("01_bad.svg", str(ctx.exception))
        self.assertIn("ERROR", formatted)


if __name__ == "__main__":
    unittest.main()
