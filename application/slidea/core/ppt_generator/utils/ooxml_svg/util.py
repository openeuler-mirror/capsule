from __future__ import annotations

import math
import re
import unicodedata

from .model import Matrix


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def fmt(value: float, digits: int = 3) -> str:
    if abs(value) < 0.0005:
        return "0"
    out = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if out == "-0" else out


def safe_id(value: str, prefix: str = "element") -> str:
    value = unicodedata.normalize("NFKD", value)
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    if not value or not re.match(r"[A-Za-z_]", value):
        value = f"{prefix}-{value}"
    return value


def color_hex(value: str, fallback: str = "#000000") -> str:
    value = (value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return "#" + value.upper()
    if re.fullmatch(r"[0-9A-Fa-f]{8}", value):
        return "#" + value[:6].upper()
    return fallback


def srgb_to_hsl(hex_color: str) -> tuple[float, float, float]:
    import colorsys

    rgb = tuple(int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
    return hue, saturation, lightness


def hsl_to_srgb(hue: float, saturation: float, lightness: float) -> str:
    import colorsys

    red, green, blue = colorsys.hls_to_rgb(
        hue % 1.0,
        clamp(lightness),
        clamp(saturation),
    )
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def rotate_matrix(degrees: float, cx: float, cy: float) -> Matrix:
    rad = math.radians(degrees)
    co, si = math.cos(rad), math.sin(rad)
    return Matrix(co, si, -si, co, cx - co * cx + si * cy, cy - si * cx - co * cy)


def flip_matrix(horizontal: bool, vertical: bool, cx: float, cy: float) -> Matrix:
    return Matrix(
        -1 if horizontal else 1, 0, 0, -1 if vertical else 1, 2 * cx if horizontal else 0, 2 * cy if vertical else 0
    )


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )
