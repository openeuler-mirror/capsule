# Agent-led Page Editing

This document is the standard workflow for editing an existing slidea run — changing text on a page, swapping an image, redrawing a page as a diagram, adjusting layout, etc. **Phase 3 edits SVG files directly under `output/<run_id>/slides/` and re-exports the PPTX — it never re-runs the generation pipeline.**

## 1. When to Read diagram-basics.md

Read [diagram-basics.md](diagram-basics.md) when the user's edit request involves **drawing a diagram** — keywords like "draw", "redraw as architecture/flowchart/sequence", "add a relationship diagram", "visualize the call flow", or any request that implies shapes and arrows rather than text.

Do not read diagram-basics.md for edits that stay within existing layout: text rewording, image replacement, color tweak, position adjustment. Those edits use this file alone.

When diagram-basics.md is read, it tells you which (if any) `diagram-layouts/<type>.md` to read next. Follow that chain — do not jump to a layout doc without going through diagram-basics.md.

## 2. `output/<run_id>/` Directory Structure

Every slidea run produces one directory. Knowing what each file is for lets you pull extra context when the required files in §4 are not enough.

```
output/<run_id>/
├── <topic>.pptx                    # Final PPTX deliverable
├── run.json                        # CLI invocation parameters (input snapshot)
├── ppt.json                        # Path linker: run_id/topic/render_mode/slides_dir/svg_dir/template_name/pptx_path
├── checkpointer.sqlite*            # LangGraph resume state (auto-removed on success; present only after a failed run)
├── outline/
│   └── outline.json                # Full outline: per-page {title, abstract, type, index, reference_doc, reference_images, source}
├── research/                       # Varies by research mode:
│   ├── research.json               #   simple mode: Tavily search results
│   ├── deep_report.md              #   deep mode: deep research final report
│   └── todo_*.txt                  #   deep mode: chapter-planning snapshots
├── references/
│   ├── parsed_requirements.json    # Structured user request (audience/topic/goal/urls/missing_info)
│   ├── images.json                 # Image paths extracted from user-provided sources
│   ├── references.txt              # URL/file text extracted from user-provided sources
│   └── references_all.txt          # Combined raw_content + deep_report + search_results used during thought generation
├── thought/
│   └── thought.md                  # PPT-wide writing strategy, page-by-page intent
└── slides/
    ├── images/                     # Image assets (SVG references these as images/xxx.png)
    ├── prompts/                    # Per-page full LLM input snapshot
    │   └── <idx>_<title>.txt       #   Contains: SVG contract + per-page outline + reference_doc + image materials + template usage rules + full template SVG
    └── svg/                        # SVG source files (single source of truth)
        ├── 01_<title>.svg          #   Filename format: {idx+1:02d}_<title>.svg
        ├── ...
        ├── vlm_svg_candidates/     # VLM-review candidate versions
        ├── vlm_screenshots/        # VLM-review screenshots
        └── <page>_vlm_review.json  # VLM-review audit record
```

## 3. `type` Field Meanings

`outline/outline.json` lists every page with a `type` field. The type drives which template elements a page uses, so it changes what you can and cannot move.

| Value | Type | Structure typical for this type |
|---|---|---|
| `1` | `CONTENT` | Title bar + content area. Carries `reference_doc` and `reference_images`. Most edits land here. |
| `2` | `TOC` | Title bar + list of chapter titles. Usually no images, sparse layout. |
| `3` | `SEPARATOR` | Big chapter title centered, minimal decoration. Acts as a divider between chapters. |
| `4` | `COVER_THANKS` | Cover page or back-cover thanks page. Largest typography, hero layout. |

The `source` field on each page is the chapter index (`-1` for non-chapter pages like cover/TOC).

## 4. Required Reading Before Editing

Read these five files every time, before making any change:

| # | File | What you get |
|---|---|---|
| 1 | `ppt.json` | Absolute paths to `slides_dir`, `svg_dir`, `pptx_path`, plus the `template_name` to use for color/font reference |
| 2 | `references/parsed_requirements.json` | The user's actual structured request: `audience`, `topic`, `goal`, `urls`, `missing_info` |
| 3 | `thought/thought.md` | The PPT-wide narrative strategy — explains why each page exists and what it should say |
| 4 | `outline/outline.json` | The full outline, with the target page's `title`, `abstract`, `type`, `reference_doc`, `reference_images` |
| 5 | `slides/<idx+1:02d>_*.svg` | The current SVG implementation of the page being edited |

