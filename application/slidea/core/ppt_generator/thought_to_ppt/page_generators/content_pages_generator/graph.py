import operator
from typing import Annotated, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.state import (
    ContentPagesState,
    ContentWorkerState,
    ImgScoreWorkerState,
    ImageScore
)
from core.ppt_generator.thought_to_ppt.state import GeneratedPageResult
from core.ppt_generator.thought_to_ppt.page_generators.content_pages_generator.node import (
    get_content_pages_node,
    extract_relevant_doc_node,
    generate_image_queries_node,
    get_web_ai_images_node,
    get_final_images_node,
    get_img_score_node,
    extend_relevant_material_node,
    generate_content_page_node
)


# 定义输出 Schema，只包含需要聚合的字段, 防止 query, relevant_material 等字段回传导致冲突
class WorkerOutput(TypedDict):
    generated_pages: Annotated[List[GeneratedPageResult], operator.add]


class ImageScoreOutput(TypedDict):
    img_scores: Annotated[List[Optional[ImageScore]], operator.add]


# 构建图片评分的子图
img_scoring_workflow = StateGraph(ImgScoreWorkerState, output_schema=ImageScoreOutput)
img_scoring_workflow.add_node("get_img_score", get_img_score_node)
img_scoring_workflow.add_edge(START, "get_img_score")
img_scoring_workflow.add_edge("get_img_score", END)
img_scoring_app = img_scoring_workflow.compile()


def assign_img_score_workers(state: ContentWorkerState):
    """分配图片评分任务"""
    reference_images = state["reference_images"]
    reference_image_descriptions = state.get("reference_image_descriptions") or {}
    if not reference_images:
        return "extend_relevant_material"

    return [Send("img_scoring_worker",
                 {
                     "relevant_material": state["relevant_material"],
                     "image_path": image_path,
                     "image_description": reference_image_descriptions.get(image_path, ""),
                 }
                 ) for image_path in reference_images]


# 构建内容页生成的子图 (解决 content_page 并发问题)
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
    [
        "img_scoring_worker",
        "extend_relevant_material"
    ]
)
content_worker_workflow.add_edge("img_scoring_worker", "extend_relevant_material")
content_worker_workflow.add_edge("extend_relevant_material", "generate_content_page")
content_worker_workflow.add_edge("generate_content_page", END)

content_page_worker_app = content_worker_workflow.compile()


def assign_workers(state: ContentPagesState):
    """分配页面生成任务"""
    pages = state["content_pages"]
    return [Send("content_page_worker",
                 {
                     "query": state["query"],
                     "outline": state["outline"],
                     "save_dir": state["save_dir"],
                     "ppt_prompt": state["ppt_prompt"],
                     "language": state["language"],
                     "html_template": state["html_template"],
                     "content_page": page,
                     # 初始化累加器，防止 None 错误
                     "reference_image_descriptions": {},
                     "img_scores": [],
                     "generated_pages": []
                 }
                 ) for page in pages]


# 构建主图
content_pages_workflow = StateGraph(ContentPagesState)

content_pages_workflow.add_node("get_content_pages", get_content_pages_node)
content_pages_workflow.add_node("content_page_worker", content_page_worker_app)

content_pages_workflow.add_edge(START, "get_content_pages")
content_pages_workflow.add_conditional_edges(
    "get_content_pages",
    assign_workers,
    ["content_page_worker"]
)
content_pages_workflow.add_edge("content_page_worker", END)

generate_content_pages_app = content_pages_workflow.compile()
