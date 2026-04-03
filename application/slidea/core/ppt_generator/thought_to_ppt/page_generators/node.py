import os
from datetime import datetime
import asyncio
import json
import base64
import copy
from typing import List, Optional, Any

from langchain.messages import HumanMessage
import aiofiles.os
from json_repair import repair_json
from langgraph.types import StreamWriter

from core.utils.logger import logger
from core.utils.config import app_base_dir, output_files_dir, settings
from core.utils.cache import get_run_id, run_dir_from_config, save_json
from core.ppt_generator.utils.common import htmls_to_pptx, sanitize_filename, download_image, build_image_url
from core.utils.llm import default_llm, default_vlm, llm_invoke
from core.ppt_generator.thought_to_ppt.state import PPTState, PageType, PPTPage
from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.graph import generate_cover_thanks_pages_app
from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.graph import generate_sep_pages_app
from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.graph import generate_content_pages_app
from core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.graph import generate_toc_page_app
from core.ppt_generator.thought_to_ppt.page_generators.state import TemplateResult


def load_template_styles() -> list[dict[str, str]]:
    """Load template metadata from style.json."""
    style_path = app_base_dir / "core" / "ppt_generator" / "assets" / "templates" / "style.json"
    try:
        with open(style_path, "r", encoding="utf-8") as f:
            content = json.load(f)
    except Exception as e:
        logger.error(f"读取模板样式文件失败 {style_path}: {e}")
        raise Exception("获取PPT模板样式失败") from e

    templates = content.get("templates", [])
    if not isinstance(templates, list) or not templates:
        logger.error(f"模板样式文件格式不正确或为空: {style_path}")
        raise Exception("获取PPT模板样式失败")

    valid_templates = []
    for item in templates:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        if name:
            valid_templates.append({"name": name, "description": description})

    if not valid_templates:
        logger.error(f"模板样式文件中没有有效模板: {style_path}")
        raise Exception("获取PPT模板样式失败")

    return valid_templates


async def prepare_generation_context_node(state: PPTState, writer: StreamWriter):
    """
    analyze ppt requirements(check language)
    """
    # 确保目录存在
    time_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not state.get("save_dir", None):
        save_dir = os.path.join(output_files_dir, f'{time_prefix}_{sanitize_filename(state["topic"])}')
    else:
        save_dir = state["save_dir"]
    await aiofiles.os.makedirs(save_dir, exist_ok=True)
    images_dir = os.path.join(save_dir, "images")
    await aiofiles.os.makedirs(images_dir, exist_ok=True)

    # 获取模板
    if not state.get('html_template_name', None):
        html_template_name = await select_ppt_template(state['query'], str(state['outline']))
    else:
        html_template_name = state['html_template_name']
    logger.info(f"任务{state['query']}选取的模板名称为{html_template_name}")

    template_dir = app_base_dir / "core" / "ppt_generator" / "assets" / "templates" / f"{html_template_name}.html"
    try:
        with open(template_dir, "r", encoding="utf-8") as f:
            html_template = f.read()
            if not html_template.strip():
                logger.error(f"HTML模板文件为空: {template_dir}")
                raise Exception("获取PPT模板失败")
    except Exception as e:
        logger.error(f"读取HTML模板文件失败 {template_dir}: {e}")
        raise Exception("获取PPT模板失败") from e

    # 获取PPT通用提示词
    prompt_dir = app_base_dir / "core" / "ppt_generator" / "assets" / "prompts" / "ppt_generator_prompt.txt"
    try:
        with open(prompt_dir, "r", encoding="utf-8") as f:
            ppt_prompt = f.read()
    except Exception as e:
        logger.error(f"读取PPT Prompt文件失败 {template_dir}: {e}")
        raise Exception("获取PPT Prompt失败") from e

    # 确定语言
    response = await llm_invoke(default_llm,
                                [
                                    HumanMessage(
                                        content=f"根据'{state['query']}'确定使用的语言,只回答'中文'、'英文'等结果。"),
                                ]
                                )

    # 下载outline中的图片
    outline = await download_outline_images(state["outline"], images_dir)

    writer(
        {
            "step": "准备生成所需上下文",
            "text": f"已创建输出目录，选择模板: {html_template_name}，检测语言: {response}。",
        }
    )

    return {
        "outline": outline,
        "save_dir": save_dir,
        "language": response,
        "html_template_name": html_template_name,
        "html_template": html_template,
        "ppt_prompt": ppt_prompt,
    }


