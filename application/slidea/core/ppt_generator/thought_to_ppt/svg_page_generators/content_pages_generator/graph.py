import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.state import (
    ContentPagesState,
    ContentWorkerState,
    ImgScoreWorkerState,
    ImageScore,
)
from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult
from core.ppt_generator.thought_to_ppt.svg_page_generators.content_pages_generator.node import (
    get_content_pages_node,
    extract_relevant_doc_node,
    generate_image_queries_node,
    get_web_ai_images_node,
    get_final_images_node,
    get_img_score_node,
    extend_relevant_material_node,
    generate_content_page_node,
)


class WorkerOutput(TypedDict):
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class ImageScoreOutput(TypedDict):
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]


img_scoring_workflow = StateGraph(ImgScoreWorkerState, output_schema=ImageScoreOutput)
img_scoring_workflow.add_node("get_img_score", get_img_score_node)
img_scoring_workflow.add_edge(START, "get_img_score")
img_scoring_workflow.add_edge("get_img_score", END)
img_scoring_app = img_scoring_workflow.compile()


def assign_img_score_workers(state: ContentWorkerState):
    reference_images = state["reference_images"]
    reference_image_descriptions = state.get("reference_image_descriptions") or {}
    # 公式已在 get_final_images_node 里预填 img_scores（score=10.0），跳过 VLM worker。
    formula_paths = set(state.get("formula_image_paths") or [])
    candidate_images = [p for p in (reference_images or []) if p not in formula_paths]
    if not candidate_images:
        return "extend_relevant_material"

    # 每页只提示一次：VLM 未配置时下游 worker 会走兜底打分（不调 VLM API）。
    # 仍然需要 worker 来收集图片尺寸、路径和上游传来的描述（Tavily 搜图描述 / AI 生图 prompt），
    # 下游 extend_relevant_material_node 据此把可用图片告诉生图 LLM，所以不能整段跳过。
    # 真正缺失的是 VLM 看图打分能力——所有候选图都给固定 5.0 分，排序退化为按原顺序取前 N。
    from core.utils.logger import logger
    from core.utils.llm import can_vlm_invoke
    if not can_vlm_invoke():
        n_images = len(candidate_images)
        logger.info(
            f"VLM not configured; {n_images} reference image(s) will skip VLM scoring. "
            "Each image gets a fixed score of 5.0; path/dimensions/upstream descriptions are still collected. "
            "Ranking falls back to original order."
        )

    return [Send("img_scoring_worker",
                 {
                     "relevant_material": state["relevant_material"],
                     "image_path": image_path,
                     "image_description": reference_image_descriptions.get(image_path, ""),
                 }) for image_path in candidate_images]


content_worker_workflow = StateGraph(ContentWorkerState, output_schema=WorkerOutput)

content_worker_workflow.add_node("extract_relevant_doc", extract_relevant_doc_node)
content_worker_workflow.add_node("generate_image_queries", generate_image_queries_node)
content_worker_workflow.add_node("get_web_ai_images", get_web_ai_images_node)
content_worker_workflow.add_node("get_final_images", get_final_images_node)

content_worker_workflow.add_node("img_scoring_worker", img_scoring_app)

content_worker_workflow.add_node("extend_relevant_material", extend_relevant_material_node)
content_worker_workflow.add_node("generate_content_page", generate_content_page_node)

content_worker_workflow.add_edge(START, "extract_relevant_doc")
content_worker_workflow.add_edge("extract_relevant_doc", "generate_image_queries")
content_worker_workflow.add_edge("generate_image_queries", "get_web_ai_images")
content_worker_workflow.add_edge("get_web_ai_images", "get_final_images")

content_worker_workflow.add_conditional_edges(
    "get_final_images",
    assign_img_score_workers,
    ["img_scoring_worker", "extend_relevant_material"],
)
content_worker_workflow.add_edge("img_scoring_worker", "extend_relevant_material")
content_worker_workflow.add_edge("extend_relevant_material", "generate_content_page")
content_worker_workflow.add_edge("generate_content_page", END)

content_page_worker_app = content_worker_workflow.compile()


def assign_workers(state: ContentPagesState):
    pages = state["content_pages"]
    return [Send("content_page_worker",
                 {
                     "query": state["query"],
                     "outline": state["outline"],
                     "save_dir": state["save_dir"],
                     "ppt_prompt": state["ppt_prompt"],
                     "language": state["language"],
                     "template": state["template"],
                     "content_page": page,
                     "reference_image_descriptions": {},
                     "img_scores": [],
                     "generated_pages": [],
                 }) for page in pages]


content_pages_workflow = StateGraph(ContentPagesState)

content_pages_workflow.add_node("get_content_pages", get_content_pages_node)
content_pages_workflow.add_node("content_page_worker", content_page_worker_app)

content_pages_workflow.add_edge(START, "get_content_pages")
content_pages_workflow.add_conditional_edges(
    "get_content_pages",
    assign_workers,
    ["content_page_worker"],
)
content_pages_workflow.add_edge("content_page_worker", END)

generate_content_pages_app = content_pages_workflow.compile()
