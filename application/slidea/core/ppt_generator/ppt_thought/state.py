from pydantic import BaseModel, Field
from typing import TypedDict, List, Literal


class ParseQuery(BaseModel):
    valid: bool = Field(
        ...,
        description="是否为合理的PPT请求"
    )
    audience: str = Field(
        ...,
        description="PPT受众（例如：公司高管/部门同事/投资机构/学术评委等），没有明确提及则为''",
    )
    topic: str = Field(
        ...,
        description="PPT的主题，不需要太过具体，没有提供则为''"
    )
    goal: str = Field(
        ...,
        description="PPT的目标（例如：获取批准/争取资源/工作汇报/项目推介等），不需要太过具体，没有明确提及则为''",
    )
    urls: list = Field(
        ..., description="用户提供的文件路径和网页链接列表，没有提供则为[]"
    )
    missing_info: str = Field(
        ..., description="如果PPT的受众，主题、目标缺失，提示用户补充信息（给出推荐选项），否则为''"
    )


class ResearchMode(BaseModel):
    mode: Literal["skip", "simple", "deep"] = Field(
        ...,
        description="""
        选择下一步的信息搜集步骤（只针对写作内容，不涉及视觉设计）
        skip： 以下场景不需要检索：1、科普类、幼儿类等内容浅显场景；2、用户已经提供了详细的写作内容；3、用户要求只根据指定的参考文档和链接编写
        simple：用户要求写作的内容通过简单的搜索即可获取全面的信息
        deep：用户明确要求洞察，且用户要求写作的内容有很高的广度和深度要求，但是当PPT写作页数<10页时，不选择该模式
        """
    )
    queries: list = Field(
        ..., 
        description="""
        返回需要进一步搜索或洞察的关键词(<=5个)列表
        生成的关键词要契合主题，精确，明确标明具体的从属或约束关系，并且要素齐全，不能太过宽泛，避免产生歧义，
        """
    )
    research_query: str = Field(
        ..., 
        description="""
        如果mode为deep，从用户需求中提取需要写作内容的具体要求，作为deep research的输入，尽量保持需求中对内容的原始描述，不丢失关键信息，
        否则返回''
        """
    )
    reason: str = Field(..., description="选择该模式的原因")


class ThoughtState(TypedDict):
    request: str  # 用户原始请求
    messages: List[str]  # 对话历史（用于支持交互）

    raw_content: str  # 用户指定的文件/网页内容
    parsed_requirements: ParseQuery  # 解析出的结构：受众、主题、目的等
    interaction_count: int
    invalid_reseaon: str

    research_mode: Literal["skip", "simple", "deep"]
    queries: list # 搜索关键词
    search_results: str  # 搜索结果
    research_request: str  # Deep Research描述
    deep_report: str  # Deep Research 报告内容
    report_file: str  # Deep Research 报告路径

    thought: str  # 最终生成的PPT思路
    references: str  # 所有的参考信息