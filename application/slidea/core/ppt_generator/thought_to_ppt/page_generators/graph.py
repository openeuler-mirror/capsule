from langgraph.graph import StateGraph, START, END

from core.ppt_generator.thought_to_ppt.state import PPTState
from core.ppt_generator.thought_to_ppt.page_generators.node import (
    prepare_generation_context_node,
    generate_cover_thanks_pages_node,
    generate_sep_pages_node,
    generate_toc_page_node,
    generate_content_pages_node,
    ppt_synthesizer_node,
    htmls2pptx_node,
)

workflow = StateGraph(state_schema=PPTState)

workflow.add_node("prepare_generation_context", prepare_generation_context_node)
workflow.add_node("generate_cover_thanks_pages", generate_cover_thanks_pages_node)
workflow.add_node("generate_sep_pages", generate_sep_pages_node)
workflow.add_node("generate_toc_page", generate_toc_page_node)
workflow.add_node("generate_content_pages", generate_content_pages_node)
workflow.add_node("ppt_synthesizer", ppt_synthesizer_node)
workflow.add_node("htmls2pptx", htmls2pptx_node)

workflow.add_edge(START, "prepare_generation_context")
workflow.add_edge("prepare_generation_context", "generate_cover_thanks_pages")
workflow.add_edge("prepare_generation_context", "generate_sep_pages")
workflow.add_edge("prepare_generation_context", "generate_toc_page")
workflow.add_edge("prepare_generation_context", "generate_content_pages")
workflow.add_edge("generate_cover_thanks_pages", "ppt_synthesizer")
workflow.add_edge("generate_sep_pages", "ppt_synthesizer")
workflow.add_edge("generate_toc_page", "ppt_synthesizer")
workflow.add_edge("generate_content_pages", "ppt_synthesizer")
workflow.add_edge("ppt_synthesizer", "htmls2pptx")
workflow.add_edge("htmls2pptx", END)

generate_pages_app = workflow.compile()
