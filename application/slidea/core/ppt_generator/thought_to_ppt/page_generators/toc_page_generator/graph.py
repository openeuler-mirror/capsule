from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.state import PPTState
from core.ppt_generator.thought_to_ppt.page_generators.toc_page_generator.node import generate_toc_page_node

workflow = StateGraph(PPTState)

workflow.add_node("generate_toc_page", generate_toc_page_node)

workflow.add_edge(START, "generate_toc_page")
workflow.add_edge("generate_toc_page", END)

generate_toc_page_app = workflow.compile()
