import os
import asyncio
from typing import List
from datetime import datetime
import numpy as np
import httpx

from langchain_core.messages import HumanMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.utils.config import settings, app_base_dir
from core.utils.llm import llm_invoke, embedding_llm, default_llm as llm
from core.utils.logger import logger
from core.deep_research.state import ReferenceItem


def _normalize_base_url(base_url: str) -> str:
    if not base_url:
        return ""
    return base_url.rstrip("/")


async def embed_text(text: str):
    """Direct embedding call to avoid langchain payload quirks."""
    model = settings.EMBEDDING_MODEL
    base_url = _normalize_base_url(settings.EMBEDDING_API_BASE_URL)
    api_key = settings.EMBEDDING_API_KEY
    missing = settings.missing_embedding_settings()
    if missing:
        raise RuntimeError(
            "Missing embedding settings: " + ", ".join(missing)
        )

    url = f"{base_url}/embeddings"
    payload = {"model": model, "input": text}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["embedding"]


WORKSPACE_DIR = os.path.join(app_base_dir, "research_workspace")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

MAX_CONTEXT_LEN = 81920
MAX_TRUNK_LEN = 32768

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=MAX_TRUNK_LEN,
    chunk_overlap=1000,
    length_function=len,
)


def source_in_ref(refs, source):
    """check if source in refs"""
    for ref in refs:
        if source == ref["source"]:
            return True
    return False


async def add_task_reference(existing_refs: List[ReferenceItem], datas: list, topic: str):
    """add datas to ref"""
    tasks_data = []

    for item in datas:
        source = item["source"]
        if source_in_ref(existing_refs, source):
            continue

        if item.get("raw_content", False):
            chunks = [item["content"]]
            dont_summary = True
        else:
            chunks = text_splitter.split_text(item["content"])
            dont_summary = False

        for content in chunks[:5]: # 避免无意义的超大文件
            if len(content) < 100:
                continue

            tasks_data.append({
                "chunk": content,
                "source": source,
                "query": item.get("query", ""),
                "dont_summary": dont_summary,
            })

    if not tasks_data:
        return []

    # 最多处理30个任务
    tasks_data = tasks_data[:30]
    logger.debug(f"  -> 共切分为 {len(tasks_data)} 个片段，准备并发执行...")
    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")
    sem = asyncio.Semaphore(5)

    async def process_single_chunk(data):
        async with sem:
            chunk = data["chunk"]

            summary_prompt = f"""
当前时间为 {cur_time}
从下面的内容中总结出主题相关的内容

# 输出要求
首先标明当前内容和什么主题相关
保留详尽的观点、细节和数据、示例
不要采用概括或省略的方式总结，而是将所有细节全部列出
不要遗漏关键概念和数据
必须完全来自于参考内容，不要编造或虚构内容
要求是从参考内容总结相关详细事实，并非生成完整的洞察报告
不相关的主题以及未明确提及的细节不用回答，不用推断，直接略过即可
如果和所有主题均不相关，直接仅输出“不相关”即可

# 相关主题如下：
{data['query']}

# 原始需求如下（总结内容时可参考）
{topic}

# 下面是参考内容：
{chunk}
"""
            if data["dont_summary"]:
                summary = chunk
            else:
                summary = await llm_invoke(llm, [HumanMessage(content=summary_prompt)])
                if not summary:
                    summary = ""

            # normalize summary for embedding
            if summary is None:
                summary = ""
            if not isinstance(summary, str):
                try:
                    summary = str(summary)
                except Exception:
                    return {}
            summary = summary.strip()
            if len(summary) < 10:
                return {}

            summary_embedding = None
            if settings.DISABLE_EMBEDDING:
                logger.debug("embedding disabled by env, skip embedding")
            else:
                try:
                    logger.debug(f"embedding summary type={type(summary)} len={len(summary)}")
                    logger.debug(f"embedding summary preview: {summary[:2000]}")
                    summary_embedding = await embed_text(summary)
                except Exception as exc:
                    logger.warning(f"embedding failed for source {data['source']}: {exc}")
            logger.debug("  -> [完成] " + data["source"])
            return {
                "summary": summary,
                "embedding": summary_embedding,
                "content": chunk,
                "source": data["source"]
            }

    processed_results = await asyncio.gather(*[process_single_chunk(task) for task in tasks_data])

    new_refs = []
    for res in processed_results:
        if not res:
            continue
        new_refs.append({
            "summary": res["summary"],
            "embedding": res["embedding"],
            "content": res["content"],
            "source": res["source"]
        })

    return new_refs


def cosine_similarity(vec_a, vec_b):
    """计算两个向量的余弦相似度"""
    vec_a = np.array(vec_a)
    vec_b = np.array(vec_b)

    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return np.dot(vec_a, vec_b) / (norm_a * norm_b)


async def get_task_reference(references: List[ReferenceItem], query: str, max_len: int = MAX_CONTEXT_LEN):
    """get data from refs"""
    if not isinstance(query, str):
        query = "" if query is None else str(query)
    logger.debug(f"筛选参考资料 {len(references)} {query[:100]}")
    if not references:
        logger.debug("当前没有参考资料，跳过筛选。")
        return ""

    query_embedding = None
    if settings.DISABLE_EMBEDDING:
        logger.debug("embedding disabled by env, skip query embedding")
    else:
        try:
            query_embedding = await embed_text(query)
        except Exception as exc:
            logger.warning(f"embedding query failed, fallback to raw summaries: {exc}")

    scored_refs = []
    for ref in references:
        ref_emb = ref.get("embedding")
        if query_embedding is None or not ref_emb:
            scored_refs.append({
                "score": 0.0,
                "content": ref.get("summary", ""),
                "source": ref.get("source", "")
            })
            continue

        score = cosine_similarity(query_embedding, ref_emb)
        scored_refs.append({
            "score": score,
            "content" : ref["summary"],
            "source" : ref["source"],
        })

    scored_refs.sort(key=lambda x: x["score"], reverse=True)
    selected_contents = []
    for ref in scored_refs:
        content = f"【参考资料来自 {ref['source']}】\n{ref['content']}"
        content_len = len(content)
        if max_len < content_len:
            break

        selected_contents.append(content)
        max_len -= content_len

    return "\n\n".join(selected_contents)
