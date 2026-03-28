from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.state import CoverThanksPagesState
from core.ppt_generator.thought_to_ppt.page_generators.cover_thanks_pages_generator.node import (
    get_cover_thanks_pages_node,
    generate_thanks_node,
    generate_cover_node
)

workflow = StateGraph(CoverThanksPagesState)

workflow.add_node("get_cover_thanks_pages", get_cover_thanks_pages_node)
workflow.add_node("generate_thanks", generate_thanks_node)
workflow.add_node("generate_cover", generate_cover_node)

workflow.add_edge(START, "get_cover_thanks_pages")
workflow.add_edge("get_cover_thanks_pages", "generate_thanks")
workflow.add_edge("get_cover_thanks_pages", "generate_cover")
workflow.add_edge("generate_thanks", END)
workflow.add_edge("generate_cover", END)

generate_cover_thanks_pages_app = workflow.compile()
