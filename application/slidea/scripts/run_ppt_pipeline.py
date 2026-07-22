#!/usr/bin/env python3
import argparse
import asyncio
import sys
import os
from pathlib import Path
import logging
from urllib.parse import unquote, urlparse

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
from scripts.utils.run_identity import (
    resolve_run_by_session,
    session_resolution_message,
)


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


async def _maybe_require_missing(parsed):
    missing = getattr(parsed, 'missing_info', '') if parsed else ''
    if missing:
        print(f"\n[INPUT REQUIRED] {missing}")
        return missing
    return ""


def _cached_svg_paths_for_outline(save_dir: str, outline) -> list[str]:
    svg_dir = Path(save_dir)
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
        "svg_dir": state["save_dir"],
        "pdf_path": pdf_path,
        "pptx_path": pptx_path,
        "style_pack_dir": state.get("style_pack_dir", ""),
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


def _reference_basename(value: str) -> str:
    """Return a comparable filename for local paths, file URLs and web URLs."""
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    normalized = unquote(path).replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1]


def _style_source_in_request(text: str, style_pack_dir: str) -> str:
    """Detect accidental reuse of a style pack's source PPTX as request content."""
    if not text or not style_pack_dir:
        return ""
    try:
        manifest = load_json(Path(style_pack_dir) / "style-pack.json")
    except (OSError, ValueError):
        # Invalid packs are handled later by _resolve_style_pack, which preserves
        # the established built-in-template fallback instead of crashing here.
        return ""
    if not isinstance(manifest, dict):
        return ""
    source = manifest.get("source")
    if not isinstance(source, str) or not source.strip():
        return ""
    source_name = _reference_basename(source.strip())
    if not source_name.lower().endswith(".pptx"):
        return ""
    request_text = unquote(text).casefold()
    return source_name if source_name.casefold() in request_text else ""


def _build_run_metadata(args, run_id: str):
    return {
        "run_id": run_id,
        "session_id": args.session_id,
        "text": args.text,
        "resume": bool(args.resume),
        "continue": bool(args.continue_run),
        "stages": args.stages,
        "render_mode": args.render_mode,
        "research_mode": args.research_mode,
        "use_cache": args.use_cache,
        "image_search": args.image_search,
        "style_pack_dir": args.style_pack,
        "allow_style_source_content": bool(args.allow_style_source_content),
    }


def _inherit_cached_runtime_options(args, cached_run: dict) -> None:
    """Keep a resumed graph on the immutable configuration of its first run."""
    for name in ("research_mode", "use_cache", "image_search"):
        cached_value = cached_run.get(name)
        if cached_value is not None:
            setattr(args, name, cached_value)


def _emit_session_resolution_error(resolution, session_id: str, *, action: str) -> None:
    emit_stage_payload(
        "invalid_request",
        {
            "stage": "invalid_request",
            "message": session_resolution_message(
                resolution,
                session_id,
                action=action,
            ),
        },
    )


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


def _resolve_style_pack(args, cached_run: dict, out_dir: str) -> str:
    """Snapshot a requested pack into the run; invalid packs safely disable it."""
    cached_style_pack = cached_run.get("style_pack_dir") or ""
    if cached_style_pack:
        if args.style_pack and Path(args.style_pack).resolve() != Path(cached_style_pack).resolve():
            logger.warning(
                "ignoring a different --style-pack during resume/staged execution; "
                "the run keeps its original immutable snapshot"
            )
        try:
            from core.ppt_generator.utils.style_pack import validate_style_pack
            validate_style_pack(cached_style_pack)
            return str(Path(cached_style_pack).resolve())
        except Exception as error:
            logger.warning(f"cached style pack is invalid; using built-in template fallback: {error}")
            return ""
    run_snapshot = Path(out_dir) / "style_pack"
    if run_snapshot.is_dir():
        try:
            from core.ppt_generator.utils.style_pack import validate_style_pack
            validate_style_pack(run_snapshot)
            return str(run_snapshot.resolve())
        except Exception as error:
            logger.warning(f"run style-pack snapshot is invalid; using built-in template fallback: {error}")
            return ""
    if not args.style_pack:
        return ""
    try:
        from core.ppt_generator.utils.style_pack import copy_style_pack_into_run
        return copy_style_pack_into_run(args.style_pack, out_dir)
    except Exception as error:
        logger.warning(f"failed to prepare --style-pack; using built-in template fallback: {error}")
        return ""


