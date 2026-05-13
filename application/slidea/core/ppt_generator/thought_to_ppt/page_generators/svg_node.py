import os
from datetime import datetime
from pathlib import Path

import aiofiles.os
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from core.ppt_generator.thought_to_ppt.page_generators.node import download_outline_images
from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator.graph import generate_svg_pages_app
from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator.node import load_svg_prompt
from core.ppt_generator.thought_to_ppt.state import PPTState
from core.ppt_generator.svg_pipeline.finalize_svg import finalize_svg_files
from core.ppt_generator.svg_pipeline.quality_checker import format_quality_issues, check_svg_files
from core.ppt_generator.svg_pipeline.spec_lock import write_svg_spec_lock
from core.ppt_generator.svg_pipeline.templates import format_svg_template_for_prompt, select_svg_template
from core.ppt_generator.utils.common import sanitize_filename
from core.ppt_generator.utils.svg import extract_svg_content, repair_svg_content, validate_svg_content
from core.ppt_generator.utils.svg_export import svgs_to_pptx
from core.utils.cache import get_run_id, run_dir_from_config, save_json
from core.utils.config import app_base_dir, output_files_dir
from core.utils.llm import ModelRoute, llm_invoke
from core.utils.logger import logger


SVG_QUALITY_REPAIR_MAX_ATTEMPTS = 1


async def prepare_svg_generation_context_node(state: PPTState, writer: StreamWriter):
    """Prepare output directory, images, language, and SVG prompt."""
    time_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not state.get("save_dir", None):
        save_dir = os.path.join(output_files_dir, f'{time_prefix}_{sanitize_filename(state["topic"])}')
    else:
        save_dir = state["save_dir"]
    await aiofiles.os.makedirs(save_dir, exist_ok=True)
    images_dir = os.path.join(save_dir, "images")
    await aiofiles.os.makedirs(images_dir, exist_ok=True)

    response = await llm_invoke(
        ModelRoute.DEFAULT,
        [HumanMessage(content=f"根据'{state['query']}'确定使用的语言,只回答'中文'、'英文'等结果。")],
    )
    outline = await download_outline_images(state["outline"], images_dir)
    svg_prompt = load_svg_prompt()
    svg_template = select_svg_template(state["query"], outline, state.get("svg_template_name"))
    spec_lock = write_svg_spec_lock(
        save_dir,
        query=state["query"],
        topic=state["topic"],
        language=response,
        outline=outline,
        template=svg_template,
    )
    template_prompt = format_svg_template_for_prompt(svg_template)

    writer(
        {
            "step": "准备 SVG 生成上下文",
            "text": f"已创建输出目录，检测语言: {response}，选择 SVG 模板: {svg_template.get('name')}。",
        }
    )

    return {
        "outline": outline,
        "save_dir": save_dir,
        "language": response,
        "svg_template_name": svg_template.get("name", ""),
        "svg_template": template_prompt,
        "svg_prompt": svg_prompt,
        "svg_spec_lock": spec_lock,
    }


async def generate_svg_pages_node(state: PPTState, writer: StreamWriter):
    """Generate all pages as SVG."""
    task_payload = {
        "query": state["query"],
        "outline": state["outline"],
        "save_dir": state["save_dir"],
        "svg_prompt": state["svg_prompt"],
        "svg_spec_lock": state["svg_spec_lock"],
        "svg_template": state["svg_template"],
        "language": state["language"],
    }
    output = await generate_svg_pages_app.ainvoke(task_payload)
    writer(
        {
            "step": "生成 SVG 页面",
            "text": f"已完成 SVG 页面生成，共 {len(output['generated_pages'])} 页。",
        }
    )
    return {"generated_pages": output["generated_pages"]}


async def svg_synthesizer_node(state: PPTState, writer: StreamWriter):
    """Synthesize generated SVG paths in slide order."""
    svgs = [
        item["file_path"]
        for item in sorted(state["generated_pages"], key=lambda x: x["index"])
    ]
    writer(
        {
            "step": "合并 SVG 页面",
            "text": f"已根据索引合并所有 SVG 页面，共 {len(svgs)} 页。",
        }
    )
    return {"svgs": svgs}


