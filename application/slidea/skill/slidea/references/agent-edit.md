# Agent-led Page Editing

This document is the standard workflow for editing an existing slidea run — changing text on a page, swapping an image, redrawing a page as a diagram, adjusting layout, etc. **Phase 3 edits SVG files directly under `output/<run_id>/slides/` and never re-runs the generation pipeline.**

## 0. ⚡ Critical Rules (read before every edit)

Phase 3 has two non-negotiable rules. Violating either one silently breaks the user's deliverable. Both are common failure modes — re-read this section before every edit.

### Rule 1 — Defer PPTX export until the user explicitly signals

Phase 3 is a **batch-edit** workflow. After every SVG edit:

- **Do NOT run `svg_to_pptx.py`.** Not "to verify the edit worked". Not "to give the user something to look at". Not "because the edit is small". Never.
- Report the SVG path + a one-line summary of what changed, then **stop and wait** for the next instruction.
- Only run `svg_to_pptx.py` when the user **explicitly** signals completion — e.g. "导出" / "可以导出了" / "完成了" / "都改好了导出吧" / "export" / "done" / "now export the PPT" or equivalent explicit instruction.
- Ambiguous cases ("改完这页就导出" / "差不多可以了" / "应该没别的了") → **ask, don't guess**. Confirm with one focused question before exporting.
- A single user message that bundles an edit + an explicit export request (e.g. "改第5页标题然后导出") → edit the SVG, then export in the same turn.
- **When in doubt: do not export.** Asking is cheap; an unwanted export forces the user to reopen a fresh PPTX and re-track what changed.

Why: re-exporting iterates every page, rewrites the PPTX, and forces the user to reopen the file after each tiny change. Batch the edits, export once at the end.

The full "when to export" procedure is in §5 Step 10.

### Rule 2 — Edit in place, never copy the run directory

Always operate on the SVG files at their original path under `output/<run_id>/slides/`, and always export to the original `<run_id_dir>`. Specifically:

- Do **not** copy the run directory to a new location (e.g. `output/<run_id>_copy/`, `/tmp/<run_id>/`, `~/Desktop/<run_id>/`) and edit there.
- Do **not** copy individual SVG files to a backup path before editing. The pipeline already keeps revision history under `slides/vlm_svg_candidates/` when it needs to; you do not need your own backup.
- Do **not** create a "working" sub-directory inside the run.
- When exporting, pass `<svg_dir>` and `<run_id_dir>` **exactly as they appear in `ppt.json`** to `svg_to_pptx.py`. Do not invent a new output directory.

Why: `ppt.json`'s `pptx_path` is the contract — that path is what the user opens. If you edit a copy or export to a different directory, your changes never reach the file the user opens, and the user sees an unchanged PPTX. The pipeline's `output/<run_id>/` is the single source of truth; honor it.

### Rule 3 — No self-initiated rendering / visual QC

Do **not** render the edited SVG/HTML to PNG, view images, compute pixel stats, or call a vision model to "verify" the page. The only QC is the structural `check_svg_file` JSON check in §5 Step 8. Visual verification is the user's job — they open the PPTX in PowerPoint / Keynote / LibreOffice.

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
        ├── vlm_svg_candidates/     # VLM-review candidate versions (only when ENABLE_VLM_VISUAL_REVIEW=true)
        ├── vlm_screenshots/        # VLM-review screenshots (only when ENABLE_VLM_VISUAL_REVIEW=true)
        └── <page>_vlm_review.json  # VLM-review audit record (only when ENABLE_VLM_VISUAL_REVIEW=true)
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
- **Image edit**: replace one image with another, add a new image, remove a broken image. **See §7 for the full image-replacement workflow**.
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
- **Edit in place** (per §0 Rule 2): operate on the file at `output/<run_id>/slides/<idx+1:02d>_*.svg` directly. Do not copy it elsewhere first, do not create a backup, do not work in a temp directory.

### Step 8. Quality self-check (recommended)

Run the same quality check the generation pipeline uses, on the edited SVG:

