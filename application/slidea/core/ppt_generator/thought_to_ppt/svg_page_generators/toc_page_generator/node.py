from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType, PPTState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app
from core.ppt_generator.utils.style_pack import (
    reference_svg_for_page,
    style_reference_is_shell_only,
    style_guidance_for_page,
)


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_toc_prompt(*, ppt_prompt, template, language, page) -> str:
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        guidance_section = (
            "# Agent 编写的样式与版式契约\n"
            + style_guidance_for_page(page)
        )
        if style_reference_is_shell_only(page):
            design_requirements = """- 用户示例 PPT 没有可用目录页；下方 SVG 是从一个内容页提取的纯外壳，只用于继承背景、母版、版式、Logo、页眉页脚和已授权装饰。
- 代码不会注入该内容页的标题、正文、图片、卡片或内容区结构；禁止复刻或补回这些已删除内容。
- 必须自行生成目录标题与全部目录条目（编号 + 章节标题），使用独立的纯文字 <text> 元素和必要的辅助图形（编号圆/方块、分隔短线）。
- 目录条目应排成清晰的纵向列表，整体水平居中或左对齐于外壳的主要空白区，条目之间间距均匀；沿用外壳的字体、颜色和对齐气质。
- 不要为条目增加底板卡片、概念图、流程图、时间线或另一套视觉装饰；不要生成图片。
- 不要输出全页背景、Logo、页眉页脚或固定装饰；禁止引用 `style-reference-only/` 或 `images/style-pack/` 图片路径。"""
            template_heading = "# 用户示例 PPT 内容页外壳 SVG（目录页回退）"
        else:
            design_requirements = """- 该 SVG 是用户示例 PPT 中预分配的目录参考页。代码会精确注入其背景、母版、可识别的目录标题/标识、Logo、页眉页脚和固定装饰。
- 只生成动态目录条目，不要输出或重画目录标题/标识、背景、Logo、页眉页脚、页码和固定装饰。
- 每个目录条目必须完整包含参考页所需的文字与承载它的动态行框、色块、分隔线等结构；这些目录行不是代码注入的固定外壳。
- 目录条目必须放入参考页现有正文占位区域，沿用其列数、起始位置、间距、编号形式、行框几何和字体层级；不得改变整体构图。
- 禁止复制原目录文字、业务内容和图片路径；禁止引用 `style-reference-only/` 或 `images/style-pack/`。
- 不得新增参考页目录行之外的卡片系统、顶部标签、统计数字、说明段落或另一套装饰；有参考目录页时应保持其框架基本不变。"""
            template_heading = "# 用户示例 PPT 目录参考 SVG"
    else:
        guidance_section = ""
        design_requirements = """- 该 SVG 是内置模板示意。
- 主体是清晰编号的目录列表，避免堆叠正文。
- 配色与字体必须沿用下方模板示意 SVG 的视觉语言。
- 这是目录页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质；除此之外可以发挥创造力，设计清晰、有节奏的目录页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。"""
        template_heading = "# 模板 SVG"
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 目录页 SVG，目录页题目为"{page.title}"，目录条目内容如下：
{page.abstract}

# 设计要求
{design_requirements}

{guidance_section}

# 语言
生成页面文字必须使用：{language}

{template_heading}
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
