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
from core.utils.config import output_files_dir, settings
from core.utils.logger import logger
from scripts.utils.preflight import print_preflight_report, run_preflight


async def new_semantic_run_id(text: str, fallback_prefix: str = "ppt") -> str:
    """Build a run_id as <timestamp>_<llm_summary>. Falls back to <ts>_<prefix> on any failure."""
    from datetime import datetime, timezone
    from core.utils.llm import InvokeOptions, ModelRoute, llm_invoke
    from langchain.messages import HumanMessage
    from core.ppt_generator.utils.common import sanitize_filename

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
        summary = sanitize_filename(response.strip())[:30]
        if summary and summary != "slide":
            return f"{ts}_{summary}"
    except Exception as error:
        logger.warning(f"semantic run_id generation failed, falling back: {error}")
    return f"{ts}_{fallback_prefix}"



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
        source_dir = payload.get("source_dir")
        if source_dir:
            print(f"\n>>> 源文件目录：{source_dir}")


async def _load_cached_text(base_dir: str, rel_path: str) -> str:
    p = Path(base_dir) / rel_path
    if p.exists():
        return p.read_text(encoding='utf-8')
    return ""


def _find_run_id_by_session(output_root: str, session_id: str) -> str:
    """Locate the ORIGINAL run_id whose run.json recorded the given session_id.

    Used when resuming: the original run_id (potentially carrying a semantic
    suffix) is recovered instead of generating a fresh fallback id. Returns
    an empty string when no match is found.

    A session_id can legitimately appear in multiple run.json files — every
    resume attempt writes a new run.json. We filter out those `resume: true`
    records to find the original run that started the session.
    """
    if not session_id:
        return ""
    root = Path(output_root)
    if not root.is_dir():
        return ""
    for run_json in root.glob("*/run.json"):
        try:
            data = load_json(str(run_json))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("session_id") != session_id:
            continue
        if data.get("resume"):
            # This is a resume-attempt record, not the original. Skip.
            continue
        rid = data.get("run_id")
        if isinstance(rid, str) and rid:
            return rid
    return ""

async def _maybe_require_missing(parsed):
    missing = getattr(parsed, 'missing_info', '') if parsed else ''
    if missing:
        print(f"\n[INPUT REQUIRED] {missing}")
        return missing
    return ""


def _cached_svg_paths_for_outline(save_dir: str, outline) -> list[str]:
    svg_dir = Path(save_dir) / "svg"
    if not svg_dir.exists():
        return []
    paths = []
    for page in sorted(outline, key=lambda item: item.index):
        matches = sorted(svg_dir.glob(f"{page.index + 1:02d}_*.svg"))
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

    pdf_path, pptx_path = await svgs_to_pptx(svg_paths, out_dir, sanitize_filename(state["topic"]))
    save_json(Path(out_dir) / "ppt.json", {
        "run_id": run_id,
        "topic": state["topic"],
        "render_mode": "svg",
        "slides_dir": state["save_dir"],
        "svg_dir": str(Path(state["save_dir"]) / "svg"),
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

    return "svg"


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
            slides_dir = ppt_cached.get("slides_dir")
            pdf_path = ppt_cached.get("pdf_path")
            if slides_dir:
                state["save_dir"] = slides_dir
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
    # Preflight 仅关心用户意图：显式 --render-mode html 才需要 HTML 路线相关的
    # 运行时检查（playwright/libreoffice）。其他情况（默认或显式 svg）按 SVG
    # 路线检查，不依赖这两个组件。完整的 render_mode 解析（含 cached run 回填）
    # 在 preflight 之后再做。
    preflight_render_mode = args.render_mode or "svg"
    preflight = run_preflight(stages=stages, dry_run=args.dry_run, render_mode=preflight_render_mode)
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

    if args.run_id:
        run_id = args.run_id
    elif args.resume:
        # Resume must reuse the original run_id so cache and artifacts stay
        # in a single directory. Recover it from any prior run.json matching
        # the session_id; fail loudly if none exists (cannot resume a run
        # that was never started).
        recovered = _find_run_id_by_session(output_files_dir, args.session_id)
        if not recovered:
            emit_stage_payload(
                "invalid_request",
                {
                    "stage": "invalid_request",
                    "message": (
                        f"resume requested but no prior run.json matches "
                        f"session_id={args.session_id!r}. Start a new run "
                        f"with --text instead."
                    ),
                },
            )
            return
        run_id = recovered
    else:
        run_id = await new_semantic_run_id(args.text or "")
    out_dir = run_dir(run_id)
    cached_run = _cached_run_metadata(out_dir)
    args.render_mode = _resolve_render_mode(args, cached_run)
    # run.json is the snapshot of the INITIAL run that started this session.
    # Subsequent resume calls must not overwrite it — otherwise the resume:true
    # marker hides the original record and breaks session-id-based lookup.
    run_json_path = Path(out_dir) / "run.json"
    if not run_json_path.exists():
        save_json(run_json_path, _build_run_metadata(args, run_id))

    if stages == ["all"]:
        await _run_all_stages(args, run_id, out_dir)
        return

    await _run_staged_pipeline(args, stages, run_id, out_dir)


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
