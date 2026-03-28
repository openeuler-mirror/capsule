from core.utils.logger import logger

from core.ppt_generator.thought_to_ppt.state import PageType, PPTState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph import generate_ppt_page_app


async def generate_toc_page_node(state: PPTState):
    """generate toc page"""
    toc_page = None
    for page in state["outline"]:
        if page.type == PageType.TOC:
            toc_page = page
            break
    if toc_page is None:
        return {"generated_pages": []}

    logger.info(f'start generate toc page {toc_page.index}...')

    generate_ppt_prompt = f"""
撰写一个ppt的目录网页，该页PPT题目为{toc_page.title}，PPT的目录如下：
{toc_page.abstract}
只需要撰写简单的目录即可。
生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！生成的PPT中使用的语言必须为{state["language"]}！
{state["ppt_prompt"]}
{state["html_template"]}
"""
    task_payload = {
        "index": toc_page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "html_content": None
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
