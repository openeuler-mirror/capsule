import base64
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core.utils.config import Settings


class RuntimeOptionTests(unittest.TestCase):
    def test_vlm_image_payload_mode_is_explicit(self):
        settings = Settings(VLM_IMAGE_INPUT_MODE="data_url")
        self.assertTrue(settings.use_data_url_for_vlm_images())

        settings = Settings(VLM_IMAGE_INPUT_MODE="raw_base64")
        self.assertFalse(settings.use_data_url_for_vlm_images())

    def test_runtime_switches_live_in_settings(self):
        settings = Settings(
            USE_CACHE=False,
            RESEARCH_MODE_FORCE="deep",
            DISABLE_EMBEDDING=True,
        )
        self.assertFalse(settings.USE_CACHE)
        self.assertEqual(settings.RESEARCH_MODE_FORCE, "deep")
        self.assertTrue(settings.DISABLE_EMBEDDING)

    def test_build_image_payload_uses_explicit_mode(self):
        from core.utils.image_payload import build_image_url

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "sample.png"
            Image.new("RGB", (2, 2), "white").save(image_path, "PNG")

            data_url = build_image_url(str(image_path), settings=Settings(VLM_IMAGE_INPUT_MODE="data_url"))
            raw_b64 = build_image_url(str(image_path), settings=Settings(VLM_IMAGE_INPUT_MODE="raw_base64"))

        self.assertTrue(data_url.startswith("data:image/png;base64,"))
        self.assertFalse(raw_b64.startswith("data:"))

    def test_build_image_payload_rejects_corrupt_png_before_vlm_call(self):
        from core.utils.image_payload import build_image_url

        corrupt_placeholder = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "placeholder.png"
            image_path.write_bytes(corrupt_placeholder)

            with self.assertRaisesRegex(ValueError, "cannot be decoded"):
                build_image_url(str(image_path))

    def test_build_image_payload_uses_decoded_format_not_file_extension(self):
        from core.utils.image_payload import build_image_url

        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "mislabelled.png"
            Image.new("RGB", (2, 2), "white").save(image_path, "JPEG")

            data_url = build_image_url(
                str(image_path),
                settings=Settings(VLM_IMAGE_INPUT_MODE="data_url"),
            )

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
