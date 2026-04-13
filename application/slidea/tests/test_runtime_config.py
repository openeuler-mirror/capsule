import importlib
import os
import sys
import types
import unittest

from core.utils.config import Settings


class RuntimeConfigTests(unittest.TestCase):
    def test_embedding_settings_report_missing_fields_without_fake_defaults(self):
        settings = Settings(
            EMBEDDING_MODEL="text-embedding-demo",
            EMBEDDING_API_BASE_URL="",
            EMBEDDING_API_KEY="",
        )

        self.assertEqual(
            settings.missing_embedding_settings(),
            ["EMBEDDING_API_BASE_URL", "EMBEDDING_API_KEY"],
        )

    def test_tavily_requires_at_least_one_key(self):
        settings = Settings(TAVILY_API_KEYS=[])

        self.assertFalse(settings.has_tavily_search_config())

    def test_premium_defaults_are_present(self):
        settings = Settings(
            PREMIUM_LLM_MODEL="google/gemini-3.1-pro-preview",
            PREMIUM_LLM_API_BASE_URL="https://openrouter.ai/api/v1",
        )

        self.assertEqual(settings.PREMIUM_LLM_MODEL, "google/gemini-3.1-pro-preview")
        self.assertEqual(settings.PREMIUM_LLM_API_BASE_URL, "https://openrouter.ai/api/v1")

    def test_slidea_mode_is_validated(self):
        settings = Settings(SLIDEA_MODE="ECONOMIC")
        self.assertEqual(settings.get_slidea_mode(), "ECONOMIC")

        with self.assertRaises(ValueError):
            Settings(SLIDEA_MODE="fast").get_slidea_mode()

    def test_empty_slidea_mode_falls_back_to_economic_with_warning(self):
        settings = Settings(SLIDEA_MODE="")

        with self.assertLogs("slidea.config", level="WARNING") as logs:
            mode = settings.get_slidea_mode()

        self.assertEqual(mode, "ECONOMIC")
        self.assertTrue(any("Falling back to ECONOMIC mode" in message for message in logs.output))

    def test_invalid_slidea_mode_fails_during_module_import(self):
        original_mode = os.environ.get("SLIDEA_MODE")
        original_dotenv = sys.modules.get("dotenv")
        sys.modules.pop("core.utils.config", None)
        os.environ["SLIDEA_MODE"] = "fast"
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *args, **kwargs: False
        sys.modules["dotenv"] = fake_dotenv

        try:
            with self.assertRaises(ValueError):
                importlib.import_module("core.utils.config")
        finally:
            sys.modules.pop("core.utils.config", None)
            if original_dotenv is None:
                sys.modules.pop("dotenv", None)
            else:
                sys.modules["dotenv"] = original_dotenv
            if original_mode is None:
                os.environ.pop("SLIDEA_MODE", None)
            else:
                os.environ["SLIDEA_MODE"] = original_mode
            importlib.import_module("core.utils.config")


if __name__ == "__main__":
    unittest.main()
