from core.utils.logger import logger

from core.ppt_generator.utils.markdown import get_markdown_images
from core.ppt_generator.thought_to_ppt.outline_generator.node import (
    SIMPLE_CONTENT_PAGE_MAX_COUNT,
    SIMPLE_REFERENCE_DOC_CHAR_LIMIT,
)
from core.ppt_generator.thought_to_ppt.state import PPTState, PageType, PPTPage
from core.ppt_generator.thought_to_ppt.outline_generator.graph import generate_outline_app

from core.utils.cache import get_run_id, run_dir_from_config, load_json, save_json
from core.utils.config import app_base_dir
from langgraph.types import interrupt
from core.utils.interrupt import InterruptType
from json_repair import repair_json
from langchain_core.runnables import RunnableConfig
import json

def _normalize_outline_list(outline_list: list) -> list:
    """Drop malformed slides and fill missing optional fields.

    Even after retry, very rarely the model returns a slide that's missing fields.
    Filter those out here so downstream PPTPage construction never KeyErrors.
    """
    cleaned = []
    for item in outline_list or []:
        if not isinstance(item, dict):
            logger.warning(f"outline: dropping non-dict item: {item!r}")
            continue
        title = item.get("title")
        abstract = item.get("abstract")
        if not isinstance(title, str) or not title.strip():
            logger.warning(f"outline: dropping item with missing/invalid title: {item!r}")
            continue
        if not isinstance(abstract, str):
            logger.warning(f"outline: dropping item with missing/invalid abstract: {item!r}")
            continue
        if "type" not in item or item["type"] is None:
            logger.warning(f"outline: defaulting missing type to CONTENT(1) for: {item!r}")
            item = {**item, "type": 1}
        else:
            try:
                item = {**item, "type": int(item["type"])}
            except (TypeError, ValueError):
                logger.warning(f"outline: defaulting non-int type to CONTENT(1) for: {item!r}")
                item = {**item, "type": 1}
        if "source" not in item:
            item["source"] = -1
        cleaned.append(item)
    return cleaned


async def generate_outline_node(state: PPTState, config: RunnableConfig | None = None):
    """generate ppt outline"""
    run_dir = run_dir_from_config(config, str(app_base_dir))
    run_id = get_run_id(config)
    if run_dir:
        cached = load_json(f"{run_dir}/outline/outline.json")
        if cached:
            outline = [PPTPage(**item) for item in cached.get("outline", [])]
            topic = cached.get("topic")
            if outline and topic:
                return {"outline": outline, "topic": topic}

    task_payload = {
        "user_query": state["query"],
        "input_text": state["ori_doc"],
        "is_markdown_doc": state.get("is_markdown_doc", True)
    }

    logger.info(task_payload["is_markdown_doc"])
    outline_results = await generate_outline_app.ainvoke(task_payload)
    outline_list = _normalize_outline_list(outline_results["final_output"])
    if not outline_list:
        raise ValueError("Outline generation returned no usable slides after retries")

    if outline_list[0]["type"] == PageType.COVER_THANKS:
        outline_list.append(
            {
                "title": "致谢页",
                "abstract": "PPT最后的致谢页",
                "type": 4,
                "source": -1,
            }
        )
    logger.info(f"generate outline: \n{outline_list}")

    chapters = outline_results["chapters"]
    use_simple_reference_doc = outline_results["target_page_count"] <= SIMPLE_CONTENT_PAGE_MAX_COUNT
    simple_reference_doc = state["ori_doc"][:SIMPLE_REFERENCE_DOC_CHAR_LIMIT]
    outline = []
    for idx, ppt in enumerate(outline_list):
        chapter_idx = ppt["source"]
        if use_simple_reference_doc and ppt["type"] == PageType.CONTENT:
            reference_doc = simple_reference_doc
            reference_doc_is_full_context = True
        elif chapter_idx == -1:
            reference_doc = outline_results["summary_text"]
            reference_doc_is_full_context = False
        else:
            chapter = chapters[chapter_idx]
            reference_doc = f"{chapter.header}\n\n{chapter.content}"
            reference_doc_is_full_context = False

        images = get_markdown_images(reference_doc)

        ppt_page = PPTPage(title=ppt["title"],
                           abstract=ppt["abstract"],
                           type=ppt["type"],
                           index=idx,
                           reference_doc=reference_doc,
                           reference_images=images,
                           reference_doc_is_full_context=reference_doc_is_full_context)
        outline.append(ppt_page)

    result = {
        "outline": outline,
        "topic": outline_results["final_output"][0]["title"],
    }
    if run_dir:
        save_json(f"{run_dir}/outline/outline.json", {
            "run_id": run_id,
            "topic": result["topic"],
            "outline": [p.model_dump() for p in outline]
        })

    return result


async def generate_pages_node(state: PPTState):
    """generate ppt pages — dispatch to the HTML or SVG sub-pipeline by render_mode."""
    if state.get("render_mode", "html") == "svg":
        from core.ppt_generator.thought_to_ppt.svg_page_generators.graph import generate_svg_pages_app
        return await generate_svg_pages_app.ainvoke(state)

    from core.ppt_generator.thought_to_ppt.page_generators.graph import generate_pages_app
    return await generate_pages_app.ainvoke(state)
