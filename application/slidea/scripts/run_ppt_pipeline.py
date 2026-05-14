#!/usr/bin/env python3
import argparse
import asyncio
import sys
import os
from pathlib import Path
import logging

class NoUnregisteredTypeFilter(logging.Filter):
    def filter(self, record):
        # 如果日志内容包含这段文字，就返回 False (不打印)
        return "Deserializing unregistered type" not in record.getMessage()

logging.getLogger("langgraph.checkpoint.serde.jsonplus").addFilter(NoUnregisteredTypeFilter())


root = Path(__file__).resolve().parents[1]
sys.path.append(str(root))

from core.utils.cache import new_run_id, run_dir, save_json, load_json
from scripts.utils.cli_output import emit_stage_payload
from core.utils.config import settings
from core.utils.logger import logger
from scripts.utils.preflight import print_preflight_report, run_preflight



class SimpleWriter:
    def __call__(self, payload: dict):
        step = payload.get("step")
        text = payload.get("text")
        if step:
            print(f"\n>>> 【当前步骤】 {step}")
        if text:
            print(f"\n>>> {text}")
        files = payload.get("files")
        if files:
            print(f"\n>>> 生成文件：{','.join(str(f) for f in files)}")


async def _load_cached_text(base_dir: str, rel_path: str) -> str:
    p = Path(base_dir) / rel_path
    if p.exists():
        return p.read_text(encoding='utf-8')
    return ""

async def _maybe_require_missing(parsed):
    missing = getattr(parsed, 'missing_info', '') if parsed else ''
    if missing:
        print(f"\n[INPUT REQUIRED] {missing}")
        return missing
    return ""


def _cached_svg_paths_for_outline(save_dir: str, outline) -> list[str]:
    svg_output_dir = Path(save_dir) / "svg_output"
    if not svg_output_dir.exists():
        return []
    paths = []
    for page in sorted(outline, key=lambda item: item.index):
        matches = sorted(svg_output_dir.glob(f"{page.index + 1:02d}_*.svg"))
        if not matches:
            return []
        paths.append(str(matches[0]))
    return paths


async def _try_export_cached_svg(state, out_dir: str, run_id: str) -> bool:
    if state.get("render_mode") != "svg":
        return False
    svg_paths = _cached_svg_paths_for_outline(state.get("save_dir", ""), state.get("outline", []))
    if not svg_paths:
        return False

    from core.ppt_generator.utils.svg_pipeline.quality_checker import check_svg_files, format_quality_issues
    from core.ppt_generator.utils.svg_pipeline.finalize_svg import finalize_svg_files
    from core.ppt_generator.utils.common import sanitize_filename
    from core.ppt_generator.utils.svg_export import svgs_to_pptx

    results = check_svg_files(svg_paths)
    failed = [item for item in results if not item.get("passed")]
    if failed:
        emit_stage_payload(
            "svg_quality_failed",
            {
                "stage": "svg_quality_failed",
                "message": format_quality_issues(results),
            },
            run_id=run_id,
            output_dir=out_dir,
        )
        return True

    final_svgs = finalize_svg_files(svg_paths, state["save_dir"])
    pdf_path, pptx_path = await svgs_to_pptx(final_svgs, state["save_dir"], sanitize_filename(state["topic"]))
    save_json(Path(out_dir) / "ppt.json", {
        "run_id": run_id,
        "topic": state["topic"],
        "render_mode": "svg",
        "render_dir": state["save_dir"],
        "svg_output_dir": str(Path(state["save_dir"]) / "svg_output"),
        "svg_final_dir": str(Path(state["save_dir"]) / "svg_final"),
        "pdf_path": pdf_path,
        "pptx_path": pptx_path,
    })
    state["final_pdf_path"] = pdf_path
    state["final_pptx_path"] = pptx_path
    emit_stage_payload(
        "completed",
        {"stage": "completed", "files": [pdf_path, pptx_path], "used_cache": True},
        run_id=run_id,
        output_dir=out_dir,
    )
    return True


class EmitCtx:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or "local"
        self.payload = {}

    def emit(self, event: str, payload: dict):
        if event == "output.delta":
            text = payload.get("text", "")
            if text:
                print(text, end="", flush=True)

    def require_input(self, message: dict, reason: str, schema: dict, output: dict):
        print("\n[TASK SUSPEND, INPUT REQUIRED]", reason)
        print("\n[INPUT HIT]", message)
        print("\n[INPUT SCHEMA]", schema)
        print("\n[OUTPUT HINT]", output)


def _apply_runtime_overrides(args):
    if args.research_mode:
        settings.RESEARCH_MODE_FORCE = args.research_mode.strip().lower()
    if args.use_cache:
        settings.USE_CACHE = args.use_cache.strip().lower() not in {"0", "false", "no"}
    if args.image_search:
        settings.USE_WEB_IMG_SEARCH = args.image_search.strip().lower() in {"1", "true", "yes", "on"}


