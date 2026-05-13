import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from json_repair import repair_json
from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.config import settings, app_base_dir
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_raw_invoke
from core.ppt_generator.utils.common import build_image_url
from core.ppt_generator.utils.screenshot import screenshot_svg
from core.ppt_generator.utils.svg import (
    extract_svg_content,
    repair_svg_content,
    safe_svg_filename,
    validate_svg_content,
)
from core.ppt_generator.utils.svg_pipeline.finalize_svg import embed_local_images_in_content
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.state import SVGWorkerState


PROMPT_DIR = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts"
SEVERITY_RANK = {"none": 0, "minor": 1, "critical": 2, None: 3}
VLM_VISUAL_REVIEW_MAX_ITERATIONS = 3
VLM_SCREENSHOT_DIR_NAME = "vlm_screenshots"
SVG_JUDGE_PROMPT = "svg_vlm_judge_prompt.txt"
SVG_FIX_PROMPT = "svg_vlm_fix_prompt.txt"
SVG_QUALITY_REPAIR_PROMPT = "svg_quality_repair_prompt.txt"
SVG_GENERATION_REPAIR_MAX_ATTEMPTS = 1
_XLINK_NS = "http://www.w3.org/1999/xlink"


@dataclass
class VLMCandidateInput:
    iteration: int
    file_path: str
    screenshot_path: str
    content: str
    judge_result: Dict[str, Any]


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


async def _svg_process_llm_response(raw: str, *, page_index: int = 0) -> str:
    """Extract → deterministic repair → XML validate. One LLM-repair retry on parse failure."""
    svg_content = repair_svg_content(extract_svg_content(raw))
    try:
        validate_svg_content(svg_content)
        return svg_content
    except ValueError as error:
        logger.warning(
            f"svg page {page_index} initial validation failed, trying LLM repair: {error}"
        )
        last_error: Exception = error

    for attempt in range(SVG_GENERATION_REPAIR_MAX_ATTEMPTS):
        repaired = await _llm_repair_svg(svg_content, str(last_error))
        if repaired is None:
            continue
        try:
            validate_svg_content(repaired)
            logger.info(f"svg page {page_index} LLM repair succeeded on attempt {attempt + 1}")
            return repaired
        except ValueError as error:
            last_error = error
            svg_content = repaired

    validate_svg_content(svg_content)
    return svg_content


async def _llm_repair_svg(svg_content: str, issue: str) -> Optional[str]:
    try:
        prompt_template = _load_prompt(SVG_QUALITY_REPAIR_PROMPT)
        prompt = prompt_template.format(
            issues=issue or "SVG is not well-formed XML",
            content=svg_content[:30000],
        )
        response = await llm_invoke(ModelRoute.PREMIUM, [HumanMessage(content=prompt)])
        return repair_svg_content(extract_svg_content(response))
    except Exception as error:
        logger.warning(f"svg LLM repair call failed: {error}")
        return None


def _svg_page_filename(page) -> str:
    """svg_output/{nn}_{safe_title}.svg relative path."""
    return f"svg_output/{safe_svg_filename(page.index, page.title)}"


async def _svg_screenshot_with_embedded_images(source_path: str, output_path: str) -> str:
    """Screenshot an SVG for VLM review, embedding local images first.

    SVGs in svg_output/ reference images via relative paths like ``images/xxx.jpg``,
    which resolve to ``svg_output/images/...`` in Chromium and break. Embed local
    images as data URIs into a sibling temp file before calling Playwright so VLM
    sees the real images.
    """
    src = Path(source_path)
    try:
        content = src.read_text(encoding="utf-8")
    except Exception as error:
        logger.warning(f"screenshot read svg failed for {source_path}: {error}")
        return await screenshot_svg(source_path, output_path)

    embedded = embed_local_images_in_content(content, src.parent)
    if embedded == content:
        return await screenshot_svg(source_path, output_path)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".svg", dir=str(src.parent), delete=False, encoding="utf-8"
    ) as fh:
        tmp_path = fh.name
        fh.write(embedded)
    try:
        return await screenshot_svg(tmp_path, output_path)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


