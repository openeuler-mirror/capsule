from langgraph.graph import StateGraph, START, END

from core.deep_research.state import ResearchState, ResearchInputSchema
from core.deep_research.node import (
    initializer_node,
    plan_node,
    tree_selector_node,
    processor_node,
    reporter_node,
    main_router
)

workflow = StateGraph(state_schema=ResearchState, input_schema=ResearchInputSchema)

workflow.add_node("initializer", initializer_node)
workflow.add_node("plan", plan_node)
workflow.add_node("selector", tree_selector_node)
workflow.add_node("processor", processor_node)
workflow.add_node("reporter", reporter_node)

workflow.add_edge(START, "initializer")
workflow.add_edge("initializer", "plan")
workflow.add_edge("plan", "selector")

workflow.add_conditional_edges(
    "selector",
    main_router,
    {"processor": "processor", "reporter": "reporter", "selector": "selector"}
)

workflow.add_edge("processor", "selector")
workflow.add_edge("reporter", END)

research_app = workflow.compile()
