#!/usr/bin/env python3
import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.append(str(ROOT))

from core.utils.cache import load_json, run_dir, save_json
from core.utils.config import output_files_dir
from scripts.utils.cli_output import emit_stage_payload
from scripts.utils.run_identity import resolve_run_by_session, session_resolution_message


@dataclass(frozen=True)
class PatchRenderContext:
    args: argparse.Namespace
    out_dir: str
    outline: list
    topic: str
    save_dir: str
    target_indices: list[int]


class DummyWriter:
    def __call__(self, payload: dict):
        step = payload.get("step")
        text = payload.get("text")
        if step:
            print(f"\n>>> 【当前步骤】 {step}")
        if text:
            print(f"\n>>> {text}")

def parse_indices(raw: str) -> list[int]:
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    indices = []
    for p in parts:
        try:
            indices.append(int(p))
        except ValueError:
            pass
    return sorted(set(indices))


def _load_outline_or_emit(args, out_dir: str):
    outline_path = Path(out_dir) / "outline/outline.json"
    if not outline_path.exists():
        emit_stage_payload(
            "missing_outline",
            {"stage": "missing_outline", "message": f"outline not found: {outline_path}"},
            run_id=args.run_id,
            output_dir=out_dir,
        )
        return None, None

    from core.ppt_generator.thought_to_ppt.state import PPTPage, PageType

    outline_json = load_json(str(outline_path))
    outline = [PPTPage(**item) for item in outline_json.get("outline", [])]
    topic = outline_json.get("topic", "")
    if not outline:
        emit_stage_payload(
            "empty_outline",
            {"stage": "empty_outline", "message": "outline is empty; abort"},
            run_id=args.run_id,
            output_dir=out_dir,
        )
        return None, None

    return (outline, topic)


def _resolve_save_dir(out_dir: str, topic: str):
    """Resolve the slides directory for patch-render.

    Prefer ppt.json's slides_dir (set by a prior successful render). Fall back
    to <out_dir>/slides (the standard layout) when ppt.json is missing — this
    happens when patching a run that completed outline but never rendered.
    Never fall back to a top-level <output_root>/<topic> dir; that would orphan
    artifacts from the original run.
    """
    ppt_json = load_json(str(Path(out_dir) / "ppt.json"))
    if ppt_json and ppt_json.get("slides_dir"):
        return ppt_json["slides_dir"]
    if ppt_json and ppt_json.get("pdf_path"):
        return str(Path(ppt_json["pdf_path"]).parent)
    return os.path.join(out_dir, "slides")


def _resolve_style_pack_dir(out_dir: str) -> str:
    """Reuse the immutable style-pack snapshot when patching a styled run."""
    candidate = Path(out_dir) / "style_pack"
    return str(candidate) if (candidate / "style-pack.json").is_file() else ""


def _load_run_context(args, out_dir: str) -> dict:
    """Restore the original request/session rather than drifting on a patch run."""
    metadata = load_json(Path(out_dir) / "run.json") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not args.text:
        args.text = str(metadata.get("text") or "")
    if not args.session_id:
        args.session_id = str(metadata.get("session_id") or "")
    return metadata


def _resolve_target_indices(args, save_dir: str, outline):
    return _resolve_target_indices_for_mode(args, save_dir, outline, "svg")


def _resolve_target_indices_for_mode(args, save_dir: str, outline, render_mode: str = "svg"):
    target_indices = parse_indices(args.indices)
    if not target_indices:
        if render_mode == "svg":
            svg_dir = Path(save_dir)
            existing = _existing_svg_indices(svg_dir)
        else:
            existing = set(int(p.stem) for p in Path(save_dir).glob("*.html") if p.stem.isdigit())
        outline_indices = [p.index for p in outline]
        target_indices = [i for i in outline_indices if i not in existing]
    return target_indices


def _existing_svg_indices(svg_dir: Path) -> set[int]:
    indices = set()
    if not svg_dir.exists():
        return indices
    for path in svg_dir.glob("*.svg"):
        prefix = path.stem.split("_", 1)[0]
        if prefix.isdigit():
            indices.add(int(prefix) - 1)
    return indices


