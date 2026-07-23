# Process Documents into Structured Markdown

The doc-processor pipeline converts a user-provided document corpus into a single `structured.md` (H1=topic, 3-7 H2 chapters, detailed content), which feeds into the PPT generation pipeline as the `--text` input.

## Runtime Environment

All Python commands **must** use the interpreter inside `.venv` (located in `<SLIDEA_DIR>`). Do not use system `python` or `python3`.

- **Windows**: `.venv/Scripts/python.exe`
- **Unix-like**: `.venv/bin/python`

`output_files_dir` (where artifacts land under `output_files_dir/documents/<task_id>/`) is resolved by `core.utils.config`: defaults to `<SLIDEA_DIR>/output`, overridable via `OUTPUT_DIR` in `.env` — same root as the PPT pipeline.


## Directory Structure

All intermediate artifacts live under `<task_dir>`: `output_files_dir/documents/<task_id>/`:

```
output_files_dir/documents/<task_id>/
  chunks/<chunk_id>.json              # Step 2 output (self-contained: text/images/source_path)
  chunks/_chunk_index.json            # Step 2 index
  chunks/summaries.json               # Step 4 merged summaries
  images/                             # Step 3 surviving images
  images.json                         # Step 3 output
  outline_new.json                    # Step 5 agent-generated
  chapters/chapter_<index>.md         # Step 6 agent-generated
  structured.md                       # Step 7 final product (assembled + reviewed)
  doc_images.json                     # Step 8 top-30 scored images (passed to PPT pipeline)
  meta.json                           # Global metadata (preprocess maintains, agent appends)
```

## Pipeline Overview

Each step only reads/writes on-disk artifacts; existing output files are skipped (file-exists → skip).

| Step | Type | Tool/Method | Description |
|---|---|---|---|
| 1-4 | Utility | `preprocess.preprocess(docs, topic, task_id, force, enable_vlm)` | One-pass: collect & parse → chunk → image filter → summary. Returns a path dict (incl. `vlm_enabled`, `chunks_dir`, `summaries_json_path`, `images_json_path`). |
| 5 | agent | reads `summaries.json` → writes `outline_new.json` | See Step 5 |
| 6 | agent | reads outline+chunks → writes chapters into `structured.md` | Grouped by weight; parallel. See Step 6 |
| 7 | agent | reads `structured.md` → 1 global review call (diff) → writes back | **Serial** (cross-chapter dedup/ordering) |
| 8 | Utility | `postprocess.score_images_from_files(outline_path, images_path, output_path, enable_vlm)` | Reads outline+images → scores → writes `doc_images.json`; no-op when `vlm_enabled` is `False` |

**`short_circuit` branching** (the only signal to act on): `True` → `structured.md` is already final, jump to Step 9 (skip Steps 5-8); `False` → run Steps 5-8. Use the field only to pick the next step; never echo its meaning, the path taken, or any internal state to the user — to the user, doc-processing is a black box that returns a `structured.md` path.

## Pipeline Steps

### Step 0: Pre-check [agent]

**Do not read document content before `preprocess()` returns.** Pass paths only via `docs=`; never open or read file bodies. Listing directories and copying files is allowed; reading body text is not. Parsing, summarizing, and chaptering are doc-process responsibilities — the agent must not digest documents itself.

1. Validate input: `topic` empty → ask user; `docs` empty → abort with error.
   - `topic` is the "understood PPT request" from SKILL.md Step 0 (audience + goal + topic + style) — it flows into the summarization prompt and relevance judgment, so richer context → better-targeted output.
2. **File collection** [agent]: Pass the documents directory as `docs` (str), or a list of file paths (`list[str]`). If files are scattered across unrelated locations, copy them into `output_files_dir/documents/<task_id>/files/` first, then pass that `files/` path.
3. Generate or obtain `task_id` (recommended format: `<timestamp>_<slug>`, where `<timestamp>` is local time **truncated to the minute** in `%Y%m%d%H%M` — e.g. `202607151430_market_report`). When the user asks to continue a previous run, reuse the existing `task_id` of that run.
4. **Decide `enable_vlm`** [agent]: Default `False` (plain-text PPT, fast). Set `True` **only** when the user explicitly asks to use document images as illustrations. Warn the user first — VLM filtering calls once per image and is slow for image-heavy corpora.

