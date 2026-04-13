import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.utils.config import Settings


class PreflightTests(unittest.TestCase):
    def test_default_llm_settings_are_reported_missing(self):
        settings = Settings(
            DEFAULT_LLM_MODEL="",
            DEFAULT_LLM_API_KEY="",
            DEFAULT_LLM_API_BASE_URL="",
        )

        self.assertEqual(
            settings.missing_default_llm_settings(),
            [
                "DEFAULT_LLM_MODEL",
                "DEFAULT_LLM_API_KEY",
                "DEFAULT_LLM_API_BASE_URL",
            ],
        )

    def test_premium_llm_settings_are_reported_missing(self):
        settings = Settings(
            PREMIUM_LLM_MODEL="",
            PREMIUM_LLM_API_KEY="",
            PREMIUM_LLM_API_BASE_URL="",
        )

        self.assertEqual(
            settings.missing_premium_llm_settings(),
            [
                "PREMIUM_LLM_MODEL",
                "PREMIUM_LLM_API_KEY",
                "PREMIUM_LLM_API_BASE_URL",
            ],
        )

    def test_default_vlm_is_optional(self):
        settings = Settings(
            DEFAULT_VLM_MODEL="",
            DEFAULT_VLM_API_KEY="",
            DEFAULT_VLM_API_BASE_URL="",
        )

        self.assertFalse(settings.has_default_vlm_config())

    def test_browser_preflight_reports_missing_playwright(self):
        from scripts.utils.preflight import check_browser_runtime

        with patch("scripts.utils.preflight.importlib.util.find_spec", return_value=None):
            result = check_browser_runtime()

        self.assertEqual(result["status"], "warning")
        self.assertIn("playwright", result["message"].lower())

    def test_browser_preflight_reports_unlaunchable_chromium(self):
        from scripts.utils.preflight import check_browser_runtime

        with patch("scripts.utils.preflight.importlib.util.find_spec", return_value=object()), \
             patch(
                 "scripts.utils.preflight.subprocess.run",
                 return_value=SimpleNamespace(
                     returncode=1,
                     stderr="[pid=15][err] FATAL: sandbox_host_linux.cc:41 Operation not permitted\n  - [pid=15] <gracefully close end>",
                     stdout="",
                 ),
             ):
            result = check_browser_runtime()

        self.assertEqual(result["status"], "warning")
        self.assertIn("operation not permitted", result["message"].lower())

    def test_browser_preflight_reports_launchable_runtime(self):
        from scripts.utils.preflight import check_browser_runtime

        with patch("scripts.utils.preflight.importlib.util.find_spec", return_value=object()), \
             patch(
                 "scripts.utils.preflight.subprocess.run",
                 return_value=SimpleNamespace(returncode=0, stderr="", stdout=""),
             ):
            result = check_browser_runtime()

        self.assertEqual(result["status"], "ok")
        self.assertIn("html-to-pdf", result["message"].lower())

    def test_browser_smoke_test_uses_active_python_executable_without_resolving(self):
        from scripts.utils.preflight import _run_browser_smoke_test

        with patch.object(sys, "executable", "/tmp/project/.venv/bin/python"), \
             patch(
                 "scripts.utils.preflight.subprocess.run",
                 return_value=SimpleNamespace(returncode=0, stderr="", stdout=""),
             ) as mock_run:
            ok, _message = _run_browser_smoke_test()

        self.assertTrue(ok)
        self.assertEqual(mock_run.call_args.args[0][0], "/tmp/project/.venv/bin/python")

    def test_env_setup_requires_completed_bootstrap_marker(self):
        from scripts.utils.preflight import check_env_setup

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_env = Path(tmp_dir) / ".env"

            with patch("scripts.utils.preflight.env_file", fake_env):
                missing = check_env_setup()
                self.assertEqual(missing["status"], "error")
                self.assertIn("scripts/install/install.py", missing["message"])

                fake_env.write_text("\n", encoding="utf-8")
                incomplete = check_env_setup(Settings(SETUP_COMPLETED=False))
                self.assertEqual(incomplete["status"], "warning")
                self.assertIn("SETUP_COMPLETED=true", incomplete["message"])

                fake_env.write_text("SETUP_COMPLETED=true\n", encoding="utf-8")
                completed = check_env_setup(Settings(SETUP_COMPLETED=True))
                self.assertEqual(completed["status"], "ok")

    def test_runtime_python_requires_project_venv(self):
        from scripts.utils.preflight import check_runtime_python

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_root = Path(tmp_dir)
            fake_venv_python = fake_root / ".venv" / "bin" / "python"
            external_python = fake_root / "toolchain" / "python3.11"
            fake_venv_python.parent.mkdir(parents=True, exist_ok=True)
            external_python.parent.mkdir(parents=True, exist_ok=True)
            external_python.write_text("", encoding="utf-8")
            fake_venv_python.symlink_to(external_python)

            with patch("scripts.utils.preflight.app_base_dir", fake_root):
                with patch.object(sys, "executable", str(fake_root / "python")):
                    outside = check_runtime_python()
                    self.assertEqual(outside["status"], "warning")
                    self.assertIn(".venv", outside["message"])

                with patch.object(sys, "prefix", str(fake_venv_python.parent.parent)):
                    with patch.object(sys, "executable", str(fake_venv_python)):
                        inside_via_prefix = check_runtime_python()
                        self.assertEqual(inside_via_prefix["status"], "ok")

                with patch.object(sys, "executable", str(fake_venv_python)):
                    inside = check_runtime_python()
                    self.assertEqual(inside["status"], "ok")

    def test_render_stage_preflight_reports_missing_libreoffice_as_warning(self):
        from scripts.utils.preflight import run_preflight

        with patch("scripts.utils.preflight.check_env_setup", return_value={"name": "env_setup", "status": "ok", "message": "ok"}), \
             patch("scripts.utils.preflight.check_runtime_python", return_value={"name": "runtime_python", "status": "ok", "message": "ok"}), \
             patch("scripts.utils.preflight.check_browser_runtime", return_value={"name": "browser", "status": "ok", "message": "ok"}), \
             patch("scripts.utils.preflight.check_libreoffice_runtime", return_value={"name": "libreoffice", "status": "warning", "message": "missing"}):
            result = run_preflight(
                Settings(
                    SLIDEA_MODE="ECONOMIC",
                    DEFAULT_LLM_MODEL="demo",
                    DEFAULT_LLM_API_KEY="key",
                    DEFAULT_LLM_API_BASE_URL="https://example.com",
                    DISABLE_EMBEDDING=True,
                ),
                stages=["render"],
                dry_run=False,
            )

        libreoffice_checks = [item for item in result["checks"] if item["name"] == "libreoffice"]
        self.assertEqual(len(libreoffice_checks), 1)
        self.assertEqual(libreoffice_checks[0]["status"], "warning")

    def test_libreoffice_preflight_checks_environment_command_on_windows(self):
        from scripts.utils.preflight import _iter_libreoffice_candidates

        with patch("scripts.utils.preflight.platform.system", return_value="Windows"), \
             patch("scripts.utils.preflight.shutil.which", side_effect=lambda candidate: r"C:\LibreOffice\program\soffice.exe" if candidate == "soffice.exe" else None), \
             patch.dict("scripts.utils.preflight.os.environ", {"ProgramFiles": r"C:\Program Files"}, clear=True):
            candidates = _iter_libreoffice_candidates()

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0], Path(r"C:\LibreOffice\program\soffice.exe"))

    def test_render_stage_preflight_uses_agent_facing_order(self):
        from scripts.utils.preflight import run_preflight

        with patch("scripts.utils.preflight.check_env_setup", return_value={"name": "env_setup", "status": "ok", "message": "ok"}), \
             patch("scripts.utils.preflight.check_runtime_python", return_value={"name": "runtime_python", "status": "ok", "message": "ok"}), \
             patch("scripts.utils.preflight.check_browser_runtime", return_value={"name": "browser", "status": "ok", "message": "browser"}), \
             patch("scripts.utils.preflight.check_libreoffice_runtime", return_value={"name": "libreoffice", "status": "warning", "message": "libreoffice"}):
            result = run_preflight(
                Settings(
                    SLIDEA_MODE="ECONOMIC",
                    DEFAULT_LLM_MODEL="demo",
                    DEFAULT_LLM_API_KEY="key",
                    DEFAULT_LLM_API_BASE_URL="https://example.com",
                    DISABLE_EMBEDDING=True,
                ),
                stages=["render"],
                dry_run=False,
            )

        self.assertEqual(
            [item["name"] for item in result["checks"]],
            [
                "env_setup",
                "runtime_python",
                "browser",
                "default_llm",
                "tavily",
                "default_vlm",
                "embedding",
                "libreoffice",
            ],
        )

    def test_preflight_reports_premium_warning_only_in_premium_mode(self):
        from scripts.utils.preflight import run_preflight

        result = run_preflight(
            Settings(
                SLIDEA_MODE="PREMIUM",
                DEFAULT_LLM_MODEL="demo",
                DEFAULT_LLM_API_KEY="key",
                DEFAULT_LLM_API_BASE_URL="https://example.com",
                PREMIUM_LLM_MODEL="",
                PREMIUM_LLM_API_KEY="",
                PREMIUM_LLM_API_BASE_URL="",
                DISABLE_EMBEDDING=True,
            ),
            stages=["outline"],
            dry_run=True,
        )

        premium_checks = [item for item in result["checks"] if item["name"] == "premium_llm"]
        self.assertEqual(len(premium_checks), 1)
        self.assertEqual(premium_checks[0]["status"], "warning")
        self.assertIn("fall back", premium_checks[0]["message"].lower())

    def test_dry_run_uses_preflight_before_heavy_imports(self):
        env = os.environ.copy()
        env["DEFAULT_LLM_MODEL"] = ""
        env["DEFAULT_LLM_API_KEY"] = ""
        env["DEFAULT_LLM_API_BASE_URL"] = ""

        proc = subprocess.run(
            [sys.executable, "scripts/run_ppt_pipeline.py", "--text", "smoke", "--dry-run"],
            cwd=os.path.dirname(__file__) + "/..",
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["stage"], "completed")
        self.assertEqual(payload["output"]["stage"], "completed")
        self.assertIn("preflight", payload["output"])


if __name__ == "__main__":
    unittest.main()
