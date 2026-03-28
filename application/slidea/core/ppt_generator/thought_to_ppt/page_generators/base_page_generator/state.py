import operator
from typing import Annotated, List, Optional

from typing_extensions import Literal, TypedDict

from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult


class PPTWorkerState(TypedDict):
    """传递给页面生成节点的输入状态 (Map步骤用)"""
    generate_ppt_prompt: str  # 直接生成PPT的提示词
    index: int  # 生成PPT的页码索引
    save_dir: str  # 保存目录
    ppt_prompt: str  # 生成PPT用到的公共提示词

    # 循环内部状态
    html_content: Optional[str]  # 生成PPT的HTML内容
    iteration: int  # 生成过程的迭代次数
    action: Literal["generate", "regenerate", "modify", "finish"]  # 生成状态

    # 输出
    final_file_path: Optional[str]  # 生成PPT的HTML的路径
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]  # 生成的PPT页面结果列表
