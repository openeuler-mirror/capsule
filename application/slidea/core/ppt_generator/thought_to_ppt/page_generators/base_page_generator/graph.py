from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.state import PPTWorkerState
from core.ppt_generator.thought_to_ppt.page_generators.base_page_generator.node import (
    generate_ppt_page_node,
    modify_ppt_page_node,
    ratio_evaluator_node,
    ppt_submitter_node,
    route_page
)

workflow = StateGraph(PPTWorkerState)

workflow.add_node("generate_ppt_page", generate_ppt_page_node)
workflow.add_node("modify_ppt_page", modify_ppt_page_node)
workflow.add_node("ratio_evaluator_node", ratio_evaluator_node)
workflow.add_node("ppt_submitter", ppt_submitter_node)

workflow.add_edge(START, "generate_ppt_page")
workflow.add_edge("generate_ppt_page", "ratio_evaluator_node")
workflow.add_edge("modify_ppt_page", "ratio_evaluator_node")
workflow.add_conditional_edges(
    "ratio_evaluator_node",
    route_page,
    {
        "GENERATE": "generate_ppt_page",
        "MODIFY": "modify_ppt_page",
        "FINISH": "ppt_submitter"
    }
)
workflow.add_edge("ppt_submitter", END)

generate_ppt_page_app = workflow.compile()
