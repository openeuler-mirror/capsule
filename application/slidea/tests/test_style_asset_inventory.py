import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.ppt_generator.utils.style_asset_inventory import build_style_asset_inventory


class StyleAssetInventoryTests(unittest.TestCase):
    def test_inventory_reports_candidates_without_authorizing_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            reference = pack / "reference"
            images = reference / "images"
            images.mkdir(parents=True)
            Image.new("RGBA", (400, 80), (255, 0, 0, 180)).save(images / "decor.png")
            Image.new("RGB", (800, 500), (20, 30, 40)).save(images / "photo.jpg")
            Image.new("RGBA", (100, 40), (0, 0, 0, 120)).save(images / "master.png")
            for number, y in ((1, 620), (2, 620)):
                (reference / f"slide{number}.svg").write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
                    '<g id="background"/><g id="master-content">'
                    '<image href="images/master.png" x="0" y="0" width="100" height="40"/>'
                    '</g><g id="layout-content"/>'
                    '<g id="main-content">'
                    f'<g id="decor-{number}"><image href="images/decor.png" '
                    f'x="0" y="{y}" width="1280" height="100"/></g>'
                    "</g></svg>",
                    encoding="utf-8",
                )
            (reference / "slide3.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
                '<g id="background"/><g id="master-content"/><g id="layout-content"/>'
                '<g id="main-content"><g id="photo"><image href="images/photo.jpg" '
                'x="240" y="110" width="800" height="500"/></g></g>'
                "</svg>",
                encoding="utf-8",
            )

            inventory = build_style_asset_inventory(reference, pack)
            by_path = {item["path"]: item for item in inventory["assets"]}

        decor = by_path["reference/images/decor.png"]
        photo = by_path["reference/images/photo.jpg"]
        master = by_path["reference/images/master.png"]
        self.assertEqual(decor["page_count"], 2)
        self.assertTrue(decor["signals"]["repeated_across_pages"])
        self.assertTrue(decor["signals"]["has_alpha"])
        self.assertTrue(decor["signals"]["wide_strip"])
        self.assertTrue(decor["candidate"])
        self.assertFalse(photo["candidate"])
        self.assertTrue(master["signals"]["automatic_fixed_layer"])
        self.assertEqual(master["main_content_page_count"], 0)
        self.assertFalse(master["needs_explicit_authorization"])
        self.assertFalse(master["candidate"])
        self.assertNotIn("authorized", decor)


if __name__ == "__main__":
    unittest.main()
