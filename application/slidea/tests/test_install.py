import io
import subprocess
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import scripts.install.install as install


class EnsureDependenciesTests(unittest.TestCase):
    def test_set_env_value_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("FOO=bar\n", encoding="utf-8")

            install.set_env_value(env_path, "SETUP_COMPLETED", "true")

            self.assertEqual(
                install.read_env_value(env_path, "SETUP_COMPLETED"),
                "true",
            )
            self.assertIn("SETUP_COMPLETED=true", env_path.read_text(encoding="utf-8"))

    @patch("scripts.install.install.platform.system", return_value="Linux")
    def test_get_libreoffice_download_info_for_linux_appimage(self, _mock_system):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            libreoffice_dir = root_dir / "libreoffice"

            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "LIBREOFFICE_DIR", libreoffice_dir):
                url, target = install.get_libreoffice_download_info()

        self.assertIn("AppImage", url)
        self.assertEqual(target, libreoffice_dir / "libreoffice.AppImage")

    def test_get_libreoffice_download_info_for_windows_x86_64_msi(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            libreoffice_dir = root_dir / "libreoffice"

            with patch("scripts.install.install.platform.system", return_value="Windows"), \
                patch("scripts.install.install.platform.machine", return_value="AMD64"), \
                patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "LIBREOFFICE_DIR", libreoffice_dir):
                url, target = install.get_libreoffice_download_info()

        self.assertIn("Win_x86-64.msi", url)
        self.assertEqual(target, libreoffice_dir / "LibreOffice_26.2.1_Win_x86-64.msi")

    def test_get_libreoffice_download_info_for_windows_arm64_msi(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            libreoffice_dir = root_dir / "libreoffice"

            with patch("scripts.install.install.platform.system", return_value="Windows"), \
                patch("scripts.install.install.platform.machine", return_value="ARM64"), \
                patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "LIBREOFFICE_DIR", libreoffice_dir):
                url, target = install.get_libreoffice_download_info()

        self.assertIn("Win_aarch64.msi", url)
        self.assertEqual(target, libreoffice_dir / "LibreOffice_26.2.1_Win_aarch64.msi")

    def test_get_local_libreoffice_executable_for_windows_uses_soffice_com(self):
        with patch("scripts.install.install.platform.system", return_value="Windows"), \
            patch.dict("scripts.install.install.os.environ", {"ProgramFiles": r"D:\Program Files"}, clear=True):
                result = install.get_local_libreoffice_executable()

        self.assertEqual(
            result,
            Path(r"D:\Program Files") / "LibreOffice" / "program" / "soffice.com",
        )

    def test_get_linux_libreoffice_install_command_for_ubuntu_arm64(self):
        with patch("scripts.install.install.platform.system", return_value="Linux"), \
            patch("scripts.install.install.platform.machine", return_value="aarch64"), \
            patch.object(
                install,
                "read_linux_os_release",
                return_value={"ID": "ubuntu", "ID_LIKE": "debian"},
            ):
            result = install.get_linux_libreoffice_install_command()

        self.assertEqual(result, ("Ubuntu/Debian", "sudo apt install libreoffice"))

    def test_get_linux_libreoffice_install_command_for_rhel_family_arm64(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            script_dir = root_dir / "scripts" / "install"
            script_path = script_dir / install.RHEL_FAMILY_LINUX_HELPER_SCRIPT_NAME
            script_dir.mkdir(parents=True, exist_ok=True)
            script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            with patch("scripts.install.install.platform.system", return_value="Linux"), \
                patch("scripts.install.install.platform.machine", return_value="aarch64"), \
                patch.object(
                    install,
                    "read_linux_os_release",
                    return_value={"ID": "openEuler", "NAME": "openEuler", "ID_LIKE": "fedora"},
                ), \
                patch.object(install, "ROOT_DIR", root_dir):
                distro_name, command = install.get_linux_libreoffice_install_command()

            self.assertEqual(distro_name, install.LINUX_RHEL_FAMILY_DISTRO_LABEL)
            self.assertEqual(command, f'bash "{script_path}"')
            self.assertTrue(script_path.exists())
            script_content = script_path.read_text(encoding="utf-8")
            self.assertIn("#!/usr/bin/env bash", script_content)

    def test_prepared_rhel_family_script_contains_expected_commands(self):
        script_path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "install"
            / install.RHEL_FAMILY_LINUX_HELPER_SCRIPT_NAME
        )
        script_content = script_path.read_text(encoding="utf-8")

        self.assertIn('LIBREOFFICE_DIR="${ROOT_DIR}/libreoffice"', script_content)
        self.assertIn('LIBREOFFICE_VERSION="26.2.1"', script_content)
        self.assertIn('LIBREOFFICE_BUILD="26.2.1.2"', script_content)
        self.assertIn('WORK_DIR="${LIBREOFFICE_DIR}/rpm-aarch64"', script_content)
        self.assertIn('curl -fL --retry 3 -o "${ARCHIVE_PATH}" "${LIBREOFFICE_URL}"', script_content)
        self.assertIn('rpm_files=( ./*.rpm )', script_content)
        self.assertIn("sudo dnf install -y \\", script_content)
        self.assertIn('"${rpm_files[@]}" \\', script_content)
        self.assertIn("alsa-lib \\", script_content)
        self.assertIn("gtk3 \\", script_content)
        self.assertIn("mesa-libgbm \\", script_content)
        self.assertIn("nss-util \\", script_content)
        self.assertIn('command -v "${command_name}"', script_content)
        self.assertIn("libreoffice26.2 --version", script_content)
        self.assertNotIn("libssl3.so", script_content)
        self.assertNotIn("ldconfig", script_content)

    def test_print_post_install_summary_includes_linux_arm64_command(self):
        buffer = io.StringIO()
        guidance = "\n- Run this command manually: `sudo apt install libreoffice`\n"

        with redirect_stdout(buffer):
            install.print_post_install_summary([], guidance)

        output = buffer.getvalue()
        self.assertIn("The Slidea skill has been installed successfully.", output)
        self.assertIn("sudo apt install libreoffice", output)
        self.assertNotIn("sudo playwright install-deps", output)

    def test_print_post_install_summary_includes_failed_steps(self):
        buffer = io.StringIO()

        with patch.object(
            install,
            "get_linux_arm64_post_install_guidance",
            return_value="",
        ), redirect_stdout(buffer):
            install.print_post_install_summary(
                [
                    install.StepIssue(
                        step_no=2,
                        title="Install Python dependencies",
                        status="failed",
                        message="FileNotFoundError: missing requirements file",
                    )
                ]
            )

        output = buffer.getvalue()
        self.assertIn("The Slidea skill installation did not complete successfully.", output)
        self.assertIn("The following steps failed or were skipped:", output)
        self.assertIn("Step 2 (Install Python dependencies)", output)
        self.assertIn("FileNotFoundError: missing requirements file", output)

    def test_get_linux_arm64_post_install_guidance_uses_version_check(self):
        with patch.object(install, "verify_libreoffice_installation", return_value=False), \
            patch.object(
                install,
                "get_linux_libreoffice_install_command",
                return_value=("Ubuntu/Debian", "sudo apt install libreoffice"),
            ):
            guidance = install.get_linux_arm64_post_install_guidance()

        self.assertIn("Additional note for Linux (Ubuntu/Debian) ARM64 users:", guidance)
        self.assertIn("sudo apt install libreoffice", guidance)

    def test_get_linux_arm64_post_install_guidance_uses_script_command_for_rhel_family(self):
        with patch.object(install, "verify_libreoffice_installation", return_value=False), \
            patch.object(
                install,
                "get_linux_libreoffice_install_command",
                return_value=(
                    install.LINUX_RHEL_FAMILY_DISTRO_LABEL,
                    'bash "/tmp/scripts/install/extra_install_linux_rhel.sh"',
                ),
            ):
            guidance = install.get_linux_arm64_post_install_guidance()

        self.assertIn(
            "Additional note for Linux "
            f"({install.LINUX_RHEL_FAMILY_DISTRO_LABEL}) ARM64 users:",
            guidance,
        )
        self.assertIn(
            'Run this command manually: `bash "/tmp/scripts/install/extra_install_linux_rhel.sh"`',
            guidance,
        )
        self.assertNotIn("```sh", guidance)

    def test_verify_libreoffice_installation_uses_version_check(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = Path(tmp_dir) / "libreoffice"
            executable.write_text("", encoding="utf-8")

            with patch.object(install, "get_available_libreoffice_executable", return_value=executable), \
                patch("scripts.install.install.subprocess.run") as mock_run:
                mock_run.return_value = type("Completed", (), {"stdout": "LibreOffice 26.2.1\n", "stderr": ""})()
                result = install.verify_libreoffice_installation()

        self.assertTrue(result)
        mock_run.assert_called_once()

    def test_verify_playwright_installation_uses_version_check(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            python_path = Path(tmp_dir) / "python"
            python_path.write_text("", encoding="utf-8")

            with patch("scripts.install.install.subprocess.run") as mock_run:
                mock_run.return_value = type("Completed", (), {"stdout": "Version 1.54.0\n", "stderr": ""})()
                result = install.verify_playwright_installation(python_path)

        self.assertTrue(result)
        mock_run.assert_called_once()

    def test_get_available_libreoffice_executable_prefers_system_binary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_executable = Path(tmp_dir) / "AppRun"
            local_executable.write_text("", encoding="utf-8")

            with patch.object(install, "get_system_libreoffice_executable", return_value=Path("/usr/bin/libreoffice26.2")), \
                patch.object(install, "get_local_libreoffice_executable", return_value=local_executable):
                result = install.get_available_libreoffice_executable()

        self.assertEqual(result, Path("/usr/bin/libreoffice26.2"))

    def test_get_system_libreoffice_executable_checks_command_on_macos(self):
        with patch("scripts.install.install.platform.system", return_value="Darwin"), \
            patch("scripts.install.install.shutil.which", side_effect=lambda candidate: "/usr/local/bin/soffice" if candidate == "soffice" else None):
                result = install.get_system_libreoffice_executable()

        self.assertEqual(result, Path("/usr/local/bin/soffice"))

    def test_get_remote_file_size_returns_none_when_head_request_fails(self):
        with patch(
            "scripts.install.install.urllib.request.urlopen",
            side_effect=urllib.error.URLError("ssl failed"),
        ):
            result = install.get_remote_file_size("https://example.com/file.bin")

        self.assertIsNone(result)

    def test_download_file_shows_progress_and_writes_target(self):
        class FakeResponse:
            def __init__(self, payload: bytes, declared_size: int | None = None):
                self.payload = payload
                self.offset = 0
                self.declared_size = declared_size if declared_size is not None else len(payload)

            def read(self, size: int = -1) -> bytes:
                if self.offset >= len(self.payload):
                    return b""
                if size < 0:
                    size = len(self.payload) - self.offset
                chunk = self.payload[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

            def info(self):
                return {"Content-Length": str(self.declared_size)}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "download.bin"
            buffer = io.StringIO()

            with patch("scripts.install.install.urllib.request.urlopen", return_value=FakeResponse(b"abcdef")), \
                redirect_stdout(buffer):
                install.download_file("https://example.com/file.bin", target)

            output = buffer.getvalue()
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertIn("[DOWNLOAD]", output)
            self.assertIn("Download completed", output)

    def test_download_file_rejects_incomplete_download_and_removes_partial_file(self):
        class FakeResponse:
            def __init__(self, payload: bytes, declared_size: int):
                self.payload = payload
                self.offset = 0
                self.declared_size = declared_size

            def read(self, size: int = -1) -> bytes:
                if self.offset >= len(self.payload):
                    return b""
                if size < 0:
                    size = len(self.payload) - self.offset
                chunk = self.payload[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

            def info(self):
                return {"Content-Length": str(self.declared_size)}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "partial.bin"
            with patch(
                "scripts.install.install.urllib.request.urlopen",
                return_value=FakeResponse(b"abc", declared_size=10),
            ):
                with self.assertRaises(RuntimeError):
                    install.download_file(
                        "https://example.com/file.bin", target
                    )

            self.assertFalse(target.exists())

    def test_download_file_retries_up_to_three_times(self):
        class FakeResponse:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.offset = 0

            def read(self, size: int = -1) -> bytes:
                if self.offset >= len(self.payload):
                    return b""
                if size < 0:
                    size = len(self.payload) - self.offset
                chunk = self.payload[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

            def info(self):
                return {"Content-Length": str(len(self.payload))}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        attempts = [
            RuntimeError("network-1"),
            RuntimeError("network-2"),
            FakeResponse(b"abcdef"),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "retry.bin"
            buffer = io.StringIO()

            def urlopen_side_effect(_url: str):
                outcome = attempts.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            with patch("scripts.install.install.urllib.request.urlopen", side_effect=urlopen_side_effect), \
                redirect_stdout(buffer):
                install.download_file("https://example.com/file.bin", target)

            output = buffer.getvalue()
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertIn("attempt 1/3", output)
            self.assertIn("attempt 2/3", output)
            self.assertIn("attempt 3/3", output)
            self.assertIn("preparing to retry", output)

    def test_ensure_downloaded_file_skips_when_existing_size_matches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "cached.bin"
            target.write_bytes(b"abcdef")

            with patch.object(install, "get_remote_file_size", return_value=6), \
                patch.object(install, "download_file") as mock_download:
                install.ensure_downloaded_file(
                    "https://example.com/file.bin",
                    target,
                )

            self.assertEqual(target.read_bytes(), b"abcdef")
            mock_download.assert_not_called()

    def test_install_libreoffice_to_local_dir_uses_msiexec_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            libreoffice_dir = root_dir / "libreoffice"
            installer_path = libreoffice_dir / "LibreOffice_26.2.1_Win_x86-64.msi"

            def fake_download(_url: str, target: Path) -> None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("msi", encoding="utf-8")

            with patch("scripts.install.install.platform.system", return_value="Windows"), \
                patch.object(install, "LIBREOFFICE_DIR", libreoffice_dir), \
                patch.object(
                    install,
                    "get_libreoffice_download_info",
                    return_value=("https://example.com/LibreOffice_26.2.1_Win_x86-64.msi", installer_path),
                ), \
                patch.object(install, "ensure_downloaded_file", side_effect=fake_download), \
                patch.object(install, "run_command") as mock_run_command:
                install.install_libreoffice_to_local_dir()

        mock_run_command.assert_called_once_with(
            [
                "msiexec",
                "/i",
                str(installer_path),
                "/qb",
                "/norestart",
            ]
        )
        self.assertFalse(installer_path.exists())

    def test_get_python_source_override_respects_explicit_disable(self):
        with patch.dict(
            "scripts.install.install.os.environ",
            {"SLIDEA_USE_CN_PYTHON_MIRROR": "0"},
            clear=True,
        ):
            result = install.get_python_source_override()

        self.assertFalse(result)

    def test_resolve_python_install_source_config_uses_explicit_env(self):
        with patch.dict(
            "scripts.install.install.os.environ",
            {"UV_DEFAULT_INDEX": "https://example.com/simple"},
            clear=True,
        ):
            config = install.resolve_python_install_source_config()

        self.assertIsNotNone(config.env)
        self.assertEqual(config.env["UV_DEFAULT_INDEX"], "https://example.com/simple")
        self.assertIn("explicitly configured", config.reason)
        self.assertFalse(config.retry_with_mirror)

    def test_resolve_python_install_source_config_uses_official_sources_after_successful_probe(self):
        install.should_use_python_mirrors_by_network.cache_clear()
        with patch.object(install, "can_connect_to_url", return_value=True), \
            patch.dict("scripts.install.install.os.environ", {}, clear=True):
            config = install.resolve_python_install_source_config()

        self.assertIsNone(config.env)
        self.assertIn("passed the connectivity probe", config.reason)
        self.assertTrue(config.retry_with_mirror)

    def test_resolve_python_install_source_config_uses_mirrors_after_failed_probe(self):
        install.should_use_python_mirrors_by_network.cache_clear()
        with patch.object(install, "can_connect_to_url", side_effect=[False, True]), \
            patch.dict("scripts.install.install.os.environ", {}, clear=True):
            config = install.resolve_python_install_source_config()

        self.assertIsNotNone(config.env)
        self.assertEqual(
            config.env["UV_PYTHON_INSTALL_MIRROR"],
            install.MAINLAND_CHINA_UV_PYTHON_INSTALL_MIRROR,
        )
        self.assertEqual(config.env["PIP_INDEX_URL"], install.MAINLAND_CHINA_PYPI_INDEX)
        self.assertIn("did not pass the connectivity probe", config.reason)
        self.assertFalse(config.retry_with_mirror)

    def test_run_python_install_command_retries_with_mirror_after_official_failure(self):
        official_config = install.PythonInstallSourceConfig(
            env=None,
            reason="official probe succeeded",
            retry_with_mirror=True,
        )
        mirror_env = {
            "UV_PYTHON_INSTALL_MIRROR": install.MAINLAND_CHINA_UV_PYTHON_INSTALL_MIRROR,
            "UV_DEFAULT_INDEX": install.MAINLAND_CHINA_PYPI_INDEX,
            "PIP_INDEX_URL": install.MAINLAND_CHINA_PYPI_INDEX,
        }
        mirror_config = install.PythonInstallSourceConfig(
            env=mirror_env,
            reason="retrying with configured mirrors",
            retry_with_mirror=False,
        )

        with patch.object(
            install,
            "resolve_python_install_source_config",
            side_effect=[official_config, mirror_config],
        ), patch.object(
            install,
            "run_command",
            side_effect=[subprocess.CalledProcessError(1, ["uv"]), None],
        ) as mock_run_command:
            install.run_python_install_command(["uv", "venv"], step_name="Create Python virtual environment")

        self.assertEqual(mock_run_command.call_count, 2)
        self.assertIsNone(mock_run_command.call_args_list[0].kwargs["env"])
        self.assertEqual(mock_run_command.call_args_list[1].kwargs["env"], mirror_env)

    def test_create_virtualenv_uses_install_fallback_runner(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            venv_dir = root_dir / ".venv"
            python_path = venv_dir / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            with patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "run_python_install_command") as mock_run_python_install_command:
                result = install.create_virtualenv("python3")

        self.assertEqual(result, python_path)
        mock_run_python_install_command.assert_called_once()
        self.assertEqual(
            mock_run_python_install_command.call_args.args[0],
            ["uv", "venv", "--python", "3.11", "--seed", str(venv_dir)],
        )
        self.assertEqual(
            mock_run_python_install_command.call_args.kwargs["step_name"],
            "Create Python virtual environment",
        )

    def test_ensure_uv_installed_uses_package_index_when_installing_uv(self):
        with patch.object(install, "get_uv_command", side_effect=[None, ["uv"]]), \
            patch.object(install, "run_python_install_command") as mock_run_python_install_command:
            result = install.ensure_uv_installed("python3")

        self.assertEqual(result, ["uv"])
        mock_run_python_install_command.assert_called_once_with(
            [
                "python3",
                "-m",
                "pip",
                "install",
                "uv",
                "--timeout",
                install.PIP_NETWORK_TIMEOUT_SECONDS,
                "--retries",
                install.PIP_NETWORK_RETRIES,
            ],
            step_name="Install uv",
        )

    def test_main_bootstrap_flow_writes_setup_completed_and_logs_steps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            env_example = root_dir / ".env.example"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            env_example.write_text("DEFAULT_LLM_MODEL=\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "ENV_EXAMPLE_FILE", env_example), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=False), \
                patch.object(install, "get_bootstrap_python_command", return_value="python3"), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "create_virtualenv", return_value=venv_python), \
                patch.object(install, "run_python_install_command") as mock_run_python_install_command, \
                patch.object(install, "run_command") as mock_run_command, \
                patch.object(install, "verify_libreoffice_installation", side_effect=[False, False, True, True]), \
                patch.object(install, "install_libreoffice_to_local_dir") as mock_install_libreoffice, \
                patch.object(install, "is_linux_arm64", return_value=False), \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Step 1", output)
            self.assertIn("Step 2", output)
            self.assertIn("Step 3", output)
            self.assertIn("Step 4", output)
            self.assertIn("Step 5", output)
            self.assertIn("The Slidea skill has been installed successfully.", output)
            mock_run_python_install_command.assert_called_once_with(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(venv_python),
                    "-r",
                    str(requirements),
                ],
                step_name="Install Python dependencies",
            )
            mock_run_command.assert_called_once_with(
                [str(venv_python), "-m", "playwright", "install", "chromium"]
            )
            mock_install_libreoffice.assert_called_once()
            self.assertEqual(
                install.read_env_value(env_file, "SETUP_COMPLETED"),
                "true",
            )

    def test_main_when_setup_done_but_missing_libreoffice_installs_local_copy(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            env_file.write_text("SETUP_COMPLETED=true\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=True), \
                patch.object(install, "verify_libreoffice_installation", side_effect=[False, False, True, True]), \
                patch.object(install, "install_libreoffice_to_local_dir") as mock_install_libreoffice, \
                patch.object(install, "is_linux_arm64", return_value=False), \
                patch.object(install, "run_command") as mock_run_command, \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Step 1", output)
            self.assertIn("Step 4", output)
            self.assertIn("Step 5", output)
            self.assertIn("virtual environment is already installed", output)
            mock_install_libreoffice.assert_called_once()
            mock_run_command.assert_not_called()

    def test_main_when_setup_done_but_playwright_check_fails_rebuilds_runtime(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            env_example = root_dir / ".env.example"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            env_file.write_text("SETUP_COMPLETED=true\n", encoding="utf-8")
            env_example.write_text("DEFAULT_LLM_MODEL=\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "ENV_EXAMPLE_FILE", env_example), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=False), \
                patch.object(install, "get_bootstrap_python_command", return_value="python3"), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "create_virtualenv", return_value=venv_python), \
                patch.object(install, "run_python_install_command") as mock_run_python_install_command, \
                patch.object(install, "run_command") as mock_run_command, \
                patch.object(install, "verify_libreoffice_installation", side_effect=[True, True]), \
                patch.object(install, "is_linux_arm64", return_value=False), \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Playwright is not usable. Rebuilding it.", output)
            mock_run_python_install_command.assert_called_once()
            mock_run_command.assert_called_once_with(
                [str(venv_python), "-m", "playwright", "install", "chromium"]
            )

    def test_main_rhel_x86_64_requires_manual_helper_before_completion(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            env_example = root_dir / ".env.example"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"
            helper_path = root_dir / "scripts" / "install" / install.RHEL_FAMILY_LINUX_HELPER_SCRIPT_NAME

            env_example.write_text("DEFAULT_LLM_MODEL=\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")
            helper_path.parent.mkdir(parents=True, exist_ok=True)
            helper_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "ENV_EXAMPLE_FILE", env_example), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=False), \
                patch.object(install, "get_bootstrap_python_command", return_value="python3"), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "create_virtualenv", return_value=venv_python), \
                patch.object(install, "run_python_install_command"), \
                patch.object(install, "run_command"), \
                patch.object(install, "update_install_state"), \
                patch.object(install, "verify_libreoffice_installation", side_effect=[False, False, True]), \
                patch.object(install, "install_libreoffice_to_local_dir") as mock_install_libreoffice, \
                patch("scripts.install.install.platform.system", return_value="Linux"), \
                patch("scripts.install.install.platform.machine", return_value="x86_64"), \
                patch.object(
                    install,
                    "read_linux_os_release",
                    return_value={"ID": "openeuler", "ID_LIKE": "rhel fedora", "NAME": "openEuler"},
                ), \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("manual completion is still required", output.lower())
            self.assertIn(str(helper_path), output)
            self.assertIn("PDF generation will not work", output)
            self.assertIn("PDF/PPTX generation will not work correctly", output)
            self.assertIn("manual completion is still required on RHEL-family Linux", output)
            self.assertEqual(install.read_env_value(env_file, "SETUP_COMPLETED"), "true")
            mock_install_libreoffice.assert_called_once()

    def test_main_linux_arm64_skips_bundled_libreoffice_install(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            env_example = root_dir / ".env.example"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            env_example.write_text("DEFAULT_LLM_MODEL=\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "ENV_EXAMPLE_FILE", env_example), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=False), \
                patch.object(install, "get_bootstrap_python_command", return_value="python3"), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "create_virtualenv", return_value=venv_python), \
                patch.object(install, "run_command"), \
                patch.object(install, "verify_libreoffice_installation", return_value=False), \
                patch.object(install, "install_libreoffice_to_local_dir") as mock_install_libreoffice, \
                patch.object(install, "is_linux_arm64", return_value=True), \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Skipping bundled LibreOffice installation", output)
            self.assertIn("PDF generation remains available", output)
            mock_install_libreoffice.assert_not_called()

    def test_main_linux_arm64_missing_helper_script_warns_without_breaking_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            env_file = root_dir / ".env"
            env_example = root_dir / ".env.example"
            requirements = root_dir / "requirements.txt"
            venv_dir = root_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"

            env_example.write_text("DEFAULT_LLM_MODEL=\n", encoding="utf-8")
            requirements.write_text("playwright\n", encoding="utf-8")
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")

            buffer = io.StringIO()
            with patch.object(install, "ROOT_DIR", root_dir), \
                patch.object(install, "ENV_FILE", env_file), \
                patch.object(install, "ENV_EXAMPLE_FILE", env_example), \
                patch.object(install, "REQUIREMENTS_FILE", requirements), \
                patch.object(install, "VENV_DIR", venv_dir), \
                patch.object(install, "verify_playwright_installation", return_value=False), \
                patch.object(install, "get_bootstrap_python_command", return_value="python3"), \
                patch.object(install, "ensure_uv_installed", return_value=["uv"]), \
                patch.object(install, "create_virtualenv", return_value=venv_python), \
                patch.object(install, "run_command"), \
                patch.object(install, "verify_libreoffice_installation", return_value=False), \
                patch.object(install, "install_libreoffice_to_local_dir") as mock_install_libreoffice, \
                patch.object(install, "is_linux_arm64", return_value=True), \
                patch.object(
                    install,
                    "get_linux_libreoffice_install_command",
                    side_effect=FileNotFoundError("missing helper script"),
                ), \
                redirect_stdout(buffer):
                result = install.main()

            output = buffer.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Could not prepare the Linux ARM64 LibreOffice helper script", output)
            self.assertIn("manually install LibreOffice with your distro's package manager", output)
            self.assertIn("The Slidea skill has been installed successfully.", output)
            self.assertNotIn("Step 4 (Check LibreOffice)", output)
            mock_install_libreoffice.assert_not_called()


if __name__ == "__main__":
    unittest.main()
