#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.append(str(root))

from core.deep_research.graph import research_workflow
from core.utils.logger import logger
from core.utils.config import output_files_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep Research CLI")
    parser.add_argument("--research-request", help="研究请求/主题")
    parser.add_argument(
        "--session-id",
        default=None,
        help="session / thread id. If omitted, a unique id (auto_<pid>_<ts>) is "
             "generated so unrelated runs never collide. Pass an explicit value "
             "when you intend to --resume an existing run.",
    )
    parser.add_argument("--resume", action="store_true", help="从上次 checkpoint 恢复")
    parser.add_argument("--recursion-limit", type=int, default=500, help="LangGraph recursion limit")
    return parser


async def _run_research(args) -> None:
    workspace_dir = os.path.join(output_files_dir, args.session_id)
    os.makedirs(workspace_dir, exist_ok=True)

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
        logger.info("research failed {}, Use --resume --thread-id {} to retry", e, args.session_id)
        return

    for ext in ['', '-shm', '-wal']:
        db_file = db_name + ext
        if os.path.exists(db_file):
            os.remove(db_file)

    report_file = (result or {}).get("report_file") or ""
    logger.info("Deep research completed. Report saved to: {}", report_file)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.research_request and not args.resume:
        logger.error("--research-request is required for new runs")
        return 1

    # Auto-generate a unique session-id when none was provided. Prevents the
    # historical "local" default from colliding across unrelated deep_research
    # runs (each session writes to <output>/<session_id>/, so collisions would
    # contaminate the wrong session's directory).
    if not args.session_id:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        args.session_id = f"auto_{os.getpid()}_{ts}"
        logger.info(f"No --session-id provided; generated {args.session_id!r}. "
                    f"Pass --session-id explicitly to enable --resume.")

    asyncio.run(_run_research(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())