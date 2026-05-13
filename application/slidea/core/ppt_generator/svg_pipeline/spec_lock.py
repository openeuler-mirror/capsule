from pathlib import Path
from typing import Iterable, Any


DEFAULT_COLORS = {
    "background": "#FFFFFF",
    "text": "#111827",
    "muted_text": "#4B5563",
    "primary": "#2563EB",
    "secondary": "#14B8A6",
    "accent": "#F59E0B",
    "surface": "#F8FAFC",
    "line": "#CBD5E1",
}
DEFAULT_FONT = "Microsoft YaHei, Arial, sans-serif"


def write_svg_spec_lock(
    save_dir: str | Path,
    *,
    query: str,
    topic: str,
    language: str,
    outline: Iterable[Any],
    template: dict[str, Any] | None = None,
) -> str:
    """Write a lightweight design lock for SVG page generation."""
    content = build_svg_spec_lock_content(
        query=query,
        topic=topic,
        language=language,
        outline=outline,
        template=template,
    )
    path = Path(save_dir) / "spec_lock.md"
    path.write_text(content, encoding="utf-8")
    return content


def build_svg_spec_lock_content(
    *,
    query: str,
    topic: str,
    language: str,
    outline: Iterable[Any],
    template: dict[str, Any] | None = None,
) -> str:
    page_lines = []
    for page in outline:
        page_type = getattr(getattr(page, "type", ""), "name", str(getattr(page, "type", "")))
        rhythm = _page_rhythm(page_type)
        page_lines.append(
            f"- index={getattr(page, 'index', '')}; type={page_type}; rhythm={rhythm}; "
            f"title={getattr(page, 'title', '')}"
        )

    template = template or {}
    colors = template.get("colors") or DEFAULT_COLORS
    font_family = template.get("font_family") or DEFAULT_FONT
    template_name = template.get("name") or "general_modern"
    template_label = template.get("label") or "General Modern"
    layout_guidance = template.get("layout_guidance") or "Use clear hierarchy, restrained visual density, and varied page layouts."
    colors_block = "\n".join(f"- {name}: {value}" for name, value in colors.items())
    pages = "\n".join(page_lines) if page_lines else "- none"
    return f"""# SVG Spec Lock

## Canvas
- format: ppt169
- width: 1280
- height: 720
- viewBox: 0 0 1280 720

## Topic
- topic: {topic}
- language: {language}
- request: {query}

## Template
- name: {template_name}
- label: {template_label}
- layout_guidance: {layout_guidance}

## Colors
{colors_block}

## Typography
- font_family: {font_family}
- title_size: 44-60
- section_title_size: 30-40
- body_size: 20-28
- caption_size: 14-18

## Layout Discipline
- margin: keep all important content at least 48 px from slide edges
- spacing: keep at least 24 px between independent content groups
- text: keep text concise; prefer editable SVG <text>/<tspan> over rasterized text
- images: use local absolute paths when available; avoid remote image URLs
- groups: wrap semantic content blocks in top-level <g id="..."> where practical

## Page Rhythm
{pages}
"""


def _page_rhythm(page_type: str) -> str:
    if page_type in {"COVER_THANKS", "SEPARATOR"}:
        return "breathing"
    if page_type == "TOC":
        return "anchor"
    return "dense"
