from langgraph.graph import StateGraph, END, START
from langgraph.types import StreamWriter

from core.ppt_generator.ppt_thought.state import ThoughtState
from core.ppt_generator.ppt_thought.node import (
    get_reference_node,
    parse_query_node,
    ask_user_node,
    generate_thought_node,
    gather_content_router_node,
    check_research_mode_node,
    simple_search_node,
    deep_research_node,
)

workflow = StateGraph(ThoughtState)


workflow.add_node("parse_query", parse_query_node)
workflow.add_node("ask_user", ask_user_node)
workflow.add_node("get_reference", get_reference_node)
workflow.add_node("content_router", gather_content_router_node)
workflow.add_node("check_research_mode", check_research_mode_node)
workflow.add_node("do_simple_search", simple_search_node)
workflow.add_node("do_deep_research", deep_research_node)
workflow.add_node("generate_thought", generate_thought_node)


def check_validation(state: ThoughtState):
    if state["is_valid"]:
        return "get_reference"
    return END


def check_clarification(state: ThoughtState, writer: StreamWriter):
    parsed = state.get("parsed_requirements")
    if not parsed:
        return "invalid"

    if state.get("interaction_count", 0) == 0:
        if parsed.missing_info:
            writer({"step": "提示用户补充信息"})
            return "ask_user"
    elif not parsed.valid:
        # Keep asking follow-up questions instead of ending with incomplete state.
        writer({"step": "继续追问关键信息"})
        return "ask_user"

    return "get_reference"


def route_research(state: ThoughtState):
    mode = state.get("research_mode", "simple")
    if mode == "skip":
        return "generate_thought"
    elif mode == "deep":
        return "do_deep_research"
    else:
        return "do_simple_search"


workflow.add_edge(START, "parse_query")
workflow.add_conditional_edges(
    "parse_query",
    check_clarification,
    {"ask_user": "ask_user", "get_reference": "get_reference", "invalid": END}
)

workflow.add_edge("ask_user", "parse_query")
workflow.add_edge("get_reference", "content_router")
workflow.add_edge("content_router", "check_research_mode")

workflow.add_conditional_edges(
    "check_research_mode",
    route_research,
    {
        "generate_thought": "generate_thought",
        "do_simple_search": "do_simple_search",
        "do_deep_research": "do_deep_research"
    }
)

workflow.add_edge("do_simple_search", "generate_thought")
workflow.add_edge("do_deep_research", "generate_thought")
workflow.add_edge("generate_thought", END)


thought_app = workflow.compile()