async def generate_ppt_page_node(state: SVGWorkerState):
    """generate svg ppt page"""
    response = await llm_invoke(
        ModelRoute.PREMIUM,
        [HumanMessage(content=state["generate_ppt_prompt"])],
    )
    svg_content = await _svg_process_llm_response(response, page_index=state.get("index", 0))

    return {"content": svg_content}


async def vlm_judge_node(state: SVGWorkerState):
    """使用 VLM 对当前 SVG 渲染截图做视觉审阅。

    将 svg_content 写盘 → 截图 → 调用 VLM 拿结构化判定 → 更新最佳版本。
    """
    index = state["index"]
    save_dir = state["save_dir"]
    svg_content = state["content"]
    vlm_iteration = state.get("vlm_iteration") or 0

    page = state.get("page")
    if page is not None:
        file_path = Path(save_dir) / _svg_page_filename(page)
    else:
        file_path = Path(save_dir) / f"{index}.svg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    file_path_str = str(file_path.resolve())
    logger.info(f"save svg to {file_path_str}")

    screenshot_dir = os.path.join(os.path.dirname(file_path_str), VLM_SCREENSHOT_DIR_NAME)
    os.makedirs(screenshot_dir, exist_ok=True)
    img_base_name = f"{os.path.basename(file_path_str).split('.')[0]}_vlm_{vlm_iteration}.png"
    img_path = os.path.join(screenshot_dir, img_base_name)

    try:
        await _svg_screenshot_with_embedded_images(file_path_str, img_path)
    except Exception as error:
        logger.warning(f"vlm_judge screenshot failed for page {index}: {error}")
        return _fallback_finish(state, file_path_str, reason="screenshot_failed")

    judge_prompt = _load_prompt(SVG_JUDGE_PROMPT)

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
            content=svg_content,
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
        update["best_content"] = svg_content
        update["best_file_path"] = file_path_str
        update["best_severity"] = severity
        update["best_issue_count"] = len(issues)

    return update