### Step 1-4: Preprocessing [preprocess() one-pass]

```python
import asyncio
from references.doc_processor.utils.preprocess import preprocess

result = asyncio.run(preprocess(
    docs="path/to/docs_dir",   # Directory path (str) or file path list (list[str])
    topic="User PPT topic",
    task_id="<unique task ID>",
    force=False,               # True: delete <task_dir> first (full re-run)
    enable_vlm=False,          # True: extract images + VLM filtering (slow); False: plain text (default)
))
```

Each sub-step skips existing output artifacts (file-exists → skip), so re-running with the same `task_id` resumes from where it left off. Artifacts land under `<task_dir>/` (see [Directory Structure](#directory-structure) and [Artifact Formats](#artifact-formats)); the result dict returns their paths.

Two contracts worth knowing for the downstream steps:
- **`summaries.json`** is a single aggregated file (no per-chunk summary files). It **omits chunks clearly unrelated to the topic**; related/uncertain chunks are summarized normally - so do not expect every chunk to appear in it.
- The `short_circuit` branching signal is defined under [Pipeline Overview](#pipeline-overview).

**`preprocess()` returns** a dict. The fields you actually act on: `structured_md_path`, `short_circuit`, `doc_images`, `chunks_dir`, `summaries_json_path`, `images_json_path`, `meta_json_path`. (Other fields are internal bookkeeping - read them only if a step needs them; never report them to the user.)

### Step 5: Outline Generation [agent]

1. Read `summaries.json` (path from `preprocess()` return). For large summary counts, read in batches and use `keywords` to aid categorization.
2. Design H2 chapters around `topic`. If the user has provided an explicit document structure, align the outline chapters to match those requirements as closely as possible; otherwise use the user-specified chapter count if provided, defaulting to 3-7 when neither is given.
3. For each chapter, assign `chunk_ids` by evaluating each chunk's `labels`/`keywords`/`summary` against the chapter topic and `writing_desc`. A chunk may belong to **multiple chapters**. Chunks unrelated to both `topic` and any chapter → **exclude**.
4. For each chapter, assign a `weight` reflecting its importance and expected length:
   - `"major"`: core chapter requiring detailed, in-depth treatment (e.g. key architecture, main workflow).
   - `"minor"`: supporting chapter needing only a brief overview (e.g. background, terminology, future outlook).
   Weight determines how chapters are grouped for writing in Step 6: each `major` chapter is written individually; two or more consecutive `minor` chapters may be merged into a single writing call.
5. Write `<task_dir>/outline_new.json`. 

**`outline_new.json` format**:
```json
{"topic":"<user topic>","total_chunks":212,
 "chapters":[
   {"index":0,"title":"<chapter title>","chunk_ids":["a1b2c3_c2","e5f6_c0"],
    "writing_desc":"<core content and logical order>","labels":["cost"],"weight":"major","order":0},
   {"index":1,"title":"<minor title>","chunk_ids":["abc_c0"],
    "writing_desc":"<brief overview>","labels":["background"],"weight":"minor","order":1}
 ],
 "unassigned_chunks":[]}
```

**Chunk coverage**: `len(∪ chapters[].chunk_ids) + len(unassigned_chunks) = total_chunks` (a chunk may appear in multiple chapters).

### Step 6: Chapter Writing [agent]

1. Read `outline_new.json` for the chapter list and each chapter’s `chunk_ids` and `weight`.
2. **Group chapters by weight for writing** — weight also governs target depth:
   - `major` chapters: **in-depth treatment** — develop every core point with evidence (data, examples, mechanism), explain motivation / trade-offs / implications, multi-paragraph coverage per key point.
   - Consecutive `minor` chapters: **concise overview** (1-2 paragraphs per chapter), essentials only.
3. For each writing call:
   - Read the **full** `chunks/<chunk_id>.json` for the chapter(s) — summaries are only a topical index, never the writing material.
   - Follow `writing_desc` as the chapter’s logical spine: every item it calls out must be developed in prose, in that order.
   - Writing rules (violation = rewrite before saving):
     1. **Understand, then generate — no copying, no bulk dumping**: chunks are reference material only. You must fully comprehend the chunk content, then write original prose synthesizing it. Never copy chunk `text` verbatim, never concatenate or lightly rewrite raw source — the output must be newly authored content. Quote a fragment only when a specific phrase, code, or config value demands it. **Do not assemble all chunk content into a single `llm_invoke` call** — each writing call must be scoped to one chapter’s chunks + `writing_desc` + rules, so the model can focus and synthesize rather than paste.
     2. **Format serves the reader** (audience + topic encoded in `topic`): choose prose, tables, or lists to serve clarity. Lists and tables must carry substance per entry — not thin-content shortcuts.
     3. **Richness is non-negotiable**: each core point in `major` chapters must be argued with what / why / how / source-specific evidence / trade-offs — fully developed, not one-liners. Every claim must anchor to a concrete source detail (numbers, names, versions, configs); "significant improvement" without the figure is filler.
     4. **Technical voice**: no hollow verbs, no preamble, consistent formal register.
     5. **Length cap**: each chapter’s output must not exceed **5000 characters** (markdown source, including title).
   - Write to `<task_dir>/chapters/chapter_<index>.md`, starting with `## <title>`.
4. Chapters may be processed in parallel (agent decides concurrency). 

### Step 7: Assembly + Global Document Review [agent — serial]

**Must run serially** — assemble all chapters into one complete document, then review the full document for cross-chapter issues.

1. **Assemble**: read all `<task_dir>/chapters/chapter_*.md` and concatenate into `<task_dir>/structured.md` (H1=topic + chapters in outline order).
2. **Review**: ask for **diff-style revisions** focusing on:
   - Cross-chapter duplicated content
   - Factual errors
   - Do **not** change chapter split boundaries or titles. Do **not** nitpick wording or style — only fix substantive issues.
3. Apply the diffs back to `structured.md`.
4. **Cleanup**: delete `chapters/` directory (single-chapter files are no longer needed).


### Step 8: Image Scoring [utility]

Scores images against the overall document content (topic + chapter overview) and selects the top-30 most relevant. Images are scored as a pool against the overall document and passed to the PPT pipeline; they are not embedded in `structured.md`.

```python
import asyncio
from references.doc_processor.utils.postprocess import score_images_from_files

doc_images = asyncio.run(score_images_from_files(
    outline_path,           # <task_dir>/outline_new.json
    images_path,            # <task_dir>/images.json (path from preprocess() return)
    output_path,            # <task_dir>/doc_images.json
    enable_vlm=result["vlm_enabled"],   # False → returns empty list
))
```

- `enable_vlm` **must** come from `preprocess()`'s `vlm_enabled` field.
- Reads `outline_new.json` (for topic + chapter overview) + `images.json` → LLM scores each image's relevance → writes `<task_dir>/doc_images.json` (top-30, `{path, description, score}`).
- `doc_images` is also returned in the `preprocess()` result dict for convenience.


### Step 9: Output

Return `structured_md_path` + `outline_new.json` path + `meta.json` summary.

## Review & Revision (full-flow path only)

The review/revision loop applies **only when `short_circuit=False`** (full flow). When `short_circuit=True`, `structured.md` is already final — proceed directly to Phase 2.

After the user reviews `structured.md`, they may request changes to specific chapters. The revision workflow edits chapter content in place and regenerates `structured.md` — **without re-running preprocessing**.

- **Never pass `force=True`** during revision — it wipes the entire `<task_dir>` including reviewed chapters.
- **Keep outline boundaries intact** — do not change chapter splits or titles unless the user explicitly asks.
- **Edit the affected chapter**: rewrite `chapters/chapter_<index>.md` (if still exists) or edit `structured.md` directly (if `chapters/` was already deleted).
- **Rewrite/supplement** the chapter using `chunks/`, `chunks/summaries.json`, `outline_new.json` plus the user's feedback. Fully cover the changes — do not compress. Unchanged chapters are left as-is.

### User confirmation

Every revision round must be re-confirmed. After generating or regenerating `structured.md`, notify the user with **both**:

1. **The full absolute path** of `structured.md` (e.g. `<task_dir>/structured.md`).
2. **A concise document overview** — including the `topic`, total chapter count, chapter titles in order, and total chunk coverage.

Only when the user explicitly accepts, the doc-processing phase complete.

## Artifact Formats

### `chunks/<chunk_id>.json` (step 2, read by agent in step 6)

chunk_id format: `<doc_hash>_c<seq>`.

| Field | Type | Description |
|---|---|---|
| `id` | str | Unique chunk ID, equals filename |
| `text` | str | Fragment body (markdown) |
| `source_anchor` | str | Source anchor, same as `id` |
| `char_range` | [int, int] | Character offset in original text [start, end) |
| `images` | list | Images in this fragment (usually empty; filtered in step 3) |
| `doc_hash` | str | md5 hash of source document |
| `source_path` | str | Original source file path |

### `images.json` (step 3, read by step 8)

| Field | Type | Description |
|---|---|---|
| `path` | str | Surviving image path (copied to `images/`) |
| `original_path` | str | Original image path |
| `source_hash` | str | Source document hash (for per-file grouping in step 8) |
| `width` / `height` | int | Image dimensions |
| `description` | str | VLM-generated image content description |
| `reason` | str | VLM relevance judgment reasoning |

### `chunks/summaries.json` (step 4, read by agent in step 5)

Single aggregated file — there are **no per-chunk summary files**. Located in `chunks/` alongside chunk files.

| Field | Type | Description |
|---|---|---|
| `chunk_id` | str | Corresponding chunk ID |
| `doc_hash` | str | Source document hash |
| `chunk_seq` | int | Chunk sequence within the document |
| `source_path` | str | Source file path |
| `char_range` | [int, int] | Character offset in original text |
| `labels` | list[str] | Core topic labels, e.g. `["cost","trend"]` |
| `keywords` | list[str] | 3-8 keywords, aid categorization |
| `summary` | str | Structured summary body (markdown), preserves key data/arguments |
| `char_len` | int | Summary character count |

### `outline_new.json` (step 5 agent-generated, step 8 appends images)

| Field | Type | Description |
|---|---|---|
| `topic` | str | User PPT topic |
| `total_chunks` | int | Total chunk count (for conservation check) |
| `chapters` | list | Chapter list, see below |
| `unassigned_chunks` | list[str] | chunk_ids not assigned to any chapter |

**Chapter structure:**

| Field | Type | Description |
|---|---|---|
| `index` | int | Chapter index (0-based) |
| `title` | str | Chapter title |
| `chunk_ids` | list[str] | Associated chunk ID list |
| `writing_desc` | str | Core content and logical order this chapter should cover |
| `labels` | list[str] | Chapter topic labels |
| `weight` | str | `"major"` or `"minor"` — determines writing group size in Step 6 |
| `order` | int | Sort order |
| `images` | list[str] | Image path list (written by step 8) |

### `doc_images.json` (step 8 output)

| Field | Type | Description |
|---|---|---|
| `path` | str | Image path |
| `description` | str | Image content description |
| `score` | float | Relevance score (higher = more relevant) |

Top-30 sorted by score descending.

### `meta.json` (global metadata)

| Field | Type | Description |
|---|---|---|
| `task_id` | str | Unique task ID |
| `topic` | str | PPT topic |
| `vlm_enabled` | bool | Whether VLM was available (steps 3/8 skipped when false) |
| `stages_completed` | list[str] | Completed stages |
| `docs_total` | int | Total document count |
| `chunks_total` | int | Total chunk count |
| `summaries_total` | int | Total summary count |
| `failed_chunks` | list[str] | chunk_ids that failed summarization |
| `failed_chapters` | list[str] | Chapter indices that failed writing (step 6) |
| `images_relevant` | int | Surviving image count |
| `warnings` | list | Warning messages |
