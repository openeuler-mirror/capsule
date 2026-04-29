import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from json_repair import repair_json
from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.config import settings, app_base_dir
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_raw_invoke
from core.ppt_generator.utils.common import (
    build_remote_asset_request_router,
    get_scale_step_value,
    build_image_url,
    wait_for_page_assets_ready,
)
from core.ppt_generator.utils.browser import BrowserManager
from core.ppt_generator.utils.screenshot import screenshot_html
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.state import PPTWorkerState


PROMPT_DIR = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts"
SEVERITY_RANK = {"none": 0, "minor": 1, "critical": 2, None: 3}
VLM_VISUAL_REVIEW_MAX_ITERATIONS = 3
VLM_SCREENSHOT_DIR_NAME = "vlm_screenshots"


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


async def generate_ppt_page_node(state: PPTWorkerState):
    """generate ppt page"""
    response = await llm_invoke(ModelRoute.PREMIUM,
                                [
                                    HumanMessage(content=state["generate_ppt_prompt"]),
                                ]
                                )
    html_content = extract_html_content_regex(response)

    return {"html_content": html_content}


async def modify_ppt_page_node(state: PPTWorkerState):
    """modify wrong ppt page (legacy ratio-based path)"""
    html_path = state["final_file_path"]
    iteration = state["iteration"]

    if not html_path:
        raise ValueError("State中缺少 html_path，无法进行修改")
    if not can_vlm_invoke_route(ModelRoute.PREMIUM):
        logger.warning(
            "No available VLM route for page modification. "
            "Skip page modification and keep the current HTML."
        )
        return {"html_content": state["html_content"]}

    async with BrowserManager.get_browser_context() as browser:
        context = await browser.new_context(viewport={'width': 1280, 'height': 720}, ignore_https_errors=True)
        await context.route("**/*", build_remote_asset_request_router())
        page = await context.new_page()

        try:
            absolute_html_path = os.path.abspath(html_path)
            await page.goto(f'file://{absolute_html_path}', wait_until='domcontentloaded', timeout=60000)
            await wait_for_page_assets_ready(page, absolute_html_path)
            img_base_name = f"{os.path.basename(html_path).split('.')[0]}_screenshot_{iteration}.png"
            img_path = os.path.join(os.path.dirname(html_path), img_base_name)
            await page.screenshot(path=img_path)
            logger.info(f"缩放超过范围的截图已保存到: {img_path}")
        finally:
            await page.close()
            await context.close()

    summary_prompt = f"""
请将以下PPT单页HTML内容做结构化摘要，保留布局骨架、关键模块、文字要点与样式线索，避免冗余代码。
要求：
1) 输出中文摘要，分段描述：布局结构、主要文本内容、图表/图片占位、配色/字体/样式提示。
2) 不要输出HTML代码，不要省略关键文字内容。
3) 摘要应足够用于重构页面，但必须明显短于原HTML。

原HTML：
{state["html_content"]}
"""
    summarized_html = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=summary_prompt)])
    if not summarized_html:
        summarized_html = state["html_content"]

    generate_ppt_prompt = f"""
这个PPT的HTML网页因为内容过多，或者布局不合理等原因导致了HTML中缩放脚本过度缩放。
如果内容过多导致过度缩放，可以将不同内容合并、总结。
如果布局不合理导致缩放过度，可以彻底重新进行排版布局设计。
诸如此类，根据PPT的HTML网页过度缩放的原因，对该页面进行相应的调整。

请在保持原有缩放脚本生效的情况下对该PPT进行调整，请先对原因进行分析并分析修改方案，最后返回修改后的完整代码，完整代码使用"```html ```"进行包裹。
PPT的HTML网页摘要如下：
{summarized_html}

{state["ppt_prompt"]}
"""

    response = await vlm_raw_invoke(ModelRoute.PREMIUM, [HumanMessage(
        content=[
            {
                "type": "text",
                "text": generate_ppt_prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": build_image_url(img_path),
                },
            }
        ]
    )])
    html_content = extract_html_content_regex(response.content)

    return {"html_content": html_content}


