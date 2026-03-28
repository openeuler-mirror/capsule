from core.utils.logger import logger

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.state import SEPPagesState, SEPWorkerState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph import generate_ppt_page_app


async def get_sep_pages_node(state: SEPPagesState):
    """get sep pages"""
    pages = []
    for page in state["outline"]:
        if page.type == PageType.SEPARATOR:
            pages.append(page)

    return {"sep_pages": pages}


async def generate_sep_template_node(state: SEPPagesState):
    """generate sep template page"""
    page = state["sep_pages"][0]
    logger.info(f'start generate sep page {page.index}...')
    generate_ppt_prompt = f"""
撰写一个ppt的分割页，该分割页的后续部分的主要内容为"{page.title}"。
根据该主要内容生成简单精简的分割页即可，内容不要过多!
如果主要内容中没有明确说明后续部分为"第x部分"，不要在分割页中体现第几部分！不要编造内容！
生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！
请参考下方模板PPT的html代码中各内容模块的设计风格（如文字样式、色彩搭配、组件质感、元素交互逻辑等，不要参考文本内容！），生成一页完整的PPT分割页的HTML代码。分割页可以没有示例中的header部分。
分割页保持简洁、大方即可，不需要过多信息。
{state["ppt_prompt"]}
{state["html_template"]}
"""
    task_payload = {
        "index": page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "html_content": None
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"], "sep_template": output["html_content"]}


async def generate_sep_page_node(state: SEPWorkerState):
    """generate sep page"""
    page = state["sep_page"]
    logger.info(f'start generate sep page {page.index}...')
    generate_ppt_prompt = f"""
撰写一个ppt的分割页，该分割页的后续部分的主要内容为{page.title}。
根据该主要内容生成简单精简的分割页即可，内容不要过多!
如果主要内容中没有明确说明后续部分为"第x部分"，不要在分割页中体现第几部分！不要编造内容！
生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！
请参考下方生成好的其中一页PPT分割页的html代码的设计风格（如文字样式、色彩搭配、组件质感、元素交互逻辑等，不要参考文本内容！），与其保持样式一致，生成一页完整的PPT分割页的HTML代码。
{state["ppt_prompt"]}
{state["sep_template"]}
"""
    task_payload = {
        "index": page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "html_content": None
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
