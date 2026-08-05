---
name: slidea
description: "AI-Powered PPT generation and editing. Use for PPT creation where an agent needs full-run control over parse/research/outline/render flows. Also use for editing an existing slidea run — rewording text, swapping images, adjusting layout, or redrawing a page as an architecture / flowchart / sequence / structural diagram. Generates a native editable PPTX from a topic, files, or URLs, with an optional document-processing phase that consolidates provided documents into a single structured markdown. Trigger this skill whenever the user mentions slidea or a previously generated PPT, asks to modify / redraw / change a slide, or wants any diagram (architecture, flowchart, sequence, ER, org chart, etc.) drawn onto an existing slide."
---

# Slidea

Use the directory containing this SKILL.md as the Slidea skill directory (referred to as `<SLIDEA_DIR>`), and run all commands from there.

## Runtime Rule

- This skill must be run exclusively using the Python interpreter inside `.venv`. Both the `.venv` directory and the skill scripts are located in `<SLIDEA_DIR>`.
- Do not use system `python` or `python3` for pipeline commands after the environment check.
- Unix-like example: `.venv/bin/python`
- Windows example: `.venv/Scripts/python.exe`

## Render Route

Slidea renders slides as SVG and exports native editable PPTX. The SVG route is the default and only render route exposed to skill consumers; no `--render-mode` flag is needed. Do not pass `--render-mode` and do not mention any alternative render route.

## Workflow Overview

### Step 0: Clarify the PPT request (mandatory, before any pipeline)

Before entering Phase 1 or Phase 2, confirm the following with the user. These are not optional — they directly affect document-processing quality and PPT generation, and must be carried into `preprocess(topic=...)` and the `--text` parameter.

1. **Audience** (e.g. technical team / academic / management / non-technical). Determines depth and terminology.
2. **Goal** (e.g. deep technical explanation / project kickoff / industry trend briefing / general science popularization). Determines focus and framing.
3. **Topic / scope** — the concrete subject and any required coverage areas.
4. **Style / length** (optional) — page count, tone, visual style, if the user specified any.

Assemble these into a single **"understood PPT request"** string — a coherent request consolidating the user's intent (audience + goal + topic + style), not a verbatim echo when the user's words are vague. This string is reused as the `topic` for `preprocess()` and as the `--text` body for the PPT pipeline, so the same confirmed context flows through both phases.

Only after the user confirms, proceed to the entry-point decision below.

### Entry point

Decide the entry point by what the user provided:

- **User provided documents** (local files, URLs, or directories): Execute Phase 1 **Document Processing** first. Pass the confirmed "understood PPT request" as `topic` to `preprocess()` — it flows into the summarization LLM prompt and shapes which content is treated as relevant. After the user reviews and accepts the resulting `structured.md`, proceed to Phase 2.
  - **Phase 1 only - hard boundary**: `topic` carries intent only (audience/goal/topic/style) - never document summaries, outlines, or digested points. While building this string, do not open or read any document content; pass paths to `preprocess()` and let doc-process parse and digest it.
- **User only gave a topic** (no documents): Skip Phase 1 and directly execute Phase 2: **PPT Generation**.

If the user supplied a reference PPTX and explicitly wants the new deck to follow its style, execute **Phase 0: Reference Style Material Preparation** before Phase 1/2. If no reference PPTX was supplied, or the user did not request style imitation, skip Phase 0 and keep the existing workflow unchanged.

For style mode, choose one new unique `<SESSION_ID>` before Phase 0. Prefer a timestamp plus a short random suffix so it cannot collide with an earlier run. Use that exact id for both the temporary style-pack directory and the Phase 2 `--session-id`; do not generate or substitute a different id between phases unless a collision is detected. If it is occupied, choose another new id and prepare the style pack under the matching new temporary directory before starting Phase 2.

---

## Phase 0: Reference Style Material Preparation

Read [references/style-pack.md](references/style-pack.md) and follow it completely.