def _build_run_metadata(args, run_id: str):
    return {
        "run_id": run_id,
        "session_id": args.session_id,
        "text": args.text,
        "resume": bool(args.resume),
        "stages": args.stages,
        "render_mode": args.render_mode,
        "research_mode": args.research_mode,
        "use_cache": args.use_cache,
        "image_search": args.image_search,
    }


def _cached_run_metadata(out_dir: str) -> dict:
    cached = load_json(Path(out_dir) / "run.json")
    return cached if isinstance(cached, dict) else {}


def _resolve_render_mode(args, cached_run: dict) -> str:
    cached_mode = cached_run.get("render_mode")
    if cached_mode not in {"html", "svg"}:
        cached_mode = ""

    if cached_mode:
        if args.render_mode and args.render_mode != cached_mode:
            print(
                f"[WARNING] ignoring --render-mode {args.render_mode}; "
                f"cached run uses {cached_mode}",
                file=sys.stderr,
            )
        return cached_mode

    if args.render_mode:
        return args.render_mode

    return "html"


async def _run_all_stages(args, run_id: str, out_dir: str):
    from langgraph.types import Command
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from core.ppt_generator.graph import ppt_workflow
    from scripts.utils.pipeline import extract_resume_input, run_thinkflow_app

    if args.text:
        graph_input = {"request": args.text, "render_mode": args.render_mode}
    else:
        resume_value = extract_resume_input({"text": args.resume})
        graph_input = Command(resume=resume_value)

    config = {"configurable": {"thread_id": args.session_id, "run_id": run_id}, "recursion_limit": args.recursion_limit}
    ctx = EmitCtx(session_id=args.session_id)
    db_name = f"slidea_{args.session_id}.sqlite"
    async with AsyncSqliteSaver.from_conn_string(db_name) as checkpointer:
        ppt_app = ppt_workflow.compile(checkpointer=checkpointer)
        result = await run_thinkflow_app(ppt_app, graph_input, config, emit_ctx=ctx)

    if result.get("stage") == "completed":
        for ext in ['', '-shm', '-wal']:
            db_file = db_name + ext
            if os.path.exists(db_file):
                os.remove(db_file)

    emit_stage_payload(result.get("stage", "completed"), result.get("output", {}), run_id=run_id, output_dir=out_dir)


