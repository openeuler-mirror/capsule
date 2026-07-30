import asyncio
import json
import os
import tempfile
import unittest

from PIL import Image

from core.ppt_generator.utils.formula import (
    FORMULA_RENDER_COLOR,
    FORMULA_RENDER_DPI,
    MAX_UPSCALE,
    append_formula_record_sync,
    is_cjk_contaminated,
    measure_png,
    render_formula,
    svg_size_for_pixels,
)


class FormulaRendererTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.save_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_basic_render_returns_valid_png(self):
        path, dims = asyncio.run(
            render_formula("\\frac{a}{b}", self.save_dir)
        )
        self.assertIsNotNone(path)
        self.assertIsNotNone(dims)
        self.assertTrue(os.path.isfile(path))
        svg_w, svg_h = dims
        self.assertGreater(svg_w, 0)
        self.assertGreater(svg_h, 0)
        # Returned dims are SVG user units (96-DPI equivalent), not raw pixels.
        # For DPI=200, scale factor = 96/200 = 0.48, so SVG dims < raw pixels.
        pixel_w, pixel_h = measure_png(path)
        self.assertGreater(pixel_w, svg_w)
        self.assertGreater(pixel_h, svg_h)
        expected_w, expected_h = svg_size_for_pixels(pixel_w, pixel_h, FORMULA_RENDER_DPI)
        self.assertEqual((svg_w, svg_h), (expected_w, expected_h))

    def test_svg_size_for_pixels_basic(self):
        # 200 DPI: 200 px == 1 inch == 96 SVG units
        self.assertEqual(svg_size_for_pixels(200, 200, 200), (96, 96))
        # 96 DPI: pixels == SVG units
        self.assertEqual(svg_size_for_pixels(1280, 720, 96), (1280, 720))
        # 300 DPI: 300 px == 1 inch == 96 SVG units
        self.assertEqual(svg_size_for_pixels(300, 300, 300), (96, 96))
        # Non-integer results round to nearest int.
        self.assertEqual(svg_size_for_pixels(100, 100, 200), (48, 48))

    def test_svg_size_for_pixels_zero_dpi_falls_back(self):
        # dpi=0 should not raise; falls back to FORMULA_RENDER_DPI.
        result_w, result_h = svg_size_for_pixels(200, 100, 0)
        expected_w, expected_h = svg_size_for_pixels(200, 100, FORMULA_RENDER_DPI)
        self.assertEqual((result_w, result_h), (expected_w, expected_h))

    def test_cjk_detection(self):
        self.assertFalse(is_cjk_contaminated("\\frac{a}{b}"))
        self.assertFalse(is_cjk_contaminated("E = mc^2"))
        self.assertTrue(is_cjk_contaminated("其中 a = 1"))
        self.assertTrue(is_cjk_contaminated("\\frac{其中}{b}"))
        # Hiragana / Katakana / Hangul also count.
        self.assertTrue(is_cjk_contaminated("ふ"))
        self.assertTrue(is_cjk_contaminated("カ"))
        self.assertTrue(is_cjk_contaminated("한국"))

    def test_max_upscale_constant(self):
        # The hard cap on display size relative to natural size. Update both
        # the constant and any prompt/docs that mention the cap together.
        self.assertEqual(MAX_UPSCALE, 1.3)

    def test_cjk_source_is_skipped(self):
        path, dims = asyncio.run(
            render_formula("其中 a = 1", self.save_dir)
        )
        self.assertIsNone(path)
        self.assertIsNone(dims)

    def test_cache_hit_returns_same_path(self):
        latex = "\\sum_{i=1}^{n} x_i"
        path1, dims1 = asyncio.run(render_formula(latex, self.save_dir))
        path2, dims2 = asyncio.run(render_formula(latex, self.save_dir))
        self.assertEqual(path1, path2)
        self.assertEqual(dims1, dims2)
        # Cache file is the only PNG under images/.
        images_dir = os.path.join(self.save_dir, "images")
        files = [f for f in os.listdir(images_dir) if f.endswith(".png")]
        self.assertEqual(len(files), 1)

    def test_cache_distinct_for_different_color_or_dpi(self):
        latex = "E = mc^2"
        path_black, _ = asyncio.run(
            render_formula(latex, self.save_dir, color="#000000")
        )
        path_white, _ = asyncio.run(
            render_formula(latex, self.save_dir, color="#FFFFFF")
        )
        self.assertNotEqual(path_black, path_white)

    def test_invalid_latex_returns_none_without_raising(self):
        # mathtext raises ValueError for unsupported control sequences.
        path, dims = asyncio.run(
            render_formula("\\begn{notarealenv}", self.save_dir)
        )
        self.assertIsNone(path)
        self.assertIsNone(dims)

    def test_surrounding_dollar_signs_are_stripped(self):
        # Both forms should hit the same cache key.
        path_bare, _ = asyncio.run(render_formula("a + b", self.save_dir))
        path_dollar, _ = asyncio.run(render_formula("$a + b$", self.save_dir))
        path_double, _ = asyncio.run(render_formula("$$a + b$$", self.save_dir))
        self.assertEqual(path_bare, path_dollar)
        self.assertEqual(path_bare, path_double)

    def test_rendered_png_has_alpha_channel(self):
        # Transparent background means RGBA with alpha=0 pixels; otherwise the
        # formula would carry a visible white box on colored panels.
        path, _ = asyncio.run(render_formula("\\sqrt{x}", self.save_dir))
        with Image.open(path) as img:
            bands = img.getbands()
            self.assertIn("A", bands)  # RGBA or LA
            # The alpha channel is the last band in an RGBA image.
            alpha_band = img.split()[-1]
            alpha_min, _ = alpha_band.getextrema()
            self.assertEqual(alpha_min, 0)  # at least one fully transparent pixel

    def test_empty_or_disabled_returns_none(self):
        # Empty source.
        path, dims = asyncio.run(render_formula("", self.save_dir))
        self.assertIsNone(path)
        self.assertIsNone(dims)
        # Whitespace only.
        path, dims = asyncio.run(render_formula("   ", self.save_dir))
        self.assertIsNone(path)
        self.assertIsNone(dims)

    def test_measure_png_handles_missing_file(self):
        self.assertEqual(measure_png("/nonexistent/formula.png"), (0, 0))


class FormulaLedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_creates_file(self):
        log_path = os.path.join(self.run_dir, "formulas.json")
        self.assertFalse(os.path.exists(log_path))
        append_formula_record_sync(self.run_dir, {
            "latex": "E = mc^2",
            "path": "/abs/path/formula.png",
            "width": 240,
            "height": 80,
        })
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["latex"], "E = mc^2")
        self.assertIn("rendered_at", data[0])

    def test_append_extends_existing_file(self):
        log_path = os.path.join(self.run_dir, "formulas.json")
        # Seed with one record.
        append_formula_record_sync(self.run_dir, {"latex": "a"})
        append_formula_record_sync(self.run_dir, {"latex": "b"})
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 2)
        self.assertEqual([r["latex"] for r in data], ["a", "b"])

    def test_append_swallows_corrupted_existing_file(self):
        log_path = os.path.join(self.run_dir, "formulas.json")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        # Should not raise; should start a fresh list.
        append_formula_record_sync(self.run_dir, {"latex": "c"})
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)

    def test_append_with_empty_run_dir_is_noop(self):
        # Should not raise even with an empty path.
        append_formula_record_sync("", {"latex": "x"})
        # No file should have been created anywhere we care about.


if __name__ == "__main__":
    unittest.main()
