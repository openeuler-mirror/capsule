import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from core.utils.config import Settings, app_base_dir, env_file


LINUX_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice26.2", "libreoffice", "soffice")
MACOS_SYSTEM_LIBREOFFICE_CANDIDATES = ("libreoffice", "soffice")
WINDOWS_SYSTEM_LIBREOFFICE_CANDIDATES = ("soffice.com", "soffice.exe", "libreoffice", "soffice")


def _result(name: str, status: str, message: str) -> dict:
    return {"name": name, "status": status, "message": message}


def check_env_setup(settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    if not env_file.exists():
        return _result(
            "env_setup",
            "error",
            f"Missing {env_file}. Run `python3 scripts/install/install.py` before using pipeline commands.",
        )

    if not settings.SETUP_COMPLETED:
        return _result(
            "env_setup",
            "warning",
            f"{env_file} exists but `SETUP_COMPLETED=true` is missing. Run `python3 scripts/install/install.py` first.",
        )

    return _result("env_setup", "ok", f"{env_file} exists and `SETUP_COMPLETED=true` is present.")


def check_runtime_python() -> dict:
    expected_venv_dir = app_base_dir / ".venv"
    expected_candidates = {
        expected_venv_dir,
        expected_venv_dir.resolve(strict=False),
    }
    executable = Path(sys.executable)
    executable_candidates = {
        executable,
        executable.resolve(strict=False),
    }
    runtime_prefix = Path(sys.prefix)
    runtime_prefix_candidates = {
        runtime_prefix,
        runtime_prefix.resolve(strict=False),
    }
    virtual_env = os.environ.get("VIRTUAL_ENV", "").strip()
    virtual_env_candidates = set()
    if virtual_env:
        virtual_env_path = Path(virtual_env)
        virtual_env_candidates.add(virtual_env_path)
        virtual_env_candidates.add(virtual_env_path.resolve(strict=False))

    inside_project_venv = any(candidate in expected_candidates for candidate in runtime_prefix_candidates)
    if not inside_project_venv:
        inside_project_venv = any(candidate in expected_candidates for candidate in virtual_env_candidates)
    if not inside_project_venv:
        inside_project_venv = any(
            expected in candidate.parents or candidate == expected
            for candidate in executable_candidates
            for expected in expected_candidates
        )

    if inside_project_venv:
        return _result(
            "runtime_python",
            "ok",
            f"Using the project virtualenv interpreter: {executable}",
        )

    return _result(
        "runtime_python",
        "warning",
        "Pipeline commands must run with the Python interpreter inside "
        f"{expected_venv_dir.resolve(strict=False)}; "
        f"current executable is {executable.resolve(strict=False)}.",
    )


def _browser_smoke_test_code() -> str:
    return textwrap.dedent(
        """
        import asyncio
        from playwright.async_api import async_playwright

        async def main():
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            await browser.close()
            await playwright.stop()

        asyncio.run(main())
        """
    )


def _run_browser_smoke_test() -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _browser_smoke_test_code()],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return False, "Chromium launch timed out during the Playwright smoke check."
    except OSError as exc:
        return False, f"Failed to execute the Playwright smoke check: {exc}"

    if completed.returncode == 0:
        return True, "Playwright and Chromium are available for HTML-to-PDF export."

    combined_output = "\n".join(part for part in [completed.stderr, completed.stdout] if part).strip()
    lines = [line.strip() for line in combined_output.splitlines() if line.strip()]
    priority_markers = (
        "FATAL:",
        "TargetClosedError",
        "Executable doesn't exist",
        "ModuleNotFoundError",
        "BrowserType.launch:",
        "Operation not permitted",
    )
    detail_text = next(
        (line for line in lines if any(marker in line for marker in priority_markers)),
        lines[-1] if lines else "unknown browser launch failure",
    )
    return False, f"Playwright is installed, but Chromium could not be launched for HTML-to-PDF export: {detail_text}"


def check_browser_runtime() -> dict:
    if importlib.util.find_spec("playwright") is None:
        return _result(
            "browser",
            "warning",
            "Playwright is not installed. HTML-to-PDF export for render/all runs will be unavailable until it is installed.",
        )
    ok, message = _run_browser_smoke_test()
    return _result("browser", "ok" if ok else "warning", message)


