import asyncio
import base64
import json
import hashlib
import mimetypes
import os
import re
import time

from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

try:
    from fake_useragent import UserAgent
except ImportError:  # pragma: no cover - fallback for minimal environments
    class UserAgent:
        @property
        def random(self):
            return "Mozilla/5.0"
from PIL import Image

from core.utils.logger import logger
from core.utils.config import app_base_dir
from core.ppt_generator.utils.pptx_postprocess import remove_full_slide_solid_backdrops
from core.utils.image_payload import build_image_url
from core.utils.libreoffice import get_available_libreoffice_executable


UA = UserAgent()
DEFAULT_HTML_TO_PDF_CONCURRENCY = 3
DEFAULT_RENDER_READY_TIMEOUT_MS = 20000
DEFAULT_RENDER_ASSET_FETCH_TIMEOUT_S = 15.0
DEFAULT_RENDER_ASSET_CACHE_MAX_MB = 2048
REMOTE_ASSET_URL_FALLBACKS = {
    "https://cdn.jsdmirror.com/npm/tailwindcss-cdn@3.4.10/tailwindcss.js": [
        "https://cdn.jsdmirror.com/npm/tailwindcss-cdn@3.4.10/tailwindcss.js",
        "https://cdn.jsdelivr.net/npm/tailwindcss-cdn@3.4.10/tailwindcss.js",
        "https://fastly.jsdelivr.net/npm/tailwindcss-cdn@3.4.10/tailwindcss.js",
        "https://unpkg.com/tailwindcss-cdn@3.4.10/tailwindcss.js",
    ],
    "https://cdn.jsdelivr.net.cn/npm/@fortawesome/fontawesome-free@6.4.0/js/all.min.js": [
        "https://cdn.jsdelivr.net.cn/npm/@fortawesome/fontawesome-free@6.4.0/js/all.min.js",
        "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/js/all.min.js",
        "https://fastly.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.4.0/js/all.min.js",
        "https://unpkg.com/@fortawesome/fontawesome-free@6.4.0/js/all.min.js",
    ],
    "https://cdn.jsdelivr.net.cn/npm/chart.js": [
        "https://cdn.jsdelivr.net.cn/npm/chart.js",
        "https://cdn.jsdelivr.net/npm/chart.js",
        "https://fastly.jsdelivr.net/npm/chart.js",
        "https://unpkg.com/chart.js",
    ],
    "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/katex.min.css": [
        "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/katex.min.css",
        "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css",
        "https://fastly.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css",
        "https://unpkg.com/katex@0.16.9/dist/katex.min.css",
    ],
    "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/katex.min.js": [
        "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/katex.min.js",
        "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js",
        "https://fastly.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js",
        "https://unpkg.com/katex@0.16.9/dist/katex.min.js",
    ],
    "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/contrib/auto-render.min.js": [
        "https://cdn.jsdelivr.net.cn/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
        "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
        "https://fastly.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
        "https://unpkg.com/katex@0.16.9/dist/contrib/auto-render.min.js",
    ],
}
REMOTE_FONT_CSS_FALLBACK_HOSTS = (
    "fonts.googleapis.cn",
    "fonts.googleapis.com",
    "fonts.loli.net",
    "fonts.font.im",
)
REMOTE_FONT_CSS_MATCH_HOSTS = REMOTE_FONT_CSS_FALLBACK_HOSTS + ("fonts.googleapis.com.cn",)
RENDER_ASSET_CACHEABLE_RESOURCE_TYPES = {"script", "stylesheet", "font"}
RENDER_ASSET_CACHEABLE_EXTENSIONS = {
    ".css",
    ".js",
    ".mjs",
    ".woff2",
    ".woff",
    ".ttf",
    ".otf",
    ".eot",
}
RENDER_ASSET_WARMUP_URLS = tuple(
    dict.fromkeys(
        url
        for fallback_urls in REMOTE_ASSET_URL_FALLBACKS.values()
        for url in fallback_urls[:1]
    )
)
_RENDER_ASSET_LOCKS: dict[str, asyncio.Lock] = {}
_RENDER_ASSET_LOCKS_GUARD = asyncio.Lock()


