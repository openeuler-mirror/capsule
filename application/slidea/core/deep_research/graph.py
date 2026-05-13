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

research_workflow = StateGraph(state_schema=ResearchState, input_schema=ResearchInputSchema)

research_workflow.add_node("initializer", initializer_node)
research_workflow.add_node("plan", plan_node)
research_workflow.add_node("selector", tree_selector_node)
research_workflow.add_node("processor", processor_node)
research_workflow.add_node("reporter", reporter_node)

research_workflow.add_edge(START, "initializer")
research_workflow.add_edge("initializer", "plan")
research_workflow.add_edge("plan", "selector")

research_workflow.add_conditional_edges(
    "selector",
    main_router,
    {"processor": "processor", "reporter": "reporter", "selector": "selector"}
)

research_workflow.add_edge("processor", "selector")
research_workflow.add_edge("reporter", END)

research_app = research_workflow.compile()