def _iter_libreoffice_candidates() -> list[Path]:
    system_name = platform.system()
    candidates: list[Path] = []
    bundled_dir = app_base_dir / "libreoffice"

    if system_name == "Windows":
        system_candidates = WINDOWS_SYSTEM_LIBREOFFICE_CANDIDATES
    elif system_name == "Darwin":
        system_candidates = MACOS_SYSTEM_LIBREOFFICE_CANDIDATES
    else:
        system_candidates = LINUX_SYSTEM_LIBREOFFICE_CANDIDATES

    for command_name in system_candidates:
        executable = shutil.which(command_name)
        if executable:
            candidates.append(Path(executable))

    if system_name == "Windows":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates.append(program_files / "LibreOffice" / "program" / "soffice.com")
    elif system_name == "Darwin":
        candidates.append(bundled_dir / "LibreOffice.app" / "Contents" / "MacOS" / "soffice")
    else:
        candidates.append(bundled_dir / "libreoffice-app" / "AppRun")

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = os.path.normcase(str(candidate))
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        unique_candidates.append(candidate)
    return unique_candidates


def check_libreoffice_runtime() -> dict:
    for candidate in _iter_libreoffice_candidates():
        if not candidate.exists():
            continue
        try:
            completed = subprocess.run(
                [str(candidate), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except OSError:
            continue
        except subprocess.TimeoutExpired:
            continue

        if completed.returncode == 0:
            version_line = (completed.stdout or completed.stderr).strip().splitlines()
            version_text = version_line[0] if version_line else "version check succeeded"
            return _result(
                "libreoffice",
                "ok",
                f"LibreOffice is available for PDF-to-PPTX conversion via {candidate} ({version_text}).",
            )

    return _result(
        "libreoffice",
        "warning",
        "LibreOffice is not available, PDF export can still run, but PDF-to-PPTX conversion may be skipped.",
    )


def run_preflight(
    settings: Settings | None = None,
    *,
    stages: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    settings = settings or Settings()
    stages = stages or ["all"]
    checks = []

    checks.append(check_env_setup(settings))
    checks.append(check_runtime_python())
    if "all" in stages or "render" in stages:
        checks.append(check_browser_runtime())

    missing_llm = settings.missing_default_llm_settings()
    if missing_llm:
        checks.append(
            _result(
                "default_llm",
                "error",
                "Missing default LLM settings: " + ", ".join(missing_llm),
            )
        )
    else:
        checks.append(_result("default_llm", "ok", "Default LLM settings are configured."))

    if settings.get_slidea_mode() == "PREMIUM":
        if not settings.has_premium_llm_api_key():
            checks.append(
                _result(
                    "premium_llm",
                    "warning",
                    "Premium mode is enabled, but PREMIUM_LLM_API_KEY is empty. "
                    "Runtime will fall back to ECONOMIC mode and use default models.",
                )
            )
        else:
            missing_premium = settings.missing_premium_llm_settings()
            if missing_premium:
                checks.append(
                    _result(
                        "premium_llm",
                        "warning",
                        "Premium mode is enabled, but PREMIUM_LLM settings are incomplete. "
                        "Runtime will fall back to ECONOMIC mode and use default models: "
                        + ", ".join(missing_premium),
                    )
                )
            else:
                checks.append(_result("premium_llm", "ok", "Premium LLM settings are configured."))

    if settings.has_tavily_search_config():
        checks.append(_result("tavily", "ok", "Tavily is configured for web search and image search."))
    else:
        checks.append(
            _result(
                "tavily",
                "warning",
                "Tavily is not configured, so web search and image search will be skipped.",
            )
        )

    missing_vlm = settings.missing_default_vlm_settings()
    if missing_vlm:
        checks.append(
            _result(
                "default_vlm",
                "warning",
                "Default VLM settings are not configured, so PPT reflection will be skipped, may increase layout anomalies.",
            )
        )
    else:
        checks.append(_result("default_vlm", "ok", "Default VLM settings are configured."))

    if settings.DISABLE_EMBEDDING:
        checks.append(
            _result(
                "embedding",
                "warning",
                "Embedding is explicitly disabled, so deep research will be skipped.",
            )
        )
    elif settings.missing_embedding_settings():
        checks.append(
            _result(
                "embedding",
                "warning",
                "Embedding settings are incomplete, so deep research will be skipped.",
            )
        )
    else:
        checks.append(_result("embedding", "ok", "Embedding settings are configured."))

    if "all" in stages or "render" in stages:
        checks.append(check_libreoffice_runtime())

    status = "ok"
    if any(item["status"] == "error" for item in checks):
        status = "error"
    elif any(item["status"] == "warning" for item in checks):
        status = "warning"

    return {"status": status, "checks": checks}


def print_preflight_report(preflight: dict) -> None:
    visible_checks = [item for item in preflight.get("checks", []) if item["status"] in {"warning", "error"}]
    if not visible_checks:
        return

    print("\nPreflight checks:")
    for item in visible_checks:
        print(f"[{item['status'].upper()}] {item['name']}: {item['message']}")
