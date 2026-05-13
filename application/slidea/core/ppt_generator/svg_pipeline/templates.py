import json
from pathlib import Path
from typing import Any

from core.utils.config import app_base_dir


DEFAULT_TEMPLATE_NAME = "general_modern"


def load_svg_templates() -> list[dict[str, Any]]:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "svg_templates" / "style.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    templates = content.get("templates", [])
    if not isinstance(templates, list) or not templates:
        raise ValueError(f"SVG template metadata is empty or invalid: {path}")
    return templates


def select_svg_template(query: str, outline: Any, template_name: str | None = None) -> dict[str, Any]:
    templates = load_svg_templates()
    if template_name:
        matched = next((item for item in templates if item.get("name") == template_name), None)
        if matched:
            return matched

    haystack = f"{query}\n{outline}".lower()
    best_template = templates[0]
    best_score = -1
    for template in templates:
        score = 0
        for keyword in template.get("keywords", []):
            keyword_text = str(keyword).lower().strip()
            if keyword_text and keyword_text in haystack:
                score += len(keyword_text)
        if template.get("name") == DEFAULT_TEMPLATE_NAME:
            score += 1
        if score > best_score:
            best_score = score
            best_template = template
    return best_template


def format_svg_template_for_prompt(template: dict[str, Any]) -> str:
    colors = template.get("colors") or {}
    color_lines = "\n".join(f"- {name}: {value}" for name, value in colors.items())
    keywords = ", ".join(str(item) for item in template.get("keywords", []))
    return f"""# SVG Template
- name: {template.get("name", "")}
- label: {template.get("label", "")}
- description: {template.get("description", "")}
- keywords: {keywords}
- font_family: {template.get("font_family", "")}

## Template Colors
{color_lines}

## Layout Guidance
{template.get("layout_guidance", "")}
"""
