from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from PIL import Image

from .model import ConversionStats, Element, Page
from .package import OpcPackage
from .parser import PresentationParser
from .renderer import SvgRenderer
from .text import FontMetrics
from .validator import ValidationResult, validate_svg


@dataclass
class ConversionResult:
    output_dir: Path
    svg_files: list[Path]
    report_file: Path
    stats: ConversionStats
    validations: list[ValidationResult]


class Converter:
    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        *,
        font_dirs: list[str | Path] | tuple[str | Path, ...] | None = None,
        fallback_font_regular: str | Path | None = None,
        fallback_font_bold: str | Path | None = None,
    ):
        if (width, height) != (1280, 720):
            raise ValueError("This semantic SVG profile requires a fixed 1280x720 canvas")
        self.width, self.height = width, height
        self.font_metrics = FontMetrics(
            font_dirs=font_dirs,
            fallback_font_regular=fallback_font_regular,
            fallback_font_bold=fallback_font_bold,
        )

    @staticmethod
    def _walk(elements: list[Element]):
        for element in elements:
            yield element
            yield from Converter._walk(element.children)

    @staticmethod
    def _asset_name(part: str, used: dict[str, str]) -> str:
        base = PurePosixPath(part).name
        if base not in used or used[base] == part:
            used[base] = part
            return base
        stem, suffix = Path(base).stem, Path(base).suffix
        tag = hashlib.sha1(part.encode()).hexdigest()[:8]
        name = f"{stem}-{tag}{suffix}"
        used[name] = part
        return name

    def convert(self, source: str | Path, output_dir: str | Path, strict: bool = True) -> ConversionResult:
        source, output_dir = Path(source), Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)
        with OpcPackage(source) as package:
            pages = PresentationParser(package).parse()
            used_names: dict[str, str] = {}
            copied_parts: dict[str, str] = {}
            cropped_parts: dict[str, str] = {}
            for page in pages:
                for element in self._walk(page.all_elements):
                    if not element.image_part:
                        continue
                    if element.image_part not in copied_parts:
                        name = self._asset_name(element.image_part, used_names)
                        (images_dir / name).write_bytes(package.read(element.image_part))
                        copied_parts[element.image_part] = f"images/{name}"
                    element.image_href = copied_parts[element.image_part]
                    if element.crop and any(element.crop):
                        crop_key = f"{element.image_part}:{','.join(f'{v:.8f}' for v in element.crop)}"
                        if crop_key not in cropped_parts:
                            derived = self._write_cropped_image(
                                package.read(element.image_part),
                                element.image_part,
                                element.crop,
                                images_dir,
                                used_names,
                            )
                            if derived is not None:
                                cropped_parts[crop_key] = f"images/{derived}"
                        if crop_key in cropped_parts:
                            # Slidea only accepts clip-path directly on <image>,
                            # while its native converter cannot represent a
                            # rectangular SVG clip as OOXML srcRect. A derived
                            # lossless PNG keeps both paths geometrically exact.
                            element.image_href = cropped_parts[crop_key]
                            element.crop = None
            svg_files: list[Path] = []
            renderer = SvgRenderer(self.width, self.height, font_metrics=self.font_metrics)
            for page in pages:
                path = output_dir / f"slide{page.number}.svg"
                path.write_bytes(renderer.tostring(page))
                svg_files.append(path)
        stats = self._stats(pages)
        validations = [validate_svg(path) for path in svg_files]
        report = {
            "source": source.name,
            "profile": {"width": self.width, "height": self.height, "semantic_top_groups": True, "embedded_assets": False},
            "stats": asdict(stats),
            "font_resolution": renderer.text_layouter.metrics.audit(),
            "assets": copied_parts,
            "derived_cropped_assets": cropped_parts,
            "slides": [
                {"number": p.number, "source_part": p.source_part, "warnings": p.warnings,
                 "elements": len(list(self._walk(p.all_elements))), "file": f"slide{p.number}.svg"}
                for p in pages
            ],
            "validation": [asdict(v) for v in validations],
            "limitations": [
                "Animations, transitions, audio and video are not represented.",
                "SmartArt, OLE and unknown graphicFrame payloads are reported and omitted.",
                "Preset geometry outside the implemented registry falls back to a rectangle and is marked data-geometry-fallback.",
                "Text layout uses an exact source font when available, otherwise the explicitly configured portable fallback font.",
            ],
        }
        report_file = output_dir / "conversion-report.json"
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if strict and not all(v.valid for v in validations):
            failures = sum(len(v.errors) for v in validations)
            raise RuntimeError(f"SVG validation failed with {failures} error(s); see {report_file}")
        return ConversionResult(output_dir, svg_files, report_file, stats, validations)

    @staticmethod
    def _write_cropped_image(
        data: bytes,
        source_part: str,
        crop: tuple[float, float, float, float],
        images_dir: Path,
        used_names: dict[str, str],
    ) -> str | None:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                left, top, right, bottom = crop
                x0 = max(0, min(image.width - 1, round(left * image.width)))
                y0 = max(0, min(image.height - 1, round(top * image.height)))
                x1 = max(x0 + 1, min(image.width, round((1 - right) * image.width)))
                y1 = max(y0 + 1, min(image.height, round((1 - bottom) * image.height)))
                cropped = image.crop((x0, y0, x1, y1))
                tag = hashlib.sha1(
                    f"{source_part}:{left:.8f}:{top:.8f}:{right:.8f}:{bottom:.8f}".encode()
                ).hexdigest()[:10]
                stem = PurePosixPath(source_part).stem
                name = f"{stem}-crop-{tag}.png"
                if name in used_names and used_names[name] != source_part:
                    name = (
                        f"{stem}-crop-{tag}-"
                        f"{hashlib.sha1(source_part.encode()).hexdigest()[:6]}.png"
                    )
                used_names[name] = source_part
                cropped.save(images_dir / name, format="PNG")
                return name
        except (OSError, ValueError):
            return None

    def _stats(self, pages: list[Page]) -> ConversionStats:
        stats = ConversionStats(slides=len(pages))
        for page in pages:
            for element in self._walk(page.all_elements):
                if element.kind == "shape": stats.shapes += 1
                elif element.kind == "image": stats.pictures += 1
                elif element.kind == "group": stats.groups += 1
                elif element.kind == "connector": stats.connectors += 1
                elif element.kind == "table": stats.tables += 1
                elif element.kind == "chart": stats.charts += 1
                elif element.kind == "unsupported": stats.unsupported += 1
                if element.text:
                    stats.text_runs += sum(len(p.runs) for p in element.text.paragraphs)
                for warning in element.warnings:
                    stats.warnings.append({"slide": page.number, "element": element.element_id, "warning": warning})
        return stats
