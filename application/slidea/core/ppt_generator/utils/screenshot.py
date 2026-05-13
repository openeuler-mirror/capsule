import asyncio
from functools import lru_cache
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from core.utils.logger import logger
from core.ppt_generator.utils.browser import BrowserManager
from core.ppt_generator.utils.common import (
    build_remote_asset_request_router,
    wait_for_page_assets_ready,
)


SLIDE_WIDTH = 1280
SLIDE_HEIGHT = 720
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

CJK_SANS_FONT_FALLBACKS = (
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
)
CJK_SERIF_FONT_FALLBACKS = (
    "Noto Serif CJK SC",
    "Noto Serif SC",
    "Source Han Serif SC",
    "SimSun",
)
_STYLE_FONT_FAMILY_RE = re.compile(r"(font-family\s*:\s*)([^;]+)", re.IGNORECASE)


async def screenshot_html(html_path: str, output_path: str) -> str:
    """渲染 HTML 并截图到 output_path，返回截图绝对路径。"""
    absolute_html_path = os.path.abspath(html_path)
    async with BrowserManager.get_browser_context() as browser:
        context = await browser.new_context(
            viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
            ignore_https_errors=True,
        )
        await context.route("**/*", build_remote_asset_request_router())
        page = await context.new_page()
        try:
            await page.goto(f"file://{absolute_html_path}", wait_until="domcontentloaded", timeout=120000)
            await wait_for_page_assets_ready(page, absolute_html_path)
            await page.screenshot(path=output_path)
            logger.info(f"截图已保存到: {output_path}")
            return output_path
        finally:
            await page.close()
            await context.close()


async def screenshot_svg(svg_path: str, output_path: str) -> str:
    """用 CairoSVG 将 SVG 文件渲染为 PNG，返回截图绝对路径。

    不依赖 Playwright/Chromium。SVG 是静态矢量文档，无需 JS 运行时。
    ``cairosvg`` 仅由 SVG 路线使用，按需懒加载，避免 HTML 路线被牵连。
    """
    import cairosvg  # lazy import: 仅 SVG 路线需要
    absolute_svg_path = os.path.abspath(svg_path)
    svg_bytes = _prepare_svg_bytes_for_cairo(absolute_svg_path)

    def _render() -> None:
        cairosvg.svg2png(
            bytestring=svg_bytes,
            url=absolute_svg_path,
            write_to=output_path,
            output_width=SLIDE_WIDTH,
            output_height=SLIDE_HEIGHT,
        )

    await asyncio.to_thread(_render)
    logger.info(f"SVG 截图已保存到: {output_path}")
    return output_path


async def screenshot_svg_bytes(svg_bytes: bytes, output_path: str) -> str:
    """直接从内存中的 SVG 字节渲染为 PNG。

    用于已经在内存里完成图片嵌入等转换的场景，省一次磁盘写。
    """
    import cairosvg  # lazy import: 仅 SVG 路线需要
    svg_bytes = _add_cjk_font_fallbacks(svg_bytes)

    def _render() -> None:
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=output_path,
            output_width=SLIDE_WIDTH,
            output_height=SLIDE_HEIGHT,
        )

    await asyncio.to_thread(_render)
    logger.info(f"SVG 截图已保存到: {output_path}")
    return output_path


def _prepare_svg_bytes_for_cairo(svg_path: str) -> bytes | None:
    try:
        svg_bytes = open(svg_path, "rb").read()
    except OSError as error:
        logger.warning(f"读取 SVG 失败，回退到 CairoSVG 直接读取文件: {error}")
        return None
    return _add_cjk_font_fallbacks(svg_bytes)


def _add_cjk_font_fallbacks(svg_bytes: bytes) -> bytes:
    """让 CairoSVG 优先使用真实 CJK 字体渲染中文，避免中文变方块。

    生成的 SVG 为了兼容 PowerPoint，通常使用 ``Microsoft YaHei, Arial,
    sans-serif``。Linux 服务器上没有微软雅黑时，Cairo/Pango 可能会选到
    Liberation Sans 等无中文字形的字体，而不是逐字形回退。这里仅在 PNG
    栅格化前的内存副本中给字体栈加上 CJK 字体，不修改落盘 SVG。
    """
    try:
        root = ET.fromstring(svg_bytes)
    except ET.ParseError:
        return svg_bytes

    changed = False
    for elem in root.iter():
        family = elem.get("font-family")
        if family:
            patched = _prepend_cjk_font_stack(family)
            if patched != family:
                elem.set("font-family", patched)
                changed = True

        style = elem.get("style")
        if style and "font-family" in style.lower():
            patched_style = _STYLE_FONT_FAMILY_RE.sub(
                lambda match: f"{match.group(1)}{_prepend_cjk_font_stack(match.group(2).strip())}",
                style,
            )
            if patched_style != style:
                elem.set("style", patched_style)
                changed = True

    if not changed:
        return svg_bytes
    return ET.tostring(root, encoding="utf-8")


