#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import ssl
import urllib.error
import urllib.request
import urllib.request
import time
from contextlib import suppress
from pathlib import Path

from _common import (
    REQUIREMENTS_FILE,
    ROOT_DIR,
    StepIssue,
    VENV_DIR,
    ensure_uv_installed,
    format_duration,
    format_exception_message,
    get_bootstrap_python_command,
    get_venv_python_path,
    log_info,
    log_step,
    log_success,
    log_warning,
    record_step_failure,
    record_step_skip,
    run_command,
    run_python_install_command,
    update_install_state,
)

ENV_FILE = ROOT_DIR / ".env"
ENV_EXAMPLE_FILE = ROOT_DIR / ".env.example"
LIBREOFFICE_DIR = ROOT_DIR / "libreoffice"

LINUX_LIBREOFFICE_URL = "https://appimages.libreitalia.org/LibreOffice-still.standard-x86_64.AppImage"
MACOS_LIBREOFFICE_URL = "https://download.documentfoundation.org/libreoffice/stable/26.2.1/mac/aarch64/LibreOffice_26.2.1_MacOS_aarch64.dmg"
WINDOWS_X86_64_LIBREOFFICE_URL = (
    "https://download.documentfoundation.org/libreoffice/stable/26.2.1/win/x86_64/LibreOffice_26.2.1_Win_x86-64.msi"
)
WINDOWS_ARM64_LIBREOFFICE_URL = (
    "https://download.documentfoundation.org/libreoffice/stable/26.2.1/win/aarch64/LibreOffice_26.2.1_Win_aarch64.msi"
)
RHEL_FAMILY_ARM64_LIBREOFFICE_SCRIPT_NAME = "extra_install_linux_rhel_family_aarch64.sh"
LINUX_ARM64_RHEL_FAMILY_DISTRO_LABEL = "RHEL family"
LINUX_ARM64_RHEL_FAMILY_DISTRO_TOKENS = {
    "fedora",
    "rhel",
    "centos",
    "rocky",
    "almalinux",
    "openeuler",
}
LINUX_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice26.2", "libreoffice", "soffice")
MACOS_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice", "soffice")
WINDOWS_SYSTEM_LIBREOFFICE_CANDIDATES = ("soffice.com", "soffice.exe", "libreoffice", "soffice")
DOWNLOAD_RETRY_TIMES = 3


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def is_linux_arm64() -> bool:
    return platform.system() == "Linux" and platform.machine().lower() in {"arm64", "aarch64"}


def read_linux_os_release() -> dict[str, str]:
    os_release_path = Path("/etc/os-release")
    if not os_release_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in os_release_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def get_linux_distro_tokens(os_release: dict[str, str] | None = None) -> set[str]:
    os_release = os_release or read_linux_os_release()
    searchable_fields = (
        os_release.get("ID", ""),
        os_release.get("ID_LIKE", ""),
        os_release.get("NAME", ""),
        os_release.get("PRETTY_NAME", ""),
    )
    normalized_text = " ".join(value.lower().replace("/", " ").replace(",", " ") for value in searchable_fields)
    return {token for token in normalized_text.split() if token}


def is_linux_arm64_rhel_family(os_release: dict[str, str] | None = None) -> bool:
    if not is_linux_arm64():
        return False

    distro_tokens = get_linux_distro_tokens(os_release)
    return bool(distro_tokens & LINUX_ARM64_RHEL_FAMILY_DISTRO_TOKENS)


def get_prepared_rhel_family_arm64_libreoffice_script_path() -> Path:
    script_path = ROOT_DIR / "scripts" / "install" / RHEL_FAMILY_ARM64_LIBREOFFICE_SCRIPT_NAME
    if not script_path.exists():
        raise FileNotFoundError(
            f"missing {LINUX_ARM64_RHEL_FAMILY_DISTRO_LABEL} ARM64 LibreOffice install script: {script_path}"
        )
    return script_path


def get_linux_libreoffice_install_command() -> tuple[str, str] | None:
    if not is_linux_arm64():
        return None

    os_release = read_linux_os_release()
    distro_tokens = get_linux_distro_tokens(os_release)

    if is_linux_arm64_rhel_family(os_release):
        script_path = get_prepared_rhel_family_arm64_libreoffice_script_path()
        return LINUX_ARM64_RHEL_FAMILY_DISTRO_LABEL, f'bash "{script_path}"'

    if distro_tokens & {"ubuntu", "debian", "linuxmint", "pop", "popos", "elementary"}:
        return "Ubuntu/Debian", "sudo apt install libreoffice"
    if distro_tokens & {"fedora", "rhel", "centos", "rocky", "almalinux"}:
        return "Fedora", "sudo dnf install libreoffice"
    if distro_tokens & {"arch", "manjaro", "endeavouros"}:
        return "Arch Linux", "sudo pacman -S libreoffice-still"

    return None


