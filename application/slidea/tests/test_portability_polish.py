import unittest
from pathlib import Path
import shutil
import subprocess

from core.utils.config import Settings
from scripts.utils.preflight import run_preflight


class PortabilityPolishTests(unittest.TestCase):
    def test_preflight_respects_disabled_embedding(self):
        result = run_preflight(
            Settings(
                DISABLE_EMBEDDING=True,
                DEFAULT_LLM_MODEL="demo",
                DEFAULT_LLM_API_KEY="key",
                DEFAULT_LLM_API_BASE_URL="https://example.com",
            ),
            stages=["outline"],
            dry_run=True,
        )

        embedding_checks = [item for item in result["checks"] if item["name"] == "embedding"]
        self.assertEqual(len(embedding_checks), 1)
        self.assertEqual(embedding_checks[0]["status"], "warning")
        self.assertIn("deep research", embedding_checks[0]["message"].lower())

    def test_outline_stage_preflight_does_not_report_render_dependency_risks(self):
        result = run_preflight(
            Settings(
                DEFAULT_LLM_MODEL="demo",
                DEFAULT_LLM_API_KEY="key",
                DEFAULT_LLM_API_BASE_URL="https://example.com",
                DISABLE_EMBEDDING=True,
            ),
            stages=["outline"],
            dry_run=True,
        )

        browser_checks = [item for item in result["checks"] if item["name"] == "browser"]
        self.assertEqual(browser_checks, [])

    def test_readme_verification_lists_full_portability_suite(self):
        readme = Path(__file__).resolve().parents[1] / "README.md"
        content = readme.read_text(encoding="utf-8")

        self.assertIn("tests.test_preflight", content)
        self.assertIn("tests.test_runtime_options", content)
        self.assertIn("tests.test_portability_polish", content)

    def test_generate_slides_node_compiles_with_python_311(self):
        python311 = shutil.which("python3.11")
        if python311 is None:
            self.skipTest("python3.11 is not available")

        target = Path(__file__).resolve().parents[1] / "core" / "ppt_generator" / "node.py"
        result = subprocess.run(
            [python311, "-m", "py_compile", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"python3.11 failed to compile {target}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
