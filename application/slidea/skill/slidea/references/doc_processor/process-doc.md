# Process Documents into Structured Markdown

The doc-processor pipeline converts a user-provided document corpus into a single `structured.md` (H1=topic, 5-10 H2 chapters, detailed content, relevant images embedded via `![](path)`), which feeds into the PPT generation pipeline as the `--text` input.

## Runtime Environment

All Python commands **must** use the interpreter inside `.venv` (located in `<SLIDEA_DIR>`). Do not use system `python` or `python3`.

- **Windows**: `.venv/Scripts/python.exe`
- **Unix-like**: `.venv/bin/python`

`output_files_dir` (where artifacts land under `output_files_dir/documents/<task_id>/`) is resolved by `core.utils.config`: defaults to `<SLIDEA_DIR>/output`, overridable via `OUTPUT_DIR` in `.env` — same root as the PPT pipeline.

## Critical Rules

1. **Checkpoint recovery**: Every step checks for output file existence before executing. Never delete or overwrite existing intermediate artifacts unless the user explicitly asks for a full re-run.
2. **No images during chapter writing** (step 6): Chapters are pure text. Images are placed later in step 8.
3. **Chapter files start with `## `**: Never include H1 in chapter files — H1 is reserved for the topic and added during assembly (step 9).
4. **`force=True` deletes the entire `<task_dir>`** (including `structured.md`) before rebuilding — a full re-run with no resume. Use only when the corpus changed or orphan artifacts need clearing. **Never use `force=True` during the revision loop** — it wipes all reviewed chapters.

## Directory Structure

All intermediate artifacts live under `output_files_dir/documents/<task_id>/`:

```
output_files_dir/documents/<task_id>/
  files/                              # Scattered file collection dir (agent-maintained; preprocess does not write)
  chunks/<chunk_id>.json              # Step 2 output (self-contained: text/images/source_path)
  chunks/_chunk_index.json            # Step 2 index
  chunks/summaries.json               # Step 4 merged summaries
  images/                             # Step 3 surviving images
  images.json                         # Step 3 output
  outline_new.json                    # Step 5 agent-generated
  chapters/chapter_<index>.md         # Step 6 agent-generated
  chapters_reviewed/chapter_<index>.md  # Step 7 agent-revised
  structured.md                       # Step 9 final product
  meta.json                           # Global metadata (preprocess maintains, agent appends)
```

> **`parsed_docs/` lifecycle**: A transient bridge between collect→chunk and collect→image-filter. Once `preprocess()` finishes (chunks are self-contained; surviving images copied to `images/`), `parsed_docs/` is **deleted automatically**. Downstream steps read only `chunks/` and `images.json` — never `parsed_docs/`. If a run is interrupted before `preprocess()` returns, `parsed_docs/` may linger and is cleaned on the next successful completion.

## Utility Functions

| Step | Type | Tool/Method | Description |
|---|---|---|---|
| 1-4 | Utility | `preprocess.preprocess(docs, topic, task_id)` | One-pass: collect & parse → chunk → image filter → summary |
| 5 | agent | reads `summaries.json` → generates `outline_new.json` | See Step 5 |
| 6 | agent | reads outline+chunks+summaries → writes `chapters/` | See Step 6 |
| 7 | agent | reads `chapters/` → rewrites to `chapters_reviewed/` | **Serial** (global dedup/ordering) |
| 8 | Utility | `postprocess.place_images_from_files(outline_path, images_path, enable_vlm)` | Reads outline+images → matches → writes back outline |
| 9 | Utility | `postprocess.assemble_structured_md(outline_path, chapters_dir, output_path, topic)` | Reads outline+chapters → writes `structured.md` |

## End-to-end Call Sequence

1. `preprocess()` (step 1-4) → returns path dict incl. `vlm_enabled`, `chunks_dir`, `summaries_json_path`, `images_json_path`.
2. Agent writes `outline_new.json` (step 5).
3. Agent writes `chapters/chapter_<index>.md` (step 6).
4. Agent rewrites into `chapters_reviewed/` **serially** (step 7).
5. `place_images_from_files(...)` (step 8) — no-op when `vlm_enabled` is `False`.
6. `assemble_structured_md(...)` (step 9) — pass `chapters_reviewed/` if it exists, else `chapters/`.

## Pipeline Steps

Each step only reads/writes on-disk artifacts, with checkpoint recovery (file-exists → skip).

### Step 0: Pre-check [agent]

