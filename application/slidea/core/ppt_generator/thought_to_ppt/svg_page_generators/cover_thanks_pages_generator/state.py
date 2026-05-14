import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class CoverThanksPagesState(TypedDict):
    query: str  # 用户的原始输入
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成 PPT 的提示词
    language: str  # 生成 PPT 的语言
    template: str  # 生成 PPT 的 SVG 模板内容
    outline: List[PPTPage]  # PPT 的整体大纲

    cover_page: Optional[PPTPage]  # 正在生成的封面页
    thanks_page: Optional[PPTPage]  # 正在生成的封底页
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]