```bash
.venv/bin/python -c "from core.ppt_generator.utils.svg_pipeline.quality_checker import check_svg_file; import json; print(json.dumps(check_svg_file('<absolute_path_to_edited_svg>'), ensure_ascii=False, indent=2))"
```

The result is a JSON object. Look at `errors` and `warnings`:

- **Errors** must be fixed — they will block PPTX export or cause silent data loss.
- **Warnings** are advisory — common ones (e.g. a font-family that looks suspicious) can be ignored if you know what you are doing.

This is the **only** QC step. Do not render the SVG to PNG, view images, or run a vision model on the page (§0 Rule 3).

### Step 9. Report the SVG change — 🛑 STOP, do NOT export

🛑 **Do not run `svg_to_pptx.py` in this step.** Not after a single edit, not after several edits, not "just this once to verify". The PPTX is rebuilt only in Step 10, only on explicit user signal. If you are about to call `svg_to_pptx.py` here, stop and re-read §0 Rule 1.

After writing the SVG change and running the QC self-check, report to the user:

- The absolute path of the edited SVG file.
- A one-line summary of what changed on the page.
- Any warnings from the QC step that were intentionally ignored.
- **Explicitly state that the PPTX has not been re-exported yet**, and tell the user how to trigger the export when they are done (e.g. "说『导出』即可生成新 PPTX" / "say 'export' when ready").

Then stop and wait for the next instruction. The user may ask for more edits (loop back to Step 1) or signal completion (proceed to Step 10).

### Step 10. Final export (only on explicit user signal)

🛑 **Pre-flight check before running `svg_to_pptx.py`:**

1. Has the user **explicitly** said to export (e.g. "导出" / "export" / "done" / "可以了")? If not — do not run this step. Go back to Step 9 and report the SVG change instead.
2. Are `<svg_dir>` and `<run_id_dir>` the **original** paths from `ppt.json`? If you are about to pass a copied directory, a temp path, or a backup location — stop. That violates §0 Rule 2. Re-read it.

Run the export **only** when the user explicitly signals completion — e.g. "导出" / "可以导出了" / "完成了" / "都改好了导出吧" / "export" / "done" / "now export the PPT" or equivalent. See §0 Rule 1 for the full rule and ambiguity handling.

When the signal is given:

```bash
.venv/bin/python scripts/svg_to_pptx.py "<svg_dir>" -o "<run_id_dir>" -n "<topic>"
```

- `<svg_dir>`: from `ppt.json`'s `svg_dir` field (absolute path).
- `<run_id_dir>`: the `output/<run_id>/` directory (parent of `slides/`).
- `<topic>`: from `ppt.json`'s `topic` field, **passed verbatim**. The exporter applies the same `sanitize_filename` transform the generation pipeline uses (spaces → underscores, illegal chars stripped), so the re-export lands on exactly the same path the original pipeline wrote and truly overwrites it. Do not pre-sanitize `-n` yourself — that would double-transform and risk divergence.

The exporter collects every `*.svg` in `<svg_dir>` (natural-sorted by the numeric prefix), re-exports a new PPTX at `<run_id_dir>/<sanitized_topic>.pptx`, and overwrites the previous PPTX.

After the export, tell the user:

- The absolute path of the new PPTX.
- A short recap of all edits since the last export (not just the last one).
- Any warnings from earlier QC steps that were intentionally ignored.

If the user wants to verify visually, point them at the PPTX path; PowerPoint / Keynote / LibreOffice Impress all open it natively and edits are preserved as DrawingML.

If the user requests further edits after the export, loop back to Step 1 — and again defer the next export until they explicitly signal it.

## 6. Common Pitfalls

