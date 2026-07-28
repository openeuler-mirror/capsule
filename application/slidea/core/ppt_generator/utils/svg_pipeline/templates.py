import json
from pathlib import Path
from typing import Any

from langchain.messages import HumanMessage

from core.ppt_generator.thought_to_ppt.svg_page_generators.state import TemplateResult
from core.utils.config import app_base_dir
from core.utils.llm import InvokeOptions, llm_invoke
from core.utils.logger import logger


def load_svg_templates() -> list[dict[str, Any]]:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "svg_templates" / "style.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    templates = content.get("templates", [])
    if not isinstance(templates, list) or not templates:
        raise ValueError(f"SVG template metadata is empty or invalid: {path}")
    return templates


def load_svg_template_content(template_name: str) -> str:
    path = Path(app_base_dir) / "core" / "ppt_generator" / "assets" / "svg_templates" / f"{template_name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"SVG template file not found: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"SVG template file is empty: {path}")
    return content


async def select_svg_template(query: str, outline: Any) -> str:
    """LLM-pick an SVG template name based on query and outline."""
    templates = load_svg_templates()
    template_desc = [{"name": item["name"], "description": item.get("description", "")} for item in templates]

    prompt = f"""
请从模板列表中选取适合当前PPT主题和大纲的模板.
# 用户的PPT请求
{query}

# 要生成的PPT章节大纲
{outline}

# 当前已有的所有SVG模板列表信息如下
{template_desc}

# 返回格式要求
请从模板列表中选取适合当前PPT主题和大纲的模板，返回一个json：
{{
    "reason": "选择理由",
    "name": "模板name"
}}
"""
    response = await llm_invoke(
        [HumanMessage(content=prompt)],
        InvokeOptions(pydantic_schema=TemplateResult),
    )
    template = response.name
    valid_template_names = {item["name"] for item in templates}
    if template not in valid_template_names:
        logger.warning(f"LLM returned unknown SVG template '{template}', falling back to '{templates[0]['name']}'")
        template = templates[0]["name"]
    return template