def add_cjk_font_fallbacks(svg_bytes: bytes) -> bytes:
    """Public wrapper for adding CJK font fallbacks to SVG bytes."""
    return _add_cjk_font_fallbacks(svg_bytes)


def _prepend_cjk_font_stack(font_family: str) -> str:
    normalized = font_family.lower()
    if _has_available_cjk_font(normalized, _prefers_serif(normalized)):
        return font_family

    cjk_stack = _get_cjk_font_stack(_prefers_serif(normalized))
    return f"{cjk_stack}, {font_family}"


def _prefers_serif(normalized_font_family: str) -> bool:
    return "serif" in normalized_font_family and "sans-serif" not in normalized_font_family


def _has_available_cjk_font(normalized_font_family: str, serif: bool) -> bool:
    detected = detect_system_cjk_fonts(serif)
    if detected:
        return any(name.lower() in normalized_font_family for name in detected)
    fallbacks = CJK_SERIF_FONT_FALLBACKS if serif else CJK_SANS_FONT_FALLBACKS
    return any(name.lower() in normalized_font_family for name in fallbacks)


@lru_cache(maxsize=2)
def _get_cjk_font_stack(serif: bool) -> str:
    return ", ".join(_quote_font_family(name) for name in _get_cjk_font_names(serif))


@lru_cache(maxsize=2)
def _get_cjk_font_names(serif: bool) -> tuple[str, ...]:
    fallbacks = CJK_SERIF_FONT_FALLBACKS if serif else CJK_SANS_FONT_FALLBACKS
    detected = detect_system_cjk_fonts(serif)

    names = []
    seen = set()
    for name in (*detected, *fallbacks):
        cleaned = name.strip().strip("\"'")
        normalized = cleaned.lower()
        if not cleaned or normalized in seen:
            continue
        seen.add(normalized)
        names.append(cleaned)
    return tuple(names)


@lru_cache(maxsize=2)
def _detect_system_cjk_fonts(serif: bool) -> tuple[str, ...]:
    """Return installed font families that can render common Chinese chars.

    On Linux/fontconfig systems, ``fc-list :charset=4e2d`` lists fonts that
    cover the Chinese character "中". This is more accurate than matching a
    generic ``:lang=zh`` family, which can return a Latin-only alias.
    """
    fc_list_path = shutil.which("fc-list")
    if fc_list_path is None:
        return ()

    try:
        result = subprocess.run(
            [fc_list_path, ":charset=4e2d", "family"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logger.warning(f"探测系统中文字体失败，使用内置兜底字体栈: {error}")
        return ()

    if result.returncode != 0:
        return ()

    candidates = set()
    for line in result.stdout.splitlines():
        for family in line.split(","):
            cleaned = family.strip()
            if _is_probably_cjk_font(cleaned):
                candidates.add(cleaned)

    return tuple(sorted(candidates, key=lambda name: _cjk_font_score(name, serif))[:4])


def detect_system_cjk_fonts(serif: bool) -> tuple[str, ...]:
    """Public wrapper for detected CJK font families."""
    return _detect_system_cjk_fonts(serif)


def _is_probably_cjk_font(font_name: str) -> bool:
    normalized = font_name.lower()
    cjk_tokens = (
        "cjk",
        "hans",
        "han sans",
        "han serif",
        "source han",
        "noto sans sc",
        "noto serif sc",
        "wenquanyi",
        "yahei",
        "simsun",
        "simhei",
        "pingfang",
        "hiragino sans gb",
        "heiti",
        "songti",
        "kaiti",
        "fangsong",
    )
    return any(token in normalized for token in cjk_tokens)


def _cjk_font_score(font_name: str, serif: bool) -> tuple[int, int, str]:
    normalized = font_name.lower()
    locale_score = 0
    if " sc" in normalized or normalized.endswith("sc") or "hans" in normalized:
        locale_score = -30
    elif any(token in normalized for token in ("tc", "hk", "jp", "kr")):
        locale_score = 10

    style_score = 0
    if serif:
        if any(token in normalized for token in ("serif", "simsun", "songti", "kaiti", "fangsong")):
            style_score = -20
        elif "sans" in normalized or "hei" in normalized:
            style_score = 15
    else:
        if any(token in normalized for token in ("sans", "yahei", "simhei", "hei", "pingfang")):
            style_score = -20
        elif "serif" in normalized or "simsun" in normalized or "songti" in normalized:
            style_score = 15

    return (locale_score, style_score, normalized)


def _quote_font_family(font_name: str) -> str:
    escaped = font_name.replace('"', '\\"')
    return f'"{escaped}"'
