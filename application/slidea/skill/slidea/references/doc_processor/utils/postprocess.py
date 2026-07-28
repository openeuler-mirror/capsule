import asyncio
import json
import logging
import os

from langchain_core.messages import HumanMessage

from core.utils.llm import llm_invoke, InvokeOptions

logger = logging.getLogger(__name__)


async def score_images(topic: str, images: list, batch_size: int = 20,
                       max_concurrency: int = 5) -> list[dict]:
    """Score images against overall document content (topic + outline), return sorted list.

    Images are no longer matched per-chapter (they are not embedded in structured.md).
    Instead, globally evaluate each image's relevance to the PPT topic / overall document
    content and return top-N (default 20) as the PPT image pool.

    Each item: {path, description, score}; higher score = more relevant.
    """
    if not images:
        return []

    sem = asyncio.Semaphore(max_concurrency)
    scored: list[dict] = []

    async def _score_batch(batch: list) -> None:
        async with sem:
            images_desc = "\n".join(
                f"{im['path']}: {im.get('description', '')}" for im in batch
            )
            prompt_lines = [
                f"Score each image's relevance to the PPT topic: {topic}",
                "Rules:",
                "- Directly relevant, contains key info (data/architecture/flow) -> 7-10",
                "- Indirectly relevant, has reference value -> 4-6",
                "- Irrelevant or no information value -> 0-3",
                'Strict JSON output: {"scores": [{"image": path, "score": float}]}',
                f"Images:\n{images_desc}",
            ]
            prompt = "\n".join(prompt_lines)
            try:
                res = await llm_invoke(
                    [HumanMessage(content=prompt)],
                    InvokeOptions(json_schema={
                        "type": "object",
                        "properties": {
                            "scores": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "image": {"type": "string"},
                                        "score": {"type": "number"},
                                    },
                                    "required": ["image", "score"],
                                },
                            },
                        },
                        "required": ["scores"],
                    }),
                )
            except Exception as ex:
                logger.warning("llm_invoke failed during image scoring: %s", ex)
                return
            try:
                for item in res.get("scores", []):
                    path = item.get("image")
                    score = item.get("score", 0)
                    if path is None:
                        continue
                    scored.append({"path": path, "score": float(score)})
            except Exception as ex:
                logger.warning("failed to parse image scoring results: %s", ex)
                return

    for start_i in range(0, len(images), batch_size):
        batch = images[start_i:start_i + batch_size]
        await _score_batch(batch)

    best: dict[str, dict] = {}
    path_to_desc = {im["path"]: im.get("description", "") for im in images}
    for item in scored:
        path = item["path"]
        if path not in best or item["score"] > best[path]["score"]:
            best[path] = item
    result = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:30]
    for item in result:
        item["description"] = path_to_desc.get(item["path"], "")
    return result


async def score_images_from_files(outline_path: str, images_path: str,
                                   output_path: str,
                                   enable_vlm: bool = True) -> list[dict]:
    """自包含入口:读 outline_new.json + images.json -> 全局打分 -> 写 doc_images.json。

    Args:
        outline_path: outline_new.json 路径(读取 topic 用)。
        images_path: images.json 路径。
        output_path: doc_images.json 输出路径。
        enable_vlm: 若为 False,直接返回空列表(与 preprocess 的 enable_vlm 开关一致)。

    无图或 enable_vlm=False 时直接返回空列表。
    """
    if not enable_vlm:
        return []
    images: list = []
    if os.path.isfile(images_path):
        with open(images_path, "r", encoding="utf-8") as f:
            images = json.load(f) or []
    if not images:
        return []
    outline: dict = {}
    if os.path.isfile(outline_path):
        with open(outline_path, "r", encoding="utf-8") as f:
            outline = json.load(f) or {}
    topic = outline.get("topic", "")
    # 构造全局内容描述: topic + 各章节标题+摘要
    chapters_desc = ""
    for ch in outline.get("chapters", []):
        chapters_desc += f"- {ch.get('title', '')}: {ch.get('writing_desc', '')}\n"
    full_topic = f"{topic}\n\n文档章节概要:\n{chapters_desc}" if chapters_desc else topic
    result = await score_images(full_topic, images)
    if result:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    return result