- **Auto-exporting after every edit.** Despite §0 Rule 1, agents sometimes run `svg_to_pptx.py` right after editing "to verify" or "to give the user something to look at". Do not. The export deferral rule exists because re-exporting rewrites the whole PPTX and forces the user to reopen the file after each tiny change. Report the SVG change (Step 9), then wait for the explicit export signal.
- **Copying the run directory before editing.** Some agents "play safe" by copying `output/<run_id>/` to a sibling directory and editing the copy, or by exporting to a different output directory. This breaks the contract: `ppt.json` still points at the original directory, so the user opens an unchanged PPTX. Always edit the original SVG files in place and export to the original `<run_id_dir>` (§0 Rule 2).
- **Rewriting the whole SVG when only a `<g>` block changed.** This is the #1 cause of broken Phase 3 edits. The original generation went through quality check; rewriting bypasses that. Use Edit with a tight `old_string`/`new_string` pair whenever possible.
- **Renaming the SVG file.** The `{idx+1:02d}_` prefix is load-bearing — the exporter sorts by it, patch-render matches by it. Even renaming `05_xxx.svg` to `5_xxx.svg` breaks page order.
- **Forgetting to copy image files into `slides/images/`.** An `<image href="images/foo.png">` that has no matching on-disk file fails quality check with "Image file not found". The export pipeline then drops the image silently or breaks.
- **Editing a template-protected element.** not to touch `header`, `page-title-text`, `main-content-frame`, etc. Phase 3 edits should respect the boundary — moving the title bar looks fine on one page but breaks consistency with sibling pages.
- **Skipping the QC self-check.** Small SVG mistakes (an unescaped `&`, a `<tspan>` for a line break, a stray `rgba()`) silently fail PPTX export. Running the QC takes seconds and surfaces every hard error.
- **Using a non-template color.** Pulling a hex from memory or from another project's palette breaks visual consistency. Always copy color values from the current template SVG's `data-description`.

## 7. Image Replacement Workflow

Use this workflow whenever the user asks to replace, swap, or add an image on a page. It covers the full loop: clarify intent → acquire the image → place it under `slides/images/` → update the SVG.

### Step 7.1 Ask what image the user wants (only if underspecified)

If the user request is vague ("换张图" / "换个好看的" / "换成产品图" without specifying which product or angle), ask **one focused question** to find out what image the user actually wants — subject, mood, any hard constraints they care about. Keep it natural; do not turn it into a multi-dimensional checklist.

Skip this step if the user already gave a URL, a local file path, or a specific enough subject.

### Step 7.2 Acquire the image

Pick the path that matches the user's intent. In all paths, `<images_dir>` is `output/<run_id>/slides/images/`.

**A. User gave a URL** → download it:

```bash
.venv/bin/python -c "
import asyncio
from core.ppt_generator.utils.common import download_image
asyncio.run(download_image('<URL>', '<images_dir>'))
"
```

`download_image` auto-detects the extension, handles anti-leech headers, converts unsupported formats (avif/webp → jpg), and falls back to a placeholder if the URL fails.

**B. User gave a local file** → copy it:

```bash
cp "<user_file>" "<images_dir>/<name>.<ext>"
```

Pick a filename that does not collide with existing images in the directory.

**C. User described what they want → image search (three-tier fallback)**

1. **First**, try any other image-search tool or skill the user has available.
2. **If none is available or it fails**, fall back to slidea's built-in Tavily search:

   ```bash
   .venv/bin/python -m core.utils.search "<query>" --search-image --max-results 5
   ```

3. **If the Tavily fallback also fails** (e.g. `TAVILY_API_KEYS` not configured in `.env`, or the call errors out), stop and tell the user clearly: "当前没有可用的搜图工具，请直接给一个图片 URL 或本地文件路径。"

When the search returns multiple results, **show the candidates to the user and let them pick**. Output each candidate directly as a plain markdown image — no list markers, no code fences:

![<alt>](<url>)

For `<alt>`:

- If the search tool returned a description for the image, use that description as the image's caption.
- If not, fall back to `候选 1`, `候选 2`, ... so the user has a label to refer to.

Wait for the user to choose (by label or by URL). Do not silently pick one on the user's behalf.

After the user picks a URL, download it via path A.

**D. User wants AI-generated image → only if configured**

If `.env` has `IMAGE_GEN_PROVIDER` and related fields configured:

```bash
.venv/bin/python -c "
import asyncio
from core.ppt_generator.utils.image import generate_ai_image
asyncio.run(generate_ai_image('<prompt>', '<slides_dir>'))
"
```

Note: `generate_ai_image` writes to `<slides_dir>/images/`, so pass the parent `slides/` directory, not `images/` itself.

