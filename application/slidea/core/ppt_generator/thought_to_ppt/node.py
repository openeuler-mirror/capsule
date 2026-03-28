from core.utils.logger import logger

from core.ppt_generator.utils.markdown import get_markdown_images
from core.ppt_generator.thought_to_ppt.state import PPTState, PageType, PPTPage
from core.ppt_generator.thought_to_ppt.outline_generator.graph import generate_outline_app

from core.utils.cache import get_run_id, run_dir_from_config, load_json, save_json
from core.utils.config import app_base_dir
from langgraph.types import interrupt
from core.utils.interrupt import InterruptType
from json_repair import repair_json
from langchain_core.runnables import RunnableConfig
import json

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
    outline_list = outline_results["final_output"]

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
    outline = []
    for idx, ppt in enumerate(outline_list):
        chapter_idx = ppt["source"]
        if chapter_idx == -1:
            reference_doc = outline_results["summary_text"]
        else:
            chapter = chapters[chapter_idx]
            reference_doc = f"{chapter.header}\n\n{chapter.content}"

        images = get_markdown_images(reference_doc)

        ppt_page = PPTPage(title=ppt["title"],
                           abstract=ppt["abstract"],
                           type=ppt["type"],
                           index=idx,
                           reference_doc=reference_doc,
                           reference_images=images)
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
    """generate ppt pages"""
    from core.ppt_generator.thought_to_ppt.page_generators.graph import generate_pages_app

    pages_results = await generate_pages_app.ainvoke(state)
    return pages_results
