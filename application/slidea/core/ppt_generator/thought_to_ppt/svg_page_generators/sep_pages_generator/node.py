from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.sep_pages_generator.state import (
    SEPPagesState,
    SEPWorkerState,
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


def _build_sep_template_prompt(*, ppt_prompt, template, language, outline, page) -> str:
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        guidance_section = (
            "# Agent 编写的样式与版式契约\n"
            + style_guidance_for_page(page)
        )
        design_requirements = """- 这是用户示例 PPT 中预分配的章节页参考。代码会精确注入背景、母版、版式、章节标题位置、Logo、页眉页脚和固定装饰。
- 不要输出或重画章节标题、背景、Logo、页眉页脚、页码和固定装饰；只在参考页确有对应文字槽时生成最少必要的章节说明。
- 禁止复制原示例正文和图片路径，禁止引用 `style-reference-only/` 或 `images/style-pack/`。
- 不得新增卡片、流程、图标阵列或另一套装饰；章节页构图应与参考页基本一致。"""
        template_heading = "# 用户示例 PPT 章节页参考 SVG"
    else:
        guidance_section = ""
        design_requirements = """- 这是内置模板示意。
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 分割页通常较空旷，留白充足，呼吸感强。
- 这是章节分割页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主色、背景和字体气质；除此之外可以发挥创造力，设计符合章节过渡感的分割页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。"""
        template_heading = "# 模板 SVG"
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 章节分割页 SVG，该分割页后续部分的主要内容为"{page.title}"。
- 内容简洁，仅围绕该章节标题展开，禁止编造内容。
- 如果主标题中没有"第x部分"等信息，请不要在分割页中出现章节编号。

# 设计要求
{design_requirements}

{guidance_section}

# 语言
生成页面文字必须使用：{language}

{template_heading}
{template}
"""


def _build_sep_page_prompt(*, ppt_prompt, sep_template, language, outline, page) -> str:
    reference, has_reference = reference_svg_for_page(page, "")
    page_reference_section = (
        f"\n# 本页预分配的用户示例参考 SVG\n"
        "代码会精确注入其固定背景、母版、版式、标题、页眉页脚和 Logo。"
        "只生成可替换的最少正文，不得重画固定元素；禁止复制原文字和图片路径。\n"
        f"# Agent 编写的样式与版式契约\n{style_guidance_for_page(page)}\n"
        f"{reference}\n"
        if has_reference else ""
    )
    style_rule = (
        "- 本页存在独立 style pack 参考，必须以该参考的固定构图为准；"
        "不要为了跟随上一张分割页而覆盖其背景、标题位置、页眉页脚或装饰。"
        if has_reference else
        "- 后续分割页应跟随第一张分割页的版式体系，并保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质。"
    )
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
{style_rule}

# 语言
生成页面文字必须使用：{language}

# 已生成的同套分割页 SVG
{sep_template}
{page_reference_section}
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
