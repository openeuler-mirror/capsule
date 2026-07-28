import asyncio
import os
from pathlib import Path
from typing import List

from langchain.messages import HumanMessage
from PIL import Image
from pydantic import TypeAdapter

from core.utils.logger import logger
from core.utils.config import app_base_dir, settings
from core.ppt_generator.utils.common import get_web_images_content, build_image_url
from core.utils.llm import InvokeOptions, can_vlm_invoke, llm_invoke, vlm_invoke
from core.utils.search import async_search
from core.ppt_generator.utils.image import generate_ai_image, get_ai_images_content
from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.utils.doc_image_pool import (
    init_doc_image_pool,
    doc_image_pool_snapshot,
    doc_image_pool_size,
    claim_doc_image,
)
from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.state import (
    ContentPagesState,
    ContentWorkerState,
    ImgScoreWorkerState,
    ImageQueries,
    ImageScoreResult,
)
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app
from core.ppt_generator.utils.style_pack import (
    reference_svg_for_page,
    style_guidance_for_page,
)


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_content_prompt(*, query, outline, ppt_prompt, template, language,
                          relevant_material, page) -> str:
    material_title = (
        "# 可参考的完整参考资料如下，根据用户的原始请求判断应该用怎样的信息密度将相关内容呈现到最终的PPT中："
        if page.reference_doc_is_full_context
        else "# 可参考的相关资料（含本页文档要点 + 已检索/评分排序后的图片素材）如下："
    )
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        style_guidance = style_guidance_for_page(page)
        guidance_section = f"""# Agent 编写的样式与版式契约（优先级高于从 SVG 自行猜测）
{style_guidance}

# 契约执行要求
- `global_style` 是跨页不变量；不得因为当前内容不同而改成另一套几何、图文比例或装饰语言。
- `global_style.text_container_usage` 是独立的文字承载方式硬约束：必须遵守模板对“裸排文字、实色背景文字块、仅边框文字框、标签、提示横条和卡片”的偏好及适用角色。模板正文通常不加框时，不得把每段文字卡片化；模板习惯用标题带、提示色块或总结框时，也不得把相应文字直接裸放在背景上。
- `selected_layout.structure` 描述参考页的空间骨架，`layout_rules` 是本页必须满足的硬约束。
- 如果契约与参考 SVG 中的个别局部特例不同，以契约为准；例如全局要求直角时，不得把个别圆角节点扩散为整页圆角卡片。
- 若使用图片，必须遵守契约中的图文比例和配文要求；不得让单张图片或单一图形吞没正文区域。
"""
        template_heading = "# 用户示例 PPT 中为本页预分配的参考页 SVG"
        template_rules = """# Style pack 参考页使用规范
- 代码会在生成后精确注入参考页的 background、master-content、layout-content、标题区、页眉、页脚、Logo、页码，以及 style pack 显式授权的前后层可复用装饰。你只生成当前页的动态主体内容，不得输出、重画、移动或覆盖这些固定元素。
- 动态主体必须位于参考页正文区域内，避开顶部标题区和底部页脚区；不得新增另一套页眉、页脚、页码、Logo、顶栏、底栏或全页背景。
- 参考页的标题坐标、配色、字体层级、视觉重心、分栏/卡片关系、留白、圆角和线条语言都是强约束；允许替换内容和调整卡片数量，但不得另起一套装饰系统。
- 禁止复制参考页中的原始正文、数字、业务图片和可能的用户敏感信息。`style-reference-only/` 下的图片是明确不可用的原示例业务图片。
- `images/style-pack/` 下的图片属于代码管理的继承外壳或已授权可复用装饰；不要在输出中手工引用或复制。当前页新增图片只能使用上方“相关图片素材”明确列出的路径。"""
    else:
        guidance_section = ""
        template_heading = "# 模板 SVG"
        template_rules = """# 模板使用规范（严格遵守，不是“仅供参考”）
- 严格保留模板的标题栏、装饰元素、页眉页脚、页码格式、配色、字体与卡片骨架（如果有的话）。
- 模板 SVG 中的 id 与 data-description 是强约束说明：id 为 background、slide-background、header、page-title-text、main-content-frame，或以 template-、top-accent、bottom-accent、content-frame、title-accent 开头的元素代表固定模板结构，内容页必须复用并保持其位置、尺寸、颜色、字体和层级关系。
- 对于 header / page-title-text：内容页标题区必须沿用模板给定的位置、字体、字号、字重、颜色和顶部蓝条，只替换标题文字为本页标题；禁止新增标题装饰。
- 对于 main-content-safe-area / content-safe-area-guide：正式输出时必须删除这个辅助边界框；实际内容应布局在它描述的安全范围内。
- 对于 main-content-frame 及 content-frame-*：如果 data-description 标注为主体装饰框，正式输出时必须保留。
- 对于 content-placeholder 及其子元素：它们只是占位提示，正式输出时必须删除。
- 并行生成的不同内容页必须保持模板基础格式一致；其他内容可以按当前页需求发挥。"""
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 内容页 SVG。

