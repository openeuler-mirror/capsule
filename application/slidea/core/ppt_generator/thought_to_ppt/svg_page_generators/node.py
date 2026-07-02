import os
import copy
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import aiofiles.os
from json_repair import repair_json
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from core.utils.logger import logger
from core.utils.config import app_base_dir, output_files_dir
from core.utils.cache import get_run_id, run_dir_from_config, save_json
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_raw_invoke
from core.ppt_generator.utils.common import sanitize_filename, download_image, build_image_url
from core.ppt_generator.utils.svg import (
    extract_svg_content,
    repair_svg_content,
    validate_svg_content,
)
from core.ppt_generator.utils.svg_export import svgs_to_pptx
from core.ppt_generator.utils.svg_pipeline.quality_checker import (
    check_svg_files,
    format_quality_issues,
)
from core.ppt_generator.utils.svg_pipeline.templates import (
    select_svg_template,
    load_svg_template_content,
)
from core.ppt_generator.thought_to_ppt.state import PPTState, PageType, PPTPage
from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.graph import (
    generate_cover_thanks_pages_app,
)
from core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.graph import generate_sep_pages_app
from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.graph import (
    generate_content_pages_app,
)
from core.ppt_generator.thought_to_ppt.svg_page_generators.toc_page_generator.graph import generate_toc_page_app
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.node import strip_unresolvable_images


SVG_QUALITY_REPAIR_PROMPT = "svg_quality_repair_prompt.txt"


async def prepare_generation_context_node(state: PPTState, writer: StreamWriter, config: RunnableConfig | None = None):
    """
    SVG-route preparation: build save_dir, pick an SVG template, download
    outline images, set language and ppt_prompt, load template content into state.
    """
    if not state.get("save_dir", None):
        cache_dir = run_dir_from_config(config, str(app_base_dir))
        if cache_dir:
            save_dir = os.path.join(cache_dir, "slides")
        else:
            time_prefix = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
            save_dir = os.path.join(output_files_dir, f'{time_prefix}_{sanitize_filename(state["topic"])}')
    else:
        save_dir = state["save_dir"]
    await aiofiles.os.makedirs(save_dir, exist_ok=True)
    images_dir = os.path.join(save_dir, "images")
    await aiofiles.os.makedirs(images_dir, exist_ok=True)

    cached_name = state.get("template_name")
    if not cached_name:
        cached_name = await select_svg_template(state["query"], str(state["outline"]))
    logger.info(f"任务{state['query']}选取的svg模板名称为{cached_name}")

    try:
        template_content = load_svg_template_content(cached_name)
    except Exception as e:
        logger.error(f"读取svg模板文件失败 {cached_name}: {e}")
        raise Exception("获取PPT模板失败") from e

    prompt_dir = app_base_dir / "core" / "ppt_generator" / "assets" / "prompts" / "ppt_generator_prompt.txt"
    try:
        with open(prompt_dir, "r", encoding="utf-8") as f:
            ppt_prompt = f.read()
    except Exception as e:
        logger.error(f"读取PPT Prompt文件失败: {e}")
        raise Exception("获取PPT Prompt失败") from e

    response = await llm_invoke(
        ModelRoute.DEFAULT,
        [HumanMessage(content=f"根据'{state['query']}'确定使用的语言,只回答'中文'、'英文'等结果。")],
    )

    outline = await download_outline_images(state["outline"], images_dir)

    writer(
        {
            "step": "准备生成所需上下文",
            "text": f"已创建输出目录，选择模板: {cached_name}，检测语言: {response}。",
        }
    )

    return {
        "outline": outline,
        "save_dir": save_dir,
        "language": response,
        "ppt_prompt": ppt_prompt,
        "render_mode": "svg",
        "template_name": cached_name,
        "template": template_content,
    }


async def download_outline_images(outline: list[PPTPage], images_dir: str):
    """download and replace all images in outline"""
    all_tasks = []

    for page in outline:
        page_tasks = [download_image(url, images_dir) for url in page.reference_images]
        all_tasks.append(asyncio.gather(*page_tasks))

    all_results = await asyncio.gather(*all_tasks)

    for i, page in enumerate(outline):
        res = []
        for img in list(all_results[i]):
            if page.type != PageType.CONTENT:
                break
            if img:
                res.append(img)
        page.reference_images = res
    outline = await distribute_images_via_vlm(outline)
    return outline


def detect_distribution_mode(pages: List[Any]) -> str:
    content_pages = [p for p in pages if p.type.name == 'CONTENT']

    if not content_pages:
        return "global"

    first_page_imgs = content_pages[0].reference_images

    for p in content_pages[1:]:
        if p.reference_images != first_page_imgs:
            logger.info("Detected section mode in distribute images.")
            return "section"
    logger.info("Detected global mode in distribute images.")
    return "global"


def encode_image(image_path: str) -> str:
    try:
        return build_image_url(image_path)
    except Exception as e:
        logger.debug(f"Error reading image {image_path}: {e}")
        return ""


