from typing import List, Dict, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class TaskStatus:
    PENDING = "pending"             # 等待处理
    IN_PROGRESS = "in_progress"     # 正在处理（父节点等待子节点）
    COMPLETED = "completed"         # 已完成


class ReferenceItem(TypedDict):
    summary: str
    content: str
    source: str


class TaskNode(TypedDict):
    id: str
    title: str
    description: str
    content: str
    status: str
    context: str
    parent_id: Optional[str]
    children_ids: List[str]
    search_loop: int
    queries: List[str]
    depth: int
    references: List[ReferenceItem]
    important: bool


class ResearchInputSchema(TypedDict):
    research_request: str
    raw_content: Optional[str]


class ResearchState(TypedDict):
    research_request: str
    raw_content: str

    research_depth: int
    references: List[ReferenceItem]
    title: str
    queries: List[str]
    root_id: str
    task_map: Dict[str, TaskNode]
    current_task_ids: List[str]
    preprocess: bool = False

    deep_report: Optional[str]
    report_file: Optional[str]


class ChapterItem(BaseModel):
    title: str = Field(..., description="章节标题，例如'背景调研与技术选型")
    description: str = Field(
        ...,
        description="""详细描述本章的写作要求，包含需要编写的内容和重点，注意不要丢失全文写作需求中的要求。
                表明当前章节是否全文的重点，同时分析是否需要进一步拆分，拆分的思路如何，但是拆分思路不能和其他章节出现重复
                采用明确清晰的方式列出需要陈述的内容，避免使用'例如'，'等'语句来进行省略表达
                请在描述中明确标明当前章节或概念的从属及约束关系，避免歧义
                章节的写作要求不能丢失洞察任务的具体需求""",
    )
    important: Optional[bool] = Field(
        default=False,
        description="该章节是否为重点章节，注意，综述性的章节不能为重点章节",
    )


class DecomposeItem(BaseModel):
    title: str = Field(..., description="子章节标题，例如'背景调研与技术选型")
    description: str = Field(..., description="该子章节的具体描述，包含需要编写的内容和重点")
    reason: Optional[str] = Field(default="", description="分解为该子章节的理由，是否和全文其他章节出现重复")


class DecisionItem(BaseModel):
    type: str = Field(..., description="任务类型")
    queries: Optional[list] = Field(default=[], description="搜索内容列表")
    reason: Optional[str] = Field(default="", description="决策理由")


class SearchItem(BaseModel):
    need_search: bool = Field(..., description="如果需要继续搜索True，否则False")
    queries: Optional[list] = Field(
        default=[],
        description="""如果need_search为True，返回需要搜索的关键词，否则为[]
            生成的关键词需要精确，要素齐全，不能太过宽泛，避免产生歧义
            关键词要全面涵盖需求的各个方面，不要重复搜索已经搜索过的内容""",
    )
    reason: Optional[str] = Field(default="", description="当前判断的原因")