Phase 0 converts the user's reference PPTX into editable SVG material and an advisory `asset-inventory.json`. The converter must not generate `style-pack.json` or PNG previews. The Agent reads SVG code, chooses a small set of structurally distinct pages, and authors the cross-page design grammar plus each page's type/density/structure/layout rules. The global grammar must describe `text_container_usage` separately from corner geometry: whether text is normally unboxed, placed on filled backgrounds, or enclosed by border-only/filled boxes, and which roles use title bands, labels, callouts, cards, or summary strips. The Agent explicitly authorizes only reusable template decorations in `style-pack.json`. Candidate flags in the inventory are hints, never permission. When the source contains real cover, TOC, separator or thanks pages, include one representative for each available special role even if the general representative-page budget has already been reached; never substitute one special role for another. Run the validator after authoring. Do not rewrite converted SVG geometry.

The Phase 0 working directory is fixed at `/tmp/slidea/style-packs/<SESSION_ID>`. Do not place converted style material in the repository, the user's source directory, another temporary directory, or a prior PPT run.

The resulting `<STYLE_PACK_DIR>` is passed to Phase 2 with `--style-pack` before outline generation. In style mode, outline generation uses an additional prompt and output field to choose `style_reference_id` by page type, density and structure; page type is an exact-match constraint and long decks follow the existing chapter split and bounded batches. The choices are saved in `outline/outline.json` before parallel page generation. If the pack has no reference for one target page type, only that page keeps an empty `style_reference_id` and uses the existing built-in template route; other pages continue with the pack. Before fan-out, code copies inherited shell images plus Agent-authorized reusable decorations into `slides/images/style-pack`; all other source-slide body images remain unavailable. After each styled page generation/repair, code restores the reference background, master/layout, title, header/footer, fixed logos, authorized back/front decorations and page-number format. A missing/invalid pack or fixed-asset preflight error still falls back to the existing built-in template workflow for the whole run.

Keep style input and content input strictly separate. The reference PPTX path/URL is used only in Phase 0 and must not appear in Phase 2 `--text`. `--text` is parsed for content documents and URLs; putting the style source there causes its original business text to become writing material. Carry style into Phase 2 only through `--style-pack`. Read the isolation and explicit dual-use rules in [references/style-pack.md](references/style-pack.md).

---

## Phase 1: Document Processing

**When to use**: When the user provides documents (local files, URLs, or directories). Doc-processing is a black box: you call `preprocess()` and get back a final `structured.md` path. Treat it as opaque - do not describe to the user what it did internally, which path it took, or any counts/thresholds it produced. Only the `structured.md` path (and chapter overview, in the full-flow path) is user-facing.

**Read [references/doc_processor/process-doc.md](references/doc_processor/process-doc.md) for the complete pipeline instructions.** When calling `preprocess()`, pass the confirmed "understood PPT request" (from Step 0) as the `topic` argument — it flows into the summarization LLM prompt and shapes which document content is treated as relevant.

### Output contract

- The return value always contains `structured_md_path` — the final `<task_dir>/structured.md`.
- The return value contains `short_circuit` (bool) - a branching signal only. `True` -> `structured.md` is already final, proceed to Phase 2. `False` -> run the full pipeline steps, then Phase 2. Use it only to pick your next step; never describe its meaning or the path taken to the user.
- The return value contains `doc_images` (list): when non-empty, each item has `{path, description, score}` — pass the `doc_images.json` file path to the PPT pipeline via `--image-json`.
- **You do NOT perform any review of `structured.md`** — that is handled entirely inside doc-process. When `preprocess()` returns, `structured.md` is final. Proceed directly to Phase 2.
- **Do not pass the original document paths to the PPT pipeline** — only the `structured.md` produced by doc-processing.
---

## Phase 2: PPT Generation

Run the PPT pipeline to generate the final presentation.

Each logical task is identified by `--session-id` and writes everything (run metadata, research, outline, SVG source, PPTX, LangGraph checkpointer) under an internal `<output_root>/<run_id>/`, where `<run_id>` is auto-generated as `<timestamp>_<semantic-summary>`. Agents use the session id for normal follow-up operations; do not discover or pass `--run-id` unless the CLI reports a legacy session collision. Use a **new** session-id for a fresh full-pipeline run and the **same** session-id for continuation, interrupt replies, staged execution, or patch rendering. With the default `--stages all`, re-submitting `--text` with an existing session is rejected because it would create a duplicate run; non-default staged execution intentionally uses `--text`, `--stages`, and the same session id to reuse earlier stage outputs.

