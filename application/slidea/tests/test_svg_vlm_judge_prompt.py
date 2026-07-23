import unittest
from pathlib import Path


class SvgVlmJudgePromptTests(unittest.TestCase):
    def test_prompt_requires_missing_glyph_and_duplicate_text_detection(self):
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "ppt_generator"
            / "assets"
            / "prompts"
            / "svg_vlm_judge_prompt.txt"
        )
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertIn("glyph_missing", prompt)
        self.assertIn("duplicate_text_overlap", prompt)
        self.assertIn("不同语言", prompt)
        self.assertIn("方框", prompt)


if __name__ == "__main__":
    unittest.main()