1. Validate input: `topic` empty → ask user; `docs` empty → abort with error.
   - **`topic` must carry audience + goal**, not just a bare subject. It flows into the summarization LLM prompt (`_build_summary_prompt`) and the chunk relevance judgment, so richer context here → better-targeted summaries and outline. Use the "understood PPT request" confirmed in SKILL.md Step 0 (e.g. "面向技术团队，深入讲解 Agent 架构原理与实现细节").
2. **File collection** [agent]: If all documents are in one directory → pass that path as `docs`. If scattered → copy all files to `output_files_dir/documents/<task_id>/files/`, then pass that `files/` path as `docs`. (This is agent-side because it may involve user interaction.)
3. Generate or obtain `task_id` (recommended format: `timestamp_slug`, e.g. `202607151430_market_report`). Reusing an existing `task_id` resumes from checkpoints.
4. **Decide `enable_vlm`** [agent]: Default `False` (plain-text PPT, fast). Set `True` **only** when the user explicitly asks to use document images as illustrations. ⚠️ Warn the user first — VLM filtering calls once per image and is slow for image-heavy corpora. `enable_vlm=True` but VLM unconfigured → auto-degrades to `False` (logged, no hard error).

### Step 1-4: Preprocessing [preprocess() one-pass]

```python
import asyncio
from references.doc_processor.utils.preprocess import preprocess

result = asyncio.run(preprocess(
    docs="path/to/docs_dir",   # Directory path (str) or file path list (list[str])
    topic="User PPT topic",
    task_id="<unique task ID>",
    force=False,               # True: delete <task_dir> first (full re-run, no resume)
    enable_vlm=False,          # True: extract images + VLM filtering (slow); False: plain text (default)
))
```

`preprocess()` runs internally in sequence (each sub-step has checkpoint recovery):

1. **Collect & parse**: Parse each file via `get_contents`, save to `parsed_docs/<hash>.json` keyed by md5. Concurrency 3. Checkpoint: `<hash>.json` exists → skip.
2. **Chunk split**: `StructuredChunker` (max 65536 chars, 0.05 overlap). Detection: ≥2 `#{1,3}` headings → split by H2/H3 headings; otherwise character-based with overlap. Each chunk is self-contained (full `text`/`images`/`source_path`). Checkpoint: `_chunk_index.json` exists → skip.
3. **Image filter** (concurrent with step 4): collect images from `parsed_docs/` → size gate (drop if width<200 or height<100) → VLM topic relevance judgment → copy survivors to `images/` + write `images.json`. Auto-disabled when VLM unconfigured (`images.json=[]`). Checkpoint: `images.json` exists → skip.
4. **Summary generation** (concurrent with step 3): concurrency 5, per-chunk `llm_invoke` → merge into `chunks/summaries.json` (single aggregated file, the only summary artifact). Chunks **clearly unrelated** to the topic are marked `relevant=false` and excluded; related/uncertain chunks are summarized normally. Checkpoint: `summaries.json` exists → load and skip already-processed `chunk_id`s, summarize only new ones. Single chunk failure → `failed_chunks[]`, others unaffected.

Steps 3 and 4 **run concurrently** (`asyncio.gather`).

**`preprocess()` returns** a dict: `task_dir`, `chunks_dir`, `chunk_index_path`, `summaries_json_path`, `images_json_path`, `images_dir`, `meta_json_path`, `chunks_total`, `summaries_total`, `images_relevant`, `failed_chunks`, `vlm_enabled`.

### Step 5: Outline Generation [agent]

1. Read `summaries.json` (path from `preprocess()` return). For large summary counts, read in batches and use `keywords` to aid categorization.
2. Design H2 chapters around `topic`. If the user has provided an explicit document structure or PPT page count, align the outline chapters to match those requirements as closely as possible; otherwise use the user-specified chapter count if provided, defaulting to 3-7 when neither is given.
3. For each chapter, assign `chunk_ids` by evaluating each chunk's `labels`/`keywords`/`summary` against the chapter topic and `writing_desc`. A chunk may belong to **multiple chapters**. Chunks unrelated to both `topic` and any chapter → **exclude**.
4. Write `<task_dir>/outline_new.json`. Checkpoint: file exists → skip.

**`outline_new.json` format**:
```json
{"topic":"<user topic>","total_chunks":212,
 "chapters":[
   {"index":0,"title":"<chapter title>","chunk_ids":["a1b2c3_c2","e5f6_c0"],
    "writing_desc":"<core content and logical order>","labels":["cost"],"order":0}
 ],
 "unassigned_chunks":[]}
```

**Chunk coverage**: `len(∪ chapters[].chunk_ids) + len(unassigned_chunks) = total_chunks` (a chunk may appear in multiple chapters).