async def get_scale_step_value(html_path):
    """
    使用 Playwright 获取在浏览器环境中 JS 循环的最终scale step值。
    """
    # 懒加载：BrowserManager 顶部 import 会强制拉入 playwright，
    # SVG 路线默认不安装 playwright。
    from core.ppt_generator.utils.browser import BrowserManager

    absolute_html_path = os.path.abspath(html_path)
    async with BrowserManager.get_browser_context() as browser:
        context = await browser.new_context(viewport={'width': 1280, 'height': 720}, ignore_https_errors=True)
        await context.route("**/*", build_remote_asset_request_router())
        page = await context.new_page()

        try:
            await page.goto(f'file://{absolute_html_path}', wait_until='domcontentloaded', timeout=120000)
            await wait_for_page_assets_ready(page, absolute_html_path)
            await page.wait_for_function(
                "() => window.final_ratio !== undefined && window.final_ratio !== null",
                timeout=_get_render_ready_timeout_ms(),
            )
            scale_ratio = await page.evaluate("() => window.final_ratio")
            logger.info(f"html {os.path.basename(absolute_html_path)} ratio: {scale_ratio}")
            return scale_ratio
        finally:
            await page.close()
            await context.close()


def sanitize_filename(name: str) -> str:
    """
    清洗文件名，替换非法字符（Windows/Linux），并将空格转换为下划线。
    保留中文、字母、数字、下划线、短横线。
    """
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    cleaned = re.sub(r'\s+', "_", cleaned)
    return cleaned.strip()


async def htmls_to_pptx(html_paths: list[str], save_dir: str, filename: str = "output"):
    """
    将 HTML 路径列表转换为一个 PPTX 文件。
    """

    pdf_paths = await _batch_html_to_pdf(html_paths, save_dir)
    if not pdf_paths:
        raise Exception("没有生成任何 PDF 文件，请检查 HTML 路径是否正确。")

    merged_pdf_path = os.path.join(save_dir, f"{filename}.pdf")
    _merge_pdfs(pdf_paths, merged_pdf_path)

    for path in pdf_paths:
        if os.path.exists(path):
            os.remove(path)

    logger.info(f"正在转换 PDF 到 PPTX: {merged_pdf_path}")
    max_retries = 3
    pptx_path = ""
    for attempt in range(1, max_retries + 1):
        logger.info(f"PDF to PPTX conversion attempt {attempt}/{max_retries}...")
        pptx_path = await _libreoffice_convert_pdf_to_pptx(merged_pdf_path)

        if pptx_path:
            break

        if attempt < max_retries:
            wait_time = attempt * 2
            logger.warning(f"Attempt {attempt} failed. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
        else:
            logger.error("All 3 attempts to convert PDF to PPTX failed.")

    return merged_pdf_path, pptx_path


async def _batch_html_to_pdf(html_file_paths: list[str], save_dir: str) -> list[str]:
    """
    并行处理 HTML 到 PDF 的转换 (使用全局 Browser 实例)
    """
    # 懒加载：BrowserManager 仅在 HTML 路线被调用时才需要。
    from core.ppt_generator.utils.browser import BrowserManager

    await warmup_render_assets()
    semaphore = asyncio.Semaphore(_get_html_to_pdf_concurrency())
    async with BrowserManager.get_browser_context() as browser:
        tasks = [
            _convert_single_html_to_pdf_with_semaphore(semaphore, browser, html_path, save_dir)
            for html_path in html_file_paths
            if os.path.exists(html_path)
        ]
        results = await asyncio.gather(*tasks)

    return [path for path in results if path is not None]


def _get_html_to_pdf_concurrency() -> int:
    """
    避免多个页面同时拉取 CDN 资源，导致 Tailwind 等运行时样式尚未注入就开始导出。
    """
    raw_value = os.getenv("SLIDEA_HTML_TO_PDF_CONCURRENCY", str(DEFAULT_HTML_TO_PDF_CONCURRENCY))
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid SLIDEA_HTML_TO_PDF_CONCURRENCY={raw_value}, fallback to {DEFAULT_HTML_TO_PDF_CONCURRENCY}"
        )
        return DEFAULT_HTML_TO_PDF_CONCURRENCY


