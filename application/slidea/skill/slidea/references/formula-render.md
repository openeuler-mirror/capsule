# Formula Rendering

slidea supports display math formulas on PPT pages by rendering each formula to a transparent PNG and embedding it via the normal `<image>` channel. The PNG is **not** a native PowerPoint equation object — it is a picture, so double-clicking it in PowerPoint will not open an equation editor. The LaTeX source is preserved in `<run_id>/formulas.json` for later edits and for future migration paths.

## What "v1" supports

- **Display formulas only**: a formula occupies its own line on the page. Inline formulas (e.g. "$x > 0$" mid-sentence) are **not** supported because SVG `<image>` has no baseline metadata.
- **matplotlib mathtext coverage**: fractions (`\frac`), sums (`\sum`), integrals (`\int`), Greek letters, sub/superscripts, roots (`\sqrt`), and simple matrices. Complex multi-line environments (`align`, `cases`, `equation`) are **not** supported.
- **Fixed black foreground**: rendered against transparent background. Works on light backgrounds. For dark backgrounds, the Phase 3 modify-mode script supports `--color` to render in white or any other HEX.
- **CJK characters**: matplotlib mathtext does not render CJK; formulas containing CJK are silently skipped during generation. Keep Chinese/English descriptive text in normal text boxes, not in the LaTeX source.

## Generation Pipeline (auto)

When `FORMULA_RENDER_ENABLED` is true (default), the per-page worker:

1. Asks the outline LLM whether this page needs formulas and emits LaTeX source strings in a new `need_formula` field alongside `need_search_image` / `need_ai_image`.
2. Renders each LaTeX string to `<save_dir>/images/<sha1>.png` in parallel with web search and AI image generation. Formula PNGs live alongside search/AI/doc images under `images/` — no subdirectory.
3. Routes the rendered PNGs through the same `reference_image_descriptions` channel as any other image, so the SVG-authoring LLM sees them in the relevant-material block.
4. Auto-includes formulas in the page's final image list (no LLM selection — formulas are deterministic needs, not preferences).
5. Pre-injects each formula into `img_scores` with a fixed `score=10.0` so `extend_relevant_material_node`'s top-N cut naturally keeps them. Formulas do **not** consume a VLM scoring call.
6. Appends every rendered formula's metadata to `<run_id>/formulas.json`.

You do not need to do anything to enable this in generation mode. Just look at the result.

## Phase 3 Modify Mode (manual)

Phase 3 edits SVG in place under `output/<run_id>/slides/`. It does **not** re-run the generation pipeline, so adding or modifying a formula means rendering the PNG manually and updating the SVG `<image>` reference. Use the standalone CLI:

```bash
.venv/bin/python scripts/render_formula.py "<latex>" \
  --out <run_id_dir>/slides [--color #FFFFFF] [--dpi 300]
```

- The first positional argument is the LaTeX source. Surrounding `$...$` is optional and stripped.
- `--out` is the **slides directory** (the parent of `images/`), not the images directory itself. The PNG lands at `<slides>/images/<sha1>.png`, alongside any search/AI/doc images.
- `--color` overrides the foreground color (default `#000000`). Use `#FFFFFF` or another HEX for dark backgrounds.
- `--dpi` overrides render DPI (default `300`). Higher DPI = sharper but larger file.
- The script appends a record to `<run_id_dir>/formulas.json` automatically.

stdout is a single JSON object you can parse:

```json
{
  "path": "/abs/path/to/images/abc123.png",
  "width": 125,
  "height": 38,
  "latex": "E = mc^2",
  "color": "#000000",
  "dpi": 300,
  "relative_href": "images/abc123.png"
}
```

`width` and `height` are the formula's **natural render size** in SVG user units on the 1280×720 slide canvas (already scaled from raw PNG pixels by 96/dpi). They are reference data, not display size — use them to derive the aspect ratio `aspect = width / height` and as a clarity reference. The actual display size is **layout-driven**: decided by the formula's semantic role in the page (main conclusion / inline note / sidebar annotation / etc.) and the container's available space. Resizing to fit the layout is the normal case. See `svg_generator_prompt.txt §9` and `agent-edit.md §8.3` for the full layout-driven procedure.