### Step 6: Chapter Writing [agent]

1. Read `outline_new.json` for the chapter list and each chapter's `chunk_ids`.
2. For each chapter:
   - Read original fragments from `chunks/<chunk_id>.json` (path from `chunks_dir`).
   - Read associated summaries from `chunks/summaries.json` (filter by `chunk_id`).
   - Write detailed chapter content, fully covering key arguments/evidence — do not compress.
   - Write to `chapters/chapter_<index>.md`, starting with `## <title>`. No H1, no images.
3. Chapters may be processed in parallel (agent decides concurrency). Checkpoint: `chapter_<index>.md` exists → skip. Failure → `meta.failed_chapters[]`, others unaffected.

### Step 7: Review [agent — serial]

⚠️ **Must run serially** — this step does global deduplication, logical reordering, and style unification across all chapters.

1. Read all `chapters/chapter_*.md`.
2. Global rewrite: deduplicate identical content / adjust logical order / fix errors / fill gaps / unify style. Do **not** change chapter split boundaries.
3. Write to `chapters_reviewed/chapter_<index>.md`. Checkpoint: `chapters_reviewed/chapter_<index>.md` exists → skip. Failure → fall back to unreviewed `chapters/` (mark in `meta.warnings`).

### Step 8: Image Placement [utility]

```python
import asyncio
from references.doc_processor.utils.postprocess import place_images_from_files

asyncio.run(place_images_from_files(
    outline_path,           # <task_dir>/outline_new.json
    images_json_path,       # <task_dir>/images.json (path from preprocess() return)
    enable_vlm=result["vlm_enabled"],   # False → no-op, returns outline unchanged
))
```

- `enable_vlm` **must** come from `preprocess()`'s `vlm_enabled` field (not the agent's input flag — the actual availability after auto-degrade).
- Reads `outline_new.json` + `images.json` → LLM matches images to chapters (grouped by source file, batched at 10/call, max concurrency 5) → writes back `outline_new.json`.

### Step 9: Assembly [utility]

```python
from references.doc_processor.utils.postprocess import assemble_structured_md

# Pass chapters_reviewed/ if it exists, else chapters/ (fallback)
chapters_dir = "chapters_reviewed" if os.path.isdir("chapters_reviewed") else "chapters"
structured_path = assemble_structured_md(
    outline_path,           # <task_dir>/outline_new.json (with image placements)
    chapters_dir,
    structured_md_path,      # <task_dir>/structured.md
    topic,
)
```

Reads `outline_new.json` (with image placements) + chapter files → mechanically assembles `structured.md` (H1=topic + `##` chapters + embedded images). Checkpoint: `structured.md` exists → skip.

### Step 10: Output

Return `structured_md_path` + `outline_new.json` path + `meta.json` summary.

## Revision Loop

After the user reviews `structured.md`, they may request changes to specific chapters. The revision workflow edits chapter content in place and regenerates `structured.md` — **without re-running preprocessing**.

- **Never pass `force=True`** during revision — it wipes the entire `<task_dir>` including reviewed chapters.
- **Keep outline boundaries intact** — do not change chapter splits or titles unless the user explicitly asks.
- **Edit the affected chapter file** based on review state:
  - If `chapters_reviewed/chapter_<index>.md` exists → edit it (consumed by assembly).
  - Else edit `chapters/chapter_<index>.md`.
- **Rewrite/supplement** the chapter using `chunks/`, `chunks/summaries.json`, `outline_new.json` plus the user's feedback. Fully cover the changes — do not compress. Unchanged chapters are skipped via checkpoint.

### Regenerating `structured.md`

Step 9 skips if `structured.md` already exists. To apply edits:

1. **Delete `<task_dir>/structured.md`** first — required, or assembly is skipped and edits won't appear.
2. Re-run step 8 **only if images changed**; otherwise the existing `outline_new.json` placements are reused.
3. Re-run step 9 (assembly).

### User confirmation

Every revision round must be re-confirmed. After generating or regenerating `structured.md`, notify the user with **both**:

1. **The full absolute path** of `structured.md` (e.g. `<task_dir>/structured.md`).
2. **A concise document overview** — including the `topic`, total chapter count, chapter titles in order, and total chunk coverage.

Only when the user explicitly accepts, the doc-processing phase complete.

## Fallback Path

- **No VLM**: `preprocess()` auto-detects → step 3 `images.json=[]`, step 8 no-op → plain text MD. `meta.vlm_enabled=false`.

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
| `order` | int | Sort order |
| `images` | list[str] | Image path list (written by step 8) |

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
