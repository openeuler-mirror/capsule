import re
import xml.etree.ElementTree as ET


HTML_ENTITY_REPLACEMENTS = {
    "&nbsp;": " ",
    "&mdash;": "—",
    "&ndash;": "–",
    "&copy;": "©",
    "&reg;": "®",
    "&rarr;": "→",
    "&larr;": "←",
    "&middot;": "·",
    "&hellip;": "…",
    "&bull;": "•",
}


def extract_svg_content(text: str) -> str:
    """Extract SVG content from an LLM response."""
    if not text:
        return ""

    pattern = r"```(?:svg|xml)\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    fallback = re.search(r"(<svg\b.*?</svg>)", text, re.DOTALL | re.IGNORECASE)
    if fallback:
        return fallback.group(1).strip()

    return text.strip()


def repair_svg_content(svg_content: str) -> str:
    """Apply safe deterministic repairs to common LLM SVG mistakes."""
    repaired = svg_content.strip()
    repaired = re.sub(r"^\s*<\?xml[^>]*>\s*", "", repaired)

    for source, target in HTML_ENTITY_REPLACEMENTS.items():
        repaired = repaired.replace(source, target)

    repaired = _repair_rgba_attributes(repaired)
    repaired = re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)",
        "&amp;",
        repaired,
    )
    return repaired


def validate_svg_content(svg_content: str) -> None:
    """Raise ValueError when SVG content is missing or not well-formed XML."""
    if not svg_content or "<svg" not in svg_content.lower():
        raise ValueError("LLM output does not contain SVG content")

    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError as error:
        raise ValueError(f"SVG is not well-formed XML: {error}") from error

    tag_name = root.tag.rsplit("}", 1)[-1].lower()
    if tag_name != "svg":
        raise ValueError("SVG content root element must be <svg>")


def safe_svg_filename(index: int, title: str) -> str:
    """Build a stable sortable SVG file name."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", title or "slide")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    cleaned = cleaned[:40] or "slide"
    return f"{index + 1:02d}_{cleaned}.svg"


def _repair_rgba_attributes(svg_content: str) -> str:
    pattern = re.compile(
        r'\b(?P<attr>fill|stroke)="rgba\(\s*(?P<r>\d{1,3})\s*,\s*(?P<g>\d{1,3})\s*,\s*(?P<b>\d{1,3})\s*,\s*(?P<a>0(?:\.\d+)?|1(?:\.0+)?)\s*\)"',
        re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        attr = match.group("attr").lower()
        r = _clamp_color(match.group("r"))
        g = _clamp_color(match.group("g"))
        b = _clamp_color(match.group("b"))
        alpha = match.group("a")
        return f'{attr}="#{r:02X}{g:02X}{b:02X}" {attr}-opacity="{alpha}"'

    return pattern.sub(replace, svg_content)


def _clamp_color(value: str) -> int:
    return max(0, min(255, int(value)))
