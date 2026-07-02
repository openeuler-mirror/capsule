import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from core.ppt_generator.utils.svg_pipeline.finalize_svg import (
    embed_local_images_in_file,
    embed_local_images_in_content,
)


SVG_NS = "http://www.w3.org/2000/svg"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class EmbedLocalImagesTests(unittest.TestCase):
    def test_embed_local_images_in_file_rewrites_relative_href_to_data_uri(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            images_dir = base / "images"
            images_dir.mkdir()
            image_path = images_dir / "pixel.png"
            image_path.write_bytes(PNG_BYTES)

            svg_path = base / "page.svg"
            svg_path.write_text(
                f"""<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="{SVG_NS}">
<rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
<image href="images/pixel.png" x="10" y="10" width="20" height="20"/>
</svg>""",
                encoding="utf-8",
            )

            changed = embed_local_images_in_file(svg_path, base)
            self.assertTrue(changed)

            root = ET.parse(svg_path).getroot()
            image = next(elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == "image")
            self.assertTrue(image.get("href").startswith("data:image/png;base64,"))

    def test_embed_local_images_in_file_returns_false_when_no_local_refs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            svg_path = base / "page.svg"
            svg_path.write_text(
                f"""<svg width="1280" height="720" viewBox="0 0 1280 720" xmlns="{SVG_NS}">
<image href="https://example.com/foo.png" x="10" y="10" width="20" height="20"/>
</svg>""",
                encoding="utf-8",
            )

            changed = embed_local_images_in_file(svg_path, base)
            self.assertFalse(changed)

    def test_embed_local_images_in_content_returns_inlined_string(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            images_dir = base / "images"
            images_dir.mkdir()
            image_path = images_dir / "pixel.png"
            image_path.write_bytes(PNG_BYTES)

            svg_content = (
                f'<svg xmlns="{SVG_NS}" width="100" height="100">'
                '<image href="images/pixel.png" x="0" y="0" width="10" height="10"/>'
                '</svg>'
            )
            inlined = embed_local_images_in_content(svg_content, base)
            self.assertIn("data:image/png;base64,", inlined)


if __name__ == "__main__":
    unittest.main()