def _get_render_ready_timeout_ms() -> int:
    raw_value = os.getenv("SLIDEA_HTML_RENDER_READY_TIMEOUT_MS", str(DEFAULT_RENDER_READY_TIMEOUT_MS))
    try:
        return max(1000, int(raw_value))
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid SLIDEA_HTML_RENDER_READY_TIMEOUT_MS={raw_value}, fallback to {DEFAULT_RENDER_READY_TIMEOUT_MS}"
        )
        return DEFAULT_RENDER_READY_TIMEOUT_MS


def _is_env_enabled(name: str, default: bool = True) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def _get_render_asset_cache_dir() -> Path:
    raw_value = os.getenv("SLIDEA_RENDER_ASSET_CACHE_DIR")
    if raw_value:
        return Path(raw_value).expanduser()
    return Path(app_base_dir) / ".cache" / "render_assets"


def _get_render_asset_fetch_timeout_s() -> float:
    raw_value = os.getenv("SLIDEA_RENDER_ASSET_FETCH_TIMEOUT_S", str(DEFAULT_RENDER_ASSET_FETCH_TIMEOUT_S))
    try:
        return max(1.0, float(raw_value))
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid SLIDEA_RENDER_ASSET_FETCH_TIMEOUT_S={raw_value}, "
            f"fallback to {DEFAULT_RENDER_ASSET_FETCH_TIMEOUT_S}"
        )
        return DEFAULT_RENDER_ASSET_FETCH_TIMEOUT_S


def _get_render_asset_cache_max_bytes() -> int:
    raw_value = os.getenv("SLIDEA_RENDER_ASSET_CACHE_MAX_MB", str(DEFAULT_RENDER_ASSET_CACHE_MAX_MB))
    try:
        return max(1, int(raw_value)) * 1024 * 1024
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid SLIDEA_RENDER_ASSET_CACHE_MAX_MB={raw_value}, "
            f"fallback to {DEFAULT_RENDER_ASSET_CACHE_MAX_MB}"
        )
        return DEFAULT_RENDER_ASSET_CACHE_MAX_MB * 1024 * 1024


def _is_render_asset_cache_enabled() -> bool:
    return _is_env_enabled("SLIDEA_RENDER_ASSET_CACHE_ENABLED", True)


def _is_render_asset_warmup_enabled() -> bool:
    return _is_env_enabled("SLIDEA_RENDER_ASSET_WARMUP_ENABLED", True)


def _is_remote_url(asset_url: str) -> bool:
    return urlparse(asset_url).scheme in {"http", "https"}


def _is_font_css_url(asset_url: str) -> bool:
    return _build_font_css_fallback_urls(asset_url) is not None


def _is_cacheable_render_asset_url(asset_url: str, resource_type: str | None = None) -> bool:
    if not _is_remote_url(asset_url):
        return False
    if resource_type in RENDER_ASSET_CACHEABLE_RESOURCE_TYPES:
        return True
    if _is_font_css_url(asset_url):
        return True

    parsed_url = urlparse(asset_url)
    path = parsed_url.path.lower()
    return any(path.endswith(extension) for extension in RENDER_ASSET_CACHEABLE_EXTENSIONS)


def _guess_render_asset_content_type(asset_url: str, fallback_content_type: str | None = None) -> str:
    if fallback_content_type:
        return fallback_content_type.split(";")[0].strip()
    if _is_font_css_url(asset_url) or urlparse(asset_url).path.endswith(".css"):
        return "text/css"
    if urlparse(asset_url).path.endswith((".js", ".mjs")):
        return "application/javascript"
    guessed_type, _ = mimetypes.guess_type(urlparse(asset_url).path)
    return guessed_type or "application/octet-stream"


def _get_render_asset_cache_paths(asset_url: str) -> tuple[Path, Path]:
    cache_key = hashlib.sha256(asset_url.encode("utf-8")).hexdigest()
    cache_dir = _get_render_asset_cache_dir()
    return cache_dir / "responses" / cache_key, cache_dir / "metadata" / f"{cache_key}.json"


