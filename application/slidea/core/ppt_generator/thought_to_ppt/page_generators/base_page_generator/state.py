import operator
from typing import Annotated, List, Optional, Any, Dict

try:
    from typing_extensions import Literal, NotRequired, TypedDict
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import Literal, NotRequired, TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult


class PPTWorkerState(TypedDict):
    """传递给 HTML 单页生成节点的输入状态 (Map步骤用)"""
    generate_ppt_prompt: str  # 直接生成PPT的提示词
    index: int  # 生成PPT的页码索引
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT用到的公共提示词

    # 循环内部状态
    content: Optional[str]  # 生成的页面 HTML 代码
    iteration: int  # 生成过程的迭代次数（老路径计数）
    action: Literal["generate", "regenerate", "modify", "finish"]  # 老路径生成状态

    # 输出
    final_file_path: Optional[str]  # 生成PPT的HTML的路径
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表

    # VLM 视觉审阅路径状态
    vlm_iteration: NotRequired[int]
    screenshot_path: NotRequired[Optional[str]]
    judge_result: NotRequired[Optional[Dict[str, Any]]]
    vlm_judge_history: NotRequired[Optional[List[Dict[str, Any]]]]
    vlm_candidates: NotRequired[Optional[List[Dict[str, Any]]]]
    vlm_selection_record: NotRequired[Optional[Dict[str, Any]]]
    best_content: NotRequired[Optional[str]]
    best_file_path: NotRequired[Optional[str]]
    best_severity: NotRequired[Optional[Literal["none", "minor", "critical"]]]
    best_issue_count: NotRequired[Optional[int]]
