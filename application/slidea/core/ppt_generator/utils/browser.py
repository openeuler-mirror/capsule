import asyncio
import threading
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

from core.utils.logger import logger


class BrowserManager:
    """
    基于线程隔离和引用计数的 Playwright 浏览器管理器。
    使用 async context manager 确保资源在 Event Loop 关闭前正确释放。
    """
    _local = threading.local()

    @classmethod
    @asynccontextmanager
    async def get_browser_context(cls):
        """
        获取浏览器实例的上下文管理器。
        使用方式: async with BrowserManager.get_browser_context() as browser: ...
        """
        cls._ensure_thread_local()

        # 1. 确保浏览器已启动
        async with cls._local.lock:
            if not (cls._local.browser and cls._local.browser.is_connected()):
                await cls._init_browser()

            # 2. 增加引用计数
            cls._local.ref_count += 1

        try:
            # 3. 将浏览器实例交出给调用者
            yield cls._local.browser
        finally:
            # 4. 离开上下文时，减少引用计数并尝试清理
            # 这一步是 await 调用的，保证了在 Event Loop 关闭前执行
            await cls._decrease_ref_and_maybe_close()

    @classmethod
    def _ensure_thread_local(cls):
        if not hasattr(cls._local, 'lock'):
            cls._local.lock = asyncio.Lock()
            cls._local.ref_count = 0
            cls._local.browser = None
            cls._local.playwright = None

    @classmethod
    async def _init_browser(cls):
        tid = threading.get_ident()
        logger.info(f"Thread-{tid}: Launching new Playwright instance...")
        try:
            cls._local.playwright = await async_playwright().start()
            cls._local.browser = await cls._local.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            logger.info(f"Thread-{tid}: Browser launched successfully.")
        except Exception as e:
            logger.error(f"Thread-{tid}: Browser launch failed: {e}")
            await cls._cleanup_now()
            raise e

    @classmethod
    async def _decrease_ref_and_maybe_close(cls):
        """减少引用计数，若归零则执行清理（这是一个 awaitable 方法）。"""
        async with cls._local.lock:
            if not hasattr(cls._local, 'ref_count'):
                return

            cls._local.ref_count -= 1

            # 如果引用归零，立即清理
            if cls._local.ref_count <= 0:
                logger.info(f"Thread-{threading.get_ident()}: Ref count 0, closing browser...")
                await cls._cleanup_now()

    @classmethod
    async def _cleanup_now(cls):
        """立即执行资源释放。"""
        if hasattr(cls._local, 'browser') and cls._local.browser:
            try:
                if cls._local.browser.is_connected():
                    await cls._local.browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            finally:
                cls._local.browser = None

        if hasattr(cls._local, 'playwright') and cls._local.playwright:
            try:
                await cls._local.playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            finally:
                cls._local.playwright = None
