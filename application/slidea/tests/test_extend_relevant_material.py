"""Tests for extend_relevant_material_node.

Verifies that formula images bypass the TOP_N_IMAGE cap while search/AI/doc
images remain subject to it.
"""

import asyncio
import unittest

from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.node import (
    extend_relevant_material_node,
)


def _score_entry(path: str, score: float, description: str = "") -> dict:
    return {
        "img_description": description or f"desc for {path}",
        "score": score,
        "size": f"图片高度为100，宽度为200",
        "image_path": path,
    }


class ExtendRelevantMaterialTests(unittest.TestCase):
    def _state(self, **overrides):
        base = {
            "relevant_material": "BASE",
            "img_scores": [],
            "formula_image_paths": [],
        }
        base.update(overrides)
        return base

    def test_only_search_images_respect_top_n(self):
        """When no formulas are present, top-N caps the search image list."""
        state = self._state(
            img_scores=[
                _score_entry("/run/images/s1.png", 8.0),
                _score_entry("/run/images/s2.png", 7.0),
                _score_entry("/run/images/s3.png", 6.0),
                _score_entry("/run/images/s4.png", 5.0),
                _score_entry("/run/images/s5.png", 4.0),
                _score_entry("/run/images/s6.png", 3.0),
            ],
            formula_image_paths=[],
        )
        result = asyncio.run(extend_relevant_material_node(state))
        material = result["relevant_material"]
        # TOP_N_IMAGE default is 4 — only top-4 search images survive.
        for top in ("s1.png", "s2.png", "s3.png", "s4.png"):
            self.assertIn(top, material)
        self.assertNotIn("s5.png", material)
        self.assertNotIn("s6.png", material)

    def test_formulas_bypass_top_n(self):
        """All formula images must survive even when total > TOP_N_IMAGE."""
        state = self._state(
            img_scores=[
                _score_entry("/run/images/f1.png", 10.0, "数学公式：$a$"),
                _score_entry("/run/images/f2.png", 10.0, "数学公式：$b$"),
                _score_entry("/run/images/f3.png", 10.0, "数学公式：$c$"),
                _score_entry("/run/images/f4.png", 10.0, "数学公式：$d$"),
                _score_entry("/run/images/f5.png", 10.0, "数学公式：$e$"),
                _score_entry("/run/images/s1.png", 8.0),
                _score_entry("/run/images/s2.png", 7.0),
                _score_entry("/run/images/s3.png", 6.0),
                _score_entry("/run/images/s4.png", 5.0),
                _score_entry("/run/images/s5.png", 4.0),
            ],
            formula_image_paths=[
                "/run/images/f1.png",
                "/run/images/f2.png",
                "/run/images/f3.png",
                "/run/images/f4.png",
                "/run/images/f5.png",
            ],
        )
        result = asyncio.run(extend_relevant_material_node(state))
        material = result["relevant_material"]
        # All 5 formulas survive.
        for f in ("f1.png", "f2.png", "f3.png", "f4.png", "f5.png"):
            self.assertIn(f, material)
        # Search images still capped at TOP_N_IMAGE=4.
        for s in ("s1.png", "s2.png", "s3.png", "s4.png"):
            self.assertIn(s, material)
        self.assertNotIn("s5.png", material)

    def test_formulas_listed_before_search_images(self):
        """Formula materials come first in the assembled prompt text."""
        state = self._state(
            img_scores=[
                _score_entry("/run/images/s1.png", 9.0),
                _score_entry("/run/images/f1.png", 10.0, "数学公式：$x$"),
            ],
            formula_image_paths=["/run/images/f1.png"],
        )
        result = asyncio.run(extend_relevant_material_node(state))
        material = result["relevant_material"]
        f_pos = material.find("f1.png")
        s_pos = material.find("s1.png")
        self.assertGreater(f_pos, 0)
        self.assertGreater(s_pos, f_pos, "formula must appear before search image")

    def test_no_formulas_no_img_scores_returns_just_header(self):
        """Empty img_scores still produces a valid (header-only) material."""
        state = self._state()
        result = asyncio.run(extend_relevant_material_node(state))
        self.assertIn("可以使用的相关图片素材如下:", result["relevant_material"])

    def test_formula_paths_pointing_to_missing_img_scores_are_ignored(self):
        """If formula_image_paths lists a path with no matching img_score, skip silently."""
        state = self._state(
            img_scores=[
                _score_entry("/run/images/f1.png", 10.0),
                _score_entry("/run/images/s1.png", 8.0),
            ],
            formula_image_paths=[
                "/run/images/f1.png",
                "/run/images/ghost.png",  # no matching score entry
            ],
        )
        result = asyncio.run(extend_relevant_material_node(state))
        material = result["relevant_material"]
        self.assertIn("f1.png", material)
        self.assertIn("s1.png", material)
        self.assertNotIn("ghost.png", material)


if __name__ == "__main__":
    unittest.main()
