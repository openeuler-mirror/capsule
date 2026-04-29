from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.state import PPTWorkerState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.node import (
    generate_ppt_page_node,
    modify_ppt_page_node,
    ratio_evaluator_node,
    vlm_judge_node,
    vlm_modify_node,
    ppt_submitter_node,
    route_after_generate,
    route_after_judge,
    route_page,
)

workflow = StateGraph(PPTWorkerState)

workflow.add_node("generate_ppt_page", generate_ppt_page_node)
workflow.add_node("modify_ppt_page", modify_ppt_page_node)
workflow.add_node("ratio_evaluator_node", ratio_evaluator_node)
workflow.add_node("vlm_judge", vlm_judge_node)
workflow.add_node("vlm_modify", vlm_modify_node)
workflow.add_node("ppt_submitter", ppt_submitter_node)

workflow.add_edge(START, "generate_ppt_page")

workflow.add_conditional_edges(
    "generate_ppt_page",
    route_after_generate,
    {
        "VLM": "vlm_judge",
        "RATIO": "ratio_evaluator_node",
    },
)

# 老路径（无 VLM）：generate ↔ modify ↔ ratio_evaluator → submitter
workflow.add_edge("modify_ppt_page", "ratio_evaluator_node")
workflow.add_conditional_edges(
    "ratio_evaluator_node",
    route_page,
    {
        "GENERATE": "generate_ppt_page",
        "MODIFY": "modify_ppt_page",
        "FINISH": "ppt_submitter",
    },
)

# 新路径（有 VLM）：vlm_judge ↔ vlm_modify → submitter
workflow.add_conditional_edges(
    "vlm_judge",
    route_after_judge,
    {
        "MODIFY": "vlm_modify",
        "FINISH": "ppt_submitter",
    },
)
workflow.add_edge("vlm_modify", "vlm_judge")

workflow.add_edge("ppt_submitter", END)

generate_ppt_page_app = workflow.compile()
