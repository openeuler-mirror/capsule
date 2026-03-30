#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
VENV_DIR = ROOT_DIR / ".venv"
VERSION_STATE_FILE = ROOT_DIR / ".install_state.json"

MAINLAND_CHINA_UV_PYTHON_INSTALL_MIRROR = (
    "https://registry.npmmirror.com/-/binary/python-build-standalone/"
)
MAINLAND_CHINA_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
OFFICIAL_PYTHON_PROBE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/latest/download/SHA256SUMS"
)
OFFICIAL_PYPI_PROBE_URL = "https://pypi.org/simple/pip/"
OFFICIAL_SOURCE_PROBE_TIMEOUT_SECONDS = 3.0
PIP_NETWORK_TIMEOUT_SECONDS = "15"
PIP_NETWORK_RETRIES = "2"

TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
FALSY_ENV_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class PythonInstallSourceConfig:
    env: dict[str, str] | None
    reason: str
    retry_with_mirror: bool


@dataclass(frozen=True)
class StepIssue:
    step_no: int
    title: str
    status: str
    message: str


def log_step(step_no: int, title: str) -> None:
    print(f"\n{'=' * 18} Step {step_no}: {title} {'=' * 18}")


def log_success(message: str) -> None:
    print(f"[OK] {message}")


def log_info(message: str) -> None:
    print(f"[INFO] {message}")


def log_warning(message: str) -> None:
    print(f"[WARN] {message}")


def log_error(message: str) -> None:
    print(f"[ERROR] {message}")


def format_duration(seconds: float) -> str:
    return f"{seconds:.2f} seconds"


def format_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def record_step_failure(issues: list[StepIssue], step_no: int, title: str, exc: Exception) -> None:
    message = format_exception_message(exc)
    log_error(f"Step {step_no} failed ({title}): {message}")
    issues.append(StepIssue(step_no=step_no, title=title, status="failed", message=message))


def record_step_skip(issues: list[StepIssue], step_no: int, title: str, reason: str) -> None:
    log_warning(f"Skipping step {step_no} ({title}): {reason}")
    issues.append(StepIssue(step_no=step_no, title=title, status="skipped", message=reason))


def get_venv_python_path() -> Path:
    if sys.platform.startswith("win"):
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def get_bootstrap_python_command() -> str:
    python3 = shutil.which("python3")
    if python3:
        return python3
    python = shutil.which("python")
    if python:
        return python
    return sys.executable