async def _run_staged_pipeline(args, stages: list[str], run_id: str, out_dir: str):
    from core.ppt_generator.thought_to_ppt.node import generate_outline_node, generate_pages_node
    from core.ppt_generator.thought_to_ppt.state import PPTState
    from core.ppt_generator.ppt_thought.node import (
        parse_query_node,
        get_reference_node,
        gather_content_router_node,
        simple_search_node,
        deep_research_node,
        generate_thought_node,
    )
    from core.ppt_generator.ppt_thought.state import ThoughtState
    from core.utils.cache import load_json as cache_load_json

    config = {"configurable": {"thread_id": args.session_id, "run_id": run_id}, "recursion_limit": args.recursion_limit}
    writer = SimpleWriter()

    if "parse" in stages or "research" in stages:
        tstate: ThoughtState = {
            "request": args.text or "",
            "messages": [],
            "raw_content": "",
            "parsed_requirements": None,
            "interaction_count": 0,
            "invalid_reseaon": "",
            "research_mode": "skip",
            "queries": [],
            "search_results": "",
            "research_request": "",
            "deep_report": "",
            "report_file": "",
            "thought": "",
            "references": "",
        }
        if "parse" in stages:
            parsed = await parse_query_node(tstate, config=config)
            tstate.update(parsed)
            missing_info = await _maybe_require_missing(tstate.get("parsed_requirements"))
            if missing_info:
                emit_stage_payload(
                    "missing_required_info",
                    {
                        "stage": "missing_required_info",
                        "failed_stage": "parse",
                        "message": missing_info,
                    },
                    run_id=run_id,
                    output_dir=out_dir,
                )
                return
            ref = await get_reference_node(tstate, writer, config=config)
            tstate.update(ref)

        if "research" in stages:
            if not tstate.get("parsed_requirements"):
                parsed = await parse_query_node(tstate, config=config)
                tstate.update(parsed)
            missing_info = await _maybe_require_missing(tstate.get("parsed_requirements"))
            if missing_info:
                emit_stage_payload(
                    "missing_required_info",
                    {
                        "stage": "missing_required_info",
                        "failed_stage": "research",
                        "message": missing_info,
                    },
                    run_id=run_id,
                    output_dir=out_dir,
                )
                return
            if not tstate.get("raw_content"):
                ref = await get_reference_node(tstate, writer, config=config)
                tstate.update(ref)
            route = await gather_content_router_node(tstate)
            tstate.update(route)
            if tstate.get("research_mode") == "deep":
                tstate.update(await deep_research_node(tstate, writer, config=config))
            elif tstate.get("research_mode") == "simple":
                tstate.update(await simple_search_node(tstate, writer, config=config))
            tstate.update(await generate_thought_node(tstate, config=config, writer=writer))

    state: PPTState = {
        "query": args.text or "",
        "render_mode": args.render_mode,
        "ori_doc": "",
        "is_markdown_doc": False,
        "outline": [],
        "save_dir": "",
        "topic": "",
        "template_name": "",
        "template": "",
        "ppt_prompt": "",
        "language": "",
        "generated_pages": [],
        "page_files": [],
        "final_pdf_path": None,
        "final_pptx_path": None,
    }

    if "outline" in stages:
        deep_report = await _load_cached_text(out_dir, 'research/deep_report.md')
        refs_all = await _load_cached_text(out_dir, 'references/references_all.txt')
        images = cache_load_json(f"{out_dir}/references/images.json") or []
        state["ori_doc"] = deep_report or refs_all or ""
        state["is_markdown_doc"] = True if deep_report else False
        state["images"] = images
        result = await generate_outline_node(state, config=config)
        state.update(result)

    if "render" in stages:
        if not state.get("outline"):
            cached = load_json(f"{out_dir}/outline/outline.json")
            if cached:
                from core.ppt_generator.thought_to_ppt.state import PPTPage
                state["outline"] = [PPTPage(**item) for item in cached.get("outline", [])]
                state["topic"] = cached.get("topic") or ""
        if not state.get("outline"):
            emit_stage_payload(
                "missing_outline",
                {
                    "stage": "missing_outline",
                    "message": "outline not found; cannot render",
                },
                run_id=run_id,
                output_dir=out_dir,
            )
            return
        ppt_cached = load_json(f"{out_dir}/ppt.json")
        if ppt_cached:
            if ppt_cached.get("render_mode"):
                state["render_mode"] = ppt_cached["render_mode"]
            render_dir = ppt_cached.get("render_dir")
            pdf_path = ppt_cached.get("pdf_path")
            if render_dir:
                state["save_dir"] = render_dir
            elif pdf_path:
                state["save_dir"] = str(Path(pdf_path).parent)
        if await _try_export_cached_svg(state, out_dir, run_id):
            return
        state.update(await generate_pages_node(state))
        emit_stage_payload(
            "completed",
            {"stage": "completed", "files": [state.get('final_pdf_path'), state.get('final_pptx_path')]},
            run_id=run_id,
            output_dir=out_dir,
        )
        return

    emit_stage_payload("completed", {"stage": "completed"}, run_id=run_id, output_dir=out_dir)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", type=str, default="")
    parser.add_argument("--resume", type=str, default="")
    parser.add_argument("--session-id", type=str, default="local")
    parser.add_argument("--stages", type=str, default="all")
    parser.add_argument("--research-mode", type=str, default="")
    parser.add_argument("--use-cache", type=str, default="true")
    parser.add_argument("--image-search", type=str, default="on")
    parser.add_argument("--render-mode", type=str, choices=["html", "svg"], default=None)
    parser.add_argument("--run-id", type=str, default="")
    parser.add_argument("--recursion-limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    logger.debug(f"All arguments: {vars(args)}")
    stages = [s.strip() for s in args.stages.split(',') if s.strip()] or ["all"]
    preflight = run_preflight(stages=stages, dry_run=args.dry_run)
    print_preflight_report(preflight)

    if args.dry_run:
        emit_stage_payload(
            "completed",
            {
                "stage": "completed",
                "message": "preflight completed",
                "preflight": preflight,
            },
        )
        return

    if preflight["status"] == "error":
        emit_stage_payload(
            "preflight_failed",
            {
                "stage": "preflight_failed",
                "preflight": preflight,
            },
        )
        return

    if not args.text and not args.resume:
        emit_stage_payload("invalid_request", {"stage": "invalid_request", "message": "missing --text or --resume"})
        return

    _apply_runtime_overrides(args)

    run_id = args.run_id or new_run_id("ppt")
    out_dir = run_dir(str(root), run_id)
    cached_run = _cached_run_metadata(out_dir)
    args.render_mode = _resolve_render_mode(args, cached_run)
    save_json(Path(out_dir) / "run.json", _build_run_metadata(args, run_id))

    if stages == ["all"]:
        await _run_all_stages(args, run_id, out_dir)
        return

    await _run_staged_pipeline(args, stages, run_id, out_dir)


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