def _svg_path_for_index(save_dir: str, index: int) -> str | None:
    matches = sorted(Path(save_dir).glob(f"{index + 1:02d}_*.svg"))
    if not matches:
        return None
    return str(matches[0])


def _emit_incomplete_render(args, out_dir: str, target_indices: list[int], missing_indices: list[int]) -> None:
    emit_stage_payload(
        "render_incomplete",
        {
            "stage": "render_incomplete",
            "message": (
                "patch generation finished without producing every outline page; "
                "PPTX export was skipped"
            ),
            "target_indices": target_indices,
            "missing_indices": missing_indices,
        },
        run_id=args.run_id,
        output_dir=out_dir,
    )


async def _patch_render_html(context: PatchRenderContext):
    """HTML-route patch render."""
    args = context.args
    out_dir = context.out_dir
    outline = context.outline
    topic = context.topic
    save_dir = context.save_dir
    target_indices = context.target_indices
    from core.ppt_generator.thought_to_ppt.state import PageType
    from core.ppt_generator.thought_to_ppt.page_generators.node import prepare_generation_context_node
    from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.node import (
        generate_cover_node,
        generate_thanks_node,
    )
    from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node import (
        generate_sep_template_node,
        generate_sep_page_node,
    )
    from core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node import generate_toc_page_node
    from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph import content_page_worker_app
    from core.ppt_generator.utils.common import sanitize_filename, htmls_to_pptx

    cached_ppt_json = load_json(str(Path(out_dir) / "ppt.json")) or {}
    cached_template_name = cached_ppt_json.get("template_name") or ""

    state = {
        "query": args.text or "",
        "outline": outline,
        "topic": topic,
        "save_dir": save_dir,
        "template_name": cached_template_name,
        "style_pack_dir": _resolve_style_pack_dir(out_dir),
        "ppt_prompt": "",
        "language": "",
        "generated_pages": [],
        "page_files": [],
        "final_pdf_path": None,
        "final_pptx_path": None,
    }
    writer = DummyWriter()
    ctx = await prepare_generation_context_node(state, writer)
    state.update(ctx)
    # Preparation may deep-copy the outline while distributing images, then
    # bind runtime-only style reference paths on that copy. Always continue
    # with the returned outline; the originally loaded objects only contain
    # persisted reference ids and would silently fall back to the base theme.
    outline = state["outline"]
    template_content = state.get("template", "")

    target_pages = [p for p in outline if p.index in set(target_indices)]
    target_types = {p.type for p in target_pages}

    cover_thanks_pages = sorted(
        (p for p in outline if p.type == PageType.COVER_THANKS),
        key=lambda page: page.index,
    )
    for page in (p for p in target_pages if p.type == PageType.COVER_THANKS):
        cover_page = page if page.index == cover_thanks_pages[0].index else None
        thanks_page = page if cover_page is None else None
        page_state = {
            "query": state["query"],
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "cover_page": cover_page,
            "thanks_page": thanks_page,
            "generated_pages": [],
        }
        if cover_page is not None:
            await generate_cover_node(page_state)
        else:
            await generate_thanks_node(page_state)

    if PageType.TOC in target_types:
        await generate_toc_page_node({
            "query": state["query"],
            "outline": outline,
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
        })

    sep_targets = [p for p in target_pages if p.type == PageType.SEPARATOR]
    if sep_targets:
        sep_out = await generate_sep_template_node({
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "sep_pages": sep_targets,
            "sep_template": None,
            "generated_pages": [],
        })
        sep_template_content = sep_out.get("sep_template", "")
        for page in sep_targets[1:]:
            await generate_sep_page_node({
                "save_dir": state["save_dir"],
                "ppt_prompt": state["ppt_prompt"],
                "language": state["language"],
                "outline": outline,
                "sep_page": page,
                "sep_template": sep_template_content,
                "generated_pages": [],
            })

    content_targets = [p for p in target_pages if p.type == PageType.CONTENT]
    for page in content_targets:
        await content_page_worker_app.ainvoke({
            "query": state["query"],
            "outline": outline,
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "content_page": page,
            "img_scores": [],
            "generated_pages": [],
        })

    outline_indices = sorted({p.index for p in outline})
    files = [str(Path(save_dir) / f"{idx}.html") for idx in outline_indices]
    missing_after_patch = [idx for idx, path in zip(outline_indices, files) if not Path(path).exists()]
    if missing_after_patch:
        _emit_incomplete_render(args, out_dir, target_indices, missing_after_patch)
        return

    pdf_path, pptx_path = await htmls_to_pptx(files, save_dir, sanitize_filename(topic))

    record = {
        "run_id": args.run_id,
        "topic": topic,
        "render_mode": "html",
        "slides_dir": save_dir,
        "style_pack_dir": state.get("style_pack_dir", ""),
        "pdf_path": pdf_path,
        "pptx_path": pptx_path,
    }
    save_json(Path(out_dir) / "ppt.json", record)

    emit_stage_payload(
        "completed",
        {
            "stage": "completed",
            "target_indices": target_indices,
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        },
        run_id=args.run_id,
        output_dir=out_dir,
    )