# 用户原始请求（决定内容密度、详略与表达深度）
{query}

# 完整 PPT 大纲：
{outline}

当前正在撰写第 {page.index + 1} / {len(outline)} 页：
- 标题："{page.title}"
- 摘要：{page.abstract}

{material_title}
{relevant_material}

# 图片使用要求
- 上方"相关图片素材"中给出的图片地址都是相对路径（如 "images/xxx.jpg"），可直接作为 <image href="images/xxx.jpg" .../> 使用，finalize 阶段会自动嵌入为 data URI。
- 如果上方"可参考的相关资料"中没有出现以"图片地址"开头的图片素材条目，本页禁止使用 <image> 元素，请用svg绘制图形的纯矢量方式表达。
- 当本页内容明显需要图片、系统架构、流程、对比关系、设备结构或概念示意来支撑表达，但缺少可用图片素材时，必须积极使用 SVG 形状、线条、箭头、流程图、架构图、示意图等纯矢量方式补充，不要因为缺少图片而只堆文字。
- 严禁编造图片文件名，包括 `图片1.png`、`图片2.png`、`image1.jpg`、`pic.png` 等占位名；只能逐字引用上方列出的真实文件名。
- 当资料里给出了图片时，可适度使用 <image> 提升表达，但宁可不用，也不要拼凑。

{template_rules}

{guidance_section}

# 语言
生成页面文字必须使用：{language}

{template_heading}
{template}
"""


async def get_content_pages_node(state: ContentPagesState):
    """get content pages from outline and initialise the document image pool."""
    # Load doc_images.json from the run directory (<save_dir>/../doc_images.json)
    save_dir = state.get("save_dir", "")
    if save_dir:
        cache_dir = os.path.dirname(save_dir)
        doc_images_path = os.path.join(cache_dir, "doc_images.json")
        logger.info("doc_image_pool: looking for {} (save_dir={}, cache_dir={})",
                     doc_images_path, save_dir, cache_dir)
        if os.path.isfile(doc_images_path):
            import json as _json
            import shutil
            with open(doc_images_path, "r", encoding="utf-8") as f:
                doc_images = _json.load(f)
            if doc_images:
                # Copy doc images to <save_dir>/images/ so that downstream
                # (LLM selection → SVG generation → PPTX export) can resolve
                # them via the standard relative-path mechanism (images/<name>).
                images_dest_dir = os.path.join(save_dir, "images")
                os.makedirs(images_dest_dir, exist_ok=True)
                relocated = 0
                for item in doc_images:
                    src = item.get("path", "")
                    if not src or not os.path.isfile(src):
                        continue
                    dest = os.path.join(images_dest_dir, os.path.basename(src))
                    if not os.path.exists(dest):
                        shutil.copy2(src, dest)
                    item["path"] = dest
                    relocated += 1
                if relocated:
                    logger.info("doc_image_pool: copied {} doc images to {}",
                                relocated, images_dest_dir)
                init_doc_image_pool(doc_images)
                logger.info("doc_image_pool: loaded {} images from {} (pool size={})",
                            len(doc_images), doc_images_path, doc_image_pool_size())
            else:
                logger.info("doc_image_pool: {} exists but is empty, pool not initialised", doc_images_path)
        else:
            logger.info("doc_image_pool: {} not found, pool not initialised", doc_images_path)
    else:
        logger.info("doc_image_pool: save_dir not set, skipping pool init")

    pages = []
    for page in state["outline"]:
        if page.type == PageType.CONTENT:
            pages.append(page)

    return {"content_pages": pages}


async def extract_relevant_doc_node(state: ContentWorkerState):
    """extract related materials for each page"""
    page = state["content_page"]
    if page.reference_doc_is_full_context:
        logger.info(f"skip relevant doc extraction for simple content page {page.index}")
        return {"relevant_material": page.reference_doc}

    prompt = f"""
