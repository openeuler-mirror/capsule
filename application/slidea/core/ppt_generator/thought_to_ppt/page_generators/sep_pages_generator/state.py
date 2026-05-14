import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class SEPPagesState(TypedDict):
    save_dir: str
    ppt_prompt: str
    language: str
    template: str  # HTML 模板内容
    outline: List[PPTPage]

    sep_pages: Optional[List[PPTPage]]
    sep_template: Optional[str]
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class SEPWorkerState(TypedDict):
    save_dir: str
    ppt_prompt: str
    language: str
    outline: List[PPTPage]

    sep_page: PPTPage
    sep_template: str
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]
