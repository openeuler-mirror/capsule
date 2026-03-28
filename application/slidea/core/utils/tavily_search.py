import asyncio
from typing import List
import random
import time

from core.utils.logger import logger
from tavily import AsyncTavilyClient

from core.utils.config import settings


class KeyPool:
    """
    Key 管理池：支持随机获取、安全移除。
    使用 asyncio.Lock 确保在并发环境下的数据一致性。
    """

    def __init__(self, keys: List[str]):
        self._keys = list(set(keys))
        self._lock = asyncio.Lock()


    async def get_random_key(self) -> str:
        """随机获取一个 Key，如果池空了则抛出异常"""
        async with self._lock:
            if not self._keys:
                raise ValueError("No API keys available in the pool!")
            random.seed(time.time_ns())
            return random.choice(self._keys)


    async def remove_key(self, key: str):
        """移除一个失效的 Key"""
        async with self._lock:
            if key in self._keys:
                self._keys.remove(key)
                logger.warning(
                    f"Key [{key[:8]}...] removed. Remaining keys: {len(self._keys)}"
                )
            else:
                pass


key_pool = KeyPool(settings.TAVILY_API_KEYS)


async def async_search(query: str, search_image: bool = False, max_results: int = 5):
    """
    执行搜索。
    机制：
    1. 随机取 Key。
    2. 尝试请求。
    3. 报错 -> 从池中移除该 Key -> 换新 Key 重试。
    """

    if not settings.has_tavily_search_config():
        logger.warning("Tavily search skipped: TAVILY_API_KEYS is not configured.")
        return []

    max_retries = 5
    attempt = 0

    logger.info(f"tavily search: {query}")
    while attempt < max_retries:
        attempt += 1
        selected_key = None

        try:
            selected_key = await key_pool.get_random_key()
            tavily_client = AsyncTavilyClient(api_key=selected_key)
            result = await tavily_client.search(
                query=query,
                topic="general",
                search_depth="advanced",
                max_results=max_results,
                include_images=search_image,
                include_image_descriptions=search_image,
                include_raw_content = True
            )

            if search_image:
                return result.get("images", [])

            return result.get("results", [])

        except ValueError as ve:
            logger.error(f"Search aborted: {ve}")
            return []

        except Exception as ex:
            logger.warning(
                f"Tavily search error: {ex} | Key: {selected_key[:8]}... | Retry: {attempt}"
            )
            if selected_key:
                await key_pool.remove_key(selected_key)

    logger.debug("Max retries exceeded for tavily search.")
    return []


async def tavily_search(queries: str | list, search_image: bool = False, max_results: int = 5):
    """support batch tavily search"""

    if not queries:
        return []

    if not isinstance(queries, list):
        queries = [queries]

    tasks = []
    for query in queries:
        task = asyncio.create_task(
            async_search(query, search_image=search_image, max_results=max_results)
        )
        tasks.append(task)

    results = []
    for r in await asyncio.gather(*tasks):
        results.extend(r)

    return results


if __name__ == "__main__":
    logger.info(
        asyncio.run(
            tavily_search(
                ["EcoCup智能恒温咖啡杯产品详情", "再生材料在咖啡杯中的应用案例"]
            )
        )
    )
