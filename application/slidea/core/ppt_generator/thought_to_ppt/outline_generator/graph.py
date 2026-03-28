from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from core.ppt_generator.thought_to_ppt.outline_generator.state import OutlineState
from core.ppt_generator.thought_to_ppt.outline_generator.node import (
    get_chapters_node,
    analyze_input_node,
    simple_generate_node,
    plan_and_allocate_node,
    generate_chapter_slides_node,
    assemble_chapters_node
)


def route_logic(state: OutlineState):
    """ 路由策略 """
    page_count = state["target_page_count"]
    chap_num = len(state["chapters"])
    if chap_num == 0 or page_count < chap_num:
        return "simple_generate"
    return "plan_and_allocate"


def split_to_map_logic(state: OutlineState):
    """分发任务到并行处理"""
    return [Send("generate_chapter_slides", {"chapter": c, "query": state["user_query"]}) for c in state["chapters"]]


workflow = StateGraph(OutlineState)

# Nodes
workflow.add_node("get_chapters", get_chapters_node)
workflow.add_node("analyze_input", analyze_input_node)
workflow.add_node("simple_generate", simple_generate_node)
workflow.add_node("plan_and_allocate", plan_and_allocate_node)
workflow.add_node("generate_chapter_slides", generate_chapter_slides_node)
workflow.add_node("assemble_chapters", assemble_chapters_node)

# Edges
workflow.add_edge(START, "analyze_input")
workflow.add_edge("analyze_input", "get_chapters")
workflow.add_conditional_edges(
    "get_chapters",
    route_logic,
    {
        "simple_generate": "simple_generate",
        "plan_and_allocate": "plan_and_allocate",
    },
)
workflow.add_edge("simple_generate", END)
workflow.add_conditional_edges(
    "plan_and_allocate", split_to_map_logic, ["generate_chapter_slides"]
)
workflow.add_edge("generate_chapter_slides", "assemble_chapters")
workflow.add_edge("assemble_chapters", END)

generate_outline_app = workflow.compile()