你是一个素材整理和过滤专家，正在整理用于撰写某页PPT的材料。

# 用户原始请求
{state["query"]}

# 完整PPT的目录结构
{str(state["outline"])}。

# 你的任务是
请从以下原始资料中抽取过滤出"{page.title}:{page.abstract}"的相关素材。

# 注意
不要遗漏关键信息，所有关键时间、地点、信息都不要遗漏！
抽取要做到全面，同时不要抽取无关内容，不要抽取和其他PPT页重复的内容！
不要只提供主要内容，尽可能保留原文，不要只说明引用而不写出引用的内容！
生成的内容使用的语言必须为{state["language"]}！！！

# 参考资料
{page.reference_doc}
"""
    response = await llm_invoke([HumanMessage(content=prompt)])
    return {"relevant_material": response}


def _coerce_str_list(value) -> list[str]:
    """容错：LLM 偶尔会把单元素 list 字段返回成裸 string，统一包成 list[str]。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


async def generate_image_queries_node(state: ContentWorkerState):
    """generate image queries for the page"""
    page = state["content_page"]
    relevant_material = state["relevant_material"]

    # 若网页搜图和 AI 生图都关闭，直接短路，不浪费一次 LLM 调用。
    if not settings.USE_WEB_IMG_SEARCH and not settings.is_image_generation_enabled():
        logger.info("skip generate_image_queries: both web image search and AI image generation are disabled")
        return {"need_search_image": [], "need_ai_image": []}

    # AI 生图未启用时，prompt 只问网络搜图关键词，避免 LLM 误返回 AI prompt 触发下游无效调用。
    if settings.is_image_generation_enabled():
        ai_image_section = """
你认为大概率网上搜不到的图片，生成一个Prompt用于指导AI绘画模型("need_ai_image"最多只包含一个Prompt，即列表只有一个对象！)。
"""
        output_format = """
{
    "need_search_image": ["需要的图片素材描述1", "需要的图片素材描述2"],
    "need_ai_image": ["需要AI生成的图片描述Prompt"]
}
"""
    else:
        ai_image_section = ""
        output_format = """
{
    "need_search_image": ["需要的图片素材描述1", "需要的图片素材描述2"],
    "need_ai_image": []
}
"""

    prompt = f"""
请根据正在撰写的PPT的文字资料，判断是否需要搜索额外的素材。
# 正在撰写的PPT页
{page.title}:{page.abstract}

# 输出格式要求
如果需要额外的图片素材，返回如下格式的json，不要返回额外内容：
{output_format}
如果不需要，返回如下格式的json，不要返回额外内容：
{{
    "need_search_image": [],
    "need_ai_image": []
}}
决定需要为此内容补充什么样的图片素材。你需要根据内容将图片需求分为两类："网络搜索图片"和"AI生成图片"。

# 核心规则
你认为大概率能在网络上搜到的图片（例如人物照片、产品照片等），优先使用网络搜索；
{ai_image_section}
# PPT的文字资料
{relevant_material}
"""
    response = await llm_invoke(
        [HumanMessage(content=prompt)],
        InvokeOptions(pydantic_schema=ImageQueries),
    )
    if not response:
        return {"need_search_image": [], "need_ai_image": []}

    # 防御性容错：即便 schema 是 List[str]，部分模型仍可能返回裸 string。
    need_search_image = _coerce_str_list(getattr(response, "need_search_image", []))
    need_ai_image = _coerce_str_list(getattr(response, "need_ai_image", []))
    # 配置兜底：若 AI 生图已被关闭，即便 LLM 返回了 prompt 也丢弃。
    if not settings.is_image_generation_enabled():
        need_ai_image = []

    return {"need_search_image": need_search_image, "need_ai_image": need_ai_image}


