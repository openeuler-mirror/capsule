from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.utils.logger import logger


SessionResolutionStatus = Literal["found", "not_found", "ambiguous"]


@dataclass(frozen=True)
class SessionRunResolution:
    """Result of resolving a public session id to an internal run directory."""

    status: SessionResolutionStatus
    run_ids: tuple[str, ...] = ()

    @property
    def run_id(self) -> str:
        return self.run_ids[0] if self.status == "found" and self.run_ids else ""


def resolve_run_by_session(output_root: str | Path, session_id: str) -> SessionRunResolution:
    """Resolve one original run for ``session_id`` without ever guessing.

    ``run_id`` is an internal storage key. Agents normally retain only the
    public ``session_id`` and use this resolver for continuation, staged work,
    and patch rendering. Historical ``resume: true`` records are ignored
    because they are attempts to continue an original run, not new runs.
    """
    if not session_id:
        return SessionRunResolution("not_found")

    output_path = Path(output_root)
    if not output_path.is_dir():
        return SessionRunResolution("not_found")

    matches: dict[str, Path] = {}
    for run_json in output_path.glob("*/run.json"):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning(f"failed to load {run_json}: {error}")
            continue
        if not isinstance(data, dict):
            continue
        if data.get("session_id") != session_id or data.get("resume"):
            continue
        run_id = data.get("run_id")
        if isinstance(run_id, str) and run_id:
            matches[run_id] = run_json

    run_ids = tuple(sorted(matches))
    if not run_ids:
        return SessionRunResolution("not_found")
    if len(run_ids) == 1:
        logger.info(
            f"Recovered run_id={run_ids[0]!r} for session_id={session_id!r}"
        )
        return SessionRunResolution("found", run_ids)

    logger.warning(
        f"session_id {session_id!r} matches {len(run_ids)} original runs: "
        f"{list(run_ids)}. Refusing to guess."
    )
    return SessionRunResolution("ambiguous", run_ids)


def session_resolution_message(
    resolution: SessionRunResolution,
    session_id: str,
    *,
    action: str,
) -> str:
    """Return a concise structured-CLI error for a failed resolution."""
    if resolution.status == "ambiguous":
        return (
            f"cannot {action}: session_id={session_id!r} matches multiple runs "
            f"({', '.join(resolution.run_ids)}). Use a unique session id; "
            "--run-id is reserved for manual disambiguation."
        )
    return (
        f"cannot {action}: no prior run.json matches session_id={session_id!r}."
    )