async def ratio_evaluator_node(state: PPTWorkerState):
    """保存并基于缩放 ratio 给出下一步动作（老路径快速预筛）。"""
    index = state["index"]
    save_dir = state["save_dir"]
    html_content = state["html_content"]
    iteration = state["iteration"]
    file_path = Path(save_dir) / f"{index}.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    file_path_str = str(file_path.resolve())

    logger.info(f"save html to {file_path_str}")
    try:
        ratio = await get_scale_step_value(file_path_str)
    except Exception as e:
        logger.warning(f"get_scale_step_value failed {e}")
        ratio = 0.1

    if ratio is None:
        logger.warning(f"page {index} ratio is None.")
        ratio = 0.1

    if ratio < 0.65:
        next_action = "regenerate"
    elif ratio < 0.80:
        next_action = "modify"
    else:
        next_action = "finish"

    return {
        "action": next_action,
        "iteration": iteration + 1,
        "final_file_path": file_path_str
    }


async def vlm_judge_node(state: PPTWorkerState):
    """使用 VLM 对当前 HTML 渲染截图做视觉审阅。

    将 html_content 写盘 → 截图 → 调用 VLM 拿结构化判定 → 更新最佳版本。
    """
    index = state["index"]
    save_dir = state["save_dir"]
    html_content = state["html_content"]
    vlm_iteration = state.get("vlm_iteration") or 0

    file_path = Path(save_dir) / f"{index}.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    file_path_str = str(file_path.resolve())
    logger.info(f"save html to {file_path_str}")

    screenshot_dir = os.path.join(os.path.dirname(file_path_str), VLM_SCREENSHOT_DIR_NAME)
    os.makedirs(screenshot_dir, exist_ok=True)
    img_base_name = f"{os.path.basename(file_path_str).split('.')[0]}_vlm_{vlm_iteration}.png"
    img_path = os.path.join(screenshot_dir, img_base_name)

    try:
        await screenshot_html(file_path_str, img_path)
    except Exception as error:
        logger.warning(f"vlm_judge screenshot failed for page {index}: {error}")
        return _fallback_finish(state, file_path_str, reason="screenshot_failed")

    judge_prompt = _load_prompt("vlm_judge_prompt.txt")

    try:
        response = await vlm_raw_invoke(
            ModelRoute.PREMIUM,
            [HumanMessage(
                content=[
                    {"type": "text", "text": judge_prompt},
                    {"type": "image_url", "image_url": {"url": build_image_url(img_path)}},
                ]
            )],
            schema_name="vlm_judge",
        )
    except Exception as error:
        logger.warning(f"vlm_judge call failed for page {index}: {error}")
        return _fallback_finish(state, file_path_str, reason="vlm_unavailable", screenshot_path=img_path)

    judge_result = _parse_judge_response(response.content if response else "")
    if judge_result is None:
        logger.warning(f"vlm_judge response unparsable for page {index}, treat as no-issue")
        judge_result = {"has_issue": False, "severity": "none", "score": 100, "issues": []}

    severity = judge_result.get("severity") or "none"
    issues = judge_result.get("issues") or []
    score = _judge_score(judge_result)
    logger.info(f"page {index} vlm_judge result: severity={severity}, score={score}, issues={len(issues)}")
    if issues:
        logger.info(
            f"page {index} vlm_judge issues:\n{_format_issues(issues)}"
        )
    judge_history = _append_judge_history(
        state.get("vlm_judge_history") or [],
        vlm_iteration,
        judge_result,
    )

    update: Dict[str, Any] = {
        "final_file_path": file_path_str,
        "screenshot_path": img_path,
        "judge_result": judge_result,
        "vlm_judge_history": judge_history,
    }

    if _is_better_judge_result(
        judge_result,
        state.get("best_severity"),
        state.get("best_score"),
        state.get("best_issue_count"),
    ):
        update["best_html_content"] = html_content
        update["best_file_path"] = file_path_str
        update["best_severity"] = severity
        update["best_score"] = score
        update["best_issue_count"] = len(issues)

    return update


