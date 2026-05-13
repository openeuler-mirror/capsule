import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class CoverThanksPagesState(TypedDict):
    query: str
    save_dir: str
    ppt_prompt: str
    language: str
    template: str  # HTML 模板内容
    outline: List[PPTPage]

    cover_page: Optional[PPTPage]
    thanks_page: Optional[PPTPage]
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]
