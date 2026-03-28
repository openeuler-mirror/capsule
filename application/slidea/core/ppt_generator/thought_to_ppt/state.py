import operator
from typing import Annotated, List, Optional
from enum import IntEnum
import json

from pydantic import BaseModel
from typing_extensions import TypedDict, Literal


class PageType(IntEnum):
    """页面类型枚举"""
    CONTENT = 1  # 普通页
    TOC = 2  # 目录
    SEPARATOR = 3  # 分隔页
    COVER_THANKS = 4  # 封面页/封底致谢页


class PPTPage(BaseModel):
    """定义大纲中单页的数据结构"""
    title: str  # 标题
    abstract: str   # PPT页面内容的摘要
    type: PageType  # 1: 普通页, 2: 目录, 3: 分隔页, 4: 封面页/封底致谢页
    index: int = 0  # 页码
    reference_doc: str = ""  # 该页PPT内容所对应的原始文档内容
    reference_images: list = []  # 该页PPT内容所对应的图片列表

    def __str__(self) -> str:
        page_dict = self.model_dump(include={'index', 'title', 'abstract', 'type'})

        # 手动将 type 的值转换为枚举的名称
        page_dict['type'] = self.type.name

        # ensure_ascii=False 确保中文字符不会被转义
        return json.dumps(page_dict, ensure_ascii=False)

    def __repr__(self) -> str:
        return self.__str__()


class GeneratedPageResult(TypedDict):
    """单个页面生成的返回结果"""
    index: int  # 页码
    file_path: str  # 生成的PPT页面文件路径
    status: Literal["success", "fail"]  # 生成状态


class PPTState(TypedDict):
    """全局状态"""
    query: str  # 用户输入的原始要求
    ori_doc: str  # 用于生成PPT的原始文档
    is_markdown_doc: bool  # 原始文档是否为Markdown格式
    outline: List[PPTPage]  # PPT大纲
    save_dir: str  # PPT保存目录
    topic: str  # PPT主题
    html_template_name: str  # 模板名称
    html_template: str  # HTML 模板内容
    ppt_prompt: str  # 生成PPT的提示词
    language: str  # 生成PPT的语言

    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表
    htmls: list  # 生成PPT的HTML文件路径列表
    final_pdf_path: Optional[str]  # 生成PPT的PDF文件路径
    final_pptx_path: Optional[str]  # 生成PPT的PPTX文件路径


class InputSchema(TypedDict):
    query: str  # 用户输入的原始要求
    ori_doc: str  # 用于生成PPT的原始文档
    is_markdown_doc: bool  # 原始文档是否为Markdown格式
    html_template_name: Optional[str]  # 模板名称
