import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from core.utils.config import output_files_dir
from core.utils.crawl import get_contents
from core.utils.llm import (
    llm_invoke,
    vlm_invoke,
    can_vlm_invoke_route,
    ModelRoute,
    InvokeOptions,
)
from core.utils.logger import logger


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


# ── 文件收集 ──────────────────────────────────────────────
_SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown",
    ".pdf",
    ".doc", ".docx",
    ".xls", ".xlsx",
    ".ppt", ".pptx",
    ".html", ".htm",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".avif", ".tiff", ".tif",
}


def _collect_files(path: str) -> list[str]:
    """路径是目录则收集该目录及其一层子目录下的支持文件,否则直接返回该路径。"""
    if not os.path.isdir(path):
        return [path]
    result = []
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        if entry.is_file():
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in _SUPPORTED_EXTENSIONS:
                result.append(entry.path)
        elif entry.is_dir():
            for sub_entry in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if sub_entry.is_file():
                    ext = os.path.splitext(sub_entry.name)[1].lower()
                    if ext in _SUPPORTED_EXTENSIONS:
                        result.append(sub_entry.path)
    if not result:
        logger.warning("_collect_files 目录 {} 中未找到可解析的文件", path)
    return result


# ── Chunk 拆分 ───────────────────────────────────────────

class _StructuredChunker:
    def __init__(self, max_chunk_chars: int = 32768, overlap_ratio: float = 0.05):
        self.max_chunk_chars = max_chunk_chars
        self.overlap_ratio = overlap_ratio

    def chunk(self, parsed_doc: dict) -> list:
        text = parsed_doc.get("text", "") or ""
        if not text.strip():
            return []
        doc_hash = parsed_doc["hash"]
        images = parsed_doc.get("images", [])
        if self._looks_like_markdown(text):
            return self._chunk_by_heading(text, doc_hash, images)
        return self._chunk_by_chars(text, doc_hash, images)

    @staticmethod
    def _looks_like_markdown(text: str) -> bool:
        return len(re.findall(r"^#{1,3} ", text, flags=re.MULTILINE)) >= 2

    def _chunk_by_heading(self, text: str, doc_hash: str, images: list) -> list:
        parts = re.split(r"(?m)(?=^#{2,3} )", text)
        parts = [p for p in parts if p.strip()]
        if len(parts) <= 1:
            return self._chunk_by_chars(text, doc_hash, images)
        
        # 第一步：先处理所有part为临时块（保留位置信息）
        temp_blocks = []
        offset = 0
        for part in parts:
            part = part.strip()
            part_start = text.find(part, offset)
            if part_start < 0:
                part_start = offset
            part_end = part_start + len(part)
            temp_blocks.append({
                "text": part,
                "start": part_start,
                "end": part_end
            })
            offset = part_end
        
        # 第二步：合并连续的临时块到接近max_chunk_chars
        chunks = []
        seq = 0
        current_merged = []
        current_length = 0
        current_start = None
        
        for block in temp_blocks:
            block_len = block["end"] - block["start"]
            # 如果加入当前块不超过限制，或者当前合并为空（避免单个块超过限制的情况）
            if current_length + block_len <= self.max_chunk_chars or not current_merged:
                current_merged.append(block)
                current_length += block_len
                if current_start is None:
                    current_start = block["start"]
            else:
                # 生成合并后的块
                merged_text = "\n".join([b["text"] for b in current_merged])
                chunks.append({
                    "id": f"{doc_hash}_c{seq}",
                    "text": merged_text,
                    "source_anchor": f"{doc_hash}_c{seq}",
                    "char_range": [current_start, current_merged[-1]["end"]],
                })
                seq += 1
                # 重置为当前块
                current_merged = [block]
                current_length = block_len
                current_start = block["start"]
        
        # 处理最后一个合并块
        if current_merged:
            merged_text = "\n".join([b["text"] for b in current_merged])
            chunks.append({
                "id": f"{doc_hash}_c{seq}",
                "text": merged_text,
                "source_anchor": f"{doc_hash}_c{seq}",
                "char_range": [current_start, current_merged[-1]["end"]],
            })
            seq += 1
        
        return chunks

    def _char_slices(self, text: str):
        limit = self.max_chunk_chars
        if len(text) <= limit:
            yield text, 0
            return
        overlap = int(limit * self.overlap_ratio)
        step = max(1, limit - overlap)
        i = 0
        while i < len(text):
            yield text[i:i + limit], i
            i += step

    def _chunk_by_chars(self, text: str, doc_hash: str, images: list) -> list:
        chunks = []
        seq = 0
        for piece_text, offset in self._char_slices(text):
            chunks.append({
                "id": f"{doc_hash}_c{seq}",
                "text": piece_text,
                "source_anchor": f"{doc_hash}_c{seq}",
                "char_range": [offset, offset + len(piece_text)],
            })
            seq += 1
        return chunks


