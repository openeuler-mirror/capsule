import operator
from typing import Annotated, List, Optional, Any, Dict

try:
    from typing_extensions import Literal, TypedDict
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import Literal, TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult


class PPTWorkerState(TypedDict):
    """传递给页面生成节点的输入状态 (Map步骤用)"""
    generate_ppt_prompt: str  # 直接生成PPT的提示词
    index: int  # 生成PPT的页码索引
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT用到的公共提示词

    # 循环内部状态
    html_content: Optional[str]  # 生成PPT的HTML内容
    iteration: int  # 生成过程的迭代次数（老路径计数）
    action: Literal["generate", "regenerate", "modify", "finish"]  # 老路径生成状态

    # 输出
    final_file_path: Optional[str]  # 生成PPT的HTML的路径
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表

    # VLM 视觉审阅路径状态
    vlm_iteration: int  # VLM judge/modify 迭代次数
    screenshot_path: Optional[str]  # 最近一次截图路径
    judge_result: Optional[Dict[str, Any]]  # 最近一次 judge 结构化结果
    vlm_judge_history: Optional[List[Dict[str, Any]]]  # 压缩后的 VLM 审阅历史
    best_html_content: Optional[str]  # 历史最佳版本的 HTML
    best_file_path: Optional[str]  # 历史最佳版本的文件路径
    best_severity: Optional[Literal["none", "minor", "critical"]]  # 历史最佳版本的严重度
    best_score: Optional[float]  # 历史最佳版本的视觉评分
    best_issue_count: Optional[int]  # 历史最佳版本的问题数量
