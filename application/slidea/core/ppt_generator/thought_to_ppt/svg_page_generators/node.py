import os
import copy
import json
import asyncio
import shutil
import tempfile
from collections import Counter
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
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.node import (
    repair_redundant_non_image_clip_paths,
    strip_unresolvable_images,
)
from core.ppt_generator.utils.style_pack import (
    apply_style_reference_shell,
    apply_style_reference_shell_file,
    bind_style_reference_paths,
    style_guidance_for_page,
    extract_style_dynamic_content,
    prepare_style_runtime_references,
)


SVG_QUALITY_REPAIR_PROMPT = "svg_quality_repair_prompt.txt"
SVG_STYLE_PACK_QUALITY_REPAIR_PROMPT = "svg_quality_repair_style_pack_prompt.txt"


async def prepare_generation_context_node(state: PPTState, writer: StreamWriter, config: RunnableConfig | None = None):
    """
    SVG-route preparation: build save_dir, pick an SVG template, download
    outline images, set language and ppt_prompt, load template content into state.
    """
    cache_dir = run_dir_from_config(config, str(app_base_dir))
    if not state.get("save_dir", None):
        if not cache_dir:
            # config must carry the run_id so we can place SVGs inside the active
            # run's cache directory. Reaching this branch means the caller forgot
            # to thread config (typical of staged execution before the
            # generate_pages_node config fix). Raise loudly so the bug surfaces
            # instead of silently creating an orphan directory at the output root.
            raise RuntimeError(
                "prepare_generation_context_node could not resolve the run cache "
                "directory: config is missing or does not carry run_id. Pass "
                "config from the caller (generate_pages_node dispatches it "
                "automatically in normal runs)."
            )
        save_dir = os.path.join(cache_dir, "slides")
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

    outline = state["outline"]
    style_pack_dir = state.get("style_pack_dir", "")
    style_pack_active = False

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

    outline = await download_outline_images(outline, images_dir)

    # Reference ids were selected and saved as part of outline generation.
    # This node only resolves ids to the immutable run snapshot; it does not
    # score or search reference pages before fan-out.
    if style_pack_dir:
        try:
            bind_style_reference_paths(outline, style_pack_dir)
            prepare_style_runtime_references(outline, style_pack_dir, save_dir)
            styled_page_count = sum(
                bool(getattr(page, "style_reference_svg", "")) for page in outline
            )
            fallback_page_count = len(outline) - styled_page_count
            style_pack_active = styled_page_count > 0
            logger.info(
                "using style references stored in outline.json; "
                "inherited shell and authorized reusable assets prepared under "
                f"slides/images/style-pack ({styled_page_count} styled page(s), "
                f"{fallback_page_count} built-in fallback page(s))"
            )
        except Exception as error:
            logger.warning(
                "outline style references are unavailable; "
                f"falling back to the built-in template workflow: {error}"
            )
            shutil.rmtree(Path(save_dir) / "style_references", ignore_errors=True)
            shutil.rmtree(Path(save_dir) / "images" / "style-pack", ignore_errors=True)
            style_pack_dir = ""
            for page in outline:
                page.style_reference_id = ""
                page.style_reference_svg = ""
                page.style_reference_page_type = ""
                page.style_reference_guidance = ""
                page.style_reference_rules = {}

    writer(
        {
            "step": "准备生成所需上下文",
            "text": (
                f"已创建输出目录，选择模板: {cached_name}，检测语言: {response}。"
                + (" 已读取 outline 中固定的逐页 style pack 参考。" if style_pack_active else "")
            ),
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
        "style_pack_dir": style_pack_dir,
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
    """Run the legacy gate, or the style-pack dynamic-only deterministic gate."""
    files = state.get("page_files") or []
    style_pages = _active_style_pack_pages(state, files)
    if style_pages is not None:
        return await _quality_check_style_dynamic_content(state, files, style_pages, writer)

    # Keep the pre-existing non-style-pack route unchanged.  In particular it
    # still uses the legacy LLM repair behavior for compatibility.
    _restore_style_shells(state, files)
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
        _restore_style_shells(state, files)
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


def _active_style_pack_pages(state: PPTState, files: list[str]) -> list[Any] | None:
    """Return ordered pages when at least one uses style-pack composition."""
    pages = sorted(state.get("outline") or [], key=lambda page: int(page.index))
    references = [str(getattr(page, "style_reference_svg", "") or "") for page in pages]
    if not any(references):
        return None
    if len(pages) != len(files):
        raise ValueError(
            f"cannot check style dynamic content: {len(pages)} outline pages != {len(files)} SVG files"
        )
    return pages


def _check_style_dynamic_candidate(
    path: Path,
    dynamic: str,
) -> tuple[dict[str, Any], str]:
    """Run deterministic local repairs and the unchanged checker on a candidate."""
    temp_path: Path | None = None
    try:
        dynamic = repair_svg_content(dynamic)
        validate_svg_content(dynamic)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".svg",
            prefix=".slidea-dynamic-",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(dynamic)
            temp_path = Path(handle.name)

        stripped = strip_unresolvable_images(str(temp_path))
        repaired_clips = repair_redundant_non_image_clip_paths(str(temp_path))
        dynamic = temp_path.read_text(encoding="utf-8")
        result = check_svg_files([str(temp_path)])[0]
        result["file"] = path.name
        result["path"] = str(path)
        result["scope"] = "dynamic-main-content"
        if stripped:
            result.setdefault("warnings", []).append(
                f"Removed {stripped} unresolvable dynamic image(s) deterministically"
            )
        if repaired_clips:
            result.setdefault("warnings", []).append(
                f"Removed {repaired_clips} redundant non-image clip-path attribute(s) deterministically"
            )
        return result, dynamic
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _validate_style_dynamic_repair_contract(original: str, repaired: str) -> None:
    """Reject a quality-repair candidate that redesigns dynamic page content."""
    import xml.etree.ElementTree as ET

    original_root = ET.fromstring(original)
    repaired_root = ET.fromstring(repaired)

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def text_values(root: ET.Element) -> list[str]:
        return [
            (item.text or "").strip()
            for item in root.iter()
            if local_name(item.tag) == "text" and (item.text or "").strip()
        ]

    def group_ids(root: ET.Element) -> Counter[str]:
        return Counter(
            item.get("id")
            for item in root.iter()
            if local_name(item.tag) == "g" and item.get("id")
        )

    def image_hrefs(root: ET.Element) -> Counter[str]:
        xlink_href = "{http://www.w3.org/1999/xlink}href"
        return Counter(
            item.get("href") or item.get(xlink_href) or ""
            for item in root.iter()
            if local_name(item.tag) == "image"
        )

    geometry_attrs = {
        "rect": ("x", "y", "width", "height", "rx", "ry", "transform"),
        "circle": ("cx", "cy", "r", "transform"),
        "ellipse": ("cx", "cy", "rx", "ry", "transform"),
        "line": ("x1", "y1", "x2", "y2", "transform"),
        "path": ("d", "transform"),
        "polygon": ("points", "transform"),
        "polyline": ("points", "transform"),
        "text": ("x", "y", "font-size", "text-anchor", "transform"),
    }

    def geometry_signatures(root: ET.Element) -> Counter[tuple[str, ...]]:
        signatures: Counter[tuple[str, ...]] = Counter()
        for item in root.iter():
            tag = local_name(item.tag)
            attrs = geometry_attrs.get(tag)
            if attrs is None:
                continue
            signatures[(tag, *(item.get(name) or "" for name in attrs))] += 1
        return signatures

    def image_signatures(root: ET.Element) -> Counter[tuple[str, ...]]:
        xlink_href = "{http://www.w3.org/1999/xlink}href"
        return Counter(
            (
                item.get("href") or item.get(xlink_href) or "",
                *(item.get(name) or "" for name in ("x", "y", "width", "height", "transform")),
            )
            for item in root.iter()
            if local_name(item.tag) == "image"
        )

    original_canvas = tuple(original_root.get(name) for name in ("width", "height", "viewBox"))
    repaired_canvas = tuple(repaired_root.get(name) for name in ("width", "height", "viewBox"))
    if repaired_canvas != original_canvas:
        raise ValueError("canvas attributes changed")
    if text_values(repaired_root) != text_values(original_root):
        raise ValueError("visible text content or order changed")
    if group_ids(repaired_root) != group_ids(original_root):
        raise ValueError("dynamic group ids changed")
    if geometry_signatures(repaired_root) != geometry_signatures(original_root):
        raise ValueError("dynamic geometry or text positioning changed")

    original_images = image_hrefs(original_root)
    repaired_images = image_hrefs(repaired_root)
    if any(count > original_images[href] for href, count in repaired_images.items()):
        raise ValueError("new or duplicated image href introduced")
    original_image_geometry = image_signatures(original_root)
    repaired_image_geometry = image_signatures(repaired_root)
    if any(
        count > original_image_geometry[signature]
        for signature, count in repaired_image_geometry.items()
    ):
        raise ValueError("image geometry changed")
    if any(
        item.get("data-slidea-style-shell") == "true"
        or item.get("data-slidea-style-shell-def") == "true"
        for item in repaired_root.iter()
    ):
        raise ValueError("fixed style shell nodes appeared in dynamic candidate")


async def _check_one_style_dynamic_svg(
    svg_path: str,
    page: Any,
) -> tuple[dict[str, Any], str | None]:
    """Repair one style page dynamically, accepting only a checked candidate."""
    path = Path(svg_path)
    try:
        original = path.read_text(encoding="utf-8")
        dynamic = extract_style_dynamic_content(original)
        initial_result, deterministic_dynamic = _check_style_dynamic_candidate(path, dynamic)
        if initial_result.get("passed"):
            initial_result["repair_mode"] = (
                "deterministic" if initial_result.get("warnings") else "none"
            )
            recomposed = apply_style_reference_shell(deterministic_dynamic, page)
            validate_svg_content(recomposed)
            return initial_result, recomposed

        repair_prompt_template = (
            Path(app_base_dir)
            / "core" / "ppt_generator" / "assets" / "prompts"
            / SVG_STYLE_PACK_QUALITY_REPAIR_PROMPT
        ).read_text(encoding="utf-8")
        prompt = repair_prompt_template.format(
            issues=format_quality_issues([initial_result]),
            style_guidance=style_guidance_for_page(page),
            content=deterministic_dynamic,
        )
        response = await llm_invoke(ModelRoute.PREMIUM, [HumanMessage(content=prompt)])
        repaired = repair_svg_content(extract_svg_content(response))
        validate_svg_content(repaired)
        _validate_style_dynamic_repair_contract(deterministic_dynamic, repaired)

        repaired_result, repaired_dynamic = _check_style_dynamic_candidate(path, repaired)
        repaired_result["repair_attempted"] = True
        repaired_result["initial_errors"] = list(initial_result.get("errors") or [])
        if not repaired_result.get("passed"):
            return repaired_result, None

        repaired_result["repair_mode"] = "dynamic-llm"
        repaired_result.setdefault("warnings", []).append(
            "Accepted a checked dynamic-only LLM quality repair"
        )
        recomposed = apply_style_reference_shell(repaired_dynamic, page)
        validate_svg_content(recomposed)
        logger.info(f"SVG dynamic-only quality repair succeeded: {path}")
        return repaired_result, recomposed
    except Exception as error:
        result = locals().get("initial_result") or {
            "file": path.name,
            "path": str(path),
            "exists": path.exists(),
            "errors": [],
            "warnings": [],
            "passed": False,
            "scope": "dynamic-main-content",
        }
        result["passed"] = False
        result["repair_attempted"] = "initial_result" in locals()
        result.setdefault("errors", []).append(
            f"Dynamic-only quality repair failed or was rejected: {error}"
        )
        logger.warning(f"SVG dynamic-only quality repair failed for {path}: {error}")
        return result, None


async def _quality_check_style_dynamic_content(
    state: PPTState,
    files: list[str],
    pages: list[Any],
    writer: StreamWriter,
) -> dict[str, Any]:
    """Check styled pages dynamically and per-page built-in fallbacks normally."""
    _restore_style_shells(state, files)
    results: list[dict[str, Any]] = []
    updates: list[tuple[Path, str]] = []
    for page, svg_path in zip(pages, files):
        if getattr(page, "style_reference_svg", ""):
            result, recomposed = await _check_one_style_dynamic_svg(svg_path, page)
        else:
            result = await _check_one_builtin_fallback_svg(svg_path)
            recomposed = None
        results.append(result)
        if recomposed is not None:
            updates.append((Path(svg_path), recomposed))

    failed = [item for item in results if not item.get("passed")]
    if failed:
        issue_text = format_quality_issues(results)
        for item in failed:
            report_path = Path(str(item["path"])).with_suffix(".quality-report.json")
            try:
                report_path.write_text(
                    json.dumps(item, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                item["quality_report_path"] = str(report_path)
            except OSError as error:
                logger.warning(f"failed to write SVG quality report {report_path}: {error}")
        logger.error(f"style dynamic SVG quality check failed:\n{issue_text}")
        writer(
            {
                "step": "SVG 动态内容质量检查失败",
                "text": issue_text,
            }
        )
        raise ValueError("style dynamic SVG quality check failed:\n" + issue_text)

    # Commit deterministic dynamic repairs only after every page passes, so a
    # failed deck keeps all original composed SVGs for diagnosis.
    for path, content in updates:
        path.write_text(content, encoding="utf-8")
        try:
            path.with_suffix(".quality-report.json").unlink(missing_ok=True)
        except OSError as error:
            logger.warning(f"failed to remove stale SVG quality report for {path}: {error}")

    warning_count = sum(len(item.get("warnings") or []) for item in results)
    writer(
        {
            "step": "SVG 动态内容质量检查",
            "text": (
                f"已完成 {len(results)} 个文件的质量检查，"
                f"发现 {warning_count} 个警告；style pack 页面仅检查动态内容，"
                "无同类型参考的页面沿用内置模板检查流程。"
            ),
        }
    )
    return {"svg_quality_report": results}


async def _check_one_builtin_fallback_svg(svg_path: str) -> dict[str, Any]:
    """Run the existing full-SVG repair behavior for one fallback page."""
    result = check_svg_files([svg_path])[0]
    if not result.get("passed"):
        await _repair_failed_svg_files([result])
        result = check_svg_files([svg_path])[0]
    result["scope"] = "full-svg-built-in-fallback"
    return result


def _restore_style_shells(state: PPTState, files: list[str]) -> None:
    """Re-apply deterministic shells after VLM changes and before final checks."""
    pages = sorted(state.get("outline") or [], key=lambda page: int(page.index))
    if not any(getattr(page, "style_reference_svg", "") for page in pages):
        return
    if len(pages) != len(files):
        raise ValueError(
            f"cannot restore style shells: {len(pages)} outline pages != {len(files)} SVG files"
        )
    for page, svg_path in zip(pages, files):
        if not getattr(page, "style_reference_svg", ""):
            continue
        apply_style_reference_shell_file(svg_path, page)


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
            "svg_dir": save_dir,
            "template_name": state.get("template_name", ""),
            "style_pack_dir": state.get("style_pack_dir", ""),
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        }
        save_json(f"{cache_dir}/ppt.json", record)

    writer(
        {
            "step": "导出 PPT 完成",
            "files": [pdf_path, pptx_path] if pdf_path else [pptx_path],
            "source_dir": save_dir,
            "text": "生成PPT结束",
        }
    )

    return {"final_pdf_path": pdf_path, "final_pptx_path": pptx_path}