If AI image generation is not configured, tell the user it is unavailable and suggest path C or A instead. Do not try to enable it from inside the skill — that is a user-side `.env` change.

### Step 7.3 Verify the image landed

After the download or copy, confirm the file actually exists and is a real image (not an error page saved as a file):

```bash
ls -lh "<images_dir>/<filename>"
file "<images_dir>/<filename>"
```

If the file is missing or not an image, redo Step 7.2 with another source.

### Step 7.4 Update the SVG

Locate the `<image>` element on the target page (typically inside an `image-panel`, `card-*-image`, or similar group). Update its `href` to `images/<filename>` (relative path).

If the new image has a different aspect ratio than the old one, also update the `<image>` element's `width`, `height`, and `preserveAspectRatio` so the image fills the slot without stretching. Do not change surrounding template-protected elements (see §5 Step 5).

### Step 7.5 QC + report (per §5 Step 8 / Step 9)

Run the QC self-check on the edited SVG — it will surface "Image file not found" or bad-reference errors. Per the export deferral rule (§0), do NOT re-export the PPTX. Report the SVG path + the new image filename + a one-line summary of the change, then wait for the next instruction.

## 8. Formula Workflow

Use this workflow whenever the user asks to add, modify, or remove a **math formula** on a page. Formulas are rendered PNG images (not editable PowerPoint equation objects), embedded via `<image>` and stored under `slides/images/<sha1>.png` alongside search/AI/doc images. Read [formula-render.md](formula-render.md) for the full feature background and limitations before proceeding.

### Step 8.1 Identify the user intent

Formula edit requests fall into four categories. Confirm which one before acting:

- **Add a new formula**: user provides LaTeX (e.g. "在第三页加上 $E=mc^2$"). Skip to Step 8.2 with the user-given LaTeX.
- **Modify an existing formula's content**: user says "把那个积分改成…" or references a formula already on the page. First locate the current `<image href="images/<hash>.png">` in the SVG, then look up `<hash>` in `<run_id>/formulas.json` to recover the original LaTeX. Ask the user what to change, then re-render.
- **Modify an existing formula's style**: change color (e.g. "白色背景上要白字") or DPI. Same lookup as content modify, then re-render with `--color` / `--dpi`.
- **Remove a formula**: user says "删掉那个公式". Skip to Step 8.4.

If the user's request is ambiguous ("改一下那个公式" without saying which), ask one focused question — do not guess.

### Step 8.2 Render the formula

Render via the standalone CLI. `<slides_dir>` is `output/<run_id>/slides/` (the parent of `images/`, **not** `images/` itself).

```bash
.venv/bin/python scripts/render_formula.py "<latex>" --out <slides_dir> [--color #FFFFFF] [--dpi 300]
```

