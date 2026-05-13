from core.utils.logger import logger

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.state import CoverThanksPagesState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.graph import generate_ppt_page_app


def _build_cover_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    return f"""
撰写一个ppt的封面，封面题目为{page.title}，封面可以参考的信息如下：
{page.abstract}
生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！
撰写封面页时只需要撰写包含标题的简单封面即可，不需要包含过多内容，且所有配色必须和以下给定的模板的配色保持一致。
{ppt_prompt}
{template}
"""


def _build_thanks_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    return f"""
撰写一个PPT的致谢页，用户的原始需求如下：
{query}
PPT的大致内容如下：
{str(outline)}
请根据以上内容生成合适的PPT最后的致谢页。
撰写简单精简的致谢页即可，内容不要过多，且所有配色必须和以下给定的模板的配色保持一致。
生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！生成的PPT中使用的语言必须为{language}！
{ppt_prompt}
{template}
"""


async def get_cover_thanks_pages_node(state: CoverThanksPagesState):
    """get cover and thanks pages"""
    pages = []
    for page in state["outline"]:
        if page.type == PageType.COVER_THANKS:
            pages.append(page)
    if len(pages) == 0:
        return {"cover_page": None, "thanks_page": None}

    return {"cover_page": pages[0], "thanks_page": pages[1]}


async def generate_cover_node(state: CoverThanksPagesState):
    """generate cover"""
    page = state["cover_page"]
    if not page:
        return {"generated_pages": []}
    logger.info(f'start generate cover page {page.index}...')
    generate_ppt_prompt = _build_cover_prompt(
        query=state["query"],
        outline=state["outline"],
        save_dir=state["save_dir"],
        ppt_prompt=state["ppt_prompt"],
        template=state["template"],
        language=state["language"],
        page=page,
    )
    task_payload = {
        "index": page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}


async def generate_thanks_node(state: CoverThanksPagesState):
    """generate thanks page"""
    page = state["thanks_page"]
    if not page:
        return {"generated_pages": []}
    logger.info(f'start generate thanks page {page.index}...')
    generate_ppt_prompt = _build_thanks_prompt(
        query=state["query"],
        outline=state["outline"],
        save_dir=state["save_dir"],
        ppt_prompt=state["ppt_prompt"],
        template=state["template"],
        language=state["language"],
        page=page,
    )
    task_payload = {
        "index": page.index,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "iteration": 0,
        "action": "generate",
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