def _chunk_and_save(parsed_dir: str, chunks_dir: str) -> dict:
    """读取 parsed_docs/ 下所有文档,切分 chunk 并落盘到 chunks/。

    若 _chunk_index.json 已存在则跳过。
    返回: {"chunks_total": N, "chunk_ids": [...]}
    """
    index_path = os.path.join(chunks_dir, "_chunk_index.json")
    if os.path.isfile(index_path):
        existing = load_json(index_path)
        if isinstance(existing, list) and existing:
            return {"chunks_total": len(existing), "chunk_ids": [e["chunk_id"] for e in existing]}

    os.makedirs(chunks_dir, exist_ok=True)
    chunker = _StructuredChunker()
    index = []
    parsed_files = sorted(
        os.path.join(parsed_dir, f) for f in os.listdir(parsed_dir) if f.endswith(".json")
    )
    for pf in parsed_files:
        pdoc = load_json(pf)
        if not pdoc or not pdoc.get("hash"):
            continue
        doc_hash = pdoc["hash"]
        source_path = pdoc.get("source_path", "")
        chunks = chunker.chunk(pdoc)
        for ch in chunks:
            chunk_file = os.path.join(chunks_dir, f"{ch['id']}.json")
            save_json(chunk_file, {
                "id": ch["id"],
                "text": ch["text"],
                "source_anchor": ch["source_anchor"],
                "char_range": ch.get("char_range", [0, 0]),
                "images": ch.get("images", []),
                "doc_hash": doc_hash,
                "source_path": source_path,
            })
            seq = int(ch["id"].rsplit("_c", 1)[1]) if "_c" in ch["id"] else 0
            index.append({
                "chunk_id": ch["id"],
                "doc_hash": doc_hash,
                "chunk_seq": seq,
                "source_path": source_path,
                "char_range": ch.get("char_range", [0, 0]),
            })
    save_json(index_path, index)
    return {"chunks_total": len(index), "chunk_ids": [e["chunk_id"] for e in index]}


# ── 图片过滤 ─────────────────────────────────────────────

# 暴露给测试 patch 的别名
shutil_copy = shutil.copy
os_path_exists = os.path.exists


# VLM 可直接接受字节(base64)的格式;其余格式需经 PIL 转 PNG 后再喂给 VLM。
_VLM_NATIVE_EXTS = {"png", "jpg", "jpeg"}
# 声明给 VLM 的 MIME 子类型(仅对 _VLM_NATIVE_EXTS 生效)。
_VLM_MIME = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}

# 每次 VLM 调用同时判断的图片数量。
_BATCH_SIZE = 3


def _encode_image(image_path: str) -> str | None:
    """将图片编码为 VLM 可接受的 data URL。编码失败返回 None。"""
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "png"
    if ext in _VLM_NATIVE_EXTS:
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except Exception:
            return None
        mime = _VLM_MIME[ext]
    else:
        # svg/avif/tiff/webp/bmp/gif 等多数 VLM 端点不接受,用 PIL 转 PNG 再喂。
        try:
            from PIL import Image as PILImage
            import io
            with PILImage.open(image_path) as im:
                im = im.convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None
        mime = "png"
    return f"data:image/{mime};base64,{b64}"


def _build_batch_prompt(images: list[dict], topic: str) -> str:
    """构造批量图片相关性判断的 prompt。"""
    n = len(images)
    source_lines = []
    for i, img in enumerate(images, 1):
        source_name = os.path.basename(img.get("source_path", "")) or "未知文档"
        source_lines.append(f"{i}. {source_name}")
    return (
        f"判断以下 {n} 张图片是否与 PPT 主题「{topic}」相关。\n"
        "图片按序号 1~" + str(n) + " 排列，请对每张图分别判断。\n"
        "图片来源文档:\n" + "\n".join(source_lines) + "\n"
        "需要详细描述每张图片内容(文字/图表/场景/元素)。\n"
        "判断规则(按优先级从高到低,命中任一即照此判定):\n"
        "1. 图片质量/有效性: 若图片是纯黑/纯白/空白页/加载失败/渲染异常,或完全无法辨识任何文字、图表、场景或元素,relevant 一律置为 false(这类图片无任何信息价值,不应保留)。\n"
        "2. 无实际意义: 若图片仅为纯色块、单一像素、极低分辨率模糊不清、或无可读信息的装饰性内容,relevant 置为 false。\n"
        "3. 主题相关性(仅当 1、2 均未命中、图片内容有效时才考虑): "
        "除非可以肯定图片与 PPT 主题完全无关,否则 relevant 置为 true (即内容有效但主题相关性相关或无法确定时,一律置为 true)。\n"
        "reason 需说明命中的规则编号及依据。\n"
        "严格输出 JSON 数组,长度必须为 " + str(n) + ",按序号对应:\n"
        '[{"relevant": boolean, "description": "详细描述", "reason": "判断理由"}, ...]'
    )