### CLI operation and fresh-run session rules (mandatory)

- The CLI requires exactly one operation: `--text`, `--resume "<user reply>"`, or `--continue`. Never combine them.
- A normal fresh full-pipeline invocation uses `--text` with the default `--stages all`; it must use a session id that has never been used by an earlier run. Prefer `<topic>_<YYYYMMDD_HHMMSS>_<random>` instead of a stable conversational name.
- `--resume` supplies the user's answer to an `input_required` interrupt. It takes the reply as its own argument, uses the same session id, and must not be combined with `--text`.
- `--continue` is a real flag that resumes unfinished LangGraph checkpoint work after timeout or process termination. It uses the same session id and must not be combined with `--text`, `--resume`, or non-default `--stages`.
- A non-default staged invocation uses `--text ... --stages ...`; it may intentionally reuse the same session id so the selected stage can read earlier cached outputs.
- If a fresh full-pipeline invocation reports that the chosen session already belongs to an existing run, treat the id as occupied even when that run completed successfully. Immediately choose a different new id and rerun the requested fresh generation.
- Do not inspect, open, summarize, return, resume, continue, patch, or reuse the old run merely because its session id collided with the new request. Existing output is relevant only when the user explicitly asks to continue, repair, inspect, edit, or retrieve that prior run.
- Reuse an existing session id only for `--continue`, `--resume`, non-default staged execution, patch rendering, or Phase 3 editing when the user intends to operate on that exact prior run.

### Assemble `--text`

`--text` is a single string. The pipeline extracts file paths from this string itself, so assemble it as:

- **If Phase 1 (Document Processing) was run**: `--text` = the understood PPT request text, followed on a new line by `参考文档: <abs_path_to_structured.md>`. **Do not include the original document paths** — only the `structured.md` produced by doc-processing.
- **If Phase 1 was skipped** (topic only, no documents): `--text` = the understood PPT request text.

"Understood PPT request" = the confirmed context from Step 0 (audience + goal + topic + style), written as a coherent request — not a verbatim echo of the user's words when they are vague. **The audience and goal must appear explicitly in this string** so the PPT pipeline receives them; do not strip them down to a bare topic.

### Pre-run user reminder (mandatory)

Before invoking the pipeline, you **must** send a short message to the user explaining that the run will take a while and asking them to be patient. Example:

> Starting PPT generation. This typically takes 15-60 minutes (parsing, research routing, thought, outline, per-page SVG generation, quality checks, PPTX export) and involves many model calls. Please be patient — I'll report back when generation completes.

Do not silently start the pipeline. The user must know up front that this is a long-running operation.

### Timeout requirement (mandatory)

The PPT pipeline makes many sequential LLM calls (parsing, research routing, thought, outline, per-page SVG generation, quality checks, PPTX export). End-to-end generation takes 15-60 minutes. Long runs are normal, not a sign of failure.

When invoking the pipeline through a shell tool that accepts a timeout:

- **Hard minimum: 15 minutes** (`timeout 15m` or equivalent). Never use anything below 15 minutes.
- **Hard maximum: 60 minutes** (`timeout 60m`). Do not exceed 60 minutes; if the run has not finished by then, something is wrong and you should investigate rather than wait longer.
- **Recommended default: 60 minutes** (`timeout 60m`).
- If the host tool does not accept a timeout flag, run the command in background mode and poll for completion instead of relying on a default short timeout.

This rule applies to **every** command in this section.

### Commands

