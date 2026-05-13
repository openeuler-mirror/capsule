from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.state import CoverThanksPagesState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_cover_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 封面 SVG，封面题目为"{page.title}"。封面参考信息如下：
{page.abstract}

# 设计要求
- 只包含标题与最少必要副标题，不要堆叠正文。
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 不要参考模板中的具体文字内容，只参考视觉风格。
- 这是封面，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质；除此之外可以发挥创造力，设计符合用户要求的封面版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。

# 语言
生成页面文字必须使用：{language}

# 模板示意 SVG（仅供视觉参考）
{template}
"""


def _build_thanks_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 致谢页 SVG。用户的原始需求如下：
{query}

PPT 整体大纲如下：
{outline}

# 设计要求
- 内容简洁，主体是一句致谢/收束语，不要堆叠正文。
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 不要参考模板中的具体文字内容，只参考视觉风格。
- 这是致谢页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主色、背景和字体气质；除此之外可以发挥创造力，设计符合用户要求的致谢/收束页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。

# 语言
生成页面文字必须使用：{language}

# 模板示意 SVG（仅供视觉参考）
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
    """generate cover (SVG)"""
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
        "page": page,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}


async def generate_thanks_node(state: CoverThanksPagesState):
    """generate thanks page (SVG)"""
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
        "page": page,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