async def _patch_render_svg(context: PatchRenderContext):
    """SVG-route patch render."""
    args = context.args
    out_dir = context.out_dir
    outline = context.outline
    topic = context.topic
    save_dir = context.save_dir
    target_indices = context.target_indices
    from core.ppt_generator.thought_to_ppt.state import PageType
    from core.ppt_generator.thought_to_ppt.svg_page_generators.node import (
        prepare_generation_context_node,
        quality_check_node,
    )
    from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.node import (
        generate_cover_node,
        generate_thanks_node,
    )
    from core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.node import (
        generate_sep_template_node,
        generate_sep_page_node,
    )
    from core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator.node import generate_toc_page_node
    from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.graph import (
        content_page_worker_app,
    )
    from core.ppt_generator.utils.common import sanitize_filename
    from core.ppt_generator.utils.svg_export import svgs_to_pptx

    cached_ppt_json = load_json(str(Path(out_dir) / "ppt.json")) or {}
    cached_template_name = cached_ppt_json.get("template_name") or ""

    state = {
        "query": args.text or "",
        "render_mode": "svg",
        "outline": outline,
        "topic": topic,
        "save_dir": save_dir,
        "template_name": cached_template_name,
        "style_pack_dir": _resolve_style_pack_dir(out_dir),
        "ppt_prompt": "",
        "language": "",
        "generated_pages": [],
        "page_files": [],
        "final_pdf_path": None,
        "final_pptx_path": None,
    }
    writer = DummyWriter()
    ctx = await prepare_generation_context_node(state, writer)
    state.update(ctx)
    # SVG preparation deep-copies the outline during image distribution and
    # binds runtime style references on that returned copy.
    outline = state["outline"]
    template_content = state.get("template", "")

    target_pages = [p for p in outline if p.index in set(target_indices)]
    target_types = {p.type for p in target_pages}

    cover_thanks_pages = sorted(
        (p for p in outline if p.type == PageType.COVER_THANKS),
        key=lambda page: page.index,
    )
    for page in (p for p in target_pages if p.type == PageType.COVER_THANKS):
        cover_page = page if page.index == cover_thanks_pages[0].index else None
        thanks_page = page if cover_page is None else None
        page_state = {
            "query": state["query"],
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "cover_page": cover_page,
            "thanks_page": thanks_page,
            "generated_pages": [],
        }
        if cover_page is not None:
            await generate_cover_node(page_state)
        else:
            await generate_thanks_node(page_state)

    if PageType.TOC in target_types:
        await generate_toc_page_node({
            "query": state["query"],
            "outline": outline,
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
        })

    sep_targets = [p for p in target_pages if p.type == PageType.SEPARATOR]
    if sep_targets:
        sep_out = await generate_sep_template_node({
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "sep_pages": sep_targets,
            "sep_template": None,
            "generated_pages": [],
        })
        sep_template_content = sep_out.get("sep_template", "")
        for page in sep_targets[1:]:
            await generate_sep_page_node({
                "save_dir": state["save_dir"],
                "ppt_prompt": state["ppt_prompt"],
                "language": state["language"],
                "outline": outline,
                "sep_page": page,
                "sep_template": sep_template_content,
                "generated_pages": [],
            })

    content_targets = [p for p in target_pages if p.type == PageType.CONTENT]
    for page in content_targets:
        await content_page_worker_app.ainvoke({
            "query": state["query"],
            "outline": outline,
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "content_page": page,
            "img_scores": [],
            "generated_pages": [],
        })

    outline_indices = sorted({p.index for p in outline})
    files = []
    missing_after_patch = []
    for idx in outline_indices:
        path = _svg_path_for_index(save_dir, idx)
        if path:
            files.append(path)
        else:
            missing_after_patch.append(idx)

    if missing_after_patch:
        _emit_incomplete_render(args, out_dir, target_indices, missing_after_patch)
        return

    try:
        await quality_check_node(
            {
                "page_files": files,
                "outline": outline,
                "style_pack_dir": state.get("style_pack_dir", ""),
            },
            writer,
        )
    except ValueError as error:
        emit_stage_payload(
            "svg_quality_failed",
            {
                "stage": "svg_quality_failed",
                "message": str(error),
                "target_indices": target_indices,
            },
            run_id=args.run_id,
            output_dir=out_dir,
        )
        return

    pdf_path, pptx_path = await svgs_to_pptx(files, out_dir, sanitize_filename(topic))

    record = {
        "run_id": args.run_id,
        "topic": topic,
        "render_mode": "svg",
        "slides_dir": save_dir,
        "svg_dir": save_dir,
        "template_name": state.get("template_name", ""),
        "style_pack_dir": state.get("style_pack_dir", ""),
        "pdf_path": pdf_path,
        "pptx_path": pptx_path,
    }
    save_json(Path(out_dir) / "ppt.json", record)

    emit_stage_payload(
        "completed",
        {
            "stage": "completed",
            "target_indices": target_indices,
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        },
        run_id=args.run_id,
        output_dir=out_dir,
    )


