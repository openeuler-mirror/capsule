from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.state import SEPPagesState, SEPWorkerState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_sep_template_prompt(*, ppt_prompt, template, language, outline, page) -> str:
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 章节分割页 SVG，该分割页后续部分的主要内容为"{page.title}"。
- 内容简洁，仅围绕该章节标题展开，禁止编造内容。
- 如果主标题中没有"第x部分"等信息，请不要在分割页中出现章节编号。

# 设计要求
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 分割页通常较空旷，留白充足，呼吸感强。
- 这是章节分割页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主色、背景和字体气质；除此之外可以发挥创造力，设计符合章节过渡感的分割页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。

# 语言
生成页面文字必须使用：{language}

# 模板示意 SVG（仅供视觉参考）
{template}
"""


def _build_sep_page_prompt(*, ppt_prompt, sep_template, language, outline, page) -> str:
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 章节分割页 SVG，该分割页后续部分的主要内容为"{page.title}"。
- 内容简洁，仅围绕该章节标题展开，禁止编造内容。
- 如果主标题中没有"第x部分"等信息，请不要在分割页中出现章节编号。

# 设计要求
- 必须与下方"已生成的同套分割页 SVG"在配色、字体、版式上保持一致，仅替换文字内容。
- 不要复制原 SVG 的具体文字，但视觉骨架应一致。
- 这是章节分割页，不是内容页；内容页模板中关于标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 后续分割页应跟随第一张分割页的版式体系，并保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。

# 语言
生成页面文字必须使用：{language}

# 已生成的同套分割页 SVG（仅供视觉参考）
{sep_template}
"""


async def get_sep_pages_node(state: SEPPagesState):
    """get sep pages"""
    pages = []
    for page in state["outline"]:
        if page.type == PageType.SEPARATOR:
            pages.append(page)

    return {"sep_pages": pages}


async def generate_sep_template_node(state: SEPPagesState):
    """generate sep template page (SVG)"""
    page = state["sep_pages"][0]
    logger.info(f'start generate sep page {page.index}...')
    generate_ppt_prompt = _build_sep_template_prompt(
        ppt_prompt=state["ppt_prompt"],
        template=state["template"],
        language=state["language"],
        outline=state["outline"],
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
    return {"generated_pages": output["generated_pages"], "sep_template": output["content"]}


async def generate_sep_page_node(state: SEPWorkerState):
    """generate sep page (SVG)"""
    page = state["sep_page"]
    logger.info(f'start generate sep page {page.index}...')
    generate_ppt_prompt = _build_sep_page_prompt(
        ppt_prompt=state["ppt_prompt"],
        sep_template=state["sep_template"],
        language=state["language"],
        outline=state["outline"],
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
