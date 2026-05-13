#!/usr/bin/env python3
import argparse
import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from core.utils.cache import load_json, run_dir, save_json
from scripts.utils.cli_output import emit_stage_payload


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
    from core.ppt_generator.utils.common import sanitize_filename

    ppt_json = load_json(str(Path(out_dir) / "ppt.json"))
    if ppt_json and ppt_json.get("render_dir"):
        return ppt_json["render_dir"]
    if ppt_json and ppt_json.get("pdf_path"):
        return str(Path(ppt_json["pdf_path"]).parent)
    return os.path.join(ROOT, "output", sanitize_filename(topic))


def _resolve_target_indices(args, save_dir: str, outline):
    return _resolve_target_indices_for_mode(args, save_dir, outline, "html")


def _resolve_target_indices_for_mode(args, save_dir: str, outline, render_mode: str = "html"):
    target_indices = parse_indices(args.indices)
    if not target_indices:
        if render_mode == "svg":
            svg_output_dir = Path(save_dir) / "svg_output"
            existing = _existing_svg_indices(svg_output_dir)
        else:
            existing = set(int(p.stem) for p in Path(save_dir).glob("*.html") if p.stem.isdigit())
        outline_indices = [p.index for p in outline]
        target_indices = [i for i in outline_indices if i not in existing]
    return target_indices


def _existing_svg_indices(svg_output_dir: Path) -> set[int]:
    indices = set()
    if not svg_output_dir.exists():
        return indices
    for path in svg_output_dir.glob("*.svg"):
        prefix = path.stem.split("_", 1)[0]
        if prefix.isdigit():
            indices.add(int(prefix) - 1)
    return indices


def _svg_path_for_index(save_dir: str, index: int) -> str | None:
    matches = sorted((Path(save_dir) / "svg_output").glob(f"{index + 1:02d}_*.svg"))
    if not matches:
        return None
    return str(matches[0])


async def _patch_render_html(args, out_dir: str, outline, topic: str, save_dir: str, target_indices: list[int]):
    """HTML-route patch render."""
    from core.ppt_generator.thought_to_ppt.state import PageType
    from core.ppt_generator.thought_to_ppt.page_generators.node import prepare_generation_context_node
    from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph import generate_cover_thanks_pages_app
    from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node import generate_sep_template_node, generate_sep_page_node
    from core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node import generate_toc_page_node
    from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph import content_page_worker_app
    from core.ppt_generator.utils.common import sanitize_filename, htmls_to_pptx

    state = {
        "query": args.text or "",
        "outline": outline,
        "topic": topic,
        "save_dir": save_dir,
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
    template_content = state.get("template", "")

    target_pages = [p for p in outline if p.index in set(target_indices)]
    target_types = {p.type for p in target_pages}

    if PageType.COVER_THANKS in target_types:
        await generate_cover_thanks_pages_app.ainvoke({
            "query": state["query"],
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "cover_page": None,
            "thanks_page": None,
            "generated_pages": [],
        })

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
    files = [p for p in files if Path(p).exists()]

    pdf_path, pptx_path = await htmls_to_pptx(files, save_dir, sanitize_filename(topic))

    record = {
        "run_id": args.run_id,
        "topic": topic,
        "render_mode": "html",
        "render_dir": save_dir,
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


async def _patch_render_svg(args, out_dir: str, outline, topic: str, save_dir: str, target_indices: list[int]):
    """SVG-route patch render."""
    from core.ppt_generator.thought_to_ppt.state import PageType
    from core.ppt_generator.thought_to_ppt.svg_page_generators.node import (
        prepare_generation_context_node,
        quality_check_node,
        finalize_node,
    )
    from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.graph import generate_cover_thanks_pages_app
    from core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.node import generate_sep_template_node, generate_sep_page_node
    from core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator.node import generate_toc_page_node
    from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.graph import content_page_worker_app
    from core.ppt_generator.utils.common import sanitize_filename
    from core.ppt_generator.utils.svg_export import svgs_to_pptx

    state = {
        "query": args.text or "",
        "render_mode": "svg",
        "outline": outline,
        "topic": topic,
        "save_dir": save_dir,
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
    template_content = state.get("template", "")

    target_pages = [p for p in outline if p.index in set(target_indices)]
    target_types = {p.type for p in target_pages}

    if PageType.COVER_THANKS in target_types:
        await generate_cover_thanks_pages_app.ainvoke({
            "query": state["query"],
            "save_dir": state["save_dir"],
            "ppt_prompt": state["ppt_prompt"],
            "language": state["language"],
            "template": template_content,
            "outline": outline,
            "cover_page": None,
            "thanks_page": None,
            "generated_pages": [],
        })

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
    for idx in outline_indices:
        path = _svg_path_for_index(save_dir, idx)
        if path:
            files.append(path)

    try:
        await quality_check_node({"page_files": files}, writer)
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

    finalize_update = await finalize_node({"save_dir": save_dir, "page_files": files}, writer)
    files = finalize_update.get("page_files", files)

    pdf_path, pptx_path = await svgs_to_pptx(files, save_dir, sanitize_filename(topic))

    record = {
        "run_id": args.run_id,
        "topic": topic,
        "render_mode": "svg",
        "render_dir": save_dir,
        "pdf_path": pdf_path,
        "pptx_path": pptx_path,
        "svg_output_dir": str(Path(save_dir) / "svg_output"),
        "svg_final_dir": str(Path(save_dir) / "svg_final"),
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


async def _patch_render(args, out_dir: str, outline, topic: str, save_dir: str, target_indices: list[int]):
    render_mode = getattr(args, "render_mode", None) or _read_render_mode(out_dir)
    if render_mode == "svg":
        await _patch_render_svg(args, out_dir, outline, topic, save_dir, target_indices)
    else:
        await _patch_render_html(args, out_dir, outline, topic, save_dir, target_indices)


def _read_render_mode(out_dir: str) -> str:
    try:
        ppt_record = load_json(str(Path(out_dir) / "ppt.json")) or {}
        return ppt_record.get("render_mode", "html")
    except Exception:
        return "html"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--text", required=False, default="")
    parser.add_argument("--indices", required=False, default="")
    args = parser.parse_args()

    out_dir = run_dir(str(ROOT), args.run_id)
    outline, topic = _load_outline_or_emit(args, out_dir)
    if not outline:
        return

    save_dir = _resolve_save_dir(out_dir, topic)
    ppt_json = load_json(str(Path(out_dir) / "ppt.json")) or {}
    render_mode = ppt_json.get("render_mode") or "html"
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
    await _patch_render(args, out_dir, outline, topic, save_dir, target_indices)


if __name__ == "__main__":
    asyncio.run(main())
