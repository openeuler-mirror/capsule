from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType, PPTState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.graph import generate_ppt_page_app
from core.ppt_generator.utils.style_pack import reference_svg_for_page


def _svg_prompt_header() -> str:
    return (
        Path(app_base_dir)
        / "core" / "ppt_generator" / "assets" / "prompts"
        / "svg_generator_prompt.txt"
    ).read_text(encoding="utf-8")


def _build_toc_prompt(*, ppt_prompt, template, language, page) -> str:
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        design_requirements = """- 该 SVG 是用户示例 PPT 中预分配的目录参考页。代码会精确注入其背景、母版、版式、目录标题、Logo、页眉页脚和固定装饰。
- 只生成目录条目本身，不要输出或重画目录标题、背景、Logo、页眉页脚、页码和固定装饰。
- 目录条目必须放入参考页现有正文占位区域，沿用其列数、起始位置、间距、编号形式和字体层级；不得改变整体构图。
- 禁止复制原目录文字、业务内容和图片路径；禁止引用 `style-reference-only/` 或 `images/style-pack/`。
- 不得新增卡片背景、顶部标签、统计数字、说明段落或另一套装饰；有参考目录页时应保持其框架基本不变。"""
        template_heading = "# 用户示例 PPT 目录参考 SVG"
    else:
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
