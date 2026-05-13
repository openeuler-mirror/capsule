from core.utils.logger import logger

from core.ppt_generator.thought_to_ppt.state import PageType, PPTState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph import generate_ppt_page_app


def _build_toc_prompt(*, ppt_prompt, template, language, page) -> str:
    return f"""
撰写一个ppt的目录网页，该页PPT题目为{page.title}，PPT的目录如下：
{page.abstract}
只需要撰写简单的目录即可。
这是目录页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
需要保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质；除此之外可以发挥创造力，设计清晰、有节奏的目录页版式。
生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！
{ppt_prompt}
{template}
"""


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

    generate_ppt_prompt = _build_toc_prompt(
        ppt_prompt=state["ppt_prompt"],
        template=state.get("template", ""),
        language=state["language"],
        page=toc_page,
    )
    task_payload = {
        "index": toc_page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