async def get_web_ai_images_node(state: ContentWorkerState):
    """get web, ai and mem image"""
    web_images = state["need_search_image"]
    ai_images = state["need_ai_image"]
    reference_image_descriptions = {}
    web_images_tasks = []
    if settings.USE_WEB_IMG_SEARCH:
        for image_query in web_images:
            web_images_tasks.append(
                asyncio.create_task(async_search(query=image_query, search_image=True, max_results=5))
            )

    if settings.is_image_generation_enabled():
        ai_images_tasks = []
        for image_prompt in ai_images:
            ai_images_tasks.append(asyncio.create_task(generate_ai_image(image_prompt, state["save_dir"])))
        ai_results = await asyncio.gather(*ai_images_tasks)
        ai_content, _, ai_image_descriptions = await get_ai_images_content(ai_images, ai_results, state["save_dir"])
        reference_image_descriptions.update(ai_image_descriptions)
    else:
        ai_content = ""

    if settings.USE_WEB_IMG_SEARCH:
        web_results = await asyncio.gather(*web_images_tasks) if web_images_tasks else []
        web_content, _, web_image_descriptions = await get_web_images_content(
            web_images, web_results, state["save_dir"]
        )
        reference_image_descriptions.update(web_image_descriptions)
    else:
        web_content = ""

    if settings.is_image_generation_enabled() and settings.USE_WEB_IMG_SEARCH:
        img_content = f"\n\n额外的图片搜索结果如下：{web_content}\n\n以下图片的分辨率为1280*720：\n{ai_content}\n\n"
    elif settings.is_image_generation_enabled():
        img_content = f"\n\n以下图片的分辨率为1280*720：\n{ai_content}\n\n"
    elif settings.USE_WEB_IMG_SEARCH:
        img_content = f"\n\n额外的图片搜索结果如下：{web_content}\n\n"
    else:
        img_content = ""

    return {
        "img_content": img_content,
        "reference_image_descriptions": reference_image_descriptions,
    }


async def get_final_images_node(state: ContentWorkerState):
    """select images from web/AI results + document image pool."""
    # Take a snapshot of the doc image pool (synchronous, atomic under asyncio).
    doc_snapshot = doc_image_pool_snapshot()
    page_idx = state["content_page"].index
    logger.info("doc_image_pool: page {} snapshot has {} images available (pool size={})",
                page_idx, len(doc_snapshot), doc_image_pool_size())
    doc_images_desc = ""
    if doc_snapshot:
        doc_images_desc = "\n\n# Document images (from uploaded documents)\n" + "\n".join(
            f"{item['path']}: {item.get('description', '')}" for item in doc_snapshot
        )

    prompt = f"""
请从以下图片中选择5张最适合放在该页PPT中的图片（不足5张则按需返回，可以为空[]）。
# PPT的文字素材
{state["relevant_material"]}

# 输出格式要求
只返回一个json格式的列表，如：
['图片1绝对路径', '图片2绝对路径']

# 图片绝对路径以及描述
{state["img_content"]}
{doc_images_desc}

每个路径一定要以完整的绝对路径输出！！
"""
    schema = TypeAdapter(List[str]).json_schema()
    img_list = await llm_invoke(
        [HumanMessage(content=prompt)],
        InvokeOptions(json_schema=schema),
    )
    if not img_list:
        img_list = []
    img_list.extend(state["content_page"].reference_images)

    # Claim doc images that were selected by the LLM.
    # In single-threaded asyncio this check+remove is atomic (no await between).
    # If another worker already claimed the image, skip it.
    final_img_list = []
    description_map = state.get("reference_image_descriptions") or {}
    for img_path in img_list:
        if not os.path.exists(img_path):
            continue
        # Check if this is a doc image that needs claiming
        is_doc_img = any(item["path"] == img_path for item in doc_snapshot)
        if is_doc_img:
            if claim_doc_image(img_path):
                final_img_list.append(img_path)
                doc_desc = next((item.get("description", "") for item in doc_snapshot if item["path"] == img_path), "")
                logger.info("doc_image_pool: page {} claimed doc image id={} description={}",
                            page_idx, img_path, doc_desc)
            else:
                logger.info(
                    "doc_image_pool: page {} skipped doc image {} (already claimed by another page)",
                    page_idx, img_path)
        else:
            final_img_list.append(img_path)

    # Also collect descriptions for claimed doc images
    final_description_map = {
        image_path: description_map[image_path]
        for image_path in final_img_list
        if image_path in description_map and description_map[image_path]
    }
    # Add descriptions from doc snapshot for claimed doc images
    for item in doc_snapshot:
        if item["path"] in final_img_list and item.get("description"):
            final_description_map.setdefault(item["path"], item["description"])

    logger.info("doc_image_pool: page {} final selection: {} total images (pool remaining={})",
                page_idx, len(final_img_list), doc_image_pool_size())

    return {
        "reference_images": final_img_list,
        "reference_image_descriptions": final_description_map,
    }


