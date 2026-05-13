import os
from pathlib import Path

from langchain.messages import HumanMessage

from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.node import (
    VLM_SCREENSHOT_DIR_NAME,
    _format_issues,
    _format_judge_history,
    _parse_judge_response,
)
from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator.state import SVGWorkerState
from core.ppt_generator.utils.common import build_image_url
from core.ppt_generator.utils.screenshot import screenshot_svg
from core.ppt_generator.utils.svg import extract_svg_content, repair_svg_content, safe_svg_filename, validate_svg_content
from core.utils.config import app_base_dir, settings
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_raw_invoke
from core.utils.logger import logger


SVG_VLM_REVIEW_MAX_ITERATIONS = 1


def _build_reference_images_block(paths: list[str]) -> str:
    if not paths:
        return "无"
    lines = []
    for path in paths:
        if os.path.exists(path):
            lines.append(f"- {path}")
    return "\n".join(lines) if lines else "无"


async def generate_svg_page_node(state: SVGWorkerState):
    """Generate one PPT page as SVG."""
    page = state["page"]
    logger.info(f"start generate svg page {page.index}...")

    prompt = f"""
{state["svg_prompt"]}

# 全局设计锁定
每一页都必须遵守以下 spec_lock，不要自行更换画布、主字体、基础色板和页面节奏：
{state["svg_spec_lock"]}

# SVG 模板参考
请吸收以下模板的视觉风格和布局纪律：
{state["svg_template"]}

# 整体 PPT 请求
{state["query"]}

# 完整大纲
{state["outline"]}

# 当前页面
- index: {page.index}
- title: {page.title}
- type: {page.type.name}
- abstract: {page.abstract}

# 当前页面参考资料
{page.reference_doc}

# 可用图片绝对路径
{_build_reference_images_block(page.reference_images)}

# 语言
生成页面文字必须使用：{state["language"]}
"""
    response = await llm_invoke(ModelRoute.PREMIUM, [HumanMessage(content=prompt)])
    svg_content = repair_svg_content(extract_svg_content(response))
    validate_svg_content(svg_content)

    svg_output_dir = Path(state["save_dir"]) / "svg_output"
    svg_output_dir.mkdir(parents=True, exist_ok=True)
    file_path = svg_output_dir / safe_svg_filename(page.index, page.title)
    file_path.write_text(svg_content, encoding="utf-8")
    svg_content = await _maybe_review_svg_with_vlm(str(file_path.resolve()), svg_content)
    if svg_content:
        file_path.write_text(svg_content, encoding="utf-8")

    return {
        "generated_pages": [
            {
                "index": page.index,
                "file_path": str(file_path.resolve()),
                "status": "success",
            }
        ]
    }


def load_svg_prompt() -> str:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts" / "svg_generator_prompt.txt"
    return path.read_text(encoding="utf-8")


async def _maybe_review_svg_with_vlm(svg_path: str, svg_content: str) -> str:
    if not settings.ENABLE_VLM_VISUAL_REVIEW or not can_vlm_invoke_route(ModelRoute.PREMIUM):
        return svg_content

    current_svg = svg_content
    history = []
    for iteration in range(SVG_VLM_REVIEW_MAX_ITERATIONS):
        screenshot_path = _svg_review_screenshot_path(svg_path, iteration)
        try:
            await screenshot_svg(svg_path, screenshot_path)
        except Exception as error:
            logger.warning(f"svg vlm screenshot failed for {svg_path}: {error}")
            return current_svg

        judge_result = await _judge_svg_screenshot(screenshot_path)
        if judge_result is None:
            logger.warning(f"svg vlm judge response unparsable for {svg_path}, keep current SVG")
            return current_svg

        severity = judge_result.get("severity") or "none"
        issues = judge_result.get("issues") or []
        logger.info(f"svg vlm judge result for {svg_path}: severity={severity}, issues={len(issues)}")
        if severity != "critical":
            return current_svg

        history.append({
            "iteration": iteration,
            "severity": severity,
            "issue_count": len(issues),
            "issues": issues[:5],
        })
        repaired_svg = await _fix_svg_with_vlm(screenshot_path, current_svg, judge_result, history)
        if not repaired_svg:
            return current_svg

        current_svg = repaired_svg
        Path(svg_path).write_text(current_svg, encoding="utf-8")

    return current_svg


def _svg_review_screenshot_path(svg_path: str, iteration: int) -> str:
    svg_file = Path(svg_path)
    screenshot_dir = svg_file.parent / VLM_SCREENSHOT_DIR_NAME
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return str(screenshot_dir / f"{svg_file.stem}_vlm_{iteration}.png")


async def _judge_svg_screenshot(screenshot_path: str) -> dict | None:
    judge_prompt = _load_prompt("vlm_judge_prompt.txt")
    try:
        response = await vlm_raw_invoke(
            ModelRoute.PREMIUM,
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": judge_prompt},
                        {"type": "image_url", "image_url": {"url": build_image_url(screenshot_path)}},
                    ]
                )
            ],
            schema_name="svg_vlm_judge",
        )
    except Exception as error:
        logger.warning(f"svg vlm judge call failed: {error}")
        return None
    return _parse_judge_response(response.content if response else "")


async def _fix_svg_with_vlm(
    screenshot_path: str,
    svg_content: str,
    judge_result: dict,
    history: list[dict],
) -> str | None:
    fix_prompt_template = _load_prompt("svg_vlm_fix_prompt.txt")
    fix_prompt = fix_prompt_template.format(
        issues_block=_format_issues(judge_result.get("issues") or []),
        history_block=_format_judge_history(history),
        svg_content=svg_content[:30000],
    )
    try:
        response = await vlm_raw_invoke(
            ModelRoute.PREMIUM,
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": fix_prompt},
                        {"type": "image_url", "image_url": {"url": build_image_url(screenshot_path)}},
                    ]
                )
            ],
            schema_name="svg_vlm_fix",
        )
        repaired_svg = repair_svg_content(extract_svg_content(response.content if response else ""))
        validate_svg_content(repaired_svg)
        return repaired_svg
    except Exception as error:
        logger.warning(f"svg vlm fix failed: {error}")
        return None


def _load_prompt(name: str) -> str:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts" / name
    return path.read_text(encoding="utf-8")