_SINGLE_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "description": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["relevant", "description", "reason"],
}

_BATCH_JUDGE_SCHEMA = {
    "type": "array",
    "items": _SINGLE_JUDGE_SCHEMA,
}


async def _judge_batch(images: list[dict], topic: str) -> list[dict | None]:
    """批量判断图片相关性(最多 _BATCH_SIZE 张)。

    Args:
        images: 每项含 path、source_path;已预编码为 data_url。
    Returns:
        长度与 images 一致的列表,每项为判断 dict 或 None(失败)。
    """
    n = len(images)
    content_parts: list[dict] = [{"type": "text", "text": _build_batch_prompt(images, topic)}]
    for img in images:
        data_url = img.get("data_url")
        if not data_url:
            # 编码失败的图不应进入 batch,防御性检查
            return [None] * n
        content_parts.append({"type": "image_url", "image_url": {"url": data_url}})

    msg = HumanMessage(content=content_parts)
    try:
        result = await vlm_invoke(
            ModelRoute.DEFAULT,
            [msg],
            InvokeOptions(json_schema=_BATCH_JUDGE_SCHEMA, work_node="image_relevance"),
        )
    except Exception:
        return [None] * n

    if not isinstance(result, list):
        return [None] * n

    # 对齐长度: 截断或补 None
    aligned: list[dict | None] = []
    for i in range(n):
        if i < len(result) and isinstance(result[i], dict):
            aligned.append(result[i])
        else:
            aligned.append(None)
    return aligned


async def _judge_single(img: dict, topic: str) -> dict | None:
    """单图 fallback: 编码 + VLM 调用,失败返回 None。"""
    data_url = img.get("data_url") or _encode_image(img.get("path", ""))
    if not data_url:
        return None
    source_name = os.path.basename(img.get("source_path", "")) or "未知文档"
    prompt = (
        f"判断下面这张图是否与 PPT 主题「{topic}」相关。"
        f"该图片来自文档: {source_name}。\n"
        "需要详细描述图片内容(文字/图表/场景/元素)。\n"
        "判断规则(按优先级从高到低,命中任一即照此判定):\n"
        "1. 图片质量/有效性: 若图片是纯黑/纯白/空白页/加载失败/渲染异常,或完全无法辨识任何文字、图表、场景或元素,relevant 一律置为 false(这类图片无任何信息价值,不应保留)。\n"
        "2. 无实际意义: 若图片仅为纯色块、单一像素、极低分辨率模糊不清、或无可读信息的装饰性内容,relevant 置为 false。\n"
        "3. 主题相关性(仅当 1、2 均未命中、图片内容有效时才考虑): "
        "除非可以肯定图片与 PPT 主题完全无关,否则 relevant 置为 true (即内容有效但主题相关性相关或无法确定时,一律置为 true)。\n"
        "reason 需说明命中的规则编号及依据。\n"
        "严格输出 JSON: {\"relevant\": boolean, \"description\": \"详细描述\", \"reason\": \"判断理由\"}"
    )
    msg = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ])
    try:
        return await vlm_invoke(
            ModelRoute.DEFAULT,
            [msg],
            InvokeOptions(json_schema=_SINGLE_JUDGE_SCHEMA, work_node="image_relevance"),
        )
    except Exception:
        return None


