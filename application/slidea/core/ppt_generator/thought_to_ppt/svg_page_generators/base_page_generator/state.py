import operator
from typing import Annotated, List, Optional, Any, Dict

try:
    from typing_extensions import Literal, NotRequired, TypedDict
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import Literal, NotRequired, TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult, PPTPage


class SVGWorkerState(TypedDict):
    """传递给 SVG 单页生成节点的输入状态。"""
    generate_ppt_prompt: str  # 直接生成 SVG 页面的提示词
    index: int  # 生成 PPT 的页码索引
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成 PPT 用到的公共提示词
    page: PPTPage  # 当前页对象（决定 svg 文件名）

    # 循环内部状态
    content: Optional[str]  # 生成的 SVG 代码

    # 输出
    final_file_path: Optional[str]  # 生成 SVG 的路径
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的 PPT 页面结果列表

    # VLM 视觉审阅路径状态
    vlm_iteration: NotRequired[int]  # VLM judge/modify 迭代次数
    screenshot_path: NotRequired[Optional[str]]  # 最近一次截图路径
    judge_result: NotRequired[Optional[Dict[str, Any]]]  # 最近一次 judge 结构化结果
    vlm_judge_history: NotRequired[Optional[List[Dict[str, Any]]]]  # 压缩后的 VLM 审阅历史
    vlm_candidates: NotRequired[Optional[List[Dict[str, Any]]]]  # 可供最终横向选择的 VLM 候选版本
    vlm_selection_record: NotRequired[Optional[Dict[str, Any]]]  # 最终选择记录
    best_content: NotRequired[Optional[str]]  # 历史最佳版本的 SVG
    best_file_path: NotRequired[Optional[str]]  # 历史最佳版本的文件路径
    best_severity: NotRequired[Optional[Literal["none", "minor", "critical"]]]  # 历史最佳版本的严重度
    best_issue_count: NotRequired[Optional[int]]  # 历史最佳版本的问题数量
