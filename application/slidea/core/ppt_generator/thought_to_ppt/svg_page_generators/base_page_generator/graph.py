from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.state import SVGWorkerState
from core.ppt_generator.thought_to_ppt.svg_page_generators.base_page_generator.node import (
    generate_ppt_page_node,
    save_only_node,
    vlm_judge_node,
    vlm_modify_node,
    vlm_select_best_node,
    ppt_submitter_node,
    route_after_generate,
    route_after_judge,
)

workflow = StateGraph(SVGWorkerState)

workflow.add_node("generate_ppt_page", generate_ppt_page_node)
workflow.add_node("save_only", save_only_node)
workflow.add_node("vlm_judge", vlm_judge_node)
workflow.add_node("vlm_modify", vlm_modify_node)
workflow.add_node("vlm_select_best", vlm_select_best_node)
workflow.add_node("ppt_submitter", ppt_submitter_node)

workflow.add_edge(START, "generate_ppt_page")

workflow.add_conditional_edges(
    "generate_ppt_page",
    route_after_generate,
    {
        "VLM": "vlm_judge",
        "SAVE_ONLY": "save_only",
    },
)
workflow.add_edge("save_only", "ppt_submitter")

workflow.add_conditional_edges(
    "vlm_judge",
    route_after_judge,
    {
        "MODIFY": "vlm_modify",
        "SELECT_BEST": "vlm_select_best",
        "FINISH": "ppt_submitter",
    },
)
workflow.add_edge("vlm_modify", "vlm_judge")
workflow.add_edge("vlm_select_best", "ppt_submitter")

workflow.add_edge("ppt_submitter", END)

generate_ppt_page_app = workflow.compile()
