import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from json_repair import repair_json
from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.config import settings, app_base_dir
from core.utils.llm import can_vlm_invoke, llm_invoke, vlm_raw_invoke
from core.ppt_generator.utils.common import build_image_url
from core.ppt_generator.utils.screenshot import screenshot_svg, screenshot_svg_bytes
from core.ppt_generator.utils.svg import (
    extract_svg_content,
    repair_svg_content,
    safe_svg_filename,
    validate_svg_content,
)
from core.ppt_generator.utils.svg_pipeline.finalize_svg import embed_local_images_in_content
from core.ppt_generator.utils.style_pack import (
    apply_style_reference_shell,
    extract_style_dynamic_content,
    style_guidance_for_page,
)
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.state import SVGWorkerState


PROMPT_DIR = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "prompts"
SEVERITY_RANK = {"none": 0, "minor": 1, "critical": 2, None: 3}
VLM_VISUAL_REVIEW_MAX_ITERATIONS = 1
VLM_SCREENSHOT_DIR_NAME = "vlm_screenshots"
GENERATE_PROMPT_LOG_DIR_NAME = "prompts"
SVG_JUDGE_PROMPT = "svg_vlm_judge_prompt.txt"
SVG_FIX_PROMPT = "svg_vlm_fix_prompt.txt"
SVG_STYLE_PACK_FIX_PROMPT = "svg_vlm_fix_style_pack_prompt.txt"
SVG_QUALITY_REPAIR_PROMPT = "svg_quality_repair_prompt.txt"
SVG_GENERATION_REPAIR_MAX_ATTEMPTS = 1
STYLE_PACK_COMPOSITION_RETRY_MAX_ATTEMPTS = 1
STYLE_GENERATION_CANDIDATE_DIR_NAME = "style_generation_candidates"
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


def _save_generate_prompt(state: SVGWorkerState) -> None:
    """Persist the per-page generation prompt to ``<save_dir>/prompts/`` for inspection.

    Best-effort: filesystem failures are logged but don't interrupt generation.
    """
    save_dir = state.get("save_dir")
    prompt = state.get("generate_ppt_prompt")
    if not save_dir or not prompt:
        return
    index = state.get("index", 0)
    page = state.get("page")
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


def _save_failed_style_candidate(state: SVGWorkerState, content: str, attempt: int) -> None:
    """Persist a rejected pre-composition SVG for deterministic diagnosis."""
    save_dir = state.get("save_dir")
    page = state.get("page")
    if not save_dir or page is None or not getattr(page, "style_reference_svg", ""):
        return
    candidate_dir = Path(save_dir) / STYLE_GENERATION_CANDIDATE_DIR_NAME
    filename = Path(_svg_page_filename(page)).stem
    try:
        candidate_dir.mkdir(parents=True, exist_ok=True)
        (candidate_dir / f"{filename}_rejected_{attempt + 1}.svg").write_text(
            content,
            encoding="utf-8",
        )
    except OSError as error:
        logger.warning(f"save rejected style candidate failed: {error}")


def _style_pack_retry_prompt(original_prompt: str, issue: str, page: Any) -> str:
    page_type = str(getattr(page, "style_reference_page_type", "") or "content")
    role_requirement = {
        "toc": "目录页必须生成至少一个位于参考正文区域内的完整动态目录条目。",
        "content": "内容页必须生成可见的动态正文文字或正文图片，不能只输出背景。",
        "cover": "封面页不得重画固定主标题，只保留参考页允许的动态副标题等文字。",
        "thanks": "致谢页不得重画固定致谢标题，只保留参考页允许的动态补充文字。",
    }.get(page_type, "必须生成该页面角色所需的可见动态内容。")
    return f"""{original_prompt}

# Style-pack 合成门禁的自动重试反馈
上一次 SVG 在固定外壳合成阶段被拒绝：{issue}
请重新输出一份完整、合法的 SVG，并严格满足以下要求：
- {role_requirement}
- 不要生成覆盖 1280×720 画布的全屏不透明背景矩形；背景、母版、标题、页眉页脚由代码注入。
- 动态正文不能为空，也不能放在会被固定外壳遮挡的位置。
- 只输出 SVG，不要解释。"""


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
        response = await llm_invoke([HumanMessage(content=prompt)])
        return repair_svg_content(extract_svg_content(response))
    except Exception as error:
        logger.warning(f"svg LLM repair call failed: {error}")
        return None


