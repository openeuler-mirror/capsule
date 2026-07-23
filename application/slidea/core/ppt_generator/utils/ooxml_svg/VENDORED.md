# Vendored ooxml-svg converter

This package is the deployable copy of the standalone `ooxml-svg` project used
by `scripts/pptx_to_style_pack.py`. Keep its Python sources synchronized with
the standalone project's `src/ooxml_svg/` directory.

Slidea supplies its bundled Noto Sans CJK SC font files through the converter's
public `Converter(..., font_dirs=..., fallback_font_regular=...,
fallback_font_bold=...)` interface. Do not add Slidea-specific absolute paths
inside the standalone converter.