async def vlm_modify_node(state: PPTWorkerState):
    """使用 VLM 基于截图、问题列表和压缩历史重排当前页面。"""
    index = state["index"]
    html_content = state["html_content"]
    judge_result = state.get("judge_result") or {}
    issues: List[Dict[str, Any]] = judge_result.get("issues") or []
    screenshot_path = state.get("screenshot_path")
    vlm_iteration = state.get("vlm_iteration") or 0

    if not screenshot_path or not os.path.exists(screenshot_path):
        logger.warning(f"vlm_modify missing screenshot for page {index}, skip")
        return {"vlm_iteration": vlm_iteration + 1}

    fix_prompt_template = _load_prompt("vlm_fix_prompt.txt")
    issues_block = _format_issues(issues)
    history_block = _format_judge_history(state.get("vlm_judge_history") or [])
    fix_prompt = fix_prompt_template.format(
        issues_block=issues_block,
        history_block=history_block,
        ppt_prompt=state.get("ppt_prompt", ""),
        html_content=html_content,
    )

    try:
        response = await vlm_raw_invoke(
            ModelRoute.PREMIUM,
            [HumanMessage(
                content=[
                    {"type": "text", "text": fix_prompt},
                    {"type": "image_url", "image_url": {"url": build_image_url(screenshot_path)}},
                ]
            )],
            schema_name="vlm_fix",
        )
    except Exception as error:
        logger.warning(f"vlm_modify call failed for page {index}: {error}")
        return {"vlm_iteration": vlm_iteration + 1}

    raw_content = response.content if response else ""
    new_html = extract_html_content_regex(raw_content)
    if not new_html or not new_html.lstrip().lower().startswith(("<!doctype", "<html")):
        logger.warning(f"page {index} vlm_modify returned no usable HTML, keep current html")
        return {"vlm_iteration": vlm_iteration + 1}

    return {
        "html_content": new_html,
        "vlm_iteration": vlm_iteration + 1,
    }


def ppt_submitter_node(state: PPTWorkerState):
    """submit ppt result, 优先使用记录的 best 版本。"""
    best_file_path = state.get("best_file_path")
    best_html_content = state.get("best_html_content")
    file_path = best_file_path or state["final_file_path"]

    # best 版可能不是最后一次写盘的版本，需要把 best_html_content 重写到磁盘。
    if best_file_path and best_html_content and best_html_content != state.get("html_content"):
        try:
            with open(best_file_path, "w", encoding="utf-8") as f:
                f.write(best_html_content)
            logger.info(f'page {state["index"]} restored best version to {best_file_path}')
        except Exception as error:
            logger.warning(f"write best_html_content failed: {error}")

    return {
        "generated_pages": [
            {
                "index": state["index"],
                "file_path": file_path,
                "status": "success"
            }
        ]
    }


def route_after_generate(state: PPTWorkerState) -> str:
    """generate 之后选择走 VLM 路径还是老的 ratio 路径。"""
    if settings.ENABLE_VLM_VISUAL_REVIEW and can_vlm_invoke_route(ModelRoute.PREMIUM):
        return "VLM"
    return "RATIO"


def route_page(state: PPTWorkerState) -> str:
    """老路径路由：根据 ratio 决定 regenerate / modify / finish。"""
    if state["iteration"] > 2:
        logger.info(f'page {state["index"]} reached max retries, accepting current result.')
        return "FINISH"

    if state["action"] == "finish":
        logger.info(f'generate page {state["index"]} successful!')
        return "FINISH"
    elif state["action"] == "modify":
        return "MODIFY"
    else:
        return "GENERATE"


def route_after_judge(state: PPTWorkerState) -> str:
    """VLM 路径：根据 judge 结果决定 finish 还是 modify。"""
    judge_result = state.get("judge_result") or {}
    severity = judge_result.get("severity") or "none"
    vlm_iteration = state.get("vlm_iteration") or 0
    max_iter = max(1, VLM_VISUAL_REVIEW_MAX_ITERATIONS)

    if severity != "critical":
        logger.info(f'page {state["index"]} vlm review pass (severity={severity})')
        return "FINISH"
    if vlm_iteration >= max_iter:
        logger.info(f'page {state["index"]} reached vlm max iterations ({max_iter}), submit best version')
        return "FINISH"
    return "MODIFY"


def extract_html_content_regex(s):
    """从字符串中提取 ```html ... ``` 包裹的内容，支持不完整闭合"""
    pattern = r'```html\s*(.*?)\s*```'
    match = re.search(pattern, s, re.DOTALL | re.IGNORECASE)

    if match:
        return match.group(1).strip()

    fallback_pattern = r'```html\s*(.*)'
    fallback_match = re.search(fallback_pattern, s, re.DOTALL | re.IGNORECASE)
    if fallback_match:
        return fallback_match.group(1).strip()

    return s.strip()


