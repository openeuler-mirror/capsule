import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class SEPPagesState(TypedDict):
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT的提示词
    language: str  # 生成PPT的语言
    html_template: str  # 生成PPT的模板
    outline: List[PPTPage]  # PPT的整体大纲

    sep_pages: Optional[List[PPTPage]]  # PPT中需要生成的章节分割页列表
    sep_template: Optional[str]  # 生成SEP的模板
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表


class SEPWorkerState(TypedDict):
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT的提示词
    language: str  # 生成PPT的语言
    outline: List[PPTPage]  # PPT的整体大纲

    sep_page: PPTPage  # 正在生成的章节分割页
    sep_template: str   # 生成SEP的模板
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表