**Strongly recommended as a sixth file**: `slides/prompts/<idx+1:02d>_<title>.txt` for the page being edited. This is a single-file snapshot of everything that was fed to the LLM when the page was originally generated — it contains the full SVG compatibility contract, the page's outline entry, its `reference_doc`, available image materials, the template usage rules (including the protected-id list), and the complete template SVG with `data-description` annotations. Reading this one file can replace reading several of the files above and is the authoritative source for the constraints the page was generated against.

Optional, when you need extra context:

- `references/references_all.txt` — all source material combined (raw user-provided text + deep research report + search results). Useful when the user asks for content changes that need to reference the original research.
- `research/deep_report.md` — the deep research report (only present if the run used deep research mode).
- `slides/<other_page>.svg` — sibling pages from the same run. Useful for matching visual style across pages.

## 5. Standard Edit Workflow

### Step 1. Read the required files

Always start with the five (or six) files in §4. Editing without reading them is the most common source of broken Phase 3 edits.

### Step 2. Understand the user's specific edit request

Identify exactly what the user wants changed. Common categories:

- **Text edit**: reword a title, fix a typo, change a number.
- **Image edit**: replace one image with another, add a new image, remove a broken image.
- **Layout adjustment**: move a card, resize a column, change spacing.
- **Color tweak**: change one element's color (rare — usually the template palette is right).
- **Drawing**: add or redraw a diagram on the page (architecture / flowchart / etc.).

**Minimal-edit principle.** Only change what the user asked to change. Do not "improve" the rest of the page, do not rewrite the SVG from scratch unless the user explicitly asks for a full redraw. Smaller edits preserve the original visual coherence and are less likely to break the export.

**Ambiguity handling.** When the user's edit request is underspecified or has multiple plausible interpretations, ask the user before editing rather than guessing. Common ambiguity patterns:

- The user names a location that is already occupied (e.g. "add a diagram in the bottom-right" when the bottom-right card already has content).
- The user asks to "improve" or "fix" a page without saying what is wrong.
- The user references an element by a description that does not map cleanly to one `<g id>` (e.g. "the section about X" when X appears in two places).

In each case, ask one focused question with the options you are considering. The reason for asking rather than guessing: a wrong guess either violates the minimal-edit principle (you rewrote content the user wanted kept) or forces the user to undo your change and re-explain — both are more expensive than a quick clarifying question.

### Step 3. Branch on whether the edit involves drawing

If the edit involves drawing (shapes, arrows, a diagram), read [diagram-basics.md](diagram-basics.md) and follow its responsibility chain to pick the right layout doc.

If not, continue with this workflow alone.

### Step 4. Locate the edit region in the SVG

The SVG is organized by semantic `<g id="...">` groups. Locate the one(s) the user wants changed:

- `background`, `slide-background`, `slide-border` — page background and border.
- `header`, `page-title-text` — title bar.
- `main-content-frame`, `content-frame-outer`, `content-frame-inner` — the decorative frame around the content area.
- `cards-container`, `card-1-*`, `card-2-*`, ... — content card groups.
- `footer` — page footer.
- Any custom group the original generator created (e.g. `step-1`, `chart-area`, `image-panel`).

Use Edit with a specific `old_string` taken from the group you are changing. Do not rewrite the whole file when a single `<g>` block needs editing.

### Step 5. Respect template-protected elements

The following element IDs are **template-protected**. Do not move, scale, delete, redraw, or restyle them:

- `background`, `slide-background`, `slide-border`
- `header`, `page-title-text`
- `top-accent-bar`, `bottom-accent-bar`
- `template-*` (any ID starting with `template-`)
- `main-content-frame`, `content-frame-outer`, `content-frame-inner`
- Any ID starting with `content-frame-` or `title-accent`

For color reference: each color-carrying element in the slide template SVG has a `data-description` annotation (e.g. `主色：#3F3933；使用要求：...`, meaning "Primary color: #3F3933; usage: ..."). When you need a color for a new element, copy the hex from the template's `data-description`, not from memory. The template SVG is appended to the per-page prompt file (`slides/prompts/<idx>_<title>.txt`) and is also at `assets/svg_templates/<template_name>.svg`.

### Step 6. Honor the SVG compatibility contract

The full contract the page was generated against is in `slides/prompts/<idx>_<title>.txt`. The highlights most often tripped over during edits:

