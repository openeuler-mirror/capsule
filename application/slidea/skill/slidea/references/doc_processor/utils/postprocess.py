"""后处理工具: 图片嵌图 + structured.md 组装 + 文件 I/O 工具函数。

供 agent 在 preprocess() 完成后执行步骤 8(嵌图)和步骤 9(组装)。
"""

import asyncio
import hashlib
import json
import os
from typing import Any

from langchain_core.messages import HumanMessage

from core.utils.llm import llm_invoke, ModelRoute, InvokeOptions


# ── 文件 I/O 工具 ────────────────────────────────────────

def get_file_hash(file_path: str) -> str:
    if os.path.isfile(file_path):
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    return hashlib.md5(file_path.encode("utf-8")).hexdigest()


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: str) -> Any:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def load_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()



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
                    ModelRoute.DEFAULT, [HumanMessage(content=prompt)],
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
                    }, work_node="place_images"),
                )
            except Exception:
                return
            try:
                for item in res.get("scores", []):
                    path = item.get("image")
                    score = item.get("score", 0)
                    if path is None:
                        continue
                    scored.append({"path": path, "score": float(score)})
            except Exception:
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
    images = load_json(images_path) if os.path.isfile(images_path) else []
    if not images:
        return []
    outline = load_json(outline_path) or {}
    topic = outline.get("topic", "")
    # 构造全局内容描述: topic + 各章节标题+摘要
    chapters_desc = ""
    for ch in outline.get("chapters", []):
        chapters_desc += f"- {ch.get('title', '')}: {ch.get('writing_desc', '')}\n"
    full_topic = f"{topic}\n\n文档章节概要:\n{chapters_desc}" if chapters_desc else topic
    result = await score_images(full_topic, images)
    if result:
        save_json(output_path, result)
    return result
