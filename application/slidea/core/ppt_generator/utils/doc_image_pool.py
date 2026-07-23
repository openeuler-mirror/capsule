"""Global document image pool for PPT page generation.

In single-threaded asyncio (LangGraph's execution model), concurrent
content_page_worker coroutines interleave only at ``await`` points.
Synchronous operations between two ``await``s are atomic, so a plain
module-level list needs no lock.

Usage:
  - init_doc_image_pool(images)   -- called once in get_content_pages_node
  - snapshot = doc_image_pool_snapshot()  -- take a read-only snapshot
  - claim_doc_image(path)         -- remove from pool if present; returns True if claimed
"""


_pool: list[dict] = []


def init_doc_image_pool(images: list[dict]) -> None:
    """Replace the global pool with *images* (list of {path, description, ...})."""
    global _pool
    _pool = list(images)


def doc_image_pool_snapshot() -> list[dict]:
    """Return a shallow copy of the current pool (safe to iterate after resuming)."""
    return list(_pool)


def claim_doc_image(path: str) -> bool:
    """Remove *path* from the pool if it is still present.

    Returns True if the image was claimed (found and removed), False if it
    was already taken by another worker.  This is a synchronous operation
    and thus atomic under single-threaded asyncio.
    """
    global _pool
    for i, item in enumerate(_pool):
        if item.get("path") == path:
            _pool.pop(i)
            return True
    return False


def doc_image_pool_size() -> int:
    return len(_pool)