- Canvas: `width="1280" height="720" viewBox="0 0 1280 720"`.
- HEX colors + `fill-opacity` / `stroke-opacity` / `stop-opacity`. No `rgba()`.
- No `<style>`, `class`, `@import`, `@font-face`, `<link>`, `<tspan>` (for line breaks), `<mask>`, animation tags.
- Font whitelist: Microsoft YaHei / SimHei / SimSun / Arial / Calibri / Times New Roman / Georgia / Consolas / sans-serif / serif / monospace.
- Multi-line text: split into multiple `<text>` elements, each with its own `x` / `y`. Never use `<tspan>` for line breaks.
- Image `href` must be `images/xxx.png` (relative) and the file must exist under `slides/images/`. Data URIs also accepted.
- XML escaping: `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`.
- Typographic symbols: Unicode directly (→ ← ⇒ ©). No HTML entities (`&nbsp;`, `&mdash;`) or LaTeX (`$\rightarrow$`).

When drawing is involved, [diagram-basics.md §5](diagram-basics.md#5-slidea-svg-compatibility-constraints) has the same list in a more condensed form.

### Step 7. Write the change

Use the Edit or Write tool against the target SVG file.

- **Path**: `output/<run_id>/slides/<idx+1:02d>_*.svg`.
- **Filename prefix**: `{idx+1:02d}_` (e.g. `05_`) is load-bearing — it is what the export pipeline uses to sort pages and what patch-render uses to find a specific page. Preserve it exactly. The title suffix can be anything.
- **New images**: if the edit introduces a new image, place the file under `output/<run_id>/slides/images/` and reference it as `images/<filename>` in the SVG. The export pipeline inlines images into `data:` URIs at export time, so on-disk SVGs stay editable.

### Step 8. Quality self-check (recommended)

Run the same quality check the generation pipeline uses, on the edited SVG:

```bash
.venv/bin/python -c "from core.ppt_generator.utils.svg_pipeline.quality_checker import check_svg_file; import json; print(json.dumps(check_svg_file('<absolute_path_to_edited_svg>'), ensure_ascii=False, indent=2))"
```

The result is a JSON object. Look at `errors` and `warnings`:

- **Errors** must be fixed — they will block PPTX export or cause silent data loss.
- **Warnings** are advisory — common ones (e.g. a font-family that looks suspicious) can be ignored if you know what you are doing.

### Step 9. Re-export the PPTX

After writing the SVG change, regenerate the PPTX:

```bash
.venv/bin/python scripts/svg_to_pptx.py "<svg_dir>" -o "<run_id_dir>" -n "<topic>"
```

- `<svg_dir>`: from `ppt.json`'s `svg_dir` field (absolute path).
- `<run_id_dir>`: the `output/<run_id>/` directory (parent of `slides/`).
- `<topic>`: from `ppt.json`'s `topic` field. The exporter sanitizes it into a filename.

The exporter collects every `*.svg` in `<svg_dir>` (natural-sorted by the numeric prefix), re-exports a new PPTX at `<run_id_dir>/<topic>.pptx`, and overwrites the previous PPTX.

### Step 10. Report the result

Tell the user:

- The absolute path of the new PPTX.
- A one-line summary of what changed on the page.
- Any warnings from the QC step that were intentionally ignored.

If the user wants to verify visually, point them at the PPTX path; PowerPoint / Keynote / LibreOffice Impress all open it natively and edits are preserved as DrawingML.

## 6. Common Pitfalls

- **Rewriting the whole SVG when only a `<g>` block changed.** This is the #1 cause of broken Phase 3 edits. The original generation went through VLM review and quality check; rewriting bypasses that. Use Edit with a tight `old_string`/`new_string` pair whenever possible.
- **Renaming the SVG file.** The `{idx+1:02d}_` prefix is load-bearing — the exporter sorts by it, patch-render matches by it. Even renaming `05_xxx.svg` to `5_xxx.svg` breaks page order.
- **Forgetting to copy image files into `slides/images/`.** An `<image href="images/foo.png">` that has no matching on-disk file fails quality check with "Image file not found". The export pipeline then drops the image silently or breaks.
- **Editing a template-protected element.** The VLM-fix step in the original pipeline is told not to touch `header`, `page-title-text`, `main-content-frame`, etc. Phase 3 edits should respect the same boundary — moving the title bar looks fine on one page but breaks consistency with sibling pages.
- **Skipping the QC self-check.** Small SVG mistakes (an unescaped `&`, a `<tspan>` for a line break, a stray `rgba()`) silently fail PPTX export. Running the QC takes seconds and surfaces every hard error.
- **Using a non-template color.** Pulling a hex from memory or from another project's palette breaks visual consistency. Always copy color values from the current template SVG's `data-description`.
