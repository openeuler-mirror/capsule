import operator

from typing import List, Annotated, TypedDict, Dict, Optional
from pydantic import BaseModel, Field


class Chapter(BaseModel):
    header: str = Field(description="章节标题")
    description: str = Field(description="章节内容详细介绍")
    content: str = Field(description="章节相关内容")
    idx: int = Field(description="索引")
    allocated_pages: int = Field(description="分配给该章节的PPT页数", default=0)


class UserQuery(BaseModel):
    title: Optional[str] = Field(default="", description="PPT名称",)
    target_page_count: int = Field(..., description="PPT页数")
    language: Optional[str] = Field(default="中文", description="使用语言")
    reasoning: Optional[str] = Field(default="", description="推理原因")


class ChapterDetail(BaseModel):
    title: str = Field(..., description="章节名称")
    description: str = Field(..., description="章节描述")


class SlideDetail(BaseModel):
    title: str = Field(..., description="PPT标题")
    abstract: str = Field(..., description="内容摘要或简介")
    type: Optional[int] = Field(default=1, description="页面类型")


class ChapterItem(BaseModel):
    header: str = Field(..., description="章节标题")
    allocated_pages: int = Field(..., description="分配页数")
    idx: int = Field(..., description="章节编号")


class CoverItem(BaseModel):
    cover_title: str = Field(..., description="封面标题")
    cover_abstract: str = Field(..., description="封面摘要")
    toc_intro: str = Field(..., description="目录页描述")


class OutlineState(TypedDict):
    user_query: str
    input_text: str
    is_markdown_doc: bool
    summary_text: str = ""
    target_page_count: int = 0
    chapters: List[Chapter] = []
    generated_slides_map: Annotated[Dict[int, list], operator.ior] = {}
    final_output: List[Dict] = []
    title: str = ""
    language: str = "中文"


class SectionState(TypedDict):
    chapter: Chapter
    query: str