async def vlm_modify_node(state: SVGWorkerState):
    """使用 VLM 基于截图、问题列表和压缩历史重排当前 SVG 页面。"""
    index = state["index"]
    svg_content = state["content"]
    judge_result = state.get("judge_result") or {}
    issues: List[Dict[str, Any]] = judge_result.get("issues") or []
    screenshot_path = state.get("screenshot_path")
    vlm_iteration = state.get("vlm_iteration") or 0

    if not screenshot_path or not os.path.exists(screenshot_path):
        logger.warning(f"vlm_modify missing screenshot for page {index}, skip")
        return {"vlm_iteration": vlm_iteration + 1}

    fix_prompt_template = _load_prompt(SVG_FIX_PROMPT)
    issues_block = _format_issues(issues)
    history_block = _format_judge_history(state.get("vlm_judge_history") or [])
    fix_prompt = fix_prompt_template.format(
        issues_block=issues_block,
        history_block=history_block,
        ppt_prompt=state.get("ppt_prompt", ""),
        content=svg_content,
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
    try:
        new_svg = await _svg_process_llm_response(raw_content, page_index=index)
    except Exception as error:
        logger.warning(f"page {index} vlm_modify svg processing failed: {error}, keep current")
        return {"vlm_iteration": vlm_iteration + 1}
    if not new_svg:
        logger.warning(f"page {index} vlm_modify returned empty content, keep current")
        return {"vlm_iteration": vlm_iteration + 1}

    return {
        "content": new_svg,
        "vlm_iteration": vlm_iteration + 1,
    }


async def vlm_select_best_node(state: SVGWorkerState):
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
            {
                "type": "text",
                "text": f"候选 iteration={candidate['iteration']} 的截图：",
            },
            {
                "type": "image_url",
                "image_url": {"url": build_image_url(candidate["screenshot_path"])},
            },
        ])

    try:
        response = await vlm_raw_invoke(
            ModelRoute.PREMIUM,
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
    selected_svg_content = _read_vlm_candidate_html(selected)
    if not selected_svg_content:
        logger.warning(
            f"page {index} selected candidate iteration={selected_iteration} missing svg content, "
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
        "best_content": selected_svg_content,
        "best_file_path": selected["file_path"],
        "best_severity": selected["severity"],
        "best_issue_count": selected["issue_count"],
        "vlm_selection_record": {
            "method": "vlm_select_best",
            "selected_iteration": selected_iteration,
            "reason": reason,
        },
    }


def ppt_submitter_node(state: SVGWorkerState):
    """submit svg result, 优先使用记录的 best 版本，并落盘 vlm_review.json 审计记录。"""
    best_file_path = state.get("best_file_path")
    best_svg_content = state.get("best_content")
    file_path = best_file_path or state["final_file_path"]

    if best_file_path and best_svg_content and best_svg_content != state.get("content"):
        try:
            with open(best_file_path, "w", encoding="utf-8") as f:
                f.write(best_svg_content)
            logger.info(f'page {state["index"]} restored best version to {best_file_path}')
        except Exception as error:
            logger.warning(f"write best_svg_content failed: {error}")

    _write_vlm_review_json(state, file_path)

    return {
        "generated_pages": [
            {
                "index": state["index"],
                "file_path": file_path,
                "status": "success"
            }
        ]
    }


def _write_vlm_review_json(state: SVGWorkerState, final_file_path: str) -> None:
    """Persist a per-page audit record of the VLM review process under vlm_svg_candidates/."""
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


def route_after_generate(state: SVGWorkerState) -> str:
    """generate 之后选择审阅路径：VLM 可用就走 VLM，否则直接落盘。"""
    if settings.ENABLE_VLM_VISUAL_REVIEW and can_vlm_invoke_route(ModelRoute.PREMIUM):
        return "VLM"
    return "SAVE_ONLY"


async def save_only_node(state: SVGWorkerState):
    """无审阅分支：按 SVG 命名规则把生成内容写入磁盘并准备 submit。"""
    save_dir = state["save_dir"]
    content = state["content"]
    page = state.get("page")
    if page is not None:
        file_path = Path(save_dir) / _svg_page_filename(page)
    else:
        file_path = Path(save_dir) / f"{state['index']}.svg"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    file_path_str = str(file_path.resolve())
    logger.info(f"save svg to {file_path_str} (no-review path)")
    return {"final_file_path": file_path_str}


def route_after_judge(state: SVGWorkerState) -> str:
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


def _write_vlm_candidate_html(file_path: str, iteration: int, svg_content: str) -> str:
    """Write a candidate SVG file for later cross-comparison (under vlm_svg_candidates/)."""
    base_dir = os.path.dirname(file_path)
    candidate_dir = os.path.join(base_dir, "vlm_svg_candidates")
    os.makedirs(candidate_dir, exist_ok=True)
    page_name = os.path.splitext(os.path.basename(file_path))[0]
    candidate_path = os.path.join(candidate_dir, f"{page_name}_vlm_{iteration}.svg")
    with open(candidate_path, "w", encoding="utf-8") as fh:
        fh.write(svg_content)
    return candidate_path


def _read_vlm_candidate_html(candidate: Dict[str, Any]) -> Optional[str]:
    html_path = candidate.get("html_path")
    if not html_path or not os.path.exists(html_path):
        return None
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as error:
        logger.warning(f"read vlm candidate svg failed: {error}")
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
    state: SVGWorkerState,
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


# Re-export selected helpers used by the orchestrator (e.g. quality_check_node).
def strip_unresolvable_images(svg_path: str) -> int:
    """Remove <image> elements whose href cannot be resolved on disk.

    Returns the number of stripped elements. Mirrors the path-resolution
    candidates used by quality_checker._check_images.
    """
    path = Path(svg_path)
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return 0
    root = tree.getroot()

    def _is_resolvable(href: str) -> bool:
        if not href or href.startswith("data:image/"):
            return True
        if urlparse(href).scheme in {"http", "https"}:
            return True
        candidates = [
            Path(href),
            path.parent / href,
            path.parent.parent / href,
        ]
        return any(c.exists() and c.is_file() for c in candidates)

    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag != "image":
                continue
            href = child.get("href") or child.get(f"{{{_XLINK_NS}}}href")
            if _is_resolvable(href or ""):
                continue
            parent.remove(child)
            removed += 1

    if removed:
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        ET.register_namespace("xlink", _XLINK_NS)
        tree.write(path, encoding="unicode", xml_declaration=False)
    return removed
