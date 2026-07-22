import json
import tempfile
import unittest
from pathlib import Path

from scripts.utils.run_identity import resolve_run_by_session


class RunIdentityTests(unittest.TestCase):
    @staticmethod
    def _write_run(root: Path, run_id: str, session_id: str, *, resume: bool = False):
        run_dir = root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "session_id": session_id,
                    "resume": resume,
                }
            ),
            encoding="utf-8",
        )

    def test_unique_original_run_is_found(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_run(root, "original", "session")
            self._write_run(root, "resume-attempt", "session", resume=True)

            result = resolve_run_by_session(root, "session")

        self.assertEqual(result.status, "found")
        self.assertEqual(result.run_id, "original")

    def test_multiple_original_runs_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_run(root, "run-a", "reused-session")
            self._write_run(root, "run-b", "reused-session")

            result = resolve_run_by_session(root, "reused-session")

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.run_ids, ("run-a", "run-b"))


if __name__ == "__main__":
    unittest.main()