async def _get_render_asset_lock(asset_url: str) -> asyncio.Lock:
    async with _RENDER_ASSET_LOCKS_GUARD:
        lock = _RENDER_ASSET_LOCKS.get(asset_url)
        if lock is None:
            lock = asyncio.Lock()
            _RENDER_ASSET_LOCKS[asset_url] = lock
        return lock


def _read_cached_render_asset(asset_url: str) -> tuple[bytes, dict] | None:
    body_path, metadata_path = _get_render_asset_cache_paths(asset_url)
    if not body_path.exists() or not metadata_path.exists():
        return None

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        body = body_path.read_bytes()
        metadata["last_used_at"] = time.time()
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return body, metadata
    except Exception as error:
        logger.warning(f"Failed reading render asset cache for {asset_url}: {error}")
        return None


def _write_cached_render_asset(
    asset_url: str,
    source_url: str,
    body: bytes,
    content_type: str,
    status_code: int,
) -> dict:
    body_path, metadata_path = _get_render_asset_cache_paths(asset_url)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    body_path.write_bytes(body)
    now = time.time()
    metadata = {
        "url": asset_url,
        "source_url": source_url,
        "content_type": content_type,
        "status": status_code,
        "created_at": now,
        "last_used_at": now,
        "size": len(body),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_render_asset_cache()
    return metadata


def _prune_render_asset_cache():
    cache_dir = _get_render_asset_cache_dir()
    responses_dir = cache_dir / "responses"
    metadata_dir = cache_dir / "metadata"
    if not responses_dir.exists():
        return

    max_bytes = _get_render_asset_cache_max_bytes()
    try:
        entries = []
        total_size = 0
        for body_path in responses_dir.iterdir():
            if not body_path.is_file():
                continue
            size = body_path.stat().st_size
            total_size += size
            metadata_path = metadata_dir / f"{body_path.name}.json"
            last_used_at = body_path.stat().st_mtime
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    last_used_at = float(metadata.get("last_used_at") or last_used_at)
                except Exception as error:
                    logger.debug(f"Failed reading render asset cache metadata {metadata_path}: {error}")
            entries.append((last_used_at, size, body_path, metadata_path))

        if total_size <= max_bytes:
            return

        for _, size, body_path, metadata_path in sorted(entries, key=lambda item: item[0]):
            try:
                body_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                total_size -= size
                if total_size <= max_bytes:
                    break
            except Exception as error:
                logger.warning(f"Failed pruning render asset cache file {body_path}: {error}")
    except Exception as error:
        logger.warning(f"Failed pruning render asset cache: {error}")


def _build_remote_asset_probe_headers(asset_url: str) -> dict[str, str]:
    headers = {
        "User-Agent": UA.random,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if asset_url.endswith(".css") or "fonts.googleapis" in asset_url:
        headers["Accept"] = "text/css,*/*;q=0.1"
    elif asset_url.endswith(".js"):
        headers["Accept"] = "application/javascript,text/javascript,*/*;q=0.1"
    else:
        headers["Accept"] = "*/*"
    return headers


async def _fetch_render_asset_candidate(asset_url: str) -> tuple[bytes, str, int]:
    headers = _build_remote_asset_probe_headers(asset_url)
    timeout_s = _get_render_asset_fetch_timeout_s()

    async with httpx.AsyncClient(
        verify=False,
        follow_redirects=True,
        timeout=timeout_s,
    ) as client:
        response = await client.get(asset_url, headers=headers)
        response.raise_for_status()
        content_type = _guess_render_asset_content_type(asset_url, response.headers.get("content-type"))
        return response.content, content_type, response.status_code


def _rewrite_css_relative_urls(css_body: bytes, source_url: str, content_type: str) -> bytes:
    if content_type != "text/css":
        return css_body

    try:
        css_text = css_body.decode("utf-8")
    except UnicodeDecodeError:
        return css_body

    def replace_url(match):
        quote = match.group("quote") or ""
        raw_url = match.group("url").strip()
        if _should_keep_css_url(raw_url):
            return match.group(0)
        return f"url({quote}{urljoin(source_url, raw_url)}{quote})"

    rewritten = re.sub(
        r"url\(\s*(?P<quote>['\"]?)(?P<url>[^'\")]+)(?P=quote)\s*\)",
        replace_url,
        css_text,
    )
    return rewritten.encode("utf-8")


def _should_keep_css_url(raw_url: str) -> bool:
    if not raw_url or raw_url.startswith(("#", "data:", "blob:", "//")):
        return True
    return bool(urlparse(raw_url).scheme)


def _build_font_css_fallback_urls(original_url: str) -> list[str] | None:
    parsed_url = urlparse(original_url)
    if parsed_url.netloc not in REMOTE_FONT_CSS_MATCH_HOSTS or parsed_url.path != "/css2":
        return None

    candidate_hosts = [parsed_url.netloc, *REMOTE_FONT_CSS_FALLBACK_HOSTS]
    fallback_urls = []
    seen_urls = set()
    for host in candidate_hosts:
        candidate_url = urlunparse(
            (
                parsed_url.scheme or "https",
                host,
                parsed_url.path,
                parsed_url.params,
                parsed_url.query,
                parsed_url.fragment,
            )
        )
        if candidate_url in seen_urls:
            continue
        seen_urls.add(candidate_url)
        fallback_urls.append(candidate_url)

    return fallback_urls


def _get_remote_asset_fallback_urls(original_url: str) -> list[str] | None:
    fallback_urls = REMOTE_ASSET_URL_FALLBACKS.get(original_url)
    if fallback_urls:
        return fallback_urls

    return _build_font_css_fallback_urls(original_url)


def _get_render_asset_candidate_urls(original_url: str) -> list[str]:
    fallback_urls = _get_remote_asset_fallback_urls(original_url) or [original_url]
    candidate_urls = []
    seen_urls = set()
    for candidate_url in [original_url, *fallback_urls]:
        if candidate_url in seen_urls:
            continue
        seen_urls.add(candidate_url)
        candidate_urls.append(candidate_url)
    return candidate_urls


def get_render_asset_candidate_urls(original_url: str) -> list[str]:
    return _get_render_asset_candidate_urls(original_url)


def is_cacheable_render_asset_url(asset_url: str, resource_type: str | None = None) -> bool:
    return _is_cacheable_render_asset_url(asset_url, resource_type)


def rewrite_css_relative_urls(css_body: bytes, source_url: str, content_type: str) -> bytes:
    return _rewrite_css_relative_urls(css_body, source_url, content_type)


async def _get_or_fetch_render_asset(asset_url: str) -> tuple[bytes, dict]:
    cached_asset = _read_cached_render_asset(asset_url)
    if cached_asset:
        logger.debug(f"Render asset cache hit: {asset_url}")
        return cached_asset

    lock = await _get_render_asset_lock(asset_url)
    async with lock:
        cached_asset = _read_cached_render_asset(asset_url)
        if cached_asset:
            logger.debug(f"Render asset cache hit after wait: {asset_url}")
            return cached_asset

        last_error = None
        for candidate_url in _get_render_asset_candidate_urls(asset_url):
            try:
                body, content_type, status_code = await _fetch_render_asset_candidate(candidate_url)
                body = _rewrite_css_relative_urls(body, candidate_url, content_type)
                metadata = _write_cached_render_asset(
                    asset_url,
                    candidate_url,
                    body,
                    content_type,
                    status_code,
                )
                if candidate_url != asset_url:
                    logger.warning(f"Render asset fallback fetched: {asset_url} -> {candidate_url}")
                else:
                    logger.info(f"Render asset cached: {asset_url}")
                return body, metadata
            except Exception as error:
                last_error = error
                logger.warning(f"Render asset fetch failed for {candidate_url}: {error}")

        raise RuntimeError(f"Failed to fetch render asset {asset_url}: {last_error}")


async def warmup_render_assets():
    if not _is_render_asset_cache_enabled() or not _is_render_asset_warmup_enabled():
        return

    results = await asyncio.gather(
        *(_get_or_fetch_render_asset(asset_url) for asset_url in RENDER_ASSET_WARMUP_URLS),
        return_exceptions=True,
    )
    failed_count = sum(1 for result in results if isinstance(result, Exception))
    if failed_count:
        logger.warning(f"Render asset warmup finished with {failed_count} failed asset(s)")


def build_remote_asset_request_router():
    async def route_remote_asset_request(route):
        request = route.request
        original_url = request.url

        if not _is_render_asset_cache_enabled() or not _is_cacheable_render_asset_url(
            original_url,
            getattr(request, "resource_type", None),
        ):
            await route.continue_()
            return

        try:
            body, metadata = await _get_or_fetch_render_asset(original_url)
            await route.fulfill(
                status=int(metadata.get("status") or 200),
                body=body,
                headers={
                    "content-type": metadata.get("content_type") or _guess_render_asset_content_type(original_url),
                    "access-control-allow-origin": "*",
                    "cache-control": "public, max-age=31536000",
                },
            )
        except Exception as error:
            logger.warning(
                f"Render asset cache/proxy failed for {original_url}, "
                f"continue with original request: {error}"
            )
            await route.continue_()

    return route_remote_asset_request


async def _convert_single_html_to_pdf_with_semaphore(
    semaphore: asyncio.Semaphore,
    browser,
    html_file_path: str,
    save_dir: str,
) -> str | None:
    async with semaphore:
        return await _convert_single_html_to_pdf(browser, html_file_path, save_dir)


async def wait_for_page_assets_ready(page, html_file_path: str):
    """
    显式等待字体、样式表和 Tailwind 运行时产物。
    """
    timeout_ms = _get_render_ready_timeout_ms()

    await page.wait_for_load_state("load", timeout=timeout_ms)
    await page.wait_for_function(
        """
        async () => {
            if (document.readyState !== "complete") {
                return false;
            }

            if (document.fonts && document.fonts.status !== "loaded") {
                try {
                    await document.fonts.ready;
                } catch (error) {
                    console.warn("document.fonts.ready failed", error);
                }
            }

            const stylesheetLinks = Array.from(document.querySelectorAll('link[rel="stylesheet"]'));
            const stylesheetsReady = stylesheetLinks.every((link) => {
                const href = link.getAttribute("href") || "";
                if (!href || href.startsWith("data:")) {
                    return true;
                }
                return Boolean(link.sheet);
            });
            if (!stylesheetsReady) {
                return false;
            }

            const tailwindScript = document.querySelector('script[src*="tailwindcss"]');
            if (!tailwindScript) {
                return true;
            }

            return Array.from(document.styleSheets).some((sheet) => {
                try {
                    const ownerNode = sheet.ownerNode;
                    const rules = Array.from(sheet.cssRules || []);
                    return (
                        ownerNode &&
                        ownerNode.tagName === "STYLE" &&
                        rules.length > 20 &&
                        rules.some((rule) => rule.cssText.includes("--tw-"))
                    );
                } catch (error) {
                    return false;
                }
            });
        }
        """,
        timeout=timeout_ms,
    )

    try:
        await page.evaluate(
            """
            async () => {
                if (typeof FontAwesome !== 'undefined' && FontAwesome && FontAwesome.dom) {
                    await FontAwesome.dom.i2svg();
                }
            }
            """
        )
        await page.wait_for_function(
            "() => !document.querySelector('[data-fa-i2svg-pending]')",
            timeout=3000,
        )
    except Exception as error:
        logger.warning(f"FontAwesome render wait skipped for {html_file_path}: {error}")

    await page.wait_for_timeout(1000)
    critical_asset_failures = getattr(page, "_slidea_critical_asset_failures", [])
    if critical_asset_failures:
        raise RuntimeError(
            f"Critical render asset failed for {html_file_path}: "
            + "; ".join(critical_asset_failures[:5])
        )


async def _convert_single_html_to_pdf(browser, html_file_path: str, save_dir: str) -> str | None:
    """
    单个页面转换逻辑
    """
    pdf_file_path = os.path.splitext(html_file_path)[0] + '.pdf'
    absolute_html_path = os.path.abspath(html_file_path)
    max_attempts = 2

    context = await browser.new_context(viewport={'width': 1500, 'height': 920}, ignore_https_errors=True)
    await context.route("**/*", build_remote_asset_request_router())
    page = await context.new_page()

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                setattr(page, "_slidea_critical_asset_failures", [])
                await page.goto(f'file://{absolute_html_path}', wait_until='domcontentloaded', timeout=120000)
                await wait_for_page_assets_ready(page, absolute_html_path)

                # 打印 PDF
                await page.pdf(
                    path=pdf_file_path,
                    width='1281px',
                    height='721px',
                    margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'},
                    print_background=True,
                )
                logger.info(f"Successfully converted {html_file_path} -> {pdf_file_path}")
                return pdf_file_path
            except Exception as e:
                if attempt == max_attempts:
                    raise
                logger.warning(
                    f"Convert attempt {attempt}/{max_attempts} failed for {html_file_path}, retrying: {e}"
                )
                await page.wait_for_timeout(attempt * 1000)

    except Exception as e:
        logger.error(f"转换失败 {html_file_path}: {e}")
        return None

    finally:
        try:
            await page.close()
            await context.close()
        except Exception as e:
            logger.warning(f"Error closing page/context: {e}")


def _merge_pdfs(pdf_paths: list[str], output_path: str):
    """合并 PDF"""
    # 懒加载：PyPDF2 仅在 HTML 路线（合并多页 PDF）时需要。
    from PyPDF2 import PdfWriter

    merger = PdfWriter()
    for pdf_path in pdf_paths:
        merger.append(pdf_path)

    with open(output_path, "wb") as f_out:
        merger.write(f_out)
    merger.close()


def _build_libreoffice_pdf_to_pptx_command(
    executable: Path, file_path: str, output_dir: str
) -> list[str]:
    return [
        str(executable),
        "--headless",
        "--nologo",
        "--nolockcheck",
        "--nodefault",
        "--infilter=impress_pdf_import",
        "--convert-to",
        "pptx:Impress MS PowerPoint 2007 XML",
        "--outdir",
        output_dir,
        file_path,
    ]


async def _libreoffice_convert_pdf_to_pptx(file_path):
    """使用本地 LibreOffice 将 PDF 文件转换为 PPTX 格式。"""
    if not os.path.exists(file_path):
        logger.info(f"The file {file_path} does not exist.")
        return ""

    pptx_path = os.path.splitext(file_path)[0] + ".pptx"
    output_dir = os.path.dirname(file_path)
    executable = get_available_libreoffice_executable()

    if executable is None or not executable.exists():
        logger.warning(
            "PDF to PPTX conversion skipped: no usable LibreOffice executable "
            "was found in the bundled directory or system PATH"
        )
        return ""

    try:
        if os.path.exists(pptx_path):
            os.remove(pptx_path)

        command = _build_libreoffice_pdf_to_pptx_command(executable, file_path, output_dir)
        logger.info(f"Client: Running local LibreOffice conversion: {' '.join(command)}")

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(output_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            stdout_text = stdout.decode("utf-8", errors="ignore").strip()
            stderr_text = stderr.decode("utf-8", errors="ignore").strip()
            logger.error(
                "Local LibreOffice conversion failed: "
                f"returncode={process.returncode}, stdout={stdout_text}, stderr={stderr_text}"
            )
            return ""

        if not os.path.exists(pptx_path):
            logger.error("Local LibreOffice conversion finished but no PPTX output was generated.")
            return ""

        remove_full_slide_solid_backdrops(pptx_path)

        logger.info(f"Client: Successfully converted and saved to '{pptx_path}'")
        return pptx_path

    except Exception as e:
        logger.info(f"An error occurred while converting PDF to PPTX: {str(e)}")
        return ""


def _extract_web_image_description(image: dict, image_query: str) -> str:
    for key in ["description", "image_description", "content", "caption", "alt", "title"]:
        value = image.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return image_query


async def get_web_images_content(image_query_list, image_list, save_dir):
    """download image of images query"""
    result = []
    img_list = []
    image_descriptions = {}
    images_dir = os.path.join(save_dir, "images")
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)

    nested_results = await _execute_download_images(image_list, images_dir)

    for download_result, image_result in zip(nested_results, image_list):
        for download_img, image in zip(download_result, image_result):
            image['url'] = download_img

    for image_query, image_paths in zip(image_query_list, image_list):
        image_paths = [item for item in image_paths if item['url'] is not None]
        result.append(f"图片'{image_query}'的下载结果：{image_paths}")
        for image in image_paths:
            image_path = os.path.join(save_dir, image['url'])
            img_list.append(image_path)
            image_descriptions[image_path] = _extract_web_image_description(image, image_query)

    return "\n".join(result), img_list, image_descriptions


async def _execute_download_images(image_list, images_dir):
    """execute download images"""
    tasks = []
    group_sizes = []
    for image_result in image_list:
        group_sizes.append(len(image_result))
        for image in image_result:
            img_url = image['url']
            task = asyncio.create_task(download_image(img_url, images_dir))
            tasks.append(task)
    flat_results = await asyncio.gather(*tasks)
    nested_results = []
    current_pos = 0
    for size in group_sizes:
        chunk = flat_results[current_pos: current_pos + size]
        nested_results.append(chunk)
        current_pos += size
    return nested_results


def _ensure_placeholder_image(image_dir: str) -> str:
    os.makedirs(image_dir, exist_ok=True)
    placeholder_path = os.path.join(image_dir, "placeholder.png")
    if os.path.exists(placeholder_path):
        return placeholder_path

    # 1x1 transparent PNG
    placeholder_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    with open(placeholder_path, "wb") as f:
        f.write(base64.b64decode(placeholder_b64))
    return placeholder_path


async def download_image(img_url, image_dir):
    """download image and return img used in html"""

    if img_url.startswith("//"):
        img_url = "https:" + img_url
    headers = {
        "User-Agent": UA.random,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    parsed_uri = urlparse(img_url)
    headers["Referer"] = f"{parsed_uri.scheme}://{parsed_uri.netloc}/"

    try:
        # 1. 先执行下载请求
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(
                img_url,
                headers=headers,
                timeout=20.0,
                follow_redirects=True,
            )
            if response.status_code == 403:
                # 403 常见为防盗链，去掉 Referer 重试
                headers.pop("Referer", None)
                response = await client.get(
                    img_url,
                    headers=headers,
                    timeout=20.0,
                    follow_redirects=True,
                )
            response.raise_for_status()

            # 2. 从响应头中获取内容类型 (Content-Type)
            content_type = response.headers.get("Content-Type")
            if not content_type or not content_type.startswith("image/"):
                # Some hosts return application/octet-stream for images
                if not img_url.lower().endswith((
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".gif",
                    ".bmp",
                    ".svg",
                )):
                    logger.debug(
                        f"下载失败: {img_url}, Content-Type非图片格式: {content_type}"
                    )
                    return _ensure_placeholder_image(image_dir)

            # 3. 使用mimetypes库将 'image/jpeg' 转换为 '.jpg' 等扩展名
            file_ext = mimetypes.guess_extension(content_type.split(";")[0])
            if not file_ext:
                # 如果 mimetypes 无法识别，提供一个简单的备用方案
                subtype = content_type.split("/")[-1].split(";")[0]
                file_ext = f".{subtype}"
                logger.debug(
                    f"无法从 '{content_type}' 自动推断扩展名, 回退使用: '{file_ext}'"
                )

            # 常见修正: .jpe -> .jpg
            if file_ext == ".jpe":
                file_ext = ".jpg"

            # 4. 生成文件名和路径
            filename = f"{hashlib.md5(img_url.encode()).hexdigest()}{file_ext}"
            local_path = os.path.join(image_dir, filename)

            # 5. 保存到本地
            with open(local_path, "wb") as file:
                file.write(response.content)

            # 6. 转换不支持的格式（avif/webp -> jpg）
            if file_ext in (".avif", ".webp"):
                try:
                    jpg_path = os.path.splitext(local_path)[0] + ".jpg"
                    with Image.open(local_path) as im:
                        im = im.convert("RGB")
                        im.save(jpg_path, "JPEG", quality=90)
                    return jpg_path
                except Exception as e:
                    logger.warning(f"转换图片格式失败: {local_path} - {e}")

            return local_path
    except Exception as e:
        logger.debug(f"下载时发生未知错误: {img_url} - {str(e)}")
        return _ensure_placeholder_image(image_dir)