def format_linux_arm64_post_install_guidance(install_command: tuple[str, str]) -> str:
    distro_name, command = install_command
    return f"""

Additional note for Linux ({distro_name}) ARM64 users:
- PDF generation can still work without LibreOffice.
- If you want the final `.pptx` file instead of only the `.pdf` file, please manually install LibreOffice first. Run this command manually: `{command}`
"""


def get_linux_arm64_post_install_guidance() -> str:
    if verify_libreoffice_installation(log_output=False):
        return ""

    install_command = get_linux_libreoffice_install_command()
    if install_command is None:
        return ""

    return format_linux_arm64_post_install_guidance(install_command)


def print_post_install_summary(issues: list[StepIssue], libreoffice_guidance: str = "") -> None:
    print("\n" + "=" * 52)
    status_line = "The Slidea skill has been installed successfully."
    if issues:
        status_line = "The Slidea skill installation did not complete successfully."
        step_issue_lines = "\n".join(
            f"- [{issue.status.upper()}] Step {issue.step_no} ({issue.title}): {issue.message}" for issue in issues
        )
        step_issue_summary = f"""

The following steps failed or were skipped:
{step_issue_lines}
"""
    else:
        step_issue_summary = ""

    print(
        f"""
{status_line}
{step_issue_summary}
{libreoffice_guidance}
"""
    )
    print("=" * 52)


def read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip()
    return None


def ensure_env_file() -> None:
    if not ENV_FILE.exists():
        if not ENV_EXAMPLE_FILE.exists():
            raise FileNotFoundError(f"missing env example file: {ENV_EXAMPLE_FILE}")
        shutil.copyfile(ENV_EXAMPLE_FILE, ENV_FILE)


