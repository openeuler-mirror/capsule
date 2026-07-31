"""Tests for extend_relevant_material_node.

Verifies that formula images bypass the TOP_N_IMAGE cap while search/AI/doc
images remain subject to it.

These tests monkeypatch settings.TOP_N_IMAGE to a known small value so they
don't depend on the production default (which may change over time).
"""

import asyncio
import unittest
from contextlib import contextmanager

from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.node import (
    extend_relevant_material_node,
)
from core.utils.config import settings


def _score_entry(path: str, score: float, description: str = "") -> dict:
    return {
        "img_description": description or f"desc for {path}",
        "score": score,
        "size": f"图片高度为100，宽度为200",
        "image_path": path,
    }


@contextmanager
def _top_n(n: int):
    """Temporarily override settings.TOP_N_IMAGE for the duration of a test."""
    original = settings.TOP_N_IMAGE
    settings.TOP_N_IMAGE = n
    try:
        yield
    finally:
        settings.TOP_N_IMAGE = original


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
        with _top_n(3):
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
            # top-3 survive (s1, s2, s3); s4..s6 dropped.
            for top in ("s1.png", "s2.png", "s3.png"):
                self.assertIn(top, material)
            for drop in ("s4.png", "s5.png", "s6.png"):
                self.assertNotIn(drop, material)

    def test_formulas_bypass_top_n(self):
        """All formula images must survive even when total > TOP_N_IMAGE."""
        with _top_n(3):
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
            # Search images still capped at top-N=3.
            for s in ("s1.png", "s2.png", "s3.png"):
                self.assertIn(s, material)
            for s in ("s4.png", "s5.png"):
                self.assertNotIn(s, material)

    def test_formulas_listed_before_search_images(self):
        """Formula materials come first in the assembled prompt text."""
        with _top_n(10):
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
        with _top_n(10):
            state = self._state()
            result = asyncio.run(extend_relevant_material_node(state))
            self.assertIn("可以使用的相关图片素材如下:", result["relevant_material"])

    def test_formula_paths_pointing_to_missing_img_scores_are_ignored(self):
        """If formula_image_paths lists a path with no matching img_score, skip silently."""
        with _top_n(10):
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

    def test_settings_top_n_image_default_is_10(self):
        """Guard against accidental changes to the production default."""
        # Read the default-constructed Settings instance's value (not the
        # potentially-mutated runtime singleton).
        from core.utils.config import Settings
        self.assertEqual(Settings().TOP_N_IMAGE, 10)


if __name__ == "__main__":
    unittest.main()
