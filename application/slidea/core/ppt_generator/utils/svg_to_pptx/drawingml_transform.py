"""SVG transform-list parsing and affine decomposition helpers."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import NamedTuple


_NUMBER_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')
_CALL_RE = re.compile(r'([A-Za-z]+)\s*\(([^)]*)\)')


class AffineMatrix(NamedTuple):
    """One SVG 2D affine matrix, kept tuple-compatible for existing callers."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float


@dataclass(frozen=True)
class TransformInfo:
    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    angle_deg: float = 0.0
    pivot_x: float | None = None
    pivot_y: float | None = None
    has_skew: bool = False
    matrix: AffineMatrix = AffineMatrix(1, 0, 0, 1, 0, 0)


def _multiply(
    left: AffineMatrix,
    right: AffineMatrix,
) -> AffineMatrix:
    """Multiply two SVG affine matrices using column-vector convention."""
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return AffineMatrix(
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _call_matrix(name: str, args: list[float]) -> AffineMatrix:
    if name == 'matrix' and len(args) >= 6:
        return AffineMatrix(*args[:6])
    if name == 'translate' and args:
        return AffineMatrix(1, 0, 0, 1, args[0], args[1] if len(args) > 1 else 0)
    if name == 'scale' and args:
        return AffineMatrix(args[0], 0, 0, args[1] if len(args) > 1 else args[0], 0, 0)
    if name == 'rotate' and args:
        radians = math.radians(args[0])
        cos_a, sin_a = math.cos(radians), math.sin(radians)
        rotation = AffineMatrix(cos_a, sin_a, -sin_a, cos_a, 0, 0)
        if len(args) >= 3:
            cx, cy = args[1], args[2]
            return _multiply(
                _multiply(AffineMatrix(1, 0, 0, 1, cx, cy), rotation),
                AffineMatrix(1, 0, 0, 1, -cx, -cy),
            )
        return rotation
    if name == 'skewx' and args:
        return AffineMatrix(1, 0, math.tan(math.radians(args[0])), 1, 0, 0)
    if name == 'skewy' and args:
        return AffineMatrix(1, math.tan(math.radians(args[0])), 0, 1, 0, 0)
    return AffineMatrix(1, 0, 0, 1, 0, 0)


def parse_transform_info(transform_str: str) -> TransformInfo:
    if not transform_str:
        return TransformInfo()

    calls: list[tuple[str, list[float]]] = []
    matrix = AffineMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for match in _CALL_RE.finditer(transform_str):
        name = match.group(1).lower()
        args = [float(value) for value in _NUMBER_RE.findall(match.group(2))]
        calls.append((name, args))
        matrix = _multiply(matrix, _call_matrix(name, args))

    # A standalone rotate can be represented exactly by a DrawingML group as
    # long as the group's bounding box is centered on the SVG rotation pivot.
    if len(calls) == 1 and calls[0][0] == 'rotate' and calls[0][1]:
        args = calls[0][1]
        pivot_x = args[1] if len(args) >= 3 else 0.0
        pivot_y = args[2] if len(args) >= 3 else 0.0
        return TransformInfo(
            angle_deg=args[0], pivot_x=pivot_x, pivot_y=pivot_y, matrix=matrix,
        )

    a, b, c, d, e, f = matrix
    if abs(b) < 1e-9 and abs(c) < 1e-9:
        return TransformInfo(dx=e, dy=f, sx=a, sy=d, matrix=matrix)

    scale_x = math.hypot(a, b)
    determinant = a * d - b * c
    scale_y = determinant / scale_x if scale_x else math.hypot(c, d)
    angle_deg = math.degrees(math.atan2(b, a)) if scale_x else 0.0
    has_skew = abs(a * c + b * d) > 1e-7
    return TransformInfo(
        dx=e,
        dy=f,
        sx=scale_x or 1.0,
        sy=scale_y or 1.0,
        angle_deg=angle_deg,
        has_skew=has_skew,
        matrix=matrix,
    )