async def _filter_images_by_topic(all_images: list, topic: str, output_dir: str,
                                  max_concurrency: int = 5) -> list:
    if not can_vlm_invoke_route(ModelRoute.DEFAULT):
        return []
    os.makedirs(output_dir, exist_ok=True)
    total = len(all_images)
    logger.info("图片收集完成: 共 {} 张图片，来自 {} 个解析文档", total, len(set(img.get("source_path", "") for img in all_images)))
    logger.info("开始图片相关性过滤（批量 {} 张/次，并发 {}），当前主题: {}", _BATCH_SIZE, max_concurrency, topic)

    # ── 预处理: 过滤无效/小图 + 编码 ──
    valid_images: list[dict] = []
    for idx, img in enumerate(all_images, 1):
        path = img.get("path", "")
        source_path = img.get("source_path", "未知文档")
        if not path or not os.path.isfile(path):
            logger.warning("[{}/{}] 跳过无效图片: {} (路径不存在)", idx, total, path)
            continue
        file_size = os.path.getsize(path)
        if file_size < 10 * 1024:
            logger.debug("[{}/{}] 跳过小尺寸图片: {} (大小: {}字节 < 10KB阈值)", idx, total, path, file_size)
            continue
        data_url = _encode_image(path)
        if not data_url:
            logger.warning("[{}/{}] 跳过编码失败图片: {}", idx, total, path)
            continue
        valid_images.append({**img, "data_url": data_url})

    if not valid_images:
        logger.info("图片过滤完成: 无有效图片可判断")
        return []

    # ── 分批 + 并发判断 ──
    batches = [valid_images[i:i + _BATCH_SIZE] for i in range(0, len(valid_images), _BATCH_SIZE)]
    sem = asyncio.Semaphore(max_concurrency)
    survivors: list[dict] = []

    async def _process_batch(batch_idx: int, batch: list[dict]):
        batch_start = batch_idx * _BATCH_SIZE + 1
        batch_end = min(batch_start + len(batch) - 1, len(valid_images))
        batch_label = f"{batch_start}-{batch_end}/{len(valid_images)}"

        async with sem:
            logger.info("[{}] 正在批量判断图片相关性 ({} 张)", batch_label, len(batch))
            results = await _judge_batch(batch, topic)

            # 检查是否全部失败 → fallback 单图
            if all(r is None for r in results):
                logger.warning("[{}] 批量判断全部失败,回退为单图判断", batch_label)
                single_tasks = [_judge_single(img, topic) for img in batch]
                results = await asyncio.gather(*single_tasks)

            # 处理每张图的结果
            for img, result in zip(batch, results):
                path = img.get("path", "")
                source_path = img.get("source_path", "未知文档")
                if not result:
                    logger.warning("[{}] 图片处理失败: {}，VLM返回空结果", batch_label, os.path.basename(path))
                    continue
                relevant = result.get("relevant", False)
                if relevant:
                    desc = result.get("description", "")
                    short_desc = desc[:80] + "..." if len(desc) > 80 else desc
                    logger.info("[{}] ✅ 图片相关: {}，内容: {}", batch_label, os.path.basename(path), short_desc)
                else:
                    reason = result.get("reason", "")
                    short_reason = reason[:60] + "..." if len(reason) > 60 else reason
                    logger.info("[{}] ❌ 图片无关: {}，理由: {}", batch_label, os.path.basename(path), short_reason)
                    continue

                source_hash = img.get("source_hash") or ""
                basename = os.path.basename(path)
                name = f"{source_hash}_{basename}" if source_hash else basename
                dest = os.path.join(output_dir, name)
                try:
                    shutil_copy(path, dest)
                    logger.debug("[{}] 已保存相关图片到: {}", batch_label, dest)
                except Exception as e:
                    logger.warning("[{}] 图片保存失败: {}，错误: {}", batch_label, path, str(e))
                    continue
                survivors.append({
                    "path": dest,
                    "original_path": path,
                    "source_path": source_path,
                    "source_hash": img.get("source_hash"),
                    "relevant": True,
                    "description": result.get("description", ""),
                    "reason": result.get("reason", ""),
                })

    await asyncio.gather(*[_process_batch(i, batch) for i, batch in enumerate(batches)])
    logger.info("图片过滤完成: 共处理 {} 张有效图片，幸存 {} 张相关图片", len(valid_images), len(survivors))
    return survivors


def _dedup_images_cross_doc(images: list) -> list:
    """跨文档图片去重: 按文件内容 MD5 去重，保留首次出现的图片，删除重复文件。

    单文档内部去重已在 parser.py 的 filter_images 中完成;
    此函数补足跨文档的去重——不同文档可能提取出内容完全相同的图片
    (如同一张架构图被多篇 PDF 引用)，按 MD5 去重可避免重复 VLM 调用和重复保存。
    """
    if not images:
        return images
    seen_hashes: dict[str, str] = {}
    deduped = []
    removed = 0
    for img in images:
        path = img.get("path", "")
        if not path or not os.path.isfile(path):
            continue
        file_hash = get_file_hash(path)
        if file_hash in seen_hashes:
            removed += 1
            logger.debug(
                "跨文档去重: 跳过重复图片 {} (与 {} 内容相同, hash={})",
                path, seen_hashes[file_hash], file_hash,
            )
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        seen_hashes[file_hash] = path
        deduped.append(img)
    if removed:
        logger.info(
            "跨文档去重完成: 共 {} 张图片，移除 {} 张跨文档重复图片，剩余 {} 张",
            len(images), removed, len(deduped),
        )
    return deduped


async def _filter_and_save(parsed_dir: str, images_dir: str, topic: str) -> list:
    """从 parsed_docs/ 收集图片 -> 跨文档去重 -> 尺寸闸门 + VLM 过滤 -> 保存 images.json + 复制幸存图。

    若 images.json 已存在则跳过。
    """
    images_json_path = os.path.join(images_dir, "images.json")
    if os.path.isfile(images_json_path):
        return load_json(images_json_path)

    parsed_files = [os.path.join(parsed_dir, f) for f in os.listdir(parsed_dir) if f.endswith(".json")]
    all_images = []
    for pf in parsed_files:
        pdoc = load_json(pf)
        source_path = pdoc.get("source_path", "")
        for img in pdoc.get("images", []):
            img2 = dict(img)
            img2.setdefault("source_hash", pdoc.get("hash"))
            img2.setdefault("source_path", source_path)
            all_images.append(img2)

    # 跨文档去重: 不同文档可能提取出内容相同的图片(如同一张架构图被多篇文档引用)，
    # 按文件内容 MD5 去重，保留首次出现的图片，删除重复文件。
    all_images = _dedup_images_cross_doc(all_images)

    survivors = await _filter_images_by_topic(all_images, topic, images_dir)
    save_json(images_json_path, survivors)
    return survivors


# ── 摘要生成 ─────────────────────────────────────────────

class _SummaryItem(BaseModel):
    relevant: bool = Field(default=True, description="本片段是否与主题相关。仅当内容明显与主题完全无关时置为 false;相关或无法确定时一律置为 true")
    labels: list = Field(default=[], description="本片段的核心主题标签列表,如 ['成本','趋势']")
    keywords: list = Field(default=[], description="3-8个关键词,用于后续检索关联片段")
    summary: str = Field(default="", description="结构化摘要正文(Markdown),保留关键数据/论点/论据/案例,字数为原文10%~20%")


def _build_summary_prompt(chunk: dict, topic: str) -> str:
    return (
        f"你是 PPT 内容整理助手,主题是「{topic}」。\n"
        "阅读下面的文档片段,提取所有和主题相关的核心内容,形成结构化摘要。\n"
        "输出字段说明:\n"
        "- relevant: 本片段是否与主题相关。仅当内容明显与主题完全无关时置为 false;相关或无法确定时一律置为 true\n"
        "  当 relevant=false 时,labels/keywords/summary 无需赋值,保留默认空值即可\n"
        "- labels: 本片段的核心主题标签列表,如 [\"成本\",\"趋势\"]\n"
        "- keywords: 3-8个关键词,用于后续检索关联片段\n"
        "- summary: 结构化摘要正文(Markdown),保留所有关键数据/论点/论据/案例,字数控制在原文的 10%~20%,无关内容直接过滤\n"
        f"来源文件: {os.path.basename(chunk.get('source_path', ''))}\n"
        f"文档片段:\n{chunk['text']}\n"
        "请按 JSON 格式输出上述四个字段。"
    )


async def _summarize_one(chunk: dict, topic: str) -> dict | None:
    chunk_id = chunk["id"]
    prompt = _build_summary_prompt(chunk, topic)
    msg = HumanMessage(content=prompt)
    try:
        result = await llm_invoke(
            ModelRoute.DEFAULT,
            [msg],
            InvokeOptions(json_schema=_SummaryItem.model_json_schema(), work_node="summarize"),
        )
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    relevant = bool(result.get("relevant", True))
    summary_text = str(result.get("summary", "")).strip()
    if relevant and not summary_text:
        return None
    return {
        "relevant": relevant,
        "labels": result.get("labels") or [],
        "keywords": result.get("keywords") or [],
        "summary": summary_text,
    }


async def _summarize_all(chunks_dir: str, topic: str,
                          max_concurrency: int = 5) -> dict:
    """从 chunks/_chunk_index.json 读取 chunk 列表，逐 chunk 加载->摘要->汇总到chunks/summaries.json。
    若 chunks/summaries.json 已存在则跳过已处理的chunk。
    返回：{"summaries_total": N, "failed_chunks": [...]}
    """
    sem = asyncio.Semaphore(max_concurrency)
    results = []
    failed = []
    processed_chunk_ids = set()

    chunk_index = load_json(os.path.join(chunks_dir, "_chunk_index.json"))
    if not isinstance(chunk_index, list):
        chunk_index = []

    # 读取已有的summaries.json，跳过已处理的chunk
    summaries_json_path = os.path.join(chunks_dir, "summaries.json")
    if os.path.isfile(summaries_json_path):
        existing_results = load_json(summaries_json_path)
        if isinstance(existing_results, list):
            results = existing_results
            processed_chunk_ids = {r["chunk_id"] for r in results}

    async def _one(entry: dict):
        async with sem:
            chunk_id = entry.get("chunk_id", "")
            if not chunk_id or chunk_id in processed_chunk_ids:
                return
            try:
                chunk = load_json(os.path.join(chunks_dir, f"{chunk_id}.json"))
                if not chunk:
                    failed.append(chunk_id)
                    return
                payload = await _summarize_one(chunk, topic)
                if payload is None or payload.get("relevant", True) is False:
                    failed.append(chunk_id)
                    return
                results.append({
                    "chunk_id": chunk_id,
                    "doc_hash": entry.get("doc_hash", ""),
                    "chunk_seq": entry.get("chunk_seq", 0),
                    "source_path": entry.get("source_path", ""),
                    "char_range": entry.get("char_range", [0, 0]),
                    "labels": payload.get("labels", []),
                    "keywords": payload.get("keywords", []),
                    "summary": payload.get("summary", ""),
                    "char_len": len(payload.get("summary", "")),
                })
            except Exception:
                failed.append(chunk_id)
                return

    await asyncio.gather(*[_one(e) for e in chunk_index if e.get("chunk_id") not in processed_chunk_ids])
    results.sort(key=lambda e: (e["doc_hash"], e["chunk_seq"]))
    save_json(summaries_json_path, results)
    return {"summaries_total": len(results), "failed_chunks": failed}


# ── 对外接口 ─────────────────────────────────────────────