def set_env_value(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    replaced = False

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        name, _ = raw_line.split("=", 1)
        if name.strip() == key:
            lines[index] = f"{key}={value}"
            replaced = True
            break

    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_virtualenv(bootstrap_python_cmd: str) -> Path:
    uv_command = ensure_uv_installed(bootstrap_python_cmd)
    run_python_install_command(
        [*uv_command, "venv", "--python", "3.11", "--seed", str(VENV_DIR)],
        step_name="Create Python virtual environment",
        cleanup_before_retry=lambda: shutil.rmtree(VENV_DIR, ignore_errors=True) if VENV_DIR.exists() else None,
    )

    python_path = get_venv_python_path()
    if not python_path.exists():
        raise FileNotFoundError(f"virtual environment python not found: {python_path}")
    return python_path


def get_local_libreoffice_executable() -> Path:
    os_type = platform.system()

    if os_type == "Windows":
        program_files_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        return program_files_dir / "LibreOffice" / "program" / "soffice.com"

    if os_type == "Darwin":
        return LIBREOFFICE_DIR / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"

    if os_type == "Linux":
        return LIBREOFFICE_DIR / "libreoffice-app" / "AppRun"

    raise RuntimeError(f"Unsupported operating system: {os_type}")


def get_system_libreoffice_executable() -> Path | None:
    os_type = platform.system()
    if os_type == "Linux":
        candidates = LINUX_SYSTEM_LIBREOFFICE_CANDIDATES
    elif os_type == "Darwin":
        candidates = MACOS_SYSTEM_LIBREOFFICE_CANDIDATES
    elif os_type == "Windows":
        candidates = WINDOWS_SYSTEM_LIBREOFFICE_CANDIDATES
    else:
        return None

    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return Path(executable)
    return None


def get_available_libreoffice_executable() -> Path | None:
    system_executable = get_system_libreoffice_executable()
    if system_executable is not None:
        return system_executable

    local_executable = get_local_libreoffice_executable()
    if local_executable.exists():
        return local_executable

    return None


def get_libreoffice_download_info() -> tuple[str, Path]:
    os_type = platform.system()

    if os_type == "Windows":
        machine = platform.machine().lower()
        if machine in {"arm64", "aarch64"}:
            return (
                WINDOWS_ARM64_LIBREOFFICE_URL,
                LIBREOFFICE_DIR / "LibreOffice_26.2.1_Win_aarch64.msi",
            )
        return (
            WINDOWS_X86_64_LIBREOFFICE_URL,
            LIBREOFFICE_DIR / "LibreOffice_26.2.1_Win_x86-64.msi",
        )
    if os_type == "Darwin":
        return MACOS_LIBREOFFICE_URL, LIBREOFFICE_DIR / "LibreOffice.dmg"
    if os_type == "Linux":
        return LINUX_LIBREOFFICE_URL, LIBREOFFICE_DIR / "libreoffice.AppImage"

    raise RuntimeError(f"Unsupported operating system: {os_type}")


def download_file(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for attempt in range(1, DOWNLOAD_RETRY_TIMES + 1):
        log_info(f"Starting download, attempt {attempt}/{DOWNLOAD_RETRY_TIMES}: {target.name}")
        try:
            with urllib.request.urlopen(url) as response, target.open("wb") as output_file:
                total_size = None
                with suppress(TypeError, ValueError, AttributeError):
                    total_size = int(response.info().get("Content-Length"))

                downloaded = 0
                chunk_size = 1024 * 1024
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    output_file.write(chunk)
                    downloaded += len(chunk)

                    if total_size:
                        progress = min(downloaded / total_size, 1.0)
                        bar_width = 28
                        filled = int(bar_width * progress)
                        bar = "#" * filled + "-" * (bar_width - filled)
                        print(
                            f"\r[DOWNLOAD] |{bar}| {progress * 100:5.1f}% "
                            f"({format_size(downloaded)}/{format_size(total_size)})",
                            end="",
                            flush=True,
                        )
                    else:
                        print(
                            f"\r[DOWNLOAD] Downloaded {format_size(downloaded)}",
                            end="",
                            flush=True,
                        )

            print()

            if total_size is not None and downloaded != total_size:
                raise RuntimeError(
                    f"Incomplete download: expected {format_size(total_size)}, got {format_size(downloaded)}"
                )

            log_success(f"Download completed: {target.name}")
            return
        except Exception as exc:
            last_error = exc
            if target.exists():
                target.unlink()
            print()
            if attempt < DOWNLOAD_RETRY_TIMES:
                log_warning(f"Download failed, preparing to retry. Reason: {exc}")
            else:
                log_warning(f"Download failed, maximum retry count reached. Reason: {exc}")

    if last_error is not None:
        raise last_error


def get_remote_file_size(url: str) -> int | None:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request) as response:
            with suppress(TypeError, ValueError, AttributeError):
                content_length = response.info().get("Content-Length")
                if content_length:
                    return int(content_length)
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        log_warning(f"Failed to fetch remote file size. Continuing with the download flow. Reason: {exc}")
    return None


def ensure_downloaded_file(url: str, target: Path) -> None:
    if target.exists():
        expected_size = get_remote_file_size(url)
        current_size = target.stat().st_size

        if expected_size is not None and current_size == expected_size:
            log_success(
                f"Found an existing installer with the expected size. Skipping re-download: {target.name}"
            )
            return

        log_warning(f"Found an existing file with a mismatched size. Re-downloading: {target.name}")
        target.unlink()

    download_file(url, target)


def verify_libreoffice_installation(*, log_output: bool = True) -> bool:
    executable = get_available_libreoffice_executable()
    if executable is None or not executable.exists():
        return False

    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False

    version_output = (completed.stdout or completed.stderr or "").strip()
    if version_output and log_output:
        log_info(f"LibreOffice version check output: {version_output}")
    return True


def verify_playwright_installation(python_path: Path, *, log_output: bool = True) -> bool:
    if not python_path.exists():
        return False

    try:
        completed = subprocess.run(
            [str(python_path), "-m", "playwright", "--version"],
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False

    version_output = (completed.stdout or completed.stderr or "").strip()
    if version_output and log_output:
        log_info(f"Playwright version check output: {version_output}")
    return True


def install_libreoffice_to_local_dir() -> None:
    LIBREOFFICE_DIR.mkdir(parents=True, exist_ok=True)
    os_type = platform.system()

    log_info(f"LibreOffice will be installed to: {LIBREOFFICE_DIR}")

    if os_type == "Linux":
        url, target = get_libreoffice_download_info()
        extracted_dir = LIBREOFFICE_DIR / "libreoffice-app"
        squashfs_dir = LIBREOFFICE_DIR / "squashfs-root"

        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)
        if squashfs_dir.exists():
            shutil.rmtree(squashfs_dir)

        log_info(f"Downloading LibreOffice AppImage: {url}")
        ensure_downloaded_file(url, target)
        target.chmod(target.stat().st_mode | 0o111)
        log_info("Extracting the AppImage to avoid the FUSE dependency.")
        run_command(
            [str(target), "--appimage-extract"],
            cwd=LIBREOFFICE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not squashfs_dir.exists():
            raise RuntimeError("AppImage extraction failed: squashfs-root was not created.")
        shutil.move(str(squashfs_dir), str(extracted_dir))
        return

    if os_type == "Darwin":
        url, dmg_path = get_libreoffice_download_info()
        mountpoint = LIBREOFFICE_DIR / "mount"
        app_source = mountpoint / "LibreOffice.app"
        app_target = LIBREOFFICE_DIR / "LibreOffice.app"

        if app_target.exists():
            shutil.rmtree(app_target)
        if mountpoint.exists():
            shutil.rmtree(mountpoint)

        log_info(f"Downloading LibreOffice DMG: {url}")
        ensure_downloaded_file(url, dmg_path)
        mountpoint.mkdir(parents=True, exist_ok=True)

        try:
            run_command(["hdiutil", "attach", str(dmg_path), "-mountpoint", str(mountpoint)])
            shutil.copytree(app_source, app_target)
        finally:
            try:
                run_command(["hdiutil", "detach", str(mountpoint)])
            except subprocess.CalledProcessError:
                log_warning("Failed to detach the LibreOffice disk image. Please inspect it manually later.")
            if mountpoint.exists():
                shutil.rmtree(mountpoint, ignore_errors=True)
            if dmg_path.exists():
                dmg_path.unlink()

        if shutil.which("xattr"):
            run_command(["xattr", "-rd", "com.apple.quarantine", str(app_target)])
        return

    if os_type == "Windows":
        url, installer_path = get_libreoffice_download_info()
        log_info(f"Downloading LibreOffice MSI: {url}")
        ensure_downloaded_file(url, installer_path)
        try:
            run_command(
                [
                    "msiexec",
                    "/i",
                    str(installer_path),
                    "/qb",
                    "/norestart",
                ]
            )
        finally:
            if installer_path.exists():
                installer_path.unlink()
        return

    raise RuntimeError(f"Unsupported operating system: {os_type}")


def main() -> int:
    issues: list[StepIssue] = []
    setup_completed = (
        read_env_value(ENV_FILE, "SETUP_COMPLETED") or ""
    ).lower() == "true"
    venv_ready = VENV_DIR.exists() and get_venv_python_path().exists()
    venv_python: Path | None = get_venv_python_path() if venv_ready else None
    playwright_runtime_ready = (
        verify_playwright_installation(venv_python, log_output=False) if venv_python is not None else False
    )
    libreoffice_ready = verify_libreoffice_installation()
    requirements_available = REQUIREMENTS_FILE.exists()

    step_start = time.perf_counter()
    python_runtime_ready = False
    dependencies_ready = False
    playwright_ready = False
    libreoffice_guidance = ""

    if setup_completed and venv_ready and playwright_runtime_ready:
        log_step(1, "Check runtime environment")
        log_success(
            f"Detected that the Python virtual environment is already installed. Duration: {format_duration(time.perf_counter() - step_start)}"
        )
        python_runtime_ready = True
        dependencies_ready = requirements_available
        playwright_ready = True
    else:
        bootstrap_python_cmd = get_bootstrap_python_command()

        try:
            if VENV_DIR.exists():
                log_step(1, "Rebuild Python virtual environment")
                if setup_completed and venv_ready and not playwright_runtime_ready:
                    log_warning(
                        "Detected an existing virtual environment, but Playwright is not usable. Rebuilding it."
                    )
                else:
                    log_info(f"Found an existing virtual environment. Removing it before recreating: {VENV_DIR}")
                shutil.rmtree(VENV_DIR)
                log_success("Removed the old Python virtual environment.")
            else:
                log_step(1, "Create Python virtual environment")

            log_info(f"Initializing the Python virtual environment in {ROOT_DIR}...")
            log_info(f"Using bootstrap interpreter: {bootstrap_python_cmd}")
            log_info("Using uv to create a Python 3.11 virtual environment.")
            venv_python = create_virtualenv(bootstrap_python_cmd)
            python_runtime_ready = True
            log_success(
                f"Python virtual environment created: {venv_python}. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
        except Exception as exc:
            record_step_failure(issues, 1, "Create Python virtual environment", exc)

        step_start = time.perf_counter()
        log_step(2, "Install Python dependencies")
        if not python_runtime_ready or venv_python is None:
            record_step_skip(
                issues,
                2,
                "Install Python dependencies",
                "the Python virtual environment is not ready",
            )
        elif not requirements_available:
            record_step_failure(
                issues,
                2,
                "Install Python dependencies",
                FileNotFoundError(f"missing requirements file: {REQUIREMENTS_FILE}"),
            )
        else:
            try:
                uv_command = ensure_uv_installed(bootstrap_python_cmd)
                run_python_install_command(
                    [*uv_command, "pip", "install", "--python", str(venv_python), "-r", str(REQUIREMENTS_FILE)],
                    step_name="Install Python dependencies",
                )
                dependencies_ready = True
                log_success(
                    f"Installed requirements.txt dependencies. Duration: {format_duration(time.perf_counter() - step_start)}"
                )
            except Exception as exc:
                record_step_failure(issues, 2, "Install Python dependencies", exc)

        step_start = time.perf_counter()
        log_step(3, "Install Playwright Chromium")
        if not python_runtime_ready or venv_python is None:
            record_step_skip(
                issues,
                3,
                "Install Playwright Chromium",
                "the Python virtual environment is not ready",
            )
        elif not dependencies_ready:
            record_step_skip(
                issues,
                3,
                "Install Playwright Chromium",
                "Python dependencies were not installed successfully",
            )
        else:
            try:
                run_command([str(venv_python), "-m", "playwright", "install", "chromium"])
                playwright_ready = True
                log_success(
                    f"Playwright Chromium installation completed. Duration: {format_duration(time.perf_counter() - step_start)}"
                )
            except Exception as exc:
                record_step_failure(issues, 3, "Install Playwright Chromium", exc)

    step_start = time.perf_counter()
    log_step(4, "Check LibreOffice")
    libreoffice_step_ready = False
    try:
        if libreoffice_ready or verify_libreoffice_installation():
            libreoffice_step_ready = True
            log_success(
                f"Detected that a usable LibreOffice executable is available. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
        elif is_linux_arm64():
            libreoffice_step_ready = True
            log_warning(
                "Detected Linux arm64. Skipping bundled LibreOffice installation because the current automated Linux download flow only supports the x86_64 AppImage."
            )
            install_command = None
            try:
                install_command = get_linux_libreoffice_install_command()
            except FileNotFoundError as exc:
                log_warning(
                    "Could not prepare the Linux ARM64 LibreOffice helper script. "
                    f"Falling back to generic manual guidance. Reason: {format_exception_message(exc)}"
                )
            if install_command is None:
                log_info(
                    "PDF generation remains available. If you later need PPTX output, please manually install LibreOffice with your distro's package manager. "
                    f"The extra install script is currently only prepared for {LINUX_ARM64_RHEL_FAMILY_DISTRO_LABEL} ARM64 distros."
                )
            else:
                distro_name, command = install_command
                libreoffice_guidance = format_linux_arm64_post_install_guidance(install_command)
                log_info(
                    "PDF generation remains available. "
                    f"For Linux ({distro_name}) ARM64, please manually install LibreOffice first: {command}"
                )
            log_success(
                f"Skipped bundled LibreOffice installation on Linux arm64. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
        else:
            log_info("No usable local LibreOffice installation was found. Starting download and installation.")
            install_libreoffice_to_local_dir()
            if not verify_libreoffice_installation():
                raise RuntimeError("LibreOffice did not pass the --version verification after installation.")
            libreoffice_step_ready = True
            log_success(
                f"LibreOffice download and installation completed, and --version verification passed. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
    except Exception as exc:
        record_step_failure(issues, 4, "Check LibreOffice", exc)

    step_start = time.perf_counter()
    log_step(5, "Write environment marker")
    ready_to_mark_setup = python_runtime_ready and dependencies_ready and playwright_ready and libreoffice_step_ready
    if not ready_to_mark_setup:
        record_step_skip(
            issues,
            5,
            "Write environment marker",
            "one or more required setup steps did not finish successfully",
        )
    else:
        try:
            ensure_env_file()
            set_env_value(ENV_FILE, "SETUP_COMPLETED", "true")
            log_success(
                f"Copied .env.example and wrote SETUP_COMPLETED=true. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
        except Exception as exc:
            record_step_failure(issues, 5, "Write environment marker", exc)

    step_start = time.perf_counter()
    log_step(6, "Update Install State")
    if not ready_to_mark_setup:
        record_step_skip(
            issues,
            6,
            "Update Install State",
            "installation did not complete successfully",
        )
    else:
        try:
            update_install_state()
            log_success(
                f"Install state updated. Duration: {format_duration(time.perf_counter() - step_start)}"
            )
        except Exception as exc:
            record_step_failure(issues, 6, "Update Install State", exc)

    print_post_install_summary(issues, libreoffice_guidance)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
