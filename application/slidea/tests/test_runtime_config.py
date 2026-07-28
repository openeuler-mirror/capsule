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

    def test_default_llm_requires_all_fields(self):
        settings = Settings(
            DEFAULT_LLM_MODEL="text-model",
            DEFAULT_LLM_API_KEY="",
            DEFAULT_LLM_API_BASE_URL="",
        )

        self.assertEqual(
            settings.missing_default_llm_settings(),
            ["DEFAULT_LLM_API_KEY", "DEFAULT_LLM_API_BASE_URL"],
        )

    def test_default_vlm_requires_all_fields(self):
        settings = Settings(
            DEFAULT_VLM_MODEL="vision-model",
            DEFAULT_VLM_API_KEY="vision-key",
            DEFAULT_VLM_API_BASE_URL="https://vision.example/v1",
        )

        self.assertEqual(settings.missing_default_vlm_settings(), [])
        self.assertTrue(settings.has_default_vlm_config())


if __name__ == "__main__":
    unittest.main()