async def select_ppt_template(query, outline):
    """根据任务请求选择模板"""
    template_desc = load_template_styles()

    prompt = f"""
请从模板列表中选取适合当前PPT主题和大纲的模板.
# 用户的PPT请求
{query}

# 要生成的PPT章节大纲
{outline}

# 当前已有的所有PPT模板列表信息如下
{template_desc}

# 返回格式要求
请从模板列表中选取适合当前PPT主题和大纲的模板，返回一个json：
{{
    "reason": "选择理由",
    "name": "模板name"
}}
"""
    response = await llm_invoke(
        default_llm,
        [
            HumanMessage(content=prompt),
        ],
        pydantic_schema=TemplateResult
    )
    template = response.name
    valid_template_names = {item["name"] for item in template_desc}
    if template not in valid_template_names:
        template = template_desc[0]["name"]
    return template


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
    """
    根据 reference_images 的分布特征自动判定模式：
    """
    content_pages = [p for p in pages if p.type.name == 'CONTENT']

    if not content_pages:
        return "global"

    # 获取第一个内容页的图片列表作为基准
    first_page_imgs = content_pages[0].reference_images

    # 检查后续所有内容页是否与第一页完全一致
    for p in content_pages[1:]:
        if p.reference_images != first_page_imgs:
            logger.info("Detected section mode in distribute images.")
            return "section"
    logger.info("Detected global mode in distribute images.")
    return "global"


def encode_image(image_path: str) -> str:
    """读取图片并转换为VLM兼容的编码（本地LM Studio用data URL，OpenAI用纯base64）"""
    try:
        return build_image_url(image_path)
    except Exception as e:
        logger.debug(f"Error reading image {image_path}: {e}")
        return ""


async def distribute_images_via_vlm(outline: List[Any]) -> List[Any]:
    """
    自动判断模式并分发图片到最合适的页面
    """
    processed_pages = copy.deepcopy(outline)

    if not settings.has_default_vlm_config():
        logger.warning("Default VLM settings are missing. Skip VLM-based image distribution.")
        return processed_pages

    # 1. 自动检测模式
    mode = detect_distribution_mode(processed_pages)

    # 2. 根据模式分发
    if mode == "global":
        await _process_global_mode(processed_pages)
    elif mode == "section":
        await _process_section_mode(processed_pages)

    return processed_pages


async def _process_global_mode(pages: List[Any]):
    """
    整份 PPT 的 Content 页共享同一组 Reference Images
    """
    # 1. 筛选所有 Content 页面的索引
    content_indices = [
        i for i, p in enumerate(pages)
        if p.type.name == 'CONTENT'
    ]

    if not content_indices:
        return

    # 2. 获取去重后的图片列表 (取第一个有图的页面即可)
    unique_images = []
    for idx in content_indices:
        # 直接访问属性
        if pages[idx].reference_images:
            unique_images = pages[idx].reference_images
            break

    if not unique_images:
        return

    # 3. 清空所有 Content 页原本冗余的 reference_images
    for idx in content_indices:
        pages[idx].reference_images = []

    # 4. 构建上下文文本
    context_text = _build_page_context(pages, content_indices)

    # 5. 并发请求
    tasks = [
        _ask_vlm_for_single_image(img_path, context_text, valid_indices=content_indices)
        for img_path in unique_images
    ]

    # 6. 等待结果
    results = await asyncio.gather(*tasks)

    # 7. 分配结果
    for img_path, target_index in results:
        if target_index is not None:
            # 直接 append 到 Pydantic 对象的 list 属性中
            pages[target_index].reference_images.append(img_path)