async def _patch_render(context: PatchRenderContext):
    render_mode = getattr(context.args, "render_mode", None) or _read_render_mode(context.out_dir)
    if render_mode == "svg":
        await _patch_render_svg(context)
    else:
        await _patch_render_html(context)


def _read_render_mode(out_dir: str) -> str:
    try:
        ppt_record = load_json(str(Path(out_dir) / "ppt.json")) or {}
        run_record = load_json(str(Path(out_dir) / "run.json")) or {}
        return ppt_record.get("render_mode") or run_record.get("render_mode") or "svg"
    except Exception:
        return "svg"


def _resolve_patch_run_id(args) -> str:
    if args.run_id:
        return args.run_id
    resolution = resolve_run_by_session(output_files_dir, args.session_id)
    if resolution.status == "found":
        return resolution.run_id
    emit_stage_payload(
        "invalid_request",
        {
            "stage": "invalid_request",
            "message": session_resolution_message(
                resolution,
                args.session_id,
                action="patch missing pages",
            ),
        },
    )
    return ""


async def main():
    parser = argparse.ArgumentParser()
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument(
        "--session-id",
        default="",
        help="Public task/session id. The original run directory is resolved automatically.",
    )
    identity.add_argument(
        "--run-id",
        default="",
        help="Advanced fallback for manually disambiguating legacy session collisions.",
    )
    parser.add_argument("--text", required=False, default="")
    parser.add_argument("--indices", required=False, default="")
    args = parser.parse_args()

    args.run_id = _resolve_patch_run_id(args)
    if not args.run_id:
        return
    out_dir = run_dir(args.run_id)
    _load_run_context(args, out_dir)
    outline, topic = _load_outline_or_emit(args, out_dir)
    if not outline:
        return

    save_dir = _resolve_save_dir(out_dir, topic)
    render_mode = _read_render_mode(out_dir)
    target_indices = _resolve_target_indices_for_mode(args, save_dir, outline, render_mode)
    if not target_indices:
        emit_stage_payload(
            "completed",
            {
                "stage": "completed",
                "message": "no missing pages; skip generation",
                "target_indices": [],
            },
            run_id=args.run_id,
            output_dir=out_dir,
        )
        return

    args.render_mode = render_mode
    await _patch_render(
        PatchRenderContext(
            args=args,
            out_dir=out_dir,
            outline=outline,
            topic=topic,
            save_dir=save_dir,
            target_indices=target_indices,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
