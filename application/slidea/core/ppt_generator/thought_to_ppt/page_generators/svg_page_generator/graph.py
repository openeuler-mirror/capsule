from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator.node import generate_svg_page_node
from core.ppt_generator.thought_to_ppt.page_generators.svg_page_generator.state import SVGPagesState, SVGWorkerState


svg_worker_workflow = StateGraph(SVGWorkerState)
svg_worker_workflow.add_node("generate_svg_page", generate_svg_page_node)
svg_worker_workflow.add_edge(START, "generate_svg_page")
svg_worker_workflow.add_edge("generate_svg_page", END)
svg_page_worker_app = svg_worker_workflow.compile()


def get_svg_pages_node(state: SVGPagesState):
    return {}


def assign_svg_workers(state: SVGPagesState):
    return [
        Send(
            "svg_page_worker",
            {
                "query": state["query"],
                "outline": state["outline"],
                "save_dir": state["save_dir"],
                "svg_prompt": state["svg_prompt"],
                "svg_spec_lock": state["svg_spec_lock"],
                "svg_template": state["svg_template"],
                "language": state["language"],
                "page": page,
                "svg_content": None,
                "generated_pages": [],
            },
        )
        for page in state["outline"]
    ]


svg_pages_workflow = StateGraph(SVGPagesState)
svg_pages_workflow.add_node("get_svg_pages", get_svg_pages_node)
svg_pages_workflow.add_node("svg_page_worker", svg_page_worker_app)
svg_pages_workflow.add_edge(START, "get_svg_pages")
svg_pages_workflow.add_conditional_edges("get_svg_pages", assign_svg_workers, ["svg_page_worker"])
svg_pages_workflow.add_edge("svg_page_worker", END)

generate_svg_pages_app = svg_pages_workflow.compile()
