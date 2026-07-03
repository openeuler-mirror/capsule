#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.append(str(root))

from core.deep_research.graph import research_workflow
from core.utils.logger import logger
from core.utils.config import output_files_dir


def _sanitize_filename(name: str) -> str:
    """Inline copy of slidea's sanitize_filename (deep_research manifest does
    not ship core/ppt_generator, so we can't import the original)."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    cleaned = re.sub(r'\s+', "_", cleaned)
    return cleaned.strip()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep Research CLI")
    parser.add_argument("--research-request", help="研究请求/主题")
    parser.add_argument(
        "--session-id",
        default=None,
        help="LangGraph thread/session identifier. If omitted, a unique id "
             "(auto_<pid>_<ts>) is generated. Reuse the same explicit value to "
             "enable --resume / run_id recovery.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Explicit run_id override (skips LLM-based semantic suffix "
             "generation). Rarely needed — auto-recovery from --session-id "
             "handles resume.",
    )
    parser.add_argument("--resume", action="store_true", help="从上次 checkpoint 恢复")
    parser.add_argument("--recursion-limit", type=int, default=500, help="LangGraph recursion limit")
    return parser


async def new_semantic_run_id(text: str, fallback_prefix: str = "research") -> str:
    """Build a run_id as <timestamp>_<llm_summary>.

    Mirrors slidea's new_semantic_run_id so deep_research standalone output
    directories have the same <ts>_<semantic> shape and sort lexically by time.
    Falls back to <ts>_<fallback_prefix> on any LLM failure so run_id is always
    valid.
    """
    from core.utils.llm import InvokeOptions, ModelRoute, llm_invoke
    from langchain.messages import HumanMessage

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    if not text or not text.strip():
        return f"{ts}_{fallback_prefix}"

    prompt = (
        "请把下面的请求总结为一个简短的、适合作为目录名的中文短语,"
        "不超过 20 个中文字符。\n"
        "只输出总结本身,不要引号、不要标点、不要任何说明文字。\n"
        "优先使用请求中的核心名词或主题,保持语义性。\n\n"
        f"请求:{text.strip()[:1000]}"
    )
    try:
        response = await llm_invoke(
            ModelRoute.DEFAULT,
            [HumanMessage(content=prompt)],
            InvokeOptions(work_node="run_id_summary"),
        )
        summary = _sanitize_filename(response.strip())[:30]
        if summary and summary != "slide":
            return f"{ts}_{summary}"
    except Exception as error:
        logger.warning(f"semantic run_id generation failed, falling back: {error}")
    return f"{ts}_{fallback_prefix}"


def _find_run_id_by_session(output_root: str, session_id: str) -> str | None:
    """Locate the ORIGINAL run_id whose run.json recorded the given session_id.

    Mirrors slidea's collision-aware recovery: returns the run_id when exactly
    one original (non-resume) run.json matches; refuses to guess and returns
    None when multiple runs share the session_id (typical when users reuse
    a value across unrelated tasks).
    """
    if not session_id:
        return ""
    output_path = Path(output_root)
    if not output_path.is_dir():
        return ""

    matches: list[tuple[str, str, str]] = []  # (run_id, run_json_path, request_preview)
    for run_json in output_path.glob("*/run.json"):
        try:
            with open(run_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as error:
            logger.warning(f"failed to load {run_json}: {error}")
            continue
        if not isinstance(data, dict):
            continue
        if data.get("session_id") != session_id:
            continue
        if data.get("resume"):
            # Resume-attempt record, not the original. Skip.
            continue
        rid = data.get("run_id")
        if not (isinstance(rid, str) and rid):
            continue
        request_preview = (data.get("research_request") or "")[:80]
        matches.append((rid, str(run_json), request_preview))

    if not matches:
        return ""
    if len(matches) == 1:
        rid, _, request_preview = matches[0]
        logger.info(
            f"Recovered run_id={rid!r} for session_id={session_id!r} "
            f"(original request: {request_preview!r})"
        )
        return rid
    run_ids = [m[0] for m in matches]
    logger.warning(
        f"session_id {session_id!r} matches {len(matches)} original runs: {run_ids}. "
        f"Refusing to recover — could write into the wrong run directory. "
        f"Disambiguate by passing --run-id <one_of_above> explicitly, or use a "
        f"unique --session-id for each unrelated task."
    )
    return None


def _write_run_metadata(run_json_path: str, args, run_id: str, resume: bool) -> None:
    """Write the session_id ↔ run_id binding record (mirrors slidea's run.json).

    The original (non-resume) record is what _find_run_id_by_session looks for
    later; explicit --run-id resume attempts can write resume=true in a fresh
    directory, but normal resume recovery preserves the original record.
    """
    record = {
        "run_id": run_id,
        "session_id": args.session_id,
        "research_request": args.research_request or "",
        "resume": resume,
        "research_depth": 2,
    }
    os.makedirs(os.path.dirname(run_json_path), exist_ok=True)
    with open(run_json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


async def _run_research(args, run_id: str) -> None:
    workspace_dir = os.path.join(output_files_dir, run_id)
    os.makedirs(workspace_dir, exist_ok=True)

    run_json_path = os.path.join(workspace_dir, "run.json")
    # Write the binding record once and preserve the original, which is what
    # _find_run_id_by_session scans for during resume recovery.
    if not Path(run_json_path).exists():
        _write_run_metadata(run_json_path, args, run_id, resume=args.resume)

    config = {
        "configurable": {
            "thread_id": args.session_id,
            "workspace_dir": workspace_dir,
        },
        "recursion_limit": args.recursion_limit,
    }

    payload = {
        "research_request": args.research_request or "",
        "research_depth": 2,
    }

    db_name = os.path.join(workspace_dir, "checkpointer.sqlite")
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async with AsyncSqliteSaver.from_conn_string(db_name) as checkpointer:
            app = research_workflow.compile(checkpointer=checkpointer)
            result = await app.ainvoke(payload, config=config)
    except Exception as e:
        logger.info("research failed {}, Use --resume --session-id {} to retry", e, args.session_id)
        return

    for ext in ['', '-shm', '-wal']:
        db_file = db_name + ext
        if os.path.exists(db_file):
            os.remove(db_file)

    report_file = (result or {}).get("report_file") or ""
    logger.info("Deep research completed. Report saved to: {}", report_file)
    logger.info("run_id={} session_id={}", run_id, args.session_id)


async def _resolve_run_id(args) -> str | None:
    """Resolve run_id: explicit --run-id > resume recovery > new semantic.

    Mirrors slidea's three-branch logic so deep_research users get the same
    --session-id based recovery experience.
    """
    if args.run_id:
        return args.run_id
    if args.resume:
        # Strict: must find prior run, else fail loudly (cannot resume a run
        # that was never started).
        recovered = _find_run_id_by_session(output_files_dir, args.session_id)
        if recovered is None:
            logger.error(
                f"resume requested but session_id={args.session_id!r} is ambiguous. "
                f"Pass --run-id explicitly to choose the intended run."
            )
            return None
        if not recovered:
            logger.error(
                f"resume requested but no prior run.json matches "
                f"session_id={args.session_id!r}. Start a new run with "
                f"--research-request instead."
            )
            return None
        return recovered
    return await new_semantic_run_id(args.research_request or "")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.research_request and not args.resume:
        logger.error("--research-request is required for new runs")
        return 1

    # Auto-generate a unique session-id when none was provided. Prevents the
    # historical "local" default from colliding across unrelated deep_research
    # runs (collisions would let --resume silently write into the wrong run).
    if not args.session_id:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        args.session_id = f"auto_{os.getpid()}_{ts}"
        logger.info(f"No --session-id provided; generated {args.session_id!r}. "
                    f"Pass --session-id explicitly to enable --resume.")

    run_id = asyncio.run(_resolve_run_id(args))
    if not run_id:
        return 1

    logger.info(f"run_id={run_id!r} session_id={args.session_id!r}")
    asyncio.run(_run_research(args, run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