async def distribute_images_via_vlm(outline: List[Any]) -> List[Any]:
    processed_pages = copy.deepcopy(outline)

    if not can_vlm_invoke_route(ModelRoute.DEFAULT):
        logger.warning("No available VLM route for image distribution. Skip VLM-based image distribution.")
        return processed_pages

    mode = detect_distribution_mode(processed_pages)

    if mode == "global":
        await _process_global_mode(processed_pages)
    elif mode == "section":
        await _process_section_mode(processed_pages)

    return processed_pages


async def _process_global_mode(pages: List[Any]):
    content_indices = [
        i for i, p in enumerate(pages)
        if p.type.name == 'CONTENT'
    ]

    if not content_indices:
        return

    unique_images = []
    for idx in content_indices:
        if pages[idx].reference_images:
            unique_images = pages[idx].reference_images
            break

    if not unique_images:
        return

    for idx in content_indices:
        pages[idx].reference_images = []

    context_text = _build_page_context(pages, content_indices)

    tasks = [
        _ask_vlm_for_single_image(img_path, context_text, valid_indices=content_indices)
        for img_path in unique_images
    ]

    results = await asyncio.gather(*tasks)

    for img_path, target_index in results:
        if target_index is not None:
            pages[target_index].reference_images.append(img_path)


async def _process_section_mode(pages: List[Any]):
    current_indices = []
    current_images = []

    async def process_current_section(indices: List[int], images: List[str]):
        if not indices or not images:
            return

        for idx in indices:
            pages[idx].reference_images = []

        context_str = _build_page_context(pages, indices)

        tasks = [
            _ask_vlm_for_single_image(img_path, context_str, valid_indices=indices)
            for img_path in images
        ]
        results = await asyncio.gather(*tasks)

        for img_path, target_index in results:
            if target_index is not None:
                pages[target_index].reference_images.append(img_path)

    for i, page in enumerate(pages):
        p_type_name = page.type.name

        is_sep = p_type_name == 'SEPARATOR'
        is_last = i == len(pages) - 1

        if p_type_name == 'CONTENT':
            current_indices.append(i)
            if not current_images and page.reference_images:
                current_images = page.reference_images

        if is_sep or is_last:
            if current_indices and current_images:
                await process_current_section(current_indices, current_images)

            current_indices = []
            current_images = []


async def _ask_vlm_for_single_image(
    image_path: str,
    context_text: str,
    valid_indices: List[int],
) -> tuple[str, Optional[int]]:
    b64_img = encode_image(image_path)
    if not b64_img:
        return image_path, None

    prompt = f"""
你是一个专业的PPT排版助手。
我将给你一张图片和一组PPT页面的大纲。请判断这张图片最适合放在哪一页。

图片内容：(见附件)

PPT页面大纲：
{context_text}

请分析图片内容与页面标题/摘要的相关性。
要求：
1. 必须从提供的 Page Index 中选择一个最相关的。
2. 只返回 JSON 格式结果，不要包含任何解释。
3. 格式：{{"page_index": <int>}}

如果没有特别匹配的页面，请选择语义最接近的一页。
"""

    try:
        response = await vlm_raw_invoke(ModelRoute.DEFAULT, [HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": b64_img}},
            ]
        )])

        idx = json.loads(repair_json(response.content)).get("page_index")

        if idx in valid_indices:
            logger.debug(f"Image {image_path} should go to page {idx}")
            return image_path, idx
        else:
            logger.warning(f"Warning: VLM returned invalid index {idx}, valid are {valid_indices}")

    except Exception as e:
        logger.debug(f"Error distincting image {image_path}: {e}")

    return image_path, None


def _build_page_context(pages: List[Any], indices: List[int]) -> str:
    lines = []
    for idx in indices:
        p = pages[idx]
        lines.append(f"[Page Index: {p.index}] Title: {p.title}\nAbstract: {p.abstract}\n")
    return "\n".join(lines)


async def generate_toc_page_node(state: PPTState):
    """generate toc page (SVG)"""
    output = await generate_toc_page_app.ainvoke(state)
    return {"generated_pages": output["generated_pages"]}


async def generate_content_pages_node(state: PPTState, writer: StreamWriter):
    """generate content pages (SVG)"""
    task_payload = {
        "query": state["query"],
        "outline": state["outline"],
        "save_dir": state["save_dir"],
        "ppt_prompt": state["ppt_prompt"],
        "language": state["language"],
        "template": state.get("template", ""),
    }
    output = await generate_content_pages_app.ainvoke(task_payload)
    writer(
        {
            "step": "生成内容页",
            "text": f"已完成内容页 PPT 生成，共 {len(output['generated_pages'])} 页。",
        }
    )
    return {"generated_pages": output["generated_pages"]}


async def generate_sep_pages_node(state: PPTState, writer: StreamWriter):
    """generate sep pages (SVG)"""
    task_payload = {
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "language": state["language"],
        "template": state.get("template", ""),
        "outline": state["outline"],
        "sep_pages": None,
        "sep_template": None,
    }
    output = await generate_sep_pages_app.ainvoke(task_payload)
    writer(
        {
            "step": "生成分割页",
            "text": f"已完成分割页 PPT 生成，共 {len(output['generated_pages'])} 页。",
        }
    )
    return {"generated_pages": output["generated_pages"]}