Notes:
- Surrounding `$...$` is optional and stripped automatically.
- For dark backgrounds use `--color #FFFFFF` (or another HEX matching the page text color from the template's `data-description`).
- Default DPI is 300 (matches the generation pipeline). Higher values produce sharper output at the cost of larger files; lower values save bytes when the formula will only be displayed small.
- The script appends a record to `<run_id>/formulas.json` automatically — do not write to that file by hand.
- stdout is a single JSON object: `{"path", "width", "height", "latex", "color", "dpi", "relative_href"}`. Parse it; do not extract substrings from log lines.

If the script exits non-zero (invalid LaTeX, CJK in source, write error), tell the user the render failed and what triggered it; do not silently fall back to inserting the LaTeX as `<text>` (per the SVG contract, LaTeX is banned inside `<text>`).

### Step 8.3 Verify + update SVG

Follow §7.3 (verify file exists) and §7.4 (update `<image>` element) with these specifics. The same layout-driven principle from `svg_generator_prompt.txt §9` applies — **formula sizing follows layout, not the other way around**.

- The `<image href>` must use the relative form `images/<hash>.png` — **not** an absolute path. Use the `relative_href` field from the CLI's JSON output.
- Always use `preserveAspectRatio="xMidYMid meet"` for formulas (never `slice` — it would crop the formula).
- **Treat the formula like a regular image: rect-then-fill.** Before placing the `<image>`, draw a container `<rect>` (or `<g>`-wrapped slot) defining the formula's slot at `(rx, ry, rw, rh)`. The container rect's boundary is what neighboring elements must clear — not the `<image>` box (which has transparent `meet` margins around the visible formula).
- **Compute display size with a single scale factor; cap upscaling at 1.3×.** Pick `s` such that `0 < s ≤ 1.3` and compute `dw = width × s`, `dh = height × s`. Using one `s` for both dimensions preserves the aspect ratio mathematically — never set `width`/`height` independently. PNGs render at 300 DPI, so a 1.3× upscale still gives ~231 DPI effective resolution (visually crisp); beyond 1.3× pixelates noticeably.
  1. Decide the formula's layout role in its target position (main conclusion / inline note / sidebar annotation / etc.) and pick the container it lives in.
  2. Compute the container's available space `W' × H'` after subtracting `padding ≥ 12` on all sides.
  3. `s = min(W' / width, H' / height, 1.3)`. The `1.3` term is the upscale cap. For a smaller formula (sidebar note, inline equation), pick a smaller `s` (e.g. 0.6-0.8 × the fill value). Constraint: `dw ≤ W'` and `dh ≤ H'` must both hold, and `s ≤ 1.3`.
  4. Place the `<image>` inside the container rect with an explicit alignment: left (`image_x = rx + padding`, `image_y = ry + (rh - dh) / 2`), centered (`image_x = rx + (rw - dw) / 2`), or right (`image_x = rx + rw - padding - dw`).
- **Position hard rules**: the formula's container rect `(rx, ry, rw, rh)` must satisfy:
  - Stay inside its parent layout area with ≥12 SVG units of margin on all sides.
  - Not overlap any title bar, header/footer, `<text>` element, card, chart, illustration, other formula's container rect, or fixed decoration on the page. Neighbors clear the **container rect boundary**, not the `<image>` box.
  - Not exceed the 1280×720 canvas.
  - Multiple formulas on the page: their container rects must have ≥24 SVG units of net vertical/horizontal gap.
- **Position hard rules**: the formula `<image>`'s bounding box (x, y, dw, dh) must satisfy:
  - Stay inside its container with ≥12 SVG units of margin on all sides.
  - Not overlap any title bar, header/footer, `<text>` element, card, chart, illustration, other formula, or fixed decoration on the page.
  - Not exceed the 1280×720 canvas.
  - Position within the container is up to you (left/right/center as the layout demands); the constraint is "fits and doesn't overlap", not "centered".
- Place the `<image>` inside a semantic group (e.g. `<g id="formula-display">`). Don't drop it as a stray top-level element.

### Step 8.4 Remove a formula

Just delete the `<image>` element (and its containing group if the group becomes empty) from the SVG. The cached PNG under `images/` and the record in `formulas.json` are intentionally left in place — they enable reuse if the user changes their mind, and they are not bundled into the PPTX if no SVG references them.

### Step 8.5 QC + report (per §5 Step 8 / Step 9)

Run the QC self-check on the edited SVG. Per the export deferral rule (§0), do NOT re-export the PPTX. Report:
- The absolute path of the edited SVG.
- The LaTeX source and the rendered image filename.
- Whether the formula is new, modified, or removed.
Then wait for the next instruction.

### Common pitfalls (formula-specific)

- **Treating the formula PNG as editable text** in PowerPoint. It is a picture; to edit content, re-render from LaTeX.
- **Forgetting `--color` on dark backgrounds**. Default is `#000000`; on a dark panel the formula becomes invisible. Always check the page's text-color hint in the template's `data-description`.
- **Using `slice` instead of `meet`** for `preserveAspectRatio`. `slice` fills the box by cropping; for formulas you want the entire image visible, so use `meet`.
- **Inline placement**. A formula image placed mid-sentence will not baseline-align with surrounding `<text>` and will look misaligned. Place formulas on their own line.
- **Inserting LaTeX into `<text>`** as a fallback when rendering fails. This violates the SVG contract (§6). Tell the user the render failed and offer alternatives instead.

