import unittest

from core.utils.config import Settings


class ImageConfigTests(unittest.TestCase):
    def test_defaults_do_not_include_machine_specific_paths(self):
        settings = Settings()

        self.assertEqual(settings.IMAGE_GEN_PROVIDER, "api")
        self.assertEqual(settings.COMFYUI_PROMPT_UTILS_PATH, "")
        self.assertEqual(settings.COMFYUI_CLI_PATH, "")
        self.assertEqual(settings.COMFYUI_PYTHON_BIN, "")
        self.assertFalse(settings.is_image_generation_enabled())

    def test_comfyui_local_reports_missing_required_fields(self):
        settings = Settings(
            IMAGE_GEN_PROVIDER="comfyui_local",
            COMFYUI_URL="http://127.0.0.1:8188",
        )

        self.assertEqual(
            settings.missing_comfyui_local_settings(),
            [
                "COMFYUI_WORKFLOW",
                "COMFYUI_PROMPT_UTILS_PATH",
                "COMFYUI_CLI_PATH",
                "COMFYUI_PYTHON_BIN",
            ],
        )
        self.assertFalse(settings.is_image_generation_enabled())

    def test_api_provider_requires_all_remote_fields(self):
        settings = Settings(
            IMAGE_GEN_PROVIDER="api",
            IMG_GEN_MODEL="demo-model",
        )

        self.assertEqual(
            settings.missing_image_generation_settings(),
            ["IMG_GEN_API_KEY", "IMG_GEN_API_BASE_URL"],
        )
        self.assertFalse(settings.is_image_generation_enabled())

    def test_api_provider_is_enabled_when_remote_fields_are_complete(self):
        settings = Settings(
            IMAGE_GEN_PROVIDER="api",
            IMG_GEN_MODEL="demo-model",
            IMG_GEN_API_KEY="demo-key",
            IMG_GEN_API_BASE_URL="https://example.com/generate",
        )

        self.assertEqual(settings.missing_image_generation_settings(), [])
        self.assertTrue(settings.is_image_generation_enabled())

    def test_comfyui_provider_is_enabled_when_all_local_fields_are_complete(self):
        settings = Settings(
            IMAGE_GEN_PROVIDER="comfyui_local",
            COMFYUI_URL="http://127.0.0.1:8188",
            COMFYUI_WORKFLOW="/tmp/workflow.json",
            COMFYUI_PROMPT_UTILS_PATH="/tmp/prompt_utils.py",
            COMFYUI_CLI_PATH="/tmp/comfyui_cli.py",
            COMFYUI_PYTHON_BIN="/usr/bin/python3",
        )

        self.assertEqual(settings.missing_image_generation_settings(), [])
        self.assertTrue(settings.is_image_generation_enabled())


if __name__ == "__main__":
    unittest.main()
