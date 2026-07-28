import io
import sys
import unittest
from contextlib import redirect_stdout
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
        output = io.StringIO()
        with patch.object(sys, "argv", ["review_pr.py", "--help"]):
            with self.assertRaises(SystemExit) as raised, redirect_stdout(output):
                review_pr.main()

        self.assertEqual(raised.exception.code, 0)
        help_text = output.getvalue()
        self.assertNotIn("--model", help_text)
        self.assertNotIn("--api-base", help_text)
        self.assertNotIn("--api-key", help_text)


if __name__ == "__main__":
    unittest.main()