async def svg_quality_check_node(state: PPTState, writer: StreamWriter):
    """Run SVG compatibility checks before PPTX export."""
    svgs = state["svgs"]
    results = check_svg_files(svgs)
    failed = [item for item in results if not item.get("passed")]
    if failed:
        writer(
            {
                "step": "SVG 自动修复",
                "text": f"发现 {len(failed)} 个 SVG 文件存在兼容性问题，开始自动修复。",
            }
        )
        await _repair_failed_svg_files(results)
        results = check_svg_files(svgs)
        failed = [item for item in results if not item.get("passed")]

    warning_count = sum(len(item.get("warnings") or []) for item in results)

    if failed:
        issue_text = format_quality_issues(results)
        logger.error(f"SVG quality check failed:\n{issue_text}")
        writer(
            {
                "step": "SVG 质量检查失败",
                "text": issue_text,
            }
        )
        raise ValueError("SVG quality check failed:\n" + issue_text)

    writer(
        {
            "step": "SVG 质量检查",
            "text": f"已完成 {len(results)} 个 SVG 文件检查，发现 {warning_count} 个警告。",
        }
    )
    return {"svg_quality_report": results}


async def _repair_failed_svg_files(results: list[dict]) -> None:
    for item in results:
        if item.get("passed"):
            continue
        svg_path = item.get("path")
        if not svg_path:
            continue
        for _ in range(SVG_QUALITY_REPAIR_MAX_ATTEMPTS):
            repaired = await _repair_svg_file(svg_path, format_quality_issues([item]))
            if repaired:
                break


async def _repair_svg_file(svg_path: str, issues: str) -> bool:
    try:
        repair_prompt = _load_svg_quality_repair_prompt()
        with open(svg_path, "r", encoding="utf-8") as fh:
            svg_content = fh.read()
        prompt = repair_prompt.format(
            issues=issues,
            svg_content=svg_content[:30000],
        )
        response = await llm_invoke(ModelRoute.PREMIUM, [HumanMessage(content=prompt)])
        repaired_svg = repair_svg_content(extract_svg_content(response))
        validate_svg_content(repaired_svg)
        with open(svg_path, "w", encoding="utf-8") as fh:
            fh.write(repaired_svg)
        logger.info(f"SVG quality repair succeeded: {svg_path}")
        return True
    except Exception as error:
        logger.warning(f"SVG quality repair failed for {svg_path}: {error}")
        return False


def _load_svg_quality_repair_prompt() -> str:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts" / "svg_quality_repair_prompt.txt"
    return path.read_text(encoding="utf-8")


async def svg_finalize_node(state: PPTState, writer: StreamWriter):
    """Finalize SVG files before native PPTX export."""
    final_svgs = finalize_svg_files(state["svgs"], state["save_dir"])
    writer(
        {
            "step": "SVG 后处理",
            "text": f"已生成 svg_final，并完成 {len(final_svgs)} 个 SVG 文件后处理。",
        }
    )
    return {
        "svgs": final_svgs,
        "svg_final_dir": os.path.join(state["save_dir"], "svg_final"),
    }


async def svgs2pptx_node(state: PPTState, writer: StreamWriter, config: RunnableConfig | None = None):
    """Convert SVG pages to native editable PPTX."""
    topic = sanitize_filename(state["topic"])
    svgs = state["svgs"]
    save_dir = state["save_dir"]

    writer(
        {
            "step": "开始导出 SVG PPT",
            "text": f"开始将 {len(svgs)} 个 SVG 页面导出为原生 PPTX，保存目录: {save_dir}。",
        }
    )

    pdf_path, pptx_path = await svgs_to_pptx(svgs, save_dir, topic)

    run_dir = run_dir_from_config(config, str(app_base_dir))
    run_id = get_run_id(config)
    if run_dir:
        save_json(f"{run_dir}/ppt.json", {
            "run_id": run_id,
            "topic": state["topic"],
            "render_mode": "svg",
            "render_dir": save_dir,
            "svg_output_dir": os.path.join(save_dir, "svg_output"),
            "svg_final_dir": state.get("svg_final_dir") or os.path.join(save_dir, "svg_final"),
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        })

    writer(
        {
            "step": "导出 SVG PPT 完成",
            "files": [pptx_path],
            "text": "生成PPT结束",
        }
    )

    return {"final_pdf_path": pdf_path, "final_pptx_path": pptx_path}
