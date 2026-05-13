from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType, PPTState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_toc_prompt(*, ppt_prompt, template, language, page) -> str:
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 目录页 SVG，目录页题目为"{page.title}"，目录条目内容如下：
{page.abstract}

# 设计要求
- 主体是清晰编号的目录列表，避免堆叠正文。
- 配色与字体必须沿用下方模板示意 SVG 的视觉语言。
- 这是目录页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质；除此之外可以发挥创造力，设计清晰、有节奏的目录页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。

# 语言
生成页面文字必须使用：{language}

# 模板示意 SVG（仅供视觉参考）
{template}
"""


async def generate_toc_page_node(state: PPTState):
    """generate toc page (SVG)"""
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
        "page": toc_page,
        "generate_ppt_prompt": generate_ppt_prompt,
        "ppt_prompt": state["ppt_prompt"],
        "save_dir": state["save_dir"],
        "content": None,
    }
    output = await generate_ppt_page_app.ainvoke(task_payload)
    return {"generated_pages": output["generated_pages"]}
