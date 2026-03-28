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


if __name__ == "__main__":
    unittest.main()
