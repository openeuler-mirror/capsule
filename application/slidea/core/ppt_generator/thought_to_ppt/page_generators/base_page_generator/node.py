import os
import re
from pathlib import Path

from langchain.messages import HumanMessage

from core.utils.logger import logger
from core.utils.llm import ModelRoute, can_vlm_invoke_route, llm_invoke, vlm_raw_invoke
from core.ppt_generator.utils.common import get_scale_step_value, build_image_url, wait_for_page_assets_ready
from core.ppt_generator.utils.browser import BrowserManager
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.state import PPTWorkerState


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
    """modify wrong ppt page"""
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
    """保存并检查缩放"""
    index = state["index"]
    save_dir = state["save_dir"]
    html_content = state["html_content"]
    iteration = state["iteration"]
    # 1. 保存文件
    file_path = Path(save_dir) / f"{index}.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    file_path_str = str(file_path.resolve())

    logger.info(f"save html to {file_path_str}")
    # 2. 检查
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


def ppt_submitter_node(state: PPTWorkerState):
    """submit ppt result"""
    return {
        "generated_pages": [
            {
                "index": state["index"],
                "file_path": state["final_file_path"],
                "status": "success"
            }
        ]
    }


def route_page(state: PPTWorkerState):
    """route page"""
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