async def generate_cover_thanks_pages_node(state: PPTState, writer: StreamWriter):
    """generate cover and thanks pages (SVG)"""
    task_payload = {
        "query": state["query"],
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "language": state["language"],
        "template": state.get("template", ""),
        "outline": state["outline"],
        "cover_page": None,
        "thanks_page": None,
    }
    output = await generate_cover_thanks_pages_app.ainvoke(task_payload)
    writer(
        {
            "step": "生成封面与致谢页",
            "text": f"已完成封面/致谢页 PPT 生成，共 {len(output['generated_pages'])} 页。",
        }
    )
    return {"generated_pages": output["generated_pages"]}


async def ppt_synthesizer_node(state: PPTState, writer: StreamWriter):
    """Collect all generated page file paths in slide order into state['page_files']."""
    files = [
        item["file_path"]
        for item in sorted(state["generated_pages"], key=lambda x: x["index"])
    ]
    writer(
        {
            "step": "合并 PPT 页面",
            "text": f"已根据索引合并所有 PPT 页面，共 {len(files)} 页。",
        }
    )
    return {"page_files": files}


async def quality_check_node(state: PPTState, writer: StreamWriter):
    """SVG quality gate. Run check_svg_files; on failure, attempt LLM repair and re-check."""
    files = state.get("page_files") or []
    results = check_svg_files(files)
    failed = [item for item in results if not item.get("passed")]

    if failed:
        writer(
            {
                "step": "SVG 自动修复",
                "text": f"发现 {len(failed)} 个文件存在兼容性问题，开始自动修复。",
            }
        )
        await _repair_failed_svg_files(failed)
        results = check_svg_files(files)
        failed = [item for item in results if not item.get("passed")]

    if failed:
        issue_text = format_quality_issues(results)
        logger.error(f"svg quality check failed:\n{issue_text}")
        writer(
            {
                "step": "SVG 质量检查失败",
                "text": issue_text,
            }
        )
        raise ValueError("svg quality check failed:\n" + issue_text)

    warning_count = sum(len(item.get("warnings") or []) for item in results)
    writer(
        {
            "step": "SVG 质量检查",
            "text": f"已完成 {len(results)} 个文件检查，发现 {warning_count} 个警告。",
        }
    )
    return {"svg_quality_report": results}


async def _repair_failed_svg_files(failed: list[dict]) -> None:
    repair_prompt_template = (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / SVG_QUALITY_REPAIR_PROMPT
    ).read_text(encoding="utf-8")

    for item in failed:
        svg_path = item.get("path")
        if not svg_path:
            continue
        try:
            with open(svg_path, "r", encoding="utf-8") as fh:
                svg_content = fh.read()
            prompt = repair_prompt_template.format(
                issues=format_quality_issues([item]),
                content=svg_content[:30000],
            )
            response = await llm_invoke(ModelRoute.PREMIUM, [HumanMessage(content=prompt)])
            repaired = repair_svg_content(extract_svg_content(response))
            validate_svg_content(repaired)
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(repaired)
            logger.info(f"SVG quality repair succeeded: {svg_path}")
        except Exception as error:
            logger.warning(f"SVG quality repair failed for {svg_path}: {error}")

        stripped = strip_unresolvable_images(svg_path)
        if stripped:
            logger.warning(
                f"SVG quality repair: LLM did not resolve all missing images, "
                f"stripped {stripped} unresolvable <image> from {svg_path} as fallback"
            )


async def export_node(state: PPTState, writer: StreamWriter, config: RunnableConfig | None = None):
    """Export SVG pages to native editable PPTX at the cache directory root."""
    topic = sanitize_filename(state["topic"])
    save_dir = state["save_dir"]
    files = state.get("page_files") or []

    cache_dir = run_dir_from_config(config, str(app_base_dir)) or save_dir
    pptx_output_dir = cache_dir

    writer(
        {
            "step": "开始导出 SVG PPT",
            "text": f"开始将 {len(files)} 个页面导出为 PPTX，保存目录: {pptx_output_dir}。",
        }
    )

    pdf_path, pptx_path = await svgs_to_pptx(files, pptx_output_dir, topic)

    run_id = get_run_id(config)
    if cache_dir != save_dir:
        record = {
            "run_id": run_id,
            "topic": state["topic"],
            "render_mode": "svg",
            "slides_dir": save_dir,
            "svg_dir": os.path.join(save_dir, "svg"),
            "template_name": state.get("template_name", ""),
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        }
        save_json(f"{cache_dir}/ppt.json", record)

    writer(
        {
            "step": "导出 PPT 完成",
            "files": [pdf_path, pptx_path] if pdf_path else [pptx_path],
            "source_dir": os.path.join(save_dir, "svg"),
            "text": "生成PPT结束",
        }
    )

    return {"final_pdf_path": pdf_path, "final_pptx_path": pptx_path}
