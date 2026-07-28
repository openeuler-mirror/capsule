import unittest
from pathlib import Path

from scripts.pptx_to_style_pack import _style_pack_dir


class PptxToStylePackPathTests(unittest.TestCase):
    def test_session_id_maps_to_fixed_temporary_root(self):
        self.assertEqual(
            _style_pack_dir("agent_demo_20260715"),
            Path("/tmp/slidea/style-packs/agent_demo_20260715").resolve(),
        )

    def test_unsafe_session_ids_are_rejected(self):
        for value in ("", ".", "..", "../escape", "nested/path", "contains space"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _style_pack_dir(value)


if __name__ == "__main__":
    unittest.main()
