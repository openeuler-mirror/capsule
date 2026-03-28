from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.state import PPTState, InputSchema
from core.ppt_generator.thought_to_ppt.node import generate_outline_node, generate_pages_node


workflow = StateGraph(state_schema=PPTState, input_schema=InputSchema)

workflow.add_node("generate_outline", generate_outline_node)
workflow.add_node("generate_pages", generate_pages_node)

workflow.add_edge(START, "generate_outline")
workflow.add_edge("generate_outline", "generate_pages")
workflow.add_edge("generate_pages", END)

generate_slides_app = workflow.compile()