async def _run_all_stages(args, run_id: str, out_dir: str):
    from langgraph.types import Command
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from core.ppt_generator.graph import ppt_workflow
    from scripts.utils.pipeline import extract_resume_input, run_thinkflow_app

    if args.continue_run:
        # LangGraph distinguishes process/checkpoint continuation from a
        # human-in-the-loop reply. None resumes unfinished checkpoint tasks;
        # Command(resume=...) below supplies a user's answer to interrupt().
        graph_input = None
    elif args.text:
        graph_input = {
            "request": args.text,
            "render_mode": args.render_mode,
            "style_pack_dir": args.style_pack,
        }
    else:
        resume_value = extract_resume_input({"text": args.resume})
        graph_input = Command(resume=resume_value)

    config = {"configurable": {"thread_id": args.session_id, "run_id": run_id}, "recursion_limit": args.recursion_limit}
    ctx = EmitCtx(session_id=args.session_id)
    db_name = os.path.join(out_dir, "checkpointer.sqlite")
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
        "style_pack_dir": args.style_pack,
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
        state.update(await generate_pages_node(state, config=config))
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
    parser.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help=(
            "Continue unfinished work from an existing LangGraph checkpoint. "
            "Use this after timeout/process termination; unlike --resume it "
            "does not supply a human-in-the-loop answer."
        ),
    )
    parser.add_argument("--session-id", type=str, default=None,
                        help="LangGraph thread/session identifier. If omitted, a "
                             "unique id (auto_<pid>_<ts>) is generated so unrelated "
                             "runs never collide. Pass an explicit value when you "
                             "intend to resume or run --stages against an existing run.")
    parser.add_argument("--stages", type=str, default="all")
    parser.add_argument("--research-mode", type=str, default="")
    parser.add_argument("--use-cache", type=str, default="true")
    parser.add_argument("--image-search", type=str, default="on")
    parser.add_argument("--render-mode", type=str, choices=["html", "svg"], default=None)
    parser.add_argument(
        "--style-pack",
        type=str,
        default="",
        help=(
            "Prepared style-pack directory. It is validated and copied into the run; "
            "failures fall back to built-in templates."
        ),
    )
    parser.add_argument(
        "--allow-style-source-content",
        action="store_true",
        help=(
            "Explicitly allow the PPTX named by style-pack.json 'source' to also be "
            "parsed as a content reference from --text. Use only when this dual role is intentional."
        ),
    )
    parser.add_argument("--run-id", type=str, default="")
    parser.add_argument("--recursion-limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    logger.debug(f"All arguments: {vars(args)}")
    # Auto-generate a unique session-id when none was provided. This prevents
    # the historical "local" default from colliding across unrelated runs
    # (which would let staged execution silently write into the wrong run dir).
    if not args.session_id and not (args.resume or args.continue_run):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        args.session_id = f"auto_{os.getpid()}_{ts}"
        logger.info(f"No --session-id provided; generated {args.session_id!r}. "
                    f"Pass --session-id explicitly to enable resume / staged recovery.")
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

    operation_count = sum((bool(args.text), bool(args.resume), bool(args.continue_run)))
    if operation_count != 1:
        emit_stage_payload(
            "invalid_request",
            {
                "stage": "invalid_request",
                "message": "choose exactly one of --text, --resume, or --continue",
            },
        )
        return
    if args.continue_run and stages != ["all"]:
        emit_stage_payload(
            "invalid_request",
            {
                "stage": "invalid_request",
                "message": "--continue resumes the saved full workflow and cannot be combined with --stages",
            },
        )
        return
    if (args.resume or args.continue_run) and not (args.session_id or args.run_id):
        emit_stage_payload(
            "invalid_request",
            {
                "stage": "invalid_request",
                "message": "--resume/--continue requires --session-id; --run-id is an advanced fallback",
            },
        )
        return

    style_source = _style_source_in_request(args.text, args.style_pack)
    if style_source and not args.allow_style_source_content:
        emit_stage_payload(
            "invalid_request",
            {
                "stage": "invalid_request",
                "message": (
                    f"--text contains the style source PPTX {style_source!r}. Files and URLs in "
                    "--text are parsed as content references, which can leak the template's business "
                    "content into the new deck. Remove that PPTX from --text and pass only its prepared "
                    "directory via --style-pack. If the same PPTX is intentionally also a content source, "
                    "rerun with --allow-style-source-content."
                ),
            },
        )
        return

    if args.run_id:
        run_id = args.run_id
    elif args.resume or args.continue_run:
        resolution = resolve_run_by_session(output_files_dir, args.session_id)
        if resolution.status != "found":
            _emit_session_resolution_error(
                resolution,
                args.session_id,
                action="resume the interrupted run" if args.continue_run else "submit the requested reply",
            )
            return
        run_id = resolution.run_id
    elif stages != ["all"]:
        resolution = resolve_run_by_session(output_files_dir, args.session_id)
        if resolution.status == "ambiguous":
            _emit_session_resolution_error(
                resolution,
                args.session_id,
                action="continue staged execution",
            )
            return
        run_id = resolution.run_id or await new_semantic_run_id(args.text or "")
    else:
        # A public session id identifies one logical task. Re-submitting --text
        # with the same id used to create a second directory and lose the
        # checkpoint. Refuse that ambiguity and tell callers which operation
        # they intended instead.
        resolution = resolve_run_by_session(output_files_dir, args.session_id)
        if resolution.status != "not_found":
            if resolution.status == "ambiguous":
                _emit_session_resolution_error(
                    resolution,
                    args.session_id,
                    action="start a new run",
                )
                return
            existing_dir = Path(output_files_dir) / resolution.run_id
            can_continue = (existing_dir / "checkpointer.sqlite").is_file()
            next_step = (
                f"use --continue --session-id {args.session_id!r}"
                if can_continue
                else "use a new unique --session-id"
            )
            emit_stage_payload(
                "invalid_request",
                {
                    "stage": "invalid_request",
                    "message": (
                        f"session_id={args.session_id!r} already belongs to an existing run; "
                        f"re-submitting --text would create a duplicate. {next_step}."
                    ),
                },
                run_id=resolution.run_id,
                output_dir=str(existing_dir),
            )
            return
        run_id = await new_semantic_run_id(args.text or "")

    out_dir = run_dir(run_id)
    cached_run = _cached_run_metadata(out_dir)
    if args.resume or args.continue_run:
        cached_session_id = cached_run.get("session_id") or ""
        if args.session_id and cached_session_id and args.session_id != cached_session_id:
            emit_stage_payload(
                "invalid_request",
                {
                    "stage": "invalid_request",
                    "message": (
                        f"run_id={run_id!r} belongs to session_id={cached_session_id!r}, "
                        f"not {args.session_id!r}"
                    ),
                },
                run_id=run_id,
                output_dir=out_dir,
            )
            return
        args.session_id = args.session_id or cached_session_id
        if not args.session_id:
            emit_stage_payload(
                "invalid_request",
                {
                    "stage": "invalid_request",
                    "message": "the selected run has no persisted session_id and cannot resume safely",
                },
                run_id=run_id,
                output_dir=out_dir,
            )
            return
        if not (Path(out_dir) / "checkpointer.sqlite").is_file():
            emit_stage_payload(
                "resume_unavailable",
                {
                    "stage": "resume_unavailable",
                    "message": (
                        "no checkpoint is available for this run. Use patch_render_missing.py "
                        "with --session-id to regenerate missing pages, or start a new session."
                    ),
                },
                run_id=run_id,
                output_dir=out_dir,
            )
            return
        _inherit_cached_runtime_options(args, cached_run)

    _apply_runtime_overrides(args)
    args.render_mode = _resolve_render_mode(args, cached_run)
    args.style_pack = _resolve_style_pack(args, cached_run, out_dir)
    # run.json is the snapshot of the INITIAL run that started this session.
    # Subsequent resume calls must not overwrite it — otherwise the resume:true
    # marker hides the original record and breaks session-id-based lookup.
    run_json_path = Path(out_dir) / "run.json"
    if not run_json_path.exists():
        save_json(run_json_path, _build_run_metadata(args, run_id))
    elif args.style_pack and not cached_run.get("style_pack_dir"):
        # A staged render may add a pack after parse/research/outline. Preserve
        # all original metadata and record only the immutable run snapshot.
        cached_run["style_pack_dir"] = args.style_pack
        save_json(run_json_path, cached_run)

    if stages == ["all"]:
        await _run_all_stages(args, run_id, out_dir)
        return

    await _run_staged_pipeline(args, stages, run_id, out_dir)


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
