from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.state import SEPPagesState
from core.ppt_generator.thought_to_ppt.page_generators.sep_pages_generator.node import (
    get_sep_pages_node,
    generate_sep_template_node,
    generate_sep_page_node
)


def route_sep_pages(state: SEPPagesState):
    """route sep pages"""
    if len(state.get("sep_pages", [])) == 0:
        return END
    return "generate_sep_template"


def assign_workers(state: SEPPagesState):
    """Assign a worker to each section in the plan"""
    pages = state["sep_pages"][1:]

    return [Send("generate_sep_page",
                 {
                     "ppt_prompt": state["ppt_prompt"],
                     "save_dir": state["save_dir"],
                     "language": state["language"],
                     "outline": state["outline"],
                     "sep_template": state["sep_template"],
                     "sep_page": page
                 }
                 ) for page in pages]


workflow = StateGraph(SEPPagesState)

workflow.add_node("get_sep_pages", get_sep_pages_node)
workflow.add_node("generate_sep_template", generate_sep_template_node)
workflow.add_node("generate_sep_page", generate_sep_page_node)

workflow.add_edge(START, "get_sep_pages")
workflow.add_conditional_edges(
    "get_sep_pages",
    route_sep_pages,
    {"generate_sep_template": "generate_sep_template", END: END}
)
workflow.add_conditional_edges(
    "generate_sep_template",
    assign_workers,
    ["generate_sep_page"]
)
workflow.add_edge("generate_sep_page", END)

generate_sep_pages_app = workflow.compile()
