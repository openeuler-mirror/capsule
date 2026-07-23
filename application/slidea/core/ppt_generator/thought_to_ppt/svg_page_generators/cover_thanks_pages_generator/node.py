from pathlib import Path

from core.utils.logger import logger
from core.utils.config import app_base_dir

from core.ppt_generator.thought_to_ppt.state import PageType
from core.ppt_generator.thought_to_ppt.svg_page_generators.cover_thanks_pages_generator.state import (
    CoverThanksPagesState,
)
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


def _build_cover_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        guidance_section = (
            "# Agent 编写的样式与版式契约\n"
            + style_guidance_for_page(page)
        )
        if style_reference_is_shell_only(page):
            design_requirements = """- 用户示例 PPT 没有可用封面；下方 SVG 是从一个内容页提取的纯外壳，只用于继承背景、母版、版式、Logo、页眉页脚和已授权装饰。
- 代码不会注入该内容页的标题、正文、图片、卡片或内容区结构；禁止复刻或补回这些已删除内容。
- 必须自行生成封面主标题，并可按需要增加一行副标题，以及作者、部门或日期中的少量信息；全部使用独立的纯文字 <text> 元素。
- 文字应放在外壳的主要空白区域内，保持清晰的主次层级、充足留白和安全边距；沿用外壳的字体、颜色和对齐气质。
- 不要为文字增加底板、卡片、边框或图标；不要生成图片、概念图、流程图、时间线和任何内容页版式。
- 不要输出全页背景、Logo、页眉页脚或固定装饰；禁止引用 `style-reference-only/` 或 `images/style-pack/` 图片路径。"""
            template_heading = "# 用户示例 PPT 内容页外壳 SVG（封面回退）"
        else:
            design_requirements = """- 这是用户示例 PPT 中预分配的封面参考页。代码会精确注入其背景、母版、版式、Logo、固定图片、装饰和标题位置。
- 不要输出或重画主标题、全页背景、Logo、页眉页脚和固定装饰；只生成参考页可替换区域中的最少必要副标题、作者、部门、日期等动态文字。
- 动态文字必须沿用参考页现有文字槽的坐标、对齐、字体层级和留白；没有相应文字槽就不要擅自新增。
- 禁止复制原示例中的部门、作者、日期、密级等具体内容，禁止引用 `style-reference-only/` 或 `images/style-pack/` 图片路径。
- 不得新增概念图、流程图、卡片、时间线或另一套视觉装饰；封面构图应与参考页基本一致。"""
            template_heading = "# 用户示例 PPT 封面参考 SVG"
    else:
        guidance_section = ""
        design_requirements = """- 这是内置模板示意。
- 只包含标题与最少必要副标题，不要堆叠正文。
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 不要参考模板中的具体文字内容，只参考视觉风格。
- 这是封面，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主要装饰、主色、背景和字体气质；除此之外可以发挥创造力，设计符合用户要求的封面版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。"""
        template_heading = "# 模板 SVG"
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 封面 SVG，封面题目为"{page.title}"。封面参考信息如下：
{page.abstract}

# 设计要求
{design_requirements}

{guidance_section}

# 语言
生成页面文字必须使用：{language}

{template_heading}
{template}
"""


def _build_thanks_prompt(*, query, outline, save_dir, ppt_prompt, template, language, page) -> str:
    template, is_style_reference = reference_svg_for_page(page, template)
    if is_style_reference:
        guidance_section = (
            "# Agent 编写的样式与版式契约\n"
            + style_guidance_for_page(page)
        )
        if style_reference_is_shell_only(page):
            design_requirements = """- 用户示例 PPT 没有可用致谢页；下方 SVG 是从一个内容页提取的纯外壳，只用于继承背景、母版、版式、Logo、页眉页脚和已授权装饰。
- 代码不会注入该内容页的标题、正文、图片、卡片或内容区结构；禁止复刻或补回这些已删除内容。
- 必须自行生成一句大号致谢/收束语，并可增加一行很短的补充语；全部使用独立的纯文字 <text> 元素。
- 文字应放在外壳的主要空白区域内，保持清晰的主次层级、充足留白和安全边距；沿用外壳的字体、颜色和对齐气质。
- 不要为文字增加底板、卡片、边框或图标；不要生成图片、概念图、流程图、时间线和任何内容页版式。
- 不要输出全页背景、Logo、页眉页脚或固定装饰；禁止引用 `style-reference-only/` 或 `images/style-pack/` 图片路径。"""
            template_heading = "# 用户示例 PPT 内容页外壳 SVG（致谢页回退）"
        else:
            design_requirements = """- 这是用户示例 PPT 中预分配的致谢/收束页。代码会精确注入其背景、母版、版式、Logo、固定图片、固定致谢标题和装饰。
- 不要输出或重画大号致谢标题、全页背景、Logo、版权区、页眉页脚和固定装饰；只在参考页确有可替换正文槽时生成一句简短收束语。
- 可替换文字必须沿用参考页对应文字槽的坐标、对齐、字体层级和留白；没有正文槽时可以不增加任何动态元素。
- 禁止复制原示例中的业务文字、公司信息和图片路径；禁止引用 `style-reference-only/` 或 `images/style-pack/`。
- 不得新增时间线、流程、卡片、图标阵列或另一套视觉装饰；致谢页构图应与参考页基本一致。"""
            template_heading = "# 用户示例 PPT 致谢页参考 SVG"
    else:
        guidance_section = ""
        design_requirements = """- 这是内置模板示意。
- 内容简洁，主体是一句致谢/收束语，不要堆叠正文。
- 配色必须与下方模板示意 SVG 中的配色保持一致!
- 不要参考模板中的具体文字内容，只参考视觉风格。
- 这是致谢页，不是内容页；模板中关于内容页标题位置、标题字体字号、main-content-safe-area、content-placeholder、正文安全区等要求不适用于本页。
- 需要保留同套 PPT 的主要视觉装饰与识别特征，包括主色、背景和字体气质；除此之外可以发挥创造力，设计符合用户要求的致谢/收束页版式。
- 不要为了遵守内容页模板而强行使用内容页标题栏布局。"""
        template_heading = "# 模板 SVG"
    return f"""
{_svg_prompt_header()}

# 当前任务
撰写一张 PPT 致谢页 SVG。用户的原始需求如下：
{query}

PPT 整体大纲如下：
{outline}

# 设计要求
{design_requirements}

{guidance_section}

# 语言
生成页面文字必须使用：{language}

{template_heading}
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