async def preprocess(
    docs: str | list[str],
    topic: str,
    task_id: str,
    force: bool = False,
    enable_vlm: bool = False,
) -> dict:
    """文档预处理流水线: 收集解析 -> chunk 拆分 -> 图片过滤 -> 摘要生成。

    所有中间产物落到 ``output_files_dir/documents/<task_id>/`` 下。
    agent 调用前需确保: 文档全部在同一目录(零散文件先拷贝到
    ``output_files_dir/documents/<task_id>/files/``)。

    Args:
        docs: 文档所在目录路径(str)或文件路径列表(list[str])。
        topic: PPT 主题。
        task_id: 任务唯一 ID。
        force: 若为 True 且 ``<task_id>`` 目录已存在,先整体删除再重建
            (清除旧的 chunks/summaries/images 等,全新跑一遍)。
            默认 False:已存在则跳过已生成的 chunk/summary。
        enable_vlm: 是否启用图片提取与 VLM 主题相关性筛选。**默认 False(不启用)**:
            不从文档提取图片、不调 VLM、images.json 写空 -> 纯文本 PPT。
            仅当用户明确要求"用文档中的图片作为 PPT 插图"时才置 True;
            此时每张图都要调 VLM 判断,耗时会显著增加,调用前应提示用户。
            置 True 但环境未配置 VLM 时,自动降级为 False 并记录日志。

    Returns:
        关键路径信息 dict,包含:
        - task_dir: 任务目录绝对路径
        - chunks_dir / chunk_index_path
        - summaries_json_path (摘要汇总文件,落在 chunks/ 下)
        - images_json_path / images_dir
        - meta_json_path
        - chunks_total / summaries_total / images_relevant
        - failed_chunks / vlm_enabled
    """
    if not topic:
        raise ValueError("topic 不能为空")

    # 图片处理开关: 默认关闭(纯文本 PPT)。仅当调用方明确 enable_vlm=True
    # (用户要求用文档图片作插图)且环境配置了 VLM 时才真正启用;
    # 用户要开但环境没配 -> 降级关闭并记录,不报错。
    if enable_vlm and not can_vlm_invoke_route(ModelRoute.DEFAULT):
        enable_vlm = False
        logger.warning("用户请求启用 VLM 图片筛选,但环境未配置 VLM 模型,自动降级为纯文本模式")
    if enable_vlm:
        logger.info("已启用 VLM 图片提取与筛选(耗时将显著增加): task_id={}", task_id)
    else:
        logger.info("未启用 VLM 图片处理(纯文本模式): task_id={}", task_id)

    task_dir = os.path.join(output_files_dir, "documents", task_id)
    parsed_dir = os.path.join(task_dir, "parsed_docs")
    chunks_dir = os.path.join(task_dir, "chunks")
    images_dir = os.path.join(task_dir, "images")
    meta_path = os.path.join(task_dir, "meta.json")

    # force 模式: 删除已存在的整个 task_dir(连同旧 chunks/summaries/images/structured.md 等),
    # 全新重建。用于 task_id 复用、清除上一次残留(含孤儿目录)。默认 False 保留已生成产物(按文件存在性跳过)。
    if force and os.path.isdir(task_dir):
        try:
            shutil.rmtree(task_dir)
            logger.info("force=True,已删除旧任务目录: {}", task_dir)
        except Exception as e:
            logger.warning("force 删除旧目录失败(继续重建): {}", e)

    os.makedirs(task_dir, exist_ok=True)

    logger.info("预处理开始: task_id={}, topic={}, docs={}", task_id, topic, docs)

    meta = {
        "task_id": task_id,
        "topic": topic,
        "vlm_enabled": enable_vlm,
        "stages_completed": [],
        "docs_total": 0,
        "chunks_total": 0,
        "summaries_total": 0,
        "failed_chunks": [],
        "images_relevant": 0,
        "warnings": [],
    }

    # ── 步骤 1 · 收集解析 ──
    if isinstance(docs, str):
        docs = [docs]
    all_files = []
    for d in docs:
        all_files.extend(_collect_files(d))

    if not all_files:
        raise ValueError(f"未找到可解析的文档: {docs}")

    meta["docs_total"] = len(all_files)
    logger.info("收集到 {} 个文档", len(all_files))

    os.makedirs(parsed_dir, exist_ok=True)
    parse_sem = asyncio.Semaphore(3)

    async def _parse_one(fp: str):
        async with parse_sem:
            doc_hash = get_file_hash(fp)
            parsed_path = os.path.join(parsed_dir, f"{doc_hash}.json")
            if os.path.isfile(parsed_path):
                logger.info("跳过已解析文档: {}", fp)
                return
            result = await get_contents([fp], extract_images=enable_vlm, output_dir=parsed_dir)
            pdoc = {
                "hash": doc_hash,
                "text": result["text"],
                "images": result["images"],
                "source_path": fp,
                "markdown_file": result.get("markdown_file", []),
            }
            save_json(parsed_path, pdoc)
            logger.info(
                "解析文档完成: {} (hash={}, text_len={}, images={})",
                fp, doc_hash, len(result["text"]), len(result["images"]),
            )

    await asyncio.gather(*[_parse_one(fp) for fp in all_files])
    meta["stages_completed"].append("collect")
    save_json(meta_path, meta)

    # ── 短文本分流: 解析后若合并文本 < 阈值且无需图片处理 ──
    # 直接将合并裸文档写成 structured.md,跳过 chunk/summary/outline/章节写作/review,
    # 对上层(doc-process / SKILL.md)表现为黑盒: 同样产出 structured.md 并返回路径,
    # 仅通过 short_circuit=True 告知 process-doc.md 后续步骤(step 5-9)无需执行。
    # 触发条件: 合并文本 < 阈值, 且无需图片处理 ──
    #   (a) enable_vlm=False: 图片过滤本就跳过;
    #   (b) enable_vlm=True 但未解析出任何图片: 图片过滤/打分无意义, 短文本直接产出。
    structured_md_path = os.path.join(task_dir, "structured.md")
    _threshold = _StructuredChunker().max_chunk_chars * 2
    # 仅累加长度与图片数, 不拼接全文, 避免大文档时的内存占用; 触发时再读取拼接。
    merged_len = 0
    total_images = 0
    for _pf in sorted(os.listdir(parsed_dir)):
        if not _pf.endswith(".json"):
            continue
        _pdoc = load_json(os.path.join(parsed_dir, _pf))
        if not _pdoc:
            continue
        if _pdoc.get("text"):
            merged_len += len(_pdoc["text"]) + 2
        if _pdoc.get("images"):
            total_images += len(_pdoc["images"])
    if merged_len < _threshold and (not enable_vlm or total_images == 0):
        merged_text = ""
        for _pf in sorted(os.listdir(parsed_dir)):
            if not _pf.endswith(".json"):
                continue
            _pdoc = load_json(os.path.join(parsed_dir, _pf))
            if _pdoc and _pdoc.get("text"):
                merged_text += _pdoc["text"] + "\n\n"
        save_text(
            structured_md_path,
            f"# {topic}\n\n{merged_text.strip()}\n",
        )
        meta["stages_completed"].extend(["short_circuit"])
        meta["chunks_total"] = 1
        save_json(meta_path, meta)
        try:
            if os.path.isdir(parsed_dir):
                shutil.rmtree(parsed_dir)
        except Exception as ex:
            logger.warning("删除 parsed_dir 失败: {}", ex)
            pass
        logger.info(
            "短文本分流: 合并文本 {} 字符 < {}, 图片 {} 张, 直接产出 structured.md, 跳过后续步骤",
            merged_len, _threshold, total_images,
        )
        return {
            "task_dir": task_dir,
            "structured_md_path": structured_md_path,
            "chunks_dir": chunks_dir,
            "chunk_index_path": os.path.join(chunks_dir, "_chunk_index.json"),
            "summaries_json_path": os.path.join(chunks_dir, "summaries.json"),
            "images_json_path": os.path.join(images_dir, "images.json"),
            "images_dir": images_dir,
            "meta_json_path": meta_path,
            "chunks_total": 1,
            "summaries_total": 0,
            "images_relevant": 0,
            "failed_chunks": [],
            "vlm_enabled": enable_vlm,
            "short_circuit": True,
            "doc_images": [],
        }

    # ── 步骤 2 · chunk 拆分 ──
    chunk_result = _chunk_and_save(parsed_dir, chunks_dir)
    meta["chunks_total"] = chunk_result["chunks_total"]
    meta["stages_completed"].append("chunk")
    logger.info("Chunk 拆分完成: 共 {} 个 chunk", chunk_result["chunks_total"])
    save_json(meta_path, meta)

    # ── 步骤 3+4 · 图片过滤 + 摘要生成(并发) ──
    async def _do_filter():
        if enable_vlm:
            survivors = await _filter_and_save(parsed_dir, images_dir, topic)
            logger.info("图片过滤完成: 幸存 {} 张", len(survivors))
            return survivors
        else:
            save_json(os.path.join(images_dir, "images.json"), [])
            logger.info("VLM 未启用,跳过图片过滤")
            return []

    async def _do_summarize():
        result = await _summarize_all(chunks_dir, topic)
        logger.info(
            "摘要生成完成: 共 {} 条，失败 {} 条",
            result["summaries_total"], len(result["failed_chunks"]),
        )
        return result

    filter_result, summary_result = await asyncio.gather(_do_filter(), _do_summarize())

    meta["images_relevant"] = len(filter_result)
    meta["summaries_total"] = summary_result["summaries_total"]
    meta["failed_chunks"] = summary_result["failed_chunks"]
    meta["stages_completed"].extend(["filter", "summarize"])
    save_json(meta_path, meta)

    # ── 清理 parsed_docs ──
    # parsed_docs 是 collect→chunk、collect→filter 之间的中间桥梁:
    # chunk 已自带完整 text/images/source_path(自包含),幸存图已复制到 images/,
    # 下游步骤(outline/chapter/assemble)只读 chunks/ 与 summaries/、images.json,
    # 不再依赖 parsed_docs。故全部步骤完成后清除以避免孤儿目录堆积与磁盘占用。
    # 用 try 兜底:清理失败不影响已完成的产物与返回值。
    try:
        if os.path.isdir(parsed_dir):
            shutil.rmtree(parsed_dir)
            logger.info("已清理 parsed_docs: {}", parsed_dir)
    except Exception as e:
        logger.warning("清理 parsed_docs 失败(可忽略,产物已就绪): {}", e)

    # ── 返回路径信息 ──
    result = {
        "task_dir": task_dir,
        "chunks_dir": chunks_dir,
        "chunk_index_path": os.path.join(chunks_dir, "_chunk_index.json"),
        "summaries_json_path": os.path.join(chunks_dir, "summaries.json"),
        "images_json_path": os.path.join(images_dir, "images.json"),
        "images_dir": images_dir,
        "meta_json_path": meta_path,
        "chunks_total": meta["chunks_total"],
        "summaries_total": meta["summaries_total"],
        "images_relevant": meta["images_relevant"],
        "failed_chunks": meta["failed_chunks"],
        "vlm_enabled": enable_vlm,
        "structured_md_path": None,
        "short_circuit": False,
        "doc_images": [],
    }
    logger.info("预处理完成: task_dir={}", task_dir)
    return result