def _svg_page_filename(page) -> str:
    """{nn}_{safe_title}.svg relative path.

    SVGs live directly under save_dir (not under save_dir/svg/) so that the
    relative image paths they contain (images/xxx.png) resolve correctly
    against save_dir/images/.
    """
    return safe_svg_filename(page.index, page.title)


async def _svg_screenshot_with_embedded_images(source_path: str, output_path: str) -> str:
    """Rasterize an SVG to PNG via CairoSVG for VLM review.

    SVGs in svg/ reference images via relative paths like ``images/xxx.jpg``.
    We embed those as data URIs in memory and feed the resulting bytes straight
    to CairoSVG so referenced images render correctly without a tempfile.
    """
    src = Path(source_path)
    try:
        content = src.read_text(encoding="utf-8")
    except OSError as error:
        logger.warning(f"screenshot read svg failed for {source_path}: {error}")
        return await screenshot_svg(source_path, output_path)

    embedded = embed_local_images_in_content(content, src.parent)
    return await screenshot_svg_bytes(embedded.encode("utf-8"), output_path)


async def generate_ppt_page_node(state: SVGWorkerState):
    """generate svg ppt page"""
    _save_generate_prompt(state)
    page = state.get("page")
    style_pack_active = bool(getattr(page, "style_reference_svg", ""))
    max_attempts = 1 + (
        STYLE_PACK_COMPOSITION_RETRY_MAX_ATTEMPTS if style_pack_active else 0
    )
    prompt = state["generate_ppt_prompt"]
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        response = await llm_invoke(
            [HumanMessage(content=prompt)],
        )
        raw_svg = await _svg_process_llm_response(
            response,
            page_index=state.get("index", 0),
        )
        try:
            svg_content = apply_style_reference_shell(raw_svg, page)
            validate_svg_content(svg_content)
            return {"content": svg_content}
        except ValueError as error:
            last_error = error
            if not style_pack_active or attempt + 1 >= max_attempts:
                raise
            _save_failed_style_candidate(state, raw_svg, attempt)
            logger.warning(
                f"style-pack page {state.get('index', 0)} composition rejected "
                f"attempt {attempt + 1}, retrying generation: {error}"
            )
            prompt = _style_pack_retry_prompt(
                state["generate_ppt_prompt"],
                str(error),
                page,
            )

    raise ValueError(f"style-pack SVG generation failed: {last_error}")


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

    page = state.get("page")
    style_pack_active = bool(getattr(page, "style_reference_svg", ""))
    if style_pack_active:
        # The judge still sees the fully composed screenshot, but the modifier
        # receives only model-authored nodes.  This makes it impossible for a
        # VLM rewrite to mutate or spend context on the deterministic shell.
        prompt_svg_content = extract_style_dynamic_content(svg_content)
        fix_prompt_template = _load_prompt(SVG_STYLE_PACK_FIX_PROMPT)
    else:
        # Preserve the original no-style-pack route and prompt byte-for-byte.
        prompt_svg_content = svg_content
        fix_prompt_template = _load_prompt(SVG_FIX_PROMPT)
    issues_block = _format_issues(issues)
    history_block = _format_judge_history(state.get("vlm_judge_history") or [])
    fix_prompt = fix_prompt_template.format(
        issues_block=issues_block,
        history_block=history_block,
        ppt_prompt=state.get("ppt_prompt", ""),
        style_guidance=style_guidance_for_page(page) if style_pack_active else "",
        content=prompt_svg_content,
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
    try:
        new_svg = await _svg_process_llm_response(raw_content, page_index=index)
    except Exception as error:
        logger.warning(f"page {index} vlm_modify svg processing failed: {error}, keep current")
        return {"vlm_iteration": vlm_iteration + 1}
    if not new_svg:
        logger.warning(f"page {index} vlm_modify returned empty content, keep current")
        return {"vlm_iteration": vlm_iteration + 1}
    try:
        new_svg = apply_style_reference_shell(new_svg, state.get("page"))
        validate_svg_content(new_svg)
    except ValueError as error:
        logger.warning(
            f"page {index} vlm_modify result rejected by style composition gate: {error}; "
            "keep current candidate"
        )
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
    final_content = best_svg_content or state.get("content")

    if final_content:
        try:
            final_content = apply_style_reference_shell(final_content, state.get("page"))
            validate_svg_content(final_content)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(final_content)
            if best_file_path and best_svg_content and best_svg_content != state.get("content"):
                logger.info(f'page {state["index"]} restored best version to {best_file_path}')
        except Exception as error:
            logger.warning(f"write final svg content failed: {error}")

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
    if settings.ENABLE_VLM_VISUAL_REVIEW and can_vlm_invoke():
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


def repair_redundant_non_image_clip_paths(svg_path: str) -> int:
    """Remove provably redundant ``clip-path`` attributes from dynamic rects.

    Slidea's native DrawingML route supports clipping on images, not arbitrary
    SVG shapes.  Models commonly reuse an image's rounded-rectangle clip on a
    caption overlay that is already fully contained by the same rectangle.  In
    that specific case the clip is redundant and can be removed without moving
    content.  Rounded corners are copied to overlays touching the clip edge.

    Complex, transformed or non-rectangular clips are deliberately left intact
    so the quality gate can route them to the dynamic-only LLM fallback.
    """
    path = Path(svg_path)
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return 0
    root = tree.getroot()

    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def _float(element: ET.Element, name: str, default: float = 0.0) -> float:
        try:
            return float(element.get(name, default))
        except (TypeError, ValueError):
            return default

    clip_rects: dict[str, ET.Element] = {}
    for clip in root.iter():
        if _local_name(clip.tag) != "clippath" or clip.get("transform"):
            continue
        children = [child for child in clip if isinstance(child.tag, str)]
        if len(children) != 1 or _local_name(children[0].tag) != "rect":
            continue
        rect = children[0]
        if rect.get("transform"):
            continue
        clip_id = clip.get("id")
        if clip_id:
            clip_rects[clip_id] = rect

    removed = 0
    reference_pattern = re.compile(r"^url\(#([^)]+)\)$")
    tolerance = 0.05
    for element in root.iter():
        if _local_name(element.tag) != "rect" or element.get("transform"):
            continue
        clip_attr = next(
            (name for name in element.attrib if _local_name(name) == "clip-path"),
            None,
        )
        if clip_attr is None:
            continue
        match = reference_pattern.match((element.get(clip_attr) or "").strip())
        clip_rect = clip_rects.get(match.group(1)) if match else None
        if clip_rect is None:
            continue

        x, y = _float(element, "x"), _float(element, "y")
        width, height = _float(element, "width"), _float(element, "height")
        clip_x, clip_y = _float(clip_rect, "x"), _float(clip_rect, "y")
        clip_width, clip_height = _float(clip_rect, "width"), _float(clip_rect, "height")
        dimensions = (width, height, clip_width, clip_height)
        if any(value < 0 for value in dimensions):
            continue
        contained = (
            x >= clip_x - tolerance
            and y >= clip_y - tolerance
            and x + width <= clip_x + clip_width + tolerance
            and y + height <= clip_y + clip_height + tolerance
        )
        if not contained:
            continue

        del element.attrib[clip_attr]
        removed += 1

        # A bottom/top photo caption often reaches the rounded clip boundary.
        # Carrying the same radius to the overlay preserves the visible corner
        # treatment after the now-redundant clip is removed.
        shares_width = (
            abs(x - clip_x) <= tolerance
            and abs(width - clip_width) <= tolerance
        )
        touches_vertical_edge = (
            abs(y - clip_y) <= tolerance
            or abs((y + height) - (clip_y + clip_height)) <= tolerance
        )
        if shares_width and touches_vertical_edge:
            clip_rx = clip_rect.get("rx")
            clip_ry = clip_rect.get("ry") or clip_rx
            if clip_rx and not element.get("rx"):
                element.set("rx", clip_rx)
            if clip_ry and not element.get("ry"):
                element.set("ry", clip_ry)

    if removed:
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        ET.register_namespace("xlink", _XLINK_NS)
        tree.write(path, encoding="unicode", xml_declaration=False)
    return removed