def _format_issues(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return "（审阅未给出具体问题，请综合截图自行判断需修复点）"
    lines = []
    for i, item in enumerate(issues, start=1):
        item_type = item.get("type", "other")
        location = item.get("location", "")
        desc = item.get("desc", "")
        root_cause = item.get("root_cause", "")
        repair_instruction = item.get("repair_instruction", "")
        detail = f"{i}. [{item_type}] {location} —— {desc}"
        if root_cause:
            detail += f"\n   根因：{root_cause}"
        if repair_instruction:
            detail += f"\n   修复方向：{repair_instruction}"
        lines.append(detail)
    return "\n".join(lines)


def _parse_judge_response(content: str) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    try:
        parsed = repair_json(content, ensure_ascii=False, return_objects=True)
        if isinstance(parsed, dict):
            severity = parsed.get("severity")
            if severity not in ("none", "minor", "critical"):
                parsed["severity"] = "critical" if parsed.get("has_issue") else "none"
            issues = parsed.get("issues")
            if not isinstance(issues, list):
                parsed["issues"] = []
            if parsed.get("has_issue") and parsed.get("severity") == "none":
                parsed["severity"] = "critical"
            parsed["score"] = _judge_score(parsed)
            parsed["has_issue"] = parsed["severity"] == "critical"
            if parsed["severity"] == "none":
                parsed["issues"] = []
            return parsed
    except Exception as error:
        logger.debug(f"parse judge response failed: {error}, raw={content[:200]}")
    return None


def _judge_score(judge_result: Dict[str, Any]) -> float:
    raw_score = judge_result.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        severity = judge_result.get("severity")
        issues = judge_result.get("issues") or []
        score = 100.0 if severity == "none" else max(0.0, 70.0 - len(issues) * 10.0)
    return max(0.0, min(100.0, score))


def _is_better_judge_result(
    current: Dict[str, Any],
    best_severity: Optional[str],
    best_score: Optional[float],
    best_issue_count: Optional[int],
) -> bool:
    current_severity = current.get("severity") or "none"
    current_rank = SEVERITY_RANK.get(current_severity, 3)
    best_rank = SEVERITY_RANK.get(best_severity, 3)
    if current_rank != best_rank:
        return current_rank < best_rank

    current_score = _judge_score(current)
    if best_score is None or current_score != best_score:
        return best_score is None or current_score > best_score

    current_issue_count = len(current.get("issues") or [])
    if best_issue_count is None:
        return True
    return current_issue_count < best_issue_count


def _append_judge_history(
    history: List[Dict[str, Any]],
    iteration: int,
    judge_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    issues = []
    for item in (judge_result.get("issues") or [])[:5]:
        issues.append({
            "type": item.get("type", "other"),
            "location": item.get("location", ""),
            "desc": item.get("desc", ""),
            "root_cause": item.get("root_cause", ""),
            "repair_instruction": item.get("repair_instruction", ""),
        })

    entry = {
        "iteration": iteration,
        "severity": judge_result.get("severity") or "none",
        "score": _judge_score(judge_result),
        "issue_count": len(judge_result.get("issues") or []),
        "issues": issues,
    }
    return [*history, entry][-VLM_VISUAL_REVIEW_MAX_ITERATIONS:]


def _format_judge_history(history: List[Dict[str, Any]]) -> str:
    if not history:
        return "（无历史审阅结果，这是首次 VLM 修复。）"

    lines = []
    for item in history[-VLM_VISUAL_REVIEW_MAX_ITERATIONS:]:
        lines.append(
            f"- 第 {item.get('iteration', 0)} 轮："
            f"severity={item.get('severity', 'none')}，"
            f"score={item.get('score', '未知')}，"
            f"issues={item.get('issue_count', 0)}"
        )
        for issue in item.get("issues", []):
            detail = (
                f"  * [{issue.get('type', 'other')}] "
                f"{issue.get('location', '')} —— {issue.get('desc', '')}"
            )
            root_cause = issue.get("root_cause")
            repair_instruction = issue.get("repair_instruction")
            if root_cause:
                detail += f"；根因：{root_cause}"
            if repair_instruction:
                detail += f"；修复方向：{repair_instruction}"
            lines.append(detail)
    return "\n".join(lines)


def _fallback_finish(
    state: PPTWorkerState,
    file_path_str: str,
    reason: str,
    screenshot_path: Optional[str] = None,
) -> Dict[str, Any]:
    """VLM 不可用或截图失败时的兜底：直接以当前版本结束 VLM 路径。"""
    logger.info(f'page {state["index"]} vlm path fallback to finish (reason={reason})')
    update: Dict[str, Any] = {
        "final_file_path": file_path_str,
        "judge_result": {"has_issue": False, "severity": "none", "score": 100, "issues": []},
    }
    if screenshot_path is not None:
        update["screenshot_path"] = screenshot_path
    if state.get("best_severity") is None:
        update["best_html_content"] = state["html_content"]
        update["best_file_path"] = file_path_str
        update["best_severity"] = "none"
        update["best_score"] = 100.0
        update["best_issue_count"] = 0
    return update