**Full pipeline**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --session-id <id>
```

When Phase 0 produced a style pack, append `--style-pack <STYLE_PACK_DIR>` as shown in [references/style-pack.md](references/style-pack.md).

Before running a style-mode command, inspect the final `--text` value and remove the reference PPTX path/URL, `<STYLE_PACK_DIR>`, converted SVG paths, and `style-pack.json` path. Include only the requested topic, purpose, audience, content requirements, and content sources the user actually wants read for facts. Never add a style source to `--text` merely to remind the pipeline which visual style to follow.

`<PPT request>` is the single string assembled per "Assemble `--text`" above:
- If Phase 1 ran: the understood PPT request, followed on a new line by `参考文档: <abs_path_to_structured.md>` (the doc-processing output — **not** the original document paths).
- If Phase 1 was skipped: the understood PPT request text only.

`--research-mode`:
- **Phase 1 ran (documents provided)**: pass `--research-mode "skip"`. The material is already consolidated in `structured.md`, so the PPT must be generated directly from it — **no additional data searching / research**.
- **Phase 1 skipped (topic only)**: omit the flag (or set it explicitly per `references/advanced-params.md`), so the pipeline may research the topic online as needed.

`--image-json`:
- **Phase 1 ran and `doc_images` is non-empty**: pass `--image-json <abs_path_to_doc_images.json>`. The pipeline copies this file into the run directory and uses it as a document image pool — each PPT page can select relevant images from it. If `doc_images` is empty, omit the flag.
- **Phase 1 skipped**: omit the flag. No document images are available.

**Resume after `input_required`**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "<user reply>" \
  --session-id <same_session_id> \
  --research-mode <same_value_as_initial_run>
```

**Continue after timeout, process termination, or lost shell connection**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --continue \
  --session-id <same_session_id>
