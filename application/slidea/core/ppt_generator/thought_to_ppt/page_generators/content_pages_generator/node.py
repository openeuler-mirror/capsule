import asyncio
import os
from typing import List

from langchain.messages import HumanMessage
from PIL import Image
from pydantic import TypeAdapter

from core.utils.logger import logger
from core.utils.config import settings
from core.ppt_generator.utils.common import get_web_images_content, build_image_url
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_invoke
from core.utils.tavily_search import async_search
from core.ppt_generator.utils.image import generate_ai_image, get_ai_images_content
from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.state import (
    ContentPagesState,
    ContentWorkerState,
    ImgScoreWorkerState,
    ImageQueries,
    ImageScoreResult
)
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph import generate_ppt_page_app


async def get_content_pages_node(state: ContentPagesState):
    """get content pages from outline"""
    pages = []
    for page in state["outline"]:
        if page.type == PageType.CONTENT:
            pages.append(page)

    return {"content_pages": pages}


async def extract_relevant_doc_node(state: ContentWorkerState):
    """extract related materials for each page"""
    page = state["content_page"]
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
    response = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)])
    return {"relevant_material": response}


async def generate_image_queries_node(state: ContentWorkerState):
    """generate image queries for the page"""
    page = state["content_page"]
    prompt = f"""
请根据正在撰写的PPT的文字资料，判断是否需要搜索额外的素材。
# 正在撰写的PPT页
{page.title}:{page.abstract}

# 输出格式要求
如果需要额外的图片素材，返回如下格式的json，不要返回额外内容：
{{
    "need_search_image": ["需要的图片素材描述1", "需要的图片素材描述2"],
    "need_ai_image": ["需要AI生成的图片描述Prompt"]
}}
如果不需要，返回如下格式的json，不要返回额外内容：
{{
    "need_search_image": [],
    "need_ai_image": []
}}
决定需要为此内容补充什么样的图片素材。你需要根据内容将图片需求分为两类：“网络搜索图片”和“AI生成图片”。

# 核心规则
你认为大概率能在网络上搜到的图片（例如人物照片、产品照片等），优先使用网络搜索；
你认为大概率网上搜不到的图片，生成一个Prompt用于指导用于指导AI绘画模型("need_ai_image"最多只包含一个Prompt，即列表只有一个对象！)。

# PPT的文字资料
{state["relevant_material"]}
"""
    response = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)], pydantic_schema=ImageQueries)
    if not response:
        response = ImageQueries(need_search_image=[], need_ai_image=[])

    return {"need_search_image": response.need_search_image, "need_ai_image": response.need_ai_image}


async def get_web_ai_images_node(state: ContentWorkerState):
    """get web, ai and mem image"""
    web_images = state["need_search_image"]
    ai_images = state["need_ai_image"]
    reference_image_descriptions = {}
    # web images
    web_images_tasks = []
    if settings.USE_WEB_IMG_SEARCH:
        for image_query in web_images:
            web_images_tasks.append(asyncio.create_task(async_search(query=image_query, search_image=True, max_results=5)))

    # ai images
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
        web_content, _, web_image_descriptions = await get_web_images_content(web_images, web_results, state["save_dir"])
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
    """select images from all the images"""
    prompt = f"""
请从以下图片中选择5张最适合放在该页PPT中的图片（不足5张则按需返回，可以为空[]）。
# PPT的文字素材
{state["relevant_material"]}

# 输出格式要求
只返回一个json格式的列表，如：
['图片1路径'， '图片2路径']

# 图片路径以及描述
{state["img_content"]}
"""
    schema = TypeAdapter(List[str]).json_schema()
    img_list = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)], json_schema=schema)
    if not img_list:
        img_list = []
    img_list.extend(state["content_page"].reference_images)
    final_img_list = [img for img in img_list if os.path.exists(img)]
    description_map = state.get("reference_image_descriptions") or {}
    final_description_map = {
        image_path: description_map[image_path]
        for image_path in final_img_list
        if image_path in description_map and description_map[image_path]
    }

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
        ".avif": "image/avif"
    }
    mime_type = mime_types.get(ext)
    if not mime_type:
        logger.debug(f"Error in get_img_score: Not support img type: {image_path}")
        return {"img_scores": [None]}
    # 转换不支持的格式（avif/webp -> jpg）
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

    if not can_vlm_invoke_route(ModelRoute.DEFAULT):
        height, width = get_image_size(image_path)
        size = f"图片高度为{height}，宽度为{width}"
        logger.warning(
            "No available VLM route for image scoring. Use a fallback image score without VLM analysis: "
            f"{image_path}"
        )
        return {"img_scores": [
            {
                "img_description": image_description or "参考图片，未进行 VLM 内容分析。",
                "score": 5.0,
                "size": size,
                "image_path": image_path
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
            {
                "type": "text",
                "text": prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": build_image_url(image_path),
                },
            }
        ]
    )]

    try:
        response_data = await vlm_invoke(ModelRoute.DEFAULT, messages, pydantic_schema=ImageScoreResult)

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
                "image_path": image_path
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
            f'图片地址，可以直接相对引用：\"images/{os.path.basename(img_path)}\"\n'
            f'图片描述为：{description}\n'
            f'图片大小为{size_info}\n'
        )
        final_images.append(formatted_str)
    relevant_material = relevant_material + "\n可以使用的相关图片素材如下:\n" + "\n".join(final_images)
    return {"relevant_material": relevant_material}


async def generate_content_page_node(state: ContentWorkerState):
    """generate content page"""
    page = state["content_page"]
    relevant_material = state["relevant_material"]
    logger.info(f'start generate page {page.index}...')
    prompt = f"""
想要撰写一页PPT，用户的原始请求为：{state["query"]}。
可以参考的完整PPT的目录结构如下：{str(state["outline"])}。
当前正在撰写{page.index + 1}/{len(state["outline"])}页，该页PPT题目为"{page.title}"，该页的主要内容为：{page.abstract}
如果参考模板中没有页码，不要添加页码！！
可以参考的相关资料如下：
{relevant_material}
有相关的图片要积极使用。
生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！
请参考下方生成好的其中一页PPT的html代码中各内容模块的设计风格（如文字样式、色彩搭配、组件质感、元素交互逻辑等），生成一页完整的PPT的HTML代码。
{state["ppt_prompt"]}
{state["html_template"]}
"""
    task_payload = {
        "index": page.index,
        "generate_ppt_prompt": prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "html_content": None
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
