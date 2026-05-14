import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from core.ppt_generator.utils.svg_pipeline.finalize_svg import finalize_svg_files


SVG_NS = "http://www.w3.org/2000/svg"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class SVGFinalizeTests(unittest.TestCase):
    def test_finalize_copies_to_svg_final_and_embeds_local_images(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            image_path = base / "images" / "pixel.png"
            image_path.parent.mkdir()
            image_path.write_bytes(PNG_BYTES)

            svg_output = base / "svg_output"
            svg_output.mkdir()
            svg_path = svg_output / "01_demo.svg"
            svg_path.write_text(
                f"""<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="{SVG_NS}">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<image href="{image_path}" x="10" y="10" width="20" height="20"/>
</svg>""",
                encoding="utf-8",
            )

            final_paths = finalize_svg_files([svg_path], base)

            final_path = Path(final_paths[0])
            self.assertEqual(final_path.parent.name, "svg_final")
            self.assertTrue(final_path.exists())

            root = ET.parse(final_path).getroot()
            image = next(elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == "image")
            self.assertTrue(image.get("href").startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