```

`--continue` reopens the existing checkpoint and completes unfinished graph tasks. Do not pass `--text`, `--style-pack`, `--image-search`, or other replacement settings when continuing; the original run configuration and immutable style-pack snapshot are reused. `--resume` is only for supplying a user's answer to an explicit LangGraph `input_required` interrupt. It is not timeout recovery.

If `--continue` returns `resume_unavailable`, do not start a second full run with the same session and do not delete either run directory. Read [references/patch-render.md](references/patch-render.md) and use `patch_render_missing.py --session-id <same_session_id>` to regenerate only missing pages when an outline exists.

**`--research-mode` inheritance (required)**: If the initial run set `--research-mode` (e.g. `skip`, `simple`, or `deep`), the resume command **must pass the exact same value**. Do not drop the flag, do not change the value, and do not re-ask the user — the choice made at the start of the session carries through every `--resume` call for that `--session-id`. Dropping or altering it mid-session can cause the pipeline to switch research behavior halfway through and produce inconsistent results.

If the pipeline returns `input_required` or `missing_required_info`, you must stop autonomous execution immediately and ask the user instead of continuing on your own. Your only allowed behavior is:
1. show the question, missing information request, or options to the user;
2. wait for the user's explicit answer or selection;
3. resume using the same `--session-id` (and the same `--research-mode` as the initial run) after the user responds.

### Output

After a successful run, the final PPTX is at the path returned in the structured CLI result. Run metadata is stored in `ppt.json` at `output/<run_id>/ppt.json` — see [references/caching-and-paths.md](references/caching-and-paths.md) for the full directory layout and field schema.

**No visual QC after generation.** Do not render the SVG/HTML to PNG, view images, compute pixel stats, or call a vision model to "verify" any page.

The CLI also prints a completion block to stdout that surfaces the deliverable and its editable source:

```
>>> 【当前步骤】 导出 PPT 完成
>>> 生成PPT结束
>>> 生成文件：<abs_path_to_pptx>
>>> 源文件目录：<abs_path_to_slides_svg>
```

The "源文件目录" line points at `<run_id>/slides/` — the editable on-disk SVG pages that produced the PPTX. Image inlining into `data:` URIs happens at PPTX export time inside a temporary directory, so on-disk SVGs stay small and editable.

The output root is `<SLIDEA_DIR>/output/` by default; set `OUTPUT_DIR` in `.env` to redirect every run, cache, and intermediate artifact to a different directory. See [references/advanced-params.md](references/advanced-params.md) for details.

---

## Phase 3: Agent-led Page Editing

After Phase 2 produces a PPTX, the user may ask for page-level edits: reword a title, swap an image, redraw page N as an architecture diagram, adjust layout, change colors. Phase 3 handles these edits **without re-running the generation pipeline** — it edits the on-disk SVG files directly, and only re-exports the PPTX when the user explicitly asks for it (see the deferral rule below).

For any edit request against an existing run:

1. Read [references/agent-edit.md](references/agent-edit.md) for the standard edit workflow (required reading, what to read, how to write, how to re-export).
2. Follow the responsibility chain — each layer only decides whether to read the next:
   - **SKILL.md** (this file): decides when to enter Phase 3 and read `agent-edit.md`.
   - **`agent-edit.md`**: decides when the edit involves drawing, and if so, reads `diagram-basics.md`.
   - **`diagram-basics.md`**: decides which diagram type, and if applicable, reads the matching `diagram-layouts/<type>.md`.
   - **`diagram-layouts/<type>.md`**: pure drawing knowledge for one diagram type.

The key invariant: Phase 3 edits SVG source **in place** under `output/<run_id>/slides/` — never copy the run directory or export to a different location (the user opens the path in `ppt.json`, and a copy means they see an unchanged PPTX). It does not call `run_ppt_pipeline.py` again. **The PPTX is NOT re-exported after each edit** — accumulate edits in the SVG files, report each change to the user, and only run `scripts/svg_to_pptx.py` when the user explicitly signals completion (e.g. "导出" / "可以导出了" / "完成了" / "都改好了导出吧" / "export" / "done" / "now export the PPT" or equivalent). If unsure whether the user wants export, ask rather than guess. See [references/agent-edit.md](references/agent-edit.md) §0 for the two critical rules (defer export + edit in place) and the full batch-edit workflow.

## Structured CLI Results

`scripts/run_ppt_pipeline.py` can return these top-level `stage` values:
- `completed`
- `preflight_failed`
- `invalid_request`
- `missing_required_info`
- `missing_outline`
- `input_required`
- `resume_unavailable`

Always inspect the top-level `stage` field first before deciding whether to continue, retry, or stop for user input.

## Notes

- Keep all paths relative to the working directory unless the user explicitly asks for something else.
- Once bootstrap is complete, all runtime commands must go through the Python interpreter inside `.venv`.
- `DEFAULT_LLM_MODEL`, `DEFAULT_LLM_API_KEY`, and `DEFAULT_LLM_API_BASE_URL` must be configured before running the pipeline.

## Advanced Topics (read on demand)

These are NOT needed for the common "generate a PPT" flow. Read them only when the user explicitly asks for the corresponding capability:

- [references/caching-and-paths.md](references/caching-and-paths.md) — Output directory structure, semantic run_id naming, cache reuse, how to find a previous run.
- [references/staged-execution.md](references/staged-execution.md) — Running individual stages (`parse`, `research`, `outline`, `render`) via `--stages`, useful for debugging or resuming partial runs.
- [references/patch-render.md](references/patch-render.md) — Re-rendering specific pages or fixing missing pages without a full rerun (`scripts/patch_render_missing.py`).
- [references/advanced-params.md](references/advanced-params.md) — `--research-mode`, `--image-search`, `--recursion-limit`, `--dry-run`, and other optional flags.
- [references/agent-edit.md](references/agent-edit.md) — Phase 3: editing an existing run's pages (text, image, layout, or drawing changes) without re-running the pipeline.
- [references/diagram-basics.md](references/diagram-basics.md) — Shared foundation for any Phase 3 edit that involves drawing a diagram (layering order, component patterns, spacing formulas, slidea SVG constraints).
- [references/diagram-layouts/](references/diagram-layouts/) — Per-type drawing algorithms for diagrams: [architecture](references/diagram-layouts/architecture.md), [flowchart](references/diagram-layouts/flowchart.md), [sequence](references/diagram-layouts/sequence.md), [structural](references/diagram-layouts/structural.md).
- [references/formula-render.md](references/formula-render.md) — Math formula rendering (display formulas via matplotlib mathtext → transparent PNG; auto in generation mode, manual via `scripts/render_formula.py` in modify mode).
- [UPDATE.md](UPDATE.md) — How to update the skill when source code or dependencies change.
