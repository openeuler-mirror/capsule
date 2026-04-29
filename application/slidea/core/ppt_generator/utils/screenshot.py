import os

from core.utils.logger import logger
from core.ppt_generator.utils.browser import BrowserManager
from core.ppt_generator.utils.common import (
    build_remote_asset_request_router,
    wait_for_page_assets_ready,
)


async def screenshot_html(html_path: str, output_path: str) -> str:
    """渲染 HTML 并截图到 output_path，返回截图绝对路径。"""
    absolute_html_path = os.path.abspath(html_path)
    async with BrowserManager.get_browser_context() as browser:
        context = await browser.new_context(viewport={"width": 1280, "height": 720}, ignore_https_errors=True)
        await context.route("**/*", build_remote_asset_request_router())
        page = await context.new_page()
        try:
            await page.goto(f"file://{absolute_html_path}", wait_until="domcontentloaded", timeout=60000)
            await wait_for_page_assets_ready(page, absolute_html_path)
            await page.screenshot(path=output_path)
            logger.info(f"截图已保存到: {output_path}")
            return output_path
        finally:
            await page.close()
            await context.close()
