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


# ── 图片嵌图(步骤 8) ────────────────────────────────────

def _doc_hash_from_chunk_id(chunk_id: str) -> str:
    """从 chunk_id (<doc_hash>_c<seq>) 提取 doc_hash。"""
    if "_c" in chunk_id:
        return chunk_id.rsplit("_c", 1)[0]
    return chunk_id


async def place_images(outline: dict, images: list, batch_size: int = 10,
                       max_concurrency: int = 5) -> dict:
    """按来源文件(source_hash)分组,每组图片独立匹配到相关章节。

    每个文件的图片只和与该文件相关的章节(chunk_ids 的 doc_hash 匹配)一起送 LLM,
    避免一次性全量匹配导致正确性下降。单组图片超过 batch_size 时分批执行。
    所有批次通过 Semaphore(max_concurrency) 并发执行。
    """
    if not images:
        return outline
    chapters = outline.get("chapters", [])
    if not chapters:
        return outline

    # 预计算每章关联的 doc_hash 集合
    chapter_doc_hashes = []
    for ch in chapters:
        doc_hashes = {_doc_hash_from_chunk_id(cid) for cid in ch.get("chunk_ids", [])}
        chapter_doc_hashes.append(doc_hashes)

    # 按来源文件分组图片
    groups: dict[str, list] = {}
    for im in images:
        key = im.get("source_hash") or ""
        groups.setdefault(key, []).append(im)

    sem = asyncio.Semaphore(max_concurrency)
    tasks: list = []

    async def _match_batch(batch: list, chapters_desc: str) -> None:
        async with sem:
            images_desc = "\n".join(f"{im['path']}: {im.get('description', '')}" for im in batch)
            prompt = (
                "把每张图片匹配到最契合的章节(返回该章节的序号)。每图恰好归入一章节;无契合则不放入。"
                "严格输出 JSON: {\"placement\": [{\"image\": path, \"chapter\": index}]}。\n"
                f"章节:\n{chapters_desc}\n图片:\n{images_desc}"
            )
            try:
                res = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)],
                                       InvokeOptions(json_schema={
                                           "type": "object",
                                           "properties": {
                                               "placement": {
                                                   "type": "array",
                                                   "items": {
                                                       "type": "object",
                                                       "properties": {
                                                           "image": {"type": "string"},
                                                           "chapter": {"type": "integer"},
                                                       },
                                                       "required": ["image", "chapter"],
                                                   },
                                               },
                                           },
                                           "required": ["placement"],
                                       }, work_node="place_images"))
            except Exception:
                return
            try:
                for item in res.get("placement", []):
                    idx = item.get("chapter")
                    path = item.get("image")
                    if idx is None or path is None:
                        continue
                    if 0 <= idx < len(chapters):
                        chapters[idx]["images"] = list(
                            dict.fromkeys(chapters[idx].get("images", []) + [path])
                        )
            except Exception:
                return

    for source_hash, group_images in groups.items():
        if source_hash:
            relevant_indices = [i for i, dh in enumerate(chapter_doc_hashes) if source_hash in dh]
            if not relevant_indices:
                continue
        else:
            # 无来源信息的图片,用全部章节兜底
            relevant_indices = list(range(len(chapters)))

        chapters_desc = "\n".join(
            f"{i}: {chapters[i]['title']} - {chapters[i].get('writing_desc', '')}"
            for i in relevant_indices
        )

        # 单组图片超过 batch_size 则分批
        for start in range(0, len(group_images), batch_size):
            batch = group_images[start:start + batch_size]
            tasks.append(_match_batch(batch, chapters_desc))

    if tasks:
        await asyncio.gather(*tasks)
    return outline


async def place_images_from_files(outline_path: str, images_path: str,
                                   enable_vlm: bool = True) -> dict:
    """自包含入口:读 outline_new.json + images.json -> 匹配图->章节 -> 回写 outline_new.json。

    Args:
        outline_path: outline_new.json 路径。
        images_path: images.json 路径。
        enable_vlm: 若为 False,直接返回原 outline 不做图片匹配(与 preprocess 的 enable_vlm 开关一致)。
            默认 True 保持向后兼容;调用方应根据 preprocess 返回的 vlm_enabled 传值。

    无图或 enable_vlm=False 时直接返回原 outline。
    """
    if not enable_vlm:
        return load_json(outline_path) or {}
    outline = load_json(outline_path)
    if not outline:
        return {}
    images = load_json(images_path) if os.path.isfile(images_path) else []
    if not images:
        return outline
    outline = await place_images(outline, images)
    save_json(outline_path, outline)
    return outline


# ── structured.md 组装(步骤 9) ──────────────────────────

def assemble_structured_md(outline_path: str, chapters_dir: str, output_path: str, topic: str = "") -> str:
    """读 outline_new.json + 章节文件,机械组装 structured.md。

    章节文件从 chapters_dir 读取(按 index 排序),图片按 outline.chapters[i].images 嵌入。
    断点: structured.md 已存在则跳过。
    """
    if os.path.isfile(output_path):
        return output_path
    outline = load_json(outline_path)
    if not outline:
        return ""
    real_topic = outline.get("topic", topic)
    chapters = outline.get("chapters", [])
    chapter_files = sorted(
        [f for f in os.listdir(chapters_dir) if f.startswith("chapter_") and f.endswith(".md")],
        key=lambda f: int(f[len("chapter_"):-3])
    )
    md_parts = [f"# {real_topic}", ""]
    for fname in chapter_files:
        idx = int(fname[len("chapter_"):-3])
        body = load_text(os.path.join(chapters_dir, fname))
        md_parts.append(body.rstrip())
        if idx < len(chapters):
            for img in chapters[idx].get("images", []):
                md_parts.append("")
                md_parts.append(f"![相关图]({img})")
        md_parts.append("")
    save_text(output_path, "\n".join(md_parts).strip() + "\n")
    return output_path