def get_uv_command(bootstrap_python_cmd: str) -> list[str] | None:
    uv_executable = shutil.which("uv")
    if uv_executable:
        return [uv_executable]

    try:
        subprocess.run(
            [bootstrap_python_cmd, "-m", "uv", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return [bootstrap_python_cmd, "-m", "uv"]


def get_python_source_override() -> bool | None:
    override = os.environ.get("SLIDEA_USE_CN_PYTHON_MIRROR", "").strip().lower()
    if override in TRUTHY_ENV_VALUES:
        return True
    if override in FALSY_ENV_VALUES:
        return False
    return None


def get_explicit_python_source_env() -> dict[str, str] | None:
    explicit_keys = ("UV_PYTHON_INSTALL_MIRROR", "PIP_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX_URL")
    if not any(os.environ.get(key, "").strip() for key in explicit_keys):
        return None
    return os.environ.copy()


def can_connect_to_url(url: str, timeout: float = OFFICIAL_SOURCE_PROBE_TIMEOUT_SECONDS) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
        return False


@lru_cache(maxsize=1)
def should_use_python_mirrors_by_network() -> bool:
    python_ok = can_connect_to_url(OFFICIAL_PYTHON_PROBE_URL)
    package_ok = can_connect_to_url(OFFICIAL_PYPI_PROBE_URL)
    return not (python_ok and package_ok)


def build_mirror_python_install_env() -> dict[str, str]:
    env = os.environ.copy()
    env["UV_PYTHON_INSTALL_MIRROR"] = MAINLAND_CHINA_UV_PYTHON_INSTALL_MIRROR
    env["UV_DEFAULT_INDEX"] = MAINLAND_CHINA_PYPI_INDEX
    env["PIP_INDEX_URL"] = MAINLAND_CHINA_PYPI_INDEX
    return env


def resolve_python_install_source_config(force_mirror: bool = False) -> PythonInstallSourceConfig:
    if force_mirror:
        return PythonInstallSourceConfig(
            env=build_mirror_python_install_env(),
            reason="retrying with configured mirrors",
            retry_with_mirror=False,
        )

    explicit_env = get_explicit_python_source_env()
    if explicit_env is not None:
        return PythonInstallSourceConfig(
            env=explicit_env,
            reason="using explicitly configured Python source environment variables",
            retry_with_mirror=False,
        )

    override = get_python_source_override()
    if override is True:
        return PythonInstallSourceConfig(
            env=build_mirror_python_install_env(),
            reason="forced mirror mode via SLIDEA_USE_CN_PYTHON_MIRROR=1",
            retry_with_mirror=False,
        )
    if override is False:
        return PythonInstallSourceConfig(
            env=None,
            reason="forced official mode via SLIDEA_USE_CN_PYTHON_MIRROR=0",
            retry_with_mirror=False,
        )

    if should_use_python_mirrors_by_network():
        return PythonInstallSourceConfig(
            env=build_mirror_python_install_env(),
            reason="official Python sources did not pass the connectivity probe, use mirror sources",
            retry_with_mirror=False,
        )

    return PythonInstallSourceConfig(
        env=None,
        reason="official Python sources passed the connectivity probe",
        retry_with_mirror=True,
    )


def with_pip_network_options(command: list[str]) -> list[str]:
    return [
        *command,
        "--timeout",
        PIP_NETWORK_TIMEOUT_SECONDS,
        "--retries",
        PIP_NETWORK_RETRIES,
    ]


def run_command(
    command: list[str],
    cwd: Path | None = None,
    *,
    env: dict[str, str] | None = None,
    stdout=None,
    stderr=None,
) -> None:
    subprocess.run(
        command,
        cwd=cwd or ROOT_DIR,
        env=env,
        check=True,
        stdout=stdout,
        stderr=stderr,
    )


def run_python_install_command(
    command: list[str],
    *,
    step_name: str,
    cleanup_before_retry: Callable[[], None] | None = None,
) -> None:
    source_config = resolve_python_install_source_config()
    log_info(f"{step_name}: {source_config.reason}")

    try:
        run_command(command, env=source_config.env)
    except subprocess.CalledProcessError:
        if not source_config.retry_with_mirror:
            raise

        log_warning(f"{step_name} failed while using official Python sources. Retrying with mirrors.")
        if cleanup_before_retry is not None:
            cleanup_before_retry()

        mirror_config = resolve_python_install_source_config(force_mirror=True)
        log_info(f"{step_name}: {mirror_config.reason}")
        run_command(command, env=mirror_config.env)


def ensure_uv_installed(bootstrap_python_cmd: str) -> list[str]:
    uv_command = get_uv_command(bootstrap_python_cmd)
    if uv_command is not None:
        log_success(f"Detected uv: {' '.join(uv_command)}")
        return uv_command

    log_info(f"uv not found. Installing with: {bootstrap_python_cmd}")
    run_python_install_command(
        with_pip_network_options([bootstrap_python_cmd, "-m", "pip", "install", "uv"]),
        step_name="Install uv",
    )

    uv_command = get_uv_command(bootstrap_python_cmd)
    if uv_command is not None:
        return uv_command

    user_base_completed = subprocess.run(
        [
            bootstrap_python_cmd,
            "-c",
            "import site; print(site.getuserbase())",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    user_base = Path(user_base_completed.stdout.strip())
    candidate_paths = []
    if sys.platform.startswith("win"):
        candidate_paths.append(user_base / "Scripts" / "uv.exe")
    else:
        candidate_paths.append(user_base / "bin" / "uv")

    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return [str(candidate_path)]

    raise RuntimeError(f"uv installation did not succeed for interpreter: {bootstrap_python_cmd}")


def compute_file_hash(file_path: Path) -> str:
    if not file_path.exists():
        return ""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def load_install_state() -> dict:
    if not VERSION_STATE_FILE.exists():
        return {}
    try:
        return json.loads(VERSION_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_install_state(state: dict) -> None:
    VERSION_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def update_install_state() -> None:
    state = {
        "requirements_hash": compute_file_hash(REQUIREMENTS_FILE),
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python_version": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
    }
    save_install_state(state)
