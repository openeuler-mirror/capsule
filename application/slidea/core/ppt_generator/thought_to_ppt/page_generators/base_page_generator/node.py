import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from json_repair import repair_json
from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.config import settings, app_base_dir
from core.utils.llm import can_vlm_invoke, llm_invoke, vlm_raw_invoke
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
VLM_VISUAL_REVIEW_MAX_ITERATIONS = 1
VLM_SCREENSHOT_DIR_NAME = "vlm_screenshots"
VLM_HTML_CANDIDATE_DIR_NAME = "vlm_html_candidates"
GENERATE_PROMPT_LOG_DIR_NAME = "prompts"
HTML_JUDGE_PROMPT = "vlm_judge_prompt.txt"
HTML_FIX_PROMPT = "vlm_fix_prompt.txt"


@dataclass
class VLMCandidateInput:
    iteration: int
    file_path: str
    screenshot_path: str
    content: str
    judge_result: Dict[str, Any]


def _save_generate_prompt(state: PPTWorkerState) -> None:
    """Persist the per-page generation prompt to ``<save_dir>/prompts/`` for inspection.

    Best-effort: filesystem failures are logged but don't interrupt generation.
    """
    save_dir = state.get("save_dir")
    prompt = state.get("generate_ppt_prompt")
    if not save_dir or not prompt:
        return
    index = state.get("index", 0)
    page = _resolve_page(state)
    title = getattr(page, "title", None)
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", title or "slide")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")[:40] or "slide"
    filename = f"{index + 1:02d}_{cleaned}.txt"
    prompt_dir = Path(save_dir) / GENERATE_PROMPT_LOG_DIR_NAME
    try:
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / filename).write_text(prompt, encoding="utf-8")
    except OSError as error:
        logger.warning(f"save generate prompt failed: {error}")


def _resolve_page(state: PPTWorkerState):
    """Pull the active PPTPage from state (added in Stage 2). Returns None if absent."""
    return state.get("page") if isinstance(state, dict) else None


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


async def generate_ppt_page_node(state: PPTWorkerState):
    """generate ppt page"""
    _save_generate_prompt(state)
    response = await llm_invoke(
        [HumanMessage(content=state["generate_ppt_prompt"])],
    )
    html_content = extract_html_content_regex(response)

    return {"content": html_content}


