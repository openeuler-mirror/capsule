from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.state import PPTState
from core.ppt_generator.thought_to_ppt.page_generators.svg_node import (
    prepare_svg_generation_context_node,
    generate_svg_pages_node,
    svg_synthesizer_node,
    svg_quality_check_node,
    svg_finalize_node,
    svgs2pptx_node,
)


workflow = StateGraph(state_schema=PPTState)

workflow.add_node("prepare_svg_generation_context", prepare_svg_generation_context_node)
workflow.add_node("generate_svg_pages", generate_svg_pages_node)
workflow.add_node("svg_synthesizer", svg_synthesizer_node)
workflow.add_node("svg_quality_check", svg_quality_check_node)
workflow.add_node("svg_finalize", svg_finalize_node)
workflow.add_node("svgs2pptx", svgs2pptx_node)

workflow.add_edge(START, "prepare_svg_generation_context")
workflow.add_edge("prepare_svg_generation_context", "generate_svg_pages")
workflow.add_edge("generate_svg_pages", "svg_synthesizer")
workflow.add_edge("svg_synthesizer", "svg_quality_check")
workflow.add_edge("svg_quality_check", "svg_finalize")
workflow.add_edge("svg_finalize", "svgs2pptx")
workflow.add_edge("svgs2pptx", END)

generate_svg_slides_app = workflow.compile()