async def _process_section_mode(pages: List[Any]):
    """
    每个 Separator 划分的章节内，Content 页共享一组图片
    """
    current_indices = []
    current_images = []

    # 内部处理函数
    async def process_current_section(indices: List[int], images: List[str]):
        if not indices or not images:
            return

        # 清空旧图片
        for idx in indices:
            pages[idx].reference_images = []

        context_str = _build_page_context(pages, indices)

        # 并发请求
        tasks = [
            _ask_vlm_for_single_image(img_path, context_str, valid_indices=indices)
            for img_path in images
        ]
        results = await asyncio.gather(*tasks)

        # 应用结果
        for img_path, target_index in results:
            if target_index is not None:
                pages[target_index].reference_images.append(img_path)

    # 遍历页面
    for i, page in enumerate(pages):
        # 获取枚举名称
        p_type_name = page.type.name

        is_sep = p_type_name == 'SEPARATOR'
        is_last = i == len(pages) - 1

        if p_type_name == 'CONTENT':
            current_indices.append(i)
            # 捕获图片池
            if not current_images and page.reference_images:
                current_images = page.reference_images

        # 触发结算逻辑
        if is_sep or is_last:
            if current_indices and current_images:
                await process_current_section(current_indices, current_images)

            # 重置
            current_indices = []
            current_images = []


async def _ask_vlm_for_single_image(
    image_path: str,
    context_text: str,
    valid_indices: List[int]
) -> tuple[str, Optional[int]]:
    """
    原子操作：询问 VLM 某一张图片应该放在哪个 Index
    """
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
        response = await default_vlm.ainvoke([HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": b64_img},
                }
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
    """
    构建精简的页面上下文
    Input: PPTPage 对象列表
    """
    lines = []
    for idx in indices:
        p = pages[idx]
        # 使用点号访问 Pydantic 属性
        lines.append(f"[Page Index: {p.index}] Title: {p.title}\nAbstract: {p.abstract}\n")
    return "\n".join(lines)

async def generate_toc_page_node(state: PPTState):
    """generate toc page"""
    output = await generate_toc_page_app.ainvoke(state)
    return {"generated_pages": output["generated_pages"]}


async def generate_content_pages_node(state: PPTState, writer: StreamWriter):
    """generate content pages"""
    task_payload = {
        "query": state["query"],
        "outline": state["outline"],
        "save_dir": state["save_dir"],
        "ppt_prompt": state["ppt_prompt"],
        "language": state["language"],
        "html_template": state["html_template"]
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
    """generate sep pages"""
    task_payload = {
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "language": state["language"],
        "html_template": state["html_template"],
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
    """generate cover and thanks pages"""
    task_payload = {
        "query": state["query"],
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "language": state["language"],
        "html_template": state["html_template"],
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
    """synthesize all htm"""
    htmls = [
        item["file_path"]
        for item in sorted(state["generated_pages"], key=lambda x: x["index"])
    ]
    writer(
        {
            "step": "合并 PPT 页面",
            "text": f"已根据索引合并所有 PPT 页面，共 {len(htmls)} 页。",
        }
    )
    return {"htmls": htmls}


from langchain_core.runnables import RunnableConfig

async def htmls2pptx_node(state: PPTState, writer: StreamWriter, config: RunnableConfig | None = None):
    """convert htmls to pdf and pptx"""
    topic = sanitize_filename(state["topic"])
    htmls = state["htmls"]
    save_dir = state["save_dir"]

    writer(
        {
            "step": "开始导出 PPT",
            "text": f"开始将 {len(htmls)} 个 PPT 页面导出为 PDF/PPTX，保存目录: {save_dir}。",
        }
    )

    pdf_path, pptx_path = await htmls_to_pptx(htmls, save_dir, topic)

    run_dir = run_dir_from_config(config, str(app_base_dir))
    run_id = get_run_id(config)
    if run_dir:
        save_json(f"{run_dir}/ppt.json", {
            "run_id": run_id,
            "topic": state["topic"],
            "render_dir": save_dir,
            "pdf_path": pdf_path,
            "pptx_path": pptx_path,
        })

    writer(
        {
            "step": "导出 PPT 完成",
            "files": [pdf_path, pptx_path],
            "text": f"生成PPT结束",
        }
    )

    return {"final_pdf_path": pdf_path, "final_pptx_path": pptx_path}