async def get_img_score_node(state: ImgScoreWorkerState):
    """score the image"""
    relevant_material = state["relevant_material"]
    image_path = state["image_path"]
    image_description = (state.get("image_description") or "").strip()
    _, ext = os.path.splitext(image_path)
    ext = ext.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".avif": "image/avif",
    }
    mime_type = mime_types.get(ext)
    if not mime_type:
        logger.debug(f"Error in get_img_score: Not support img type: {image_path}")
        return {"img_scores": [None]}
    if ext in [".avif", ".webp"]:
        try:
            jpg_path = os.path.splitext(image_path)[0] + ".jpg"
            with Image.open(image_path) as im:
                im = im.convert("RGB")
                im.save(jpg_path, "JPEG", quality=90)
            image_path = jpg_path
            ext = ".jpg"
            mime_type = mime_types.get(ext)
        except Exception as e:
            logger.debug(f"Error in get_img_score: convert {image_path} failed - {e}")
            return {"img_scores": [None]}

    try:
        Image.open(image_path).verify()
    except Exception as e:
        logger.debug(f"Error in get_img_score: {e} from img: {image_path}")
        return {"img_scores": [None]}

    if not can_vlm_invoke():
        height, width = get_image_size(image_path)
        size = f"图片高度为{height}，宽度为{width}"
        # VLM 未配置是合法的预期场景（见 .env.example），不需要 WARNING 级别。
        # 一次性提示由 assign_img_score_workers 给出，这里只保留 debug 级别细节。
        logger.debug(
            "VLM not configured; image scoring falls back to default score without VLM analysis: "
            f"{image_path}"
        )
        return {"img_scores": [
            {
                "img_description": image_description or "参考图片，未进行 VLM 内容分析。",
                "score": 5.0,
                "size": size,
                "image_path": image_path,
            }
        ]}

    prompt = f"""
请判断能否将该图片用于该页PPT当中，并返回图片描述以及得分(分数为0-10的float数字，0代表完全不可用，9.9代表一定能用到)。
# 用户的PPT的文字素材
{relevant_material}
# 输出示例
{{
    "img_description": "图片描述"，
    "score": 6.3
}}
"""

    messages = [HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": build_image_url(image_path)},
            },
        ]
    )]

    try:
        response_data = await vlm_invoke(
            messages,
            InvokeOptions(pydantic_schema=ImageScoreResult),
        )

        if not response_data or not response_data.img_description or not response_data.score:
            logger.debug(f"Error in get_img_score from img: {image_path}")
            return {"img_scores": [None]}
        height, width = get_image_size(image_path)
        size = f"图片高度为{height}，宽度为{width}"
        return {"img_scores": [
            {
                "img_description": response_data.img_description,
                "score": response_data.score,
                "size": size,
                "image_path": image_path,
            }
        ]}
    except Exception as e:
        logger.debug(f"Error scoring img {image_path}: {e}")
        return {"img_scores": [None]}


async def extend_relevant_material_node(state: ContentWorkerState):
    """extend relevant material with images"""
    img_scores = [item for item in state["img_scores"] if item is not None]
    sorted_list = sorted(img_scores, key=lambda item: item["score"], reverse=True)
    top_n_list = sorted_list[:min(len(sorted_list), settings.TOP_N_IMAGE)]
    relevant_material = state["relevant_material"]

    final_images = []
    for item in top_n_list:
        img_path = item["image_path"]
        description = item["img_description"]
        size_info = item["size"]

        formatted_str = (
            f'图片地址，可以直接相对引用："images/{os.path.basename(img_path)}"\n'
            f'图片描述为：{description}\n'
            f'图片大小为{size_info}\n'
        )
        final_images.append(formatted_str)
    relevant_material = relevant_material + "\n可以使用的相关图片素材如下:\n" + "\n".join(final_images)
    return {"relevant_material": relevant_material}


async def generate_content_page_node(state: ContentWorkerState):
    """generate content page (SVG)"""
    page = state["content_page"]
    relevant_material = state["relevant_material"]
    logger.info(f'start generate page {page.index}...')
    prompt = _build_content_prompt(
        query=state["query"],
        outline=state["outline"],
        ppt_prompt=state["ppt_prompt"],
        template=state["template"],
        language=state["language"],
        relevant_material=relevant_material,
        page=page,
    )
    task_payload = {
        "index": page.index,
        "page": page,
        "generate_ppt_prompt": prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}


def get_image_size(image_path):
    """get image height and width"""
    try:
        with Image.open(image_path) as img:
            height = img.height
            width = img.width
    except Exception as e:
        logger.warning(f"open Image {image_path} failed {e}")
        return 0, 0
    return height, width
