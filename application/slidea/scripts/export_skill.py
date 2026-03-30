#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT_DIR / "skill" / "manifest.json"


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"missing skill manifest: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _matches_any_pattern(path: Path, patterns: list[str]) -> bool:
    normalized_path = path.as_posix()
    return any(
        fnmatch.fnmatch(normalized_path, pattern) or fnmatch.fnmatch(path.name, pattern)
        for pattern in patterns
    )


def _ignore_for_directory(source_dir: Path, exclude_patterns: list[str]):
    def _ignore(_current_dir: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        current_path = Path(_current_dir)
        for name in names:
            candidate = current_path / name
            relative_candidate = candidate.relative_to(ROOT_DIR)
            if _matches_any_pattern(relative_candidate, exclude_patterns):
                ignored.add(name)
        return ignored

    return _ignore


def _copy_entry(source: Path, destination: Path, exclude_patterns: list[str]) -> None:
    if not source.exists():
        raise FileNotFoundError(f"missing skill-package source path: {source}")

    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True,
            ignore=_ignore_for_directory(source, exclude_patterns) if exclude_patterns else None,
        )
        return

    if _matches_any_pattern(source.relative_to(ROOT_DIR), exclude_patterns):
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def export_skill(target_dir: Path, *, force: bool = False, update: bool = False) -> Path:
    manifest = _load_manifest()
    exclude_patterns = manifest.get("exclude", [])
    include_entries = manifest.get("include", [])

    if not include_entries:
        raise ValueError(f"skill manifest has no include entries: {MANIFEST_PATH}")

    if target_dir.exists() and not update:
        if not force:
            raise FileExistsError(
                f"target directory already exists: {target_dir}. Pass --force to replace it or --update to overwrite."
            )
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    for entry in include_entries:
        source = ROOT_DIR / entry["from"]
        destination = target_dir / entry["to"]
        _copy_entry(source, destination, exclude_patterns)

    return target_dir


def bootstrap_skill(target_dir: Path) -> None:
    install_script = target_dir / "scripts" / "install" / "install.py"
    if not install_script.exists():
        raise FileNotFoundError(f"missing installer in exported skill package: {install_script}")

    subprocess.run(
        [sys.executable, str(install_script)],
        cwd=target_dir,
        check=True,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="Final output directory for the exported skill package.")
    parser.add_argument("--force", action="store_true", help="Replace the target directory if it already exists.")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Overwrite files in the target directory without deleting it. Preserves .env, .venv, .install_state.json.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="After export, run the exported scripts/install/install.py inside the target directory.",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    target_dir = Path(args.target).expanduser().resolve()
    exported_dir = export_skill(target_dir, force=args.force, update=args.update)
    print(f"Exported skill package to: {exported_dir}")
    if args.bootstrap:
        print(f"Bootstrapping exported skill package in: {exported_dir}")
        bootstrap_skill(exported_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
