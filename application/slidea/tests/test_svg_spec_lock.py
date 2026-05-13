import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.ppt_generator.svg_pipeline.spec_lock import build_svg_spec_lock_content, write_svg_spec_lock


class SVGSpecLockTests(unittest.TestCase):
    def test_build_svg_spec_lock_content_includes_canvas_palette_and_pages(self):
        outline = [
            SimpleNamespace(index=0, title="Cover", type=SimpleNamespace(name="COVER_THANKS")),
            SimpleNamespace(index=1, title="Market", type=SimpleNamespace(name="CONTENT")),
        ]

        content = build_svg_spec_lock_content(
            query="demo request",
            topic="Demo Topic",
            language="中文",
            outline=outline,
            template={
                "name": "mckinsey",
                "label": "McKinsey Style",
                "font_family": "Arial, Microsoft YaHei, sans-serif",
                "colors": {"primary": "#005587", "background": "#FFFFFF"},
                "layout_guidance": "Use consulting-style whitespace.",
            },
        )

        self.assertIn("viewBox: 0 0 1280 720", content)
        self.assertIn("name: mckinsey", content)
        self.assertIn("font_family: Arial, Microsoft YaHei, sans-serif", content)
        self.assertIn("primary: #005587", content)
        self.assertIn("index=0; type=COVER_THANKS; rhythm=breathing", content)
        self.assertIn("index=1; type=CONTENT; rhythm=dense", content)

    def test_write_svg_spec_lock_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            content = write_svg_spec_lock(
                tmp_dir,
                query="demo",
                topic="Topic",
                language="英文",
                outline=[],
            )
            path = Path(tmp_dir) / "spec_lock.md"

            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
