#!/usr/bin/env python3
from __future__ import annotations

import time
from pathlib import Path

try:
    from ._common import (
        REQUIREMENTS_FILE,
        StepIssue,
        VENV_DIR,
        compute_file_hash,
        ensure_uv_installed,
        format_duration,
        get_bootstrap_python_command,
        get_venv_python_path,
        load_install_state,
        log_info,
        log_step,
        log_success,
        record_step_failure,
        run_python_install_command,
        update_install_state,
    )
except ImportError:  # pragma: no cover - support direct script execution
    from _common import (
        REQUIREMENTS_FILE,
        StepIssue,
        VENV_DIR,
        compute_file_hash,
        ensure_uv_installed,
        format_duration,
        get_bootstrap_python_command,
        get_venv_python_path,
        load_install_state,
        log_info,
        log_step,
        log_success,
        record_step_failure,
        run_python_install_command,
        update_install_state,
    )


def print_post_update_summary(issues: list[StepIssue]) -> None:
    print("\n" + "=" * 52)
    status_line = "The Slidea skill has been updated successfully."
    if issues:
        status_line = "The Slidea skill update did not complete successfully."
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
"""
    )
    print("=" * 52)


def check_requirements_changed() -> bool:
    state = load_install_state()
    current_hash = compute_file_hash(REQUIREMENTS_FILE)
    stored_hash = state.get("requirements_hash", "")
    return current_hash != stored_hash


def update_dependencies(venv_python: Path, bootstrap_python_cmd: str) -> None:
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"missing requirements file: {REQUIREMENTS_FILE}")

    uv_command = ensure_uv_installed(bootstrap_python_cmd)
    run_python_install_command(
        [*uv_command, "pip", "install", "--python", str(venv_python), "-r", str(REQUIREMENTS_FILE)],
        step_name="Update Python dependencies",
    )


def main() -> int:
    import sys

    if len(sys.argv) > 1:
        log_info("Arguments are ignored. The update script uses local files only.")

    issues: list[StepIssue] = []

    log_info("Checking requirements.txt for changes...")
    requirements_changed = check_requirements_changed()

    if not requirements_changed:
        log_success("requirements.txt unchanged. No updates needed.")
        return 0

    log_info("requirements.txt has changed. Updating dependencies...")

    bootstrap_python_cmd = get_bootstrap_python_command()
    venv_python = get_venv_python_path()

    if not VENV_DIR.exists() or not venv_python.exists():
        log_info("Virtual environment not found. Please run install.py first.")
        return 1

    step_start = time.perf_counter()
    step_no = 0
    deps_ready = False

    step_no += 1
    log_step(step_no, "Update Python Dependencies")
    try:
        update_dependencies(venv_python, bootstrap_python_cmd)
        deps_ready = True
        log_success(
            f"Python dependencies updated. Duration: {format_duration(time.perf_counter() - step_start)}"
        )
    except Exception as exc:
        record_step_failure(issues, step_no, "Update Python Dependencies", exc)

    step_start = time.perf_counter()
    step_no += 1
    log_step(step_no, "Update Install State")
    try:
        update_install_state()
        log_success(
            f"Install state updated. Duration: {format_duration(time.perf_counter() - step_start)}"
        )
    except Exception as exc:
        record_step_failure(issues, step_no, "Update Install State", exc)

    print_post_update_summary(issues)
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