async def modify_ppt_page_node(state: PPTWorkerState):
    """modify wrong ppt page (legacy ratio-based path)"""
    html_path = state["final_file_path"]
    iteration = state["iteration"]

    if not html_path:
        raise ValueError("State中缺少 html_path，无法进行修改")
    if not can_vlm_invoke():
        logger.warning(
            "DEFAULT_VLM is not configured. "
            "Skip page modification and keep the current HTML."
        )
        return {"content": state["content"]}

    async with BrowserManager.get_browser_context() as browser:
        context = await browser.new_context(viewport={'width': 1280, 'height': 720}, ignore_https_errors=True)
        await context.route("**/*", build_remote_asset_request_router())
        page = await context.new_page()

        try:
            absolute_html_path = os.path.abspath(html_path)
            await page.goto(f'file://{absolute_html_path}', wait_until='domcontentloaded', timeout=120000)
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
{state["content"]}
"""
    summarized_html = await llm_invoke(
        [HumanMessage(content=summary_prompt)],
    )
    if not summarized_html:
        summarized_html = state["content"]

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

    response = await vlm_raw_invoke(
        [HumanMessage(
            content=[
                {"type": "text", "text": generate_ppt_prompt},
                {"type": "image_url", "image_url": {"url": build_image_url(img_path)}},
            ]
        )],
    )
    html_content = extract_html_content_regex(response.content)

    return {"content": html_content}


async def ratio_evaluator_node(state: PPTWorkerState):
    """保存并基于缩放 ratio 给出下一步动作（老路径快速预筛）。"""
    index = state["index"]
    save_dir = state["save_dir"]
    html_content = state["content"]
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
        "final_file_path": file_path_str,
    }


async def vlm_judge_node(state: PPTWorkerState):
    """使用 VLM 对当前 HTML 渲染截图做视觉审阅。"""
    index = state["index"]
    save_dir = state["save_dir"]
    html_content = state["content"]
    vlm_iteration = state.get("vlm_iteration") or 0

    file_path = Path(save_dir) / f"{index}.html"
    file_path.parent.mkdir(parents=True, exist_ok=True)
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

    judge_prompt = _load_prompt(HTML_JUDGE_PROMPT)

    try:
        response = await vlm_raw_invoke(
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
        judge_result = {"has_issue": False, "severity": "none", "issues": []}

    severity = judge_result.get("severity") or "none"
    issues = judge_result.get("issues") or []
    logger.info(f"page {index} vlm_judge result: severity={severity}, issues={len(issues)}")
    if issues:
        logger.info(f"page {index} vlm_judge issues:\n{_format_issues(issues)}")
    judge_history = _append_judge_history(
        state.get("vlm_judge_history") or [],
        vlm_iteration,
        judge_result,
    )
    candidates = _append_vlm_candidate(
        state.get("vlm_candidates") or [],
        VLMCandidateInput(
            iteration=vlm_iteration,
            file_path=file_path_str,
            screenshot_path=img_path,
            content=html_content,
            judge_result=judge_result,
        ),
    )

    update: Dict[str, Any] = {
        "final_file_path": file_path_str,
        "screenshot_path": img_path,
        "judge_result": judge_result,
        "vlm_judge_history": judge_history,
        "vlm_candidates": candidates,
    }

    if _is_better_judge_result(
        judge_result,
        state.get("best_severity"),
        state.get("best_issue_count"),
    ):
        update["best_content"] = html_content
        update["best_file_path"] = file_path_str
        update["best_severity"] = severity
        update["best_issue_count"] = len(issues)

    return update


async def vlm_modify_node(state: PPTWorkerState):
    """使用 VLM 基于截图、问题列表和压缩历史重排当前 HTML 页面。"""
    index = state["index"]
    html_content = state["content"]
    judge_result = state.get("judge_result") or {}
    issues: List[Dict[str, Any]] = judge_result.get("issues") or []
    screenshot_path = state.get("screenshot_path")
    vlm_iteration = state.get("vlm_iteration") or 0

    if not screenshot_path or not os.path.exists(screenshot_path):
        logger.warning(f"vlm_modify missing screenshot for page {index}, skip")
        return {"vlm_iteration": vlm_iteration + 1}

    fix_prompt_template = _load_prompt(HTML_FIX_PROMPT)
    issues_block = _format_issues(issues)
    history_block = _format_judge_history(state.get("vlm_judge_history") or [])
    fix_prompt = fix_prompt_template.format(
        issues_block=issues_block,
        history_block=history_block,
        ppt_prompt=state.get("ppt_prompt", ""),
        content=html_content,
    )

    try:
        response = await vlm_raw_invoke(
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
        "content": new_html,
        "vlm_iteration": vlm_iteration + 1,
    }


async def vlm_select_best_node(state: PPTWorkerState):
    """达到 VLM 修改上限后，基于多轮截图让 VLM 横向选择最佳版本。"""
    index = state["index"]
    candidates = _valid_vlm_candidates(state.get("vlm_candidates") or [])
    if len(candidates) <= 1:
        logger.info(f"page {index} skip vlm best selection, candidates={len(candidates)}")
        return {
            "vlm_selection_record": {
                "method": "skipped_single_candidate",
                "selected_iteration": candidates[0]["iteration"] if candidates else None,
                "reason": "candidates<=1, no cross-comparison needed",
            }
        }

    prompt = _build_best_selection_prompt(candidates)
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for candidate in candidates:
        content.extend([
            {"type": "text", "text": f"候选 iteration={candidate['iteration']} 的截图："},
            {"type": "image_url", "image_url": {"url": build_image_url(candidate["screenshot_path"])}},
        ])

    try:
        response = await vlm_raw_invoke(
            [HumanMessage(content=content)],
            schema_name="vlm_select_best",
        )
    except Exception as error:
        logger.warning(f"vlm_select_best call failed for page {index}: {error}")
        return {
            "vlm_selection_record": {
                "method": "issue_count_fallback",
                "selected_iteration": state.get("best_severity"),
                "reason": f"vlm_select_best call failed: {error}",
            }
        }

    selection = _parse_best_selection_response(response.content if response else "", candidates)
    selected_iteration = selection.get("selected_iteration") if selection else None
    if selected_iteration is None:
        logger.warning(f"page {index} vlm_select_best response unparsable, keep issue-count best version")
        return {
            "vlm_selection_record": {
                "method": "issue_count_fallback",
                "selected_iteration": None,
                "reason": "vlm_select_best response unparsable",
            }
        }
    reason = selection.get("reason") or ""

    selected = next((item for item in candidates if item["iteration"] == selected_iteration), None)
    if not selected:
        logger.warning(f"page {index} vlm_select_best chose missing iteration={selected_iteration}")
        return {
            "vlm_selection_record": {
                "method": "issue_count_fallback",
                "selected_iteration": None,
                "reason": f"vlm chose missing iteration={selected_iteration}",
            }
        }

    logger.info(
        f"page {index} vlm_select_best selected iteration={selected_iteration}, "
        f"reason={reason}, severity={selected['severity']}, issues={selected['issue_count']}"
    )
    selected_html_content = _read_vlm_candidate_html(selected)
    if not selected_html_content:
        logger.warning(
            f"page {index} selected candidate iteration={selected_iteration} missing html content, "
            "keep issue-count best version"
        )
        return {
            "vlm_selection_record": {
                "method": "issue_count_fallback",
                "selected_iteration": selected_iteration,
                "reason": "selected candidate file missing on disk",
            }
        }

    return {
        "final_file_path": selected["file_path"],
        "screenshot_path": selected["screenshot_path"],
        "best_content": selected_html_content,
        "best_file_path": selected["file_path"],
        "best_severity": selected["severity"],
        "best_issue_count": selected["issue_count"],
        "vlm_selection_record": {
            "method": "vlm_select_best",
            "selected_iteration": selected_iteration,
            "reason": reason,
        },
    }


def ppt_submitter_node(state: PPTWorkerState):
    """submit ppt result, 优先使用记录的 best 版本，并落盘 vlm_review.json 审计记录。"""
    best_file_path = state.get("best_file_path")
    best_html_content = state.get("best_content")
    file_path = best_file_path or state["final_file_path"]

    if best_file_path and best_html_content and best_html_content != state.get("content"):
        try:
            with open(best_file_path, "w", encoding="utf-8") as f:
                f.write(best_html_content)
            logger.info(f'page {state["index"]} restored best version to {best_file_path}')
        except Exception as error:
            logger.warning(f"write best_html_content failed: {error}")

    _write_vlm_review_json(state, file_path)

    return {
        "generated_pages": [
            {
                "index": state["index"],
                "file_path": file_path,
                "status": "success",
            }
        ]
    }


def _write_vlm_review_json(state: PPTWorkerState, final_file_path: str) -> None:
    """Persist a per-page audit record of the VLM review process (under vlm_html_candidates/)."""
    import json

    candidates = state.get("vlm_candidates") or []
    if not candidates:
        return

    candidate_dir = os.path.dirname(candidates[0].get("html_path") or "") or None
    if not candidate_dir:
        return

    selection_record = state.get("vlm_selection_record") or {
        "method": "early_exit_non_critical",
        "selected_iteration": candidates[-1].get("iteration"),
        "reason": "first iteration passed VLM judge with severity!=critical",
    }
    final_iteration = next(
        (c.get("iteration") for c in candidates if c.get("file_path") == final_file_path),
        candidates[-1].get("iteration"),
    )

    iterations_payload = []
    for candidate in candidates:
        iterations_payload.append({
            "iteration": candidate.get("iteration"),
            "candidate_path": candidate.get("html_path"),
            "screenshot_path": candidate.get("screenshot_path"),
            "severity": candidate.get("severity"),
            "issue_count": candidate.get("issue_count"),
            "issues": candidate.get("issues") or [],
        })

    record = {
        "page_index": state.get("index"),
        "final_file_path": final_file_path,
        "final_iteration": final_iteration,
        "selection": selection_record,
        "iterations": iterations_payload,
    }

    page_stem = os.path.splitext(os.path.basename(final_file_path))[0] or f"page_{state.get('index')}"
    review_path = os.path.join(candidate_dir, f"{page_stem}_vlm_review.json")
    try:
        with open(review_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
        logger.info(f"page {state.get('index')} vlm review written to {review_path}")
    except Exception as error:
        logger.warning(f"write vlm review json failed for page {state.get('index')}: {error}")


def route_after_generate(state: PPTWorkerState) -> str:
    """generate 之后选择审阅路径：VLM > ratio。"""
    if settings.ENABLE_VLM_VISUAL_REVIEW and can_vlm_invoke():
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
        logger.info(f'page {state["index"]} reached vlm max iterations ({max_iter}), select best screenshot')
        return "SELECT_BEST"
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
        repair_instruction = item.get("repair_instruction", "")
        detail = f"{i}. [{item_type}] {location} —— {desc}"
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
            issues = parsed.get("issues")
            if not isinstance(issues, list):
                parsed["issues"] = []
            has_issue = bool(parsed.get("has_issue")) or bool(parsed["issues"])
            if severity not in ("none", "critical"):
                parsed["severity"] = "critical" if has_issue else "none"
            if has_issue and parsed.get("severity") == "none":
                parsed["severity"] = "critical"
            parsed["has_issue"] = parsed["severity"] == "critical"
            if parsed["severity"] == "none":
                parsed["issues"] = []
            return parsed
    except Exception as error:
        logger.debug(f"parse judge response failed: {error}, raw={content[:200]}")
    return None


def _is_better_judge_result(
    current: Dict[str, Any],
    best_severity: Optional[str],
    best_issue_count: Optional[int],
) -> bool:
    current_severity = current.get("severity") or "none"
    current_rank = SEVERITY_RANK.get(current_severity, 3)
    best_rank = SEVERITY_RANK.get(best_severity, 3)
    if current_rank != best_rank:
        return current_rank < best_rank

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
            "repair_instruction": item.get("repair_instruction", ""),
        })

    entry = {
        "iteration": iteration,
        "severity": judge_result.get("severity") or "none",
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
            f"issues={item.get('issue_count', 0)}"
        )
        for issue in item.get("issues", []):
            detail = (
                f"  * [{issue.get('type', 'other')}] "
                f"{issue.get('location', '')} —— {issue.get('desc', '')}"
            )
            repair_instruction = issue.get("repair_instruction")
            if repair_instruction:
                detail += f"；修复方向：{repair_instruction}"
            lines.append(detail)
    return "\n".join(lines)


def _append_vlm_candidate(
    candidates: List[Dict[str, Any]],
    candidate_input: VLMCandidateInput,
) -> List[Dict[str, Any]]:
    html_path = _write_vlm_candidate_html(
        candidate_input.file_path,
        candidate_input.iteration,
        candidate_input.content,
    )
    entry = {
        "iteration": candidate_input.iteration,
        "file_path": candidate_input.file_path,
        "screenshot_path": candidate_input.screenshot_path,
        "html_path": html_path,
        "severity": candidate_input.judge_result.get("severity") or "none",
        "issue_count": len(candidate_input.judge_result.get("issues") or []),
        "issues": candidate_input.judge_result.get("issues") or [],
    }
    max_candidates = max(1, VLM_VISUAL_REVIEW_MAX_ITERATIONS + 1)
    return [*candidates, entry][-max_candidates:]


def _valid_vlm_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid = []
    seen_iterations = set()
    for candidate in candidates:
        iteration = candidate.get("iteration")
        screenshot_path = candidate.get("screenshot_path")
        html_path = candidate.get("html_path")
        if iteration in seen_iterations:
            continue
        if not screenshot_path or not os.path.exists(screenshot_path):
            continue
        if not html_path or not os.path.exists(html_path):
            continue
        valid.append(candidate)
        seen_iterations.add(iteration)
    return valid


def _write_vlm_candidate_html(file_path: str, iteration: int, html_content: str) -> str:
    """Write a candidate HTML file for later cross-comparison (under vlm_html_candidates/)."""
    base_dir = os.path.dirname(file_path)
    candidate_dir = os.path.join(base_dir, VLM_HTML_CANDIDATE_DIR_NAME)
    os.makedirs(candidate_dir, exist_ok=True)
    page_name = os.path.splitext(os.path.basename(file_path))[0]
    candidate_path = os.path.join(candidate_dir, f"{page_name}_vlm_{iteration}.html")
    with open(candidate_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    return candidate_path


def _read_vlm_candidate_html(candidate: Dict[str, Any]) -> Optional[str]:
    html_path = candidate.get("html_path")
    if not html_path or not os.path.exists(html_path):
        return None
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as error:
        logger.warning(f"read vlm candidate html failed: {error}")
        return None


def _build_best_selection_prompt(candidates: List[Dict[str, Any]]) -> str:
    candidate_lines = []
    for candidate in candidates:
        candidate_lines.append(
            f"- iteration={candidate['iteration']}: "
            f"severity={candidate['severity']}, "
            f"issue_count={candidate['issue_count']}"
        )

    return f"""
