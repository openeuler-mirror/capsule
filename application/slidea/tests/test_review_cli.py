import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ci import review_pr


class ReviewCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_review_uses_the_default_model_reviewer_interface(self):
        created_with = []

        class FakeReviewer:
            def __init__(self, patch_file):
                created_with.append(patch_file)

            async def run_review(self):
                return SimpleNamespace(
                    architecture_compliant=True,
                    overall_score=8,
                    architecture_issues=[],
                    summary="ok",
                    syntax_errors=[],
                    logic_issues=[],
                    suggestions=[],
                )

        with patch.object(review_pr, "PatchReviewer", FakeReviewer):
            result = await review_pr.run_review("/tmp/review.patch")

        self.assertEqual(result, 0)
        self.assertEqual(created_with, ["/tmp/review.patch"])

    def test_help_does_not_expose_custom_model_options(self):
        help_text = review_pr.build_argument_parser().format_help()
        self.assertNotIn("--model", help_text)
        self.assertNotIn("--api-base", help_text)
        self.assertNotIn("--api-key", help_text)


if __name__ == "__main__":
    unittest.main()