The full workflow (with file checks, SVG update rules, and the no-auto-export rule) is documented in [agent-edit.md](agent-edit.md) §8.

## Looking up an existing formula's LaTeX

Every rendered formula is recorded in `<run_id>/formulas.json`:

```json
[
  {
    "latex": "E = mc^2",
    "path": "/abs/path/to/images/abc123.png",
    "color": "#000000",
    "dpi": 300,
    "display": true,
    "width": 125,
    "height": 38,
    "first_used_page": 5,
    "rendered_at": "2026-07-28T10:00:00.123456"
  }
]
```

`width` and `height` are the natural render size in SVG user units. Display size in any given page is layout-driven (see `svg_generator_prompt.txt §9`) and usually differs from these numbers.

To modify an existing formula on a page:

1. Read the SVG and find the current `<image href="images/<hash>.png">`.
2. Look up the `<hash>` in `formulas.json` to recover the original LaTeX source.
3. Edit the LaTeX as needed and re-render via `scripts/render_formula.py`.
4. Update the SVG `<image>` element's `href`, `width`, `height` from the new render output. Keep `preserveAspectRatio="xMidYMid meet"`. Treat the formula like a regular image: **rect-then-fill** — draw a container `<rect>` first at `(rx, ry, rw, rh)` to define the slot, then place the `<image>` inside. Compute display size with a single scale factor: pick `s` such that `0 < s ≤ 1.3` (PNGs render at 300 DPI, so 1.3× still looks crisp), set `dw = rendered_width × s`, `dh = rendered_height × s`. Using one `s` for both dimensions preserves aspect ratio automatically — never set `width`/`height` independently. To fill a slot, `s = min(W' / rendered_width, H' / rendered_height, 1.3)` where `W' × H'` is the container's inner space after `padding ≥ 12`. Place the image inside the rect with explicit alignment (left/center/right). Neighboring elements must clear the **container rect boundary**, not the `<image>` box.
5. Verify the formula's bounding box fits inside its intended container with ≥12 SVG units of margin on all sides, does not overlap any other element (title, text, card, chart, decoration, other formula), and stays within the 1280×720 canvas. Position within the slot is your call (left/right/center as the layout demands); the constraint is "fits and doesn't overlap", not "centered".

## Caching

The cache key is `sha1(latex|color|dpi|display)`. Identical formulas across pages or runs hit the same PNG. The cache directory is `<slides>/images/` — formula files share the directory with search/AI/doc images, distinguished by their 40-character sha1 filename. To force re-render, delete the specific `<sha1>.png` file.

## Configuration

Defaults live in [`core/ppt_generator/utils/formula.py`](../../../core/ppt_generator/utils/formula.py) as module-level constants. There are no environment variables for this feature — edit the file to tune:

- `FORMULA_RENDER_ENABLED = True` — set to `False` to disable formula support entirely (generation mode will stop emitting `need_formula`; existing formulas remain valid).
- `FORMULA_RENDER_COLOR = "#000000"` — default foreground.
- `FORMULA_RENDER_DPI = 300` — default DPI. At 300 DPI, a 1.3× display upscale still yields ~231 DPI effective resolution (crisp on projection).
- `FORMULA_RENDER_FONT_SIZE = 14` — matplotlib points; raise for larger natural size, lower for smaller. Natural size in SVG units scales linearly with this value.

## Limitations and Workarounds

| Limitation | Workaround |
|---|---|
| No inline formulas | Place the formula on its own line; surround with normal text above/below. |
| No `align`/`cases`/multi-line | Split into multiple single-line formulas and stack as separate `<image>` elements. |
| CJK in LaTeX silently dropped | Keep CJK descriptive text in `<text>` elements, not in formulas. |
| Fixed color in v1; no auto-detect | For dark backgrounds, use `render_formula.py --color #FFFFFF` in modify mode. |
| PNG not editable in PowerPoint | Treat as picture; modify via re-rendering. Source always recoverable from `formulas.json`. |
| VLM scoring skipped for formulas | Formulas always enter top-N (score 10.0). If a page has many real photos competing for top-N slots, formulas may crowd some out — raise `TOP_N_IMAGE` if needed. |
