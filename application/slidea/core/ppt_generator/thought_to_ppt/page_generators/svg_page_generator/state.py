import operator
from typing import Annotated, List, Optional

try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class SVGPagesState(TypedDict):
    query: str
    outline: List[PPTPage]
    save_dir: str
    svg_prompt: str
    svg_spec_lock: str
    svg_template: str
    language: str

    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class SVGWorkerState(TypedDict):
    query: str
    outline: List[PPTPage]
    save_dir: str
    svg_prompt: str
    svg_spec_lock: str
    svg_template: str
    language: str
    page: PPTPage

    svg_content: Optional[str]
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]