你是一名 PPT 视觉质量仲裁员。下面会依次给出同一页 PPT 在多轮 VLM 修复过程中的截图候选，请选择最终应该提交的一版。

# 选择原则
1. 首先选择没有明显严重排版错误的一版。
2. 如果都有问题，选择问题最少、遮挡/裁切/溢出最轻、主体内容最完整的一版。
3. 不要只依赖候选摘要；截图中的实际视觉效果优先。
4. 重点比较：文字是否完整显示、卡片是否互相侵占、图片是否图裂或过小、内容是否溢出、页面是否大面积空白或错乱。
5. 只能从给出的候选 iteration 中选择一个，不要创造新编号。

# 候选摘要
{chr(10).join(candidate_lines)}

# 输出要求
仅返回 JSON，不要使用 Markdown：
{{
  "selected_iteration": 候选中的 iteration 数字,
  "reason": "一句话说明为什么这版最好"
}}
"""


def _parse_best_selection_response(
    content: str,
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not content:
        return None

    valid_iterations = {candidate["iteration"] for candidate in candidates}
    try:
        parsed = repair_json(content, ensure_ascii=False, return_objects=True)
    except Exception as error:
        logger.debug(f"parse best selection response failed: {error}, raw={content[:200]}")
        return None

    if not isinstance(parsed, dict):
        return None

    raw_iteration = (
        parsed.get("selected_iteration")
        if parsed.get("selected_iteration") is not None
        else parsed.get("iteration")
    )
    if raw_iteration is None and parsed.get("selected_index") is not None:
        try:
            index = int(parsed["selected_index"])
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(candidates):
            raw_iteration = candidates[index]["iteration"]

    try:
        iteration = int(raw_iteration)
    except (TypeError, ValueError):
        return None

    if iteration not in valid_iterations:
        return None
    return {
        "selected_iteration": iteration,
        "reason": str(parsed.get("reason") or "").strip(),
    }


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
        "judge_result": {"has_issue": False, "severity": "none", "issues": []},
    }
    if screenshot_path is not None:
        update["screenshot_path"] = screenshot_path
    if state.get("best_severity") is None:
        update["best_content"] = state["content"]
        update["best_file_path"] = file_path_str
        update["best_severity"] = "none"
        update["best_issue_count"] = 0
    return update
