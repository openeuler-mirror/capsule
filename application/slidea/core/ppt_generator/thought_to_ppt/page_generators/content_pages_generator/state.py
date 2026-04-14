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
    need_search_image: list = pydantic_field(default_factory=list, description="网络搜图的搜索关键词。")
    need_ai_image: list = pydantic_field(default_factory=list, description="AI生图的Prompt。")


class ImageScoreResult(BaseModel):
    img_description: str = pydantic_field(description="图片描述。")
    score: float = pydantic_field(description="图片适合度评分。")


class ContentPagesState(TypedDict):
    query: str  # 用户的原始输入
    outline: List[PPTPage]  # PPT的整体大纲
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT的提示词
    language: str  # 生成PPT的语言
    html_template: str  # 生成PPT的模板

    content_pages: Optional[List[PPTPage]]  # PPT中需要生成的内容页列表
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表


class ImageScore(TypedDict):
    img_description: str  # 图片描述
    score: float  # 图片适合度评分
    size: str  # 图片尺寸
    image_path: str  # 图片路径


class ContentWorkerState(TypedDict):
    query: str  # 用户的原始输入
    outline: List[PPTPage]  # PPT的整体大纲
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT的提示词
    language: str  # 生成PPT的语言
    html_template: str  # 生成PPT的模板

    relevant_material: Optional[str]  # 生成PPT页相关的背景材料
    reference_images: Optional[List[str]]  # 生成PPT页相关的原始素材中的图片列表
    reference_image_descriptions: Optional[dict[str, str]]  # 图片路径到描述的映射
    need_search_image: Optional[List[str]]  # 生成PPT页相关的网络图片列表
    need_ai_image: Optional[List[str]]  # 生成PPT页相关的网络图片列表
    img_content: Optional[str]  # 生成PPT页相关的图片地址以及描述内容
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]  # 生成的PPT页面结果列表

    content_page: PPTPage  # 正在生成的PPT页
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表


class ImgScoreWorkerState(TypedDict):
    relevant_material: Optional[str]  # 生成PPT页相关的背景材料
    image_path: str  # 评分的图片路径
    image_description: Optional[str]  # 图片已有描述
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]  # 图片评分列表
