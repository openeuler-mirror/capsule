import operator
from typing import Annotated, List, Optional
try:
    from typing_extensions import TypedDict
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import TypedDict

try:
    from pydantic import BaseModel, Field as pydantic_field
except ImportError:  # pragma: no cover - minimal fallback for test environments
    _FIELD_UNSET = object()

    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_json_schema(cls):
            return {"title": cls.__name__, "type": "object"}

    def pydantic_field(default=_FIELD_UNSET, *, default_factory=None, **_kwargs):
        if default_factory is not None:
            return default_factory()
        if default is _FIELD_UNSET:
            return ...
        return default

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class ImageQueries(BaseModel):
    need_search_image: List[str] = pydantic_field(default_factory=list, description="网络搜图的搜索关键词。")
    need_ai_image: List[str] = pydantic_field(default_factory=list, description="AI生图的Prompt。")
    need_formula: List[str] = pydantic_field(
        default_factory=list,
        description="display 数学公式的 LaTeX 源码，例如 \\\\frac{a}{b}。",
    )


class ImageScoreResult(BaseModel):
    img_description: str = pydantic_field(description="图片描述。")
    score: float = pydantic_field(description="图片适合度评分。")


class ContentPagesState(TypedDict):
    query: str
    outline: List[PPTPage]
    save_dir: str
    ppt_prompt: str
    language: str
    template: str  # SVG 模板内容

    content_pages: Optional[List[PPTPage]]
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class ImageScore(TypedDict):
    img_description: str
    score: float
    size: str
    image_path: str


class ContentWorkerState(TypedDict):
    query: str
    outline: List[PPTPage]
    save_dir: str
    ppt_prompt: str
    language: str
    template: str  # SVG 模板内容

    relevant_material: Optional[str]
    reference_images: Optional[List[str]]
    reference_image_descriptions: Optional[dict[str, str]]
    need_search_image: Optional[List[str]]
    need_ai_image: Optional[List[str]]
    need_formula: Optional[List[str]]
    formula_image_paths: Optional[List[str]]
    formula_image_sizes: Optional[dict[str, tuple[int, int]]]
    formula_image_latex: Optional[dict[str, str]]
    img_content: Optional[str]
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]

    content_page: PPTPage
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class ImgScoreWorkerState(TypedDict):
    relevant_material: Optional[str]
    image_path: str
    image_description: Optional[str]
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]
