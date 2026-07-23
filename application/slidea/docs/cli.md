# CLI Reference

This document aims to cover every command-line entrypoint currently present in the repository and to distinguish between stable user-facing CLIs and developer-only script entrypoints.

## CLI Inventory

There are four public script entrypoints under `scripts/` and two developer-only module entrypoints under `core/utils/`.

### Public script entrypoints

- `scripts/run_ppt_pipeline.py`
- `scripts/patch_render_missing.py`
- `scripts/install/install.py`
- `scripts/export_skill.py`

### Developer-only entrypoints with `__main__`

- `core/utils/crawl.py`
- `core/utils/tavily_search.py`

The developer-only entrypoints are executable, but they are not stable product interfaces and should not be treated as part of the supported runtime contract.

## 1. `scripts/run_ppt_pipeline.py`

This is the main runtime CLI for generation.

### Purpose

- run the full PPT pipeline,
- run selected stages only,
- perform preflight checks,
- emit structured JSON output for callers.

### Basic usage

```bash
python3 scripts/run_ppt_pipeline.py --text "<request>"
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--text` | No* | New slide-generation request text |
| `--resume` | No* | User reply for a deliberate `input_required` interrupt |
| `--continue` | No* | Continue unfinished checkpoint work after timeout/process termination |
| `--session-id` | No | Public task identifier. If omitted for a new run, a unique id (`auto_<pid>_<ts>`) is generated. Reuse an explicit id for continuation, interrupt replies, patching, or staged execution. |
| `--stages` | No | Comma-separated stage list, default `all` |
| `--research-mode` | No | Runtime override for research routing: `skip`, `simple`, `deep` |
| `--use-cache` | No | String boolean controlling cache-backed reuse |
| `--image-search` | No | String boolean override for web image search |
| `--style-pack` | No | Prepared style-pack directory. The pack is validated and copied into the run. |
| `--allow-style-source-content` | No | Explicitly allow the style pack's source PPTX to also be read as content. Use only for intentional dual use. |
| `--run-id` | No | Advanced internal override for legacy collision disambiguation or debugging. Normal callers use `--session-id`. |
| `--recursion-limit` | No | LangGraph recursion limit, default `500` |
| `--dry-run` | No | Run preflight only and skip generation |

Any additional render-backend flag is intentionally not advertised here. The default and only route exposed through this doc is SVG; alternative backends are opt-in and documented only in the repository README.

Exactly one of `--text`, `--resume`, or `--continue` is required for non-dry execution.

`--text` is both the natural-language request and the content-source channel: document paths and URLs found there are parsed and added to the writing references. When `--style-pack` is used, do not include the reference PPTX path/URL in `--text`; style is already supplied by the pack. If the pack's `source` PPTX is found in `--text`, the CLI returns `invalid_request` before generation. The rare intentional dual-use case requires `--allow-style-source-content`.

### Request validity rules

The command requires exactly one of `--text`, `--resume`, or `--continue`. Re-submitting `--text` with an existing session id is rejected instead of creating a duplicate run.

If both are missing, the CLI returns:

```json
{
  "stage": "invalid_request",
  "output": {
    "stage": "invalid_request",
    "message": "choose exactly one of --text, --resume, or --continue"
  }
}
```

### Stage model

`--stages all` uses the compiled top-level PPT graph and runs the full end-to-end flow.

Staged mode bypasses the top-level graph and directly invokes stage-specific nodes.

Supported stage values in the current implementation:

- `all`
- `parse`
- `research`
- `outline`
- `render`

Stage semantics:

- `parse`: parse the request and surface missing required information
- `research`: gather references, route research, optionally search, and generate the writing thought
- `outline`: generate the slide outline from cached source material
- `render`: generate pages from a cached outline
- `all`: run the full graph from request to final outputs

### Preflight contract

Before non-dry execution, the CLI calls `run_preflight()` from `scripts/utils/preflight.py`.

Preflight checks include:

- `.env` existence in the skill root,
- `SETUP_COMPLETED=true` in that `.env`,
- use of the project `.venv` Python interpreter,
- default LLM settings,
- premium LLM settings when `SLIDEA_MODE=PREMIUM`,
- Tavily availability for web search and image search,
- default VLM settings,
- embedding configuration for deep research.

The CLI also prints a human-readable preflight summary with warning/error lines before the terminal JSON payload.

Blocking vs advisory behavior:

- `env_setup` when `.env` is missing, and `default_llm`, are blocking checks.
- `premium_llm`, `tavily`, `default_vlm`, and `embedding` are advisory warnings for agents.
- `runtime_python` and incomplete `SETUP_COMPLETED` are advisory warnings and should not stop execution on their own.

Possible top-level outcomes:

- `completed`: dry-run success with embedded preflight details
- `preflight_failed`: one or more required checks failed

### Structured JSON output

The CLI terminates through `emit_stage_payload()` from `scripts/utils/cli_output.py`.

Important stdout detail:

- the script may print streamed progress text before the terminal payload,
- `emit_stage_payload()` also prints one human-readable banner line before the JSON,
- the final line is the machine-readable JSON payload.

Top-level shape:

```json
{
  "stage": "completed",
  "output": {},
  "run_id": "20260325_120000_ppt",
  "output_dir": "/abs/path/output/20260325_120000_ppt"
}
```

`output_dir` is rooted at the configured output root (`<slidea_install_dir>/output/` by default; override via `OUTPUT_DIR` in `.env`).

Known stage values produced by the current code path:

- `completed`
- `preflight_failed`
- `invalid_request`
- `missing_required_info`
- `missing_outline`
- `input_required`
- `resume_unavailable`

### Input-required behavior

LangGraph interrupts are normalized by `scripts/utils/pipeline.py`.

Supported interaction types:

- `question`
- `select`
- `edit_text`

When an interrupt occurs, the terminal JSON payload uses top-level `stage: "input_required"`.

Current implementation detail:

- the runtime internally builds interaction-specific metadata such as `interaction`, `hint`, and response schema,
- those details are currently surfaced through runtime events / console output paths,
- the final JSON emitted by `scripts/run_ppt_pipeline.py` does not currently include the full interaction payload.

When `input_required` is returned, the caller should preserve the current `session-id` and resume the same run after the user responds. The internal `run_id` is recovered automatically.

Resume input is normalized in a tolerant order:

1. `selection`
2. `answer`
3. `text`
4. `message`

This matches the interaction hints emitted by the runtime:

- `select` -> resume with `payload.selection`
- `question` -> resume with `payload.answer`
- `edit_text` -> resume with `payload.text`

### Stage-specific expected states

- `missing_required_info`: parse or research cannot proceed until the user supplies missing information
- `missing_outline`: render was requested but no cached outline exists
- `completed`: successful terminal state, optionally including generated files

### Cache side effects

The command creates a `run_id` if one is not provided, then writes `<output_root>/<run_id>/run.json` (where `<output_root>` is `<slidea_install_dir>/output/` by default, or the directory configured via `OUTPUT_DIR`).

Depending on the stage, it may also read or write:

- `references/parsed_requirements.json`
- `references/references.txt`
- `references/references_all.txt`
- `research/research.json`
- `research/deep_report.md`
- `thought/thought.md`
- `outline/outline.json`
- `ppt.json`

Notes on the reference files:

- `references/references.txt` stores fetched user-provided source content,
- `references/references_all.txt` stores the aggregated reference text used by later thought/outline stages.

### Recommended usage patterns

Full run:

```bash
python3 scripts/run_ppt_pipeline.py --text "..." --session-id demo
```

Dry run only:

```bash
python3 scripts/run_ppt_pipeline.py --text "..." --dry-run
```

Force no research:

```bash
python3 scripts/run_ppt_pipeline.py --text "..." --research-mode skip
```

Render from a cached outline:

```bash
python3 scripts/run_ppt_pipeline.py --text "..." --stages render --session-id demo
```

Reply after `stage: input_required`:

```bash
python3 scripts/run_ppt_pipeline.py \
  --resume "..." \
  --session-id demo
```

Continue after timeout or process termination:

```bash
python3 scripts/run_ppt_pipeline.py \
  --continue \
  --session-id demo
```

Continuation restores the original runtime configuration and style-pack snapshot. Do not pass `--text`, `--style-pack`, or a replacement image/research setting. If no checkpoint exists, use patch rendering for missing pages instead.

Current limitation: `--resume` is consumed only by the `all` stage path that runs the compiled top-level graph. Staged execution does not currently resume LangGraph interrupts.

## 2. `scripts/patch_render_missing.py`

This CLI is for patch rendering after an outline already exists.

### Purpose

- regenerate missing slide pages,
- regenerate a selected subset of pages by index,
- rebuild merged PDF and optional PPTX,
- refresh `ppt.json`.

### Basic usage

```bash
python3 scripts/patch_render_missing.py --session-id <session_id>
python3 scripts/patch_render_missing.py --session-id <session_id> --indices "0,3,5"
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--session-id` | Yes* | Normal public task id; resolves the internal run automatically |
| `--run-id` | Yes* | Advanced mutually exclusive fallback for legacy session collisions |
| `--text` | No | Explicit request override; normally restored from `run.json` |
| `--indices` | No | Comma-separated slide indices to regenerate |

`*` Exactly one of `--session-id` or `--run-id` is required.

### Behavior

The command:

1. resolves the original internal run from `--session-id` and restores request/render metadata from `run.json`,
2. loads `<output_root>/<run_id>/outline/outline.json`, then resolves the render directory from `ppt.json` or `<run_id>/slides`,
3. chooses target indices,
4. regenerates only the needed page types,
5. reuses the immutable style pack and runs the same style-aware dynamic-content quality gate when applicable,
6. refuses export if any outline page remains missing, otherwise rebuilds the native PPTX,
7. updates `ppt.json`.

If `--indices` is omitted, it computes missing pages by comparing outline indices against existing render outputs in the render directory.

### Structured outcomes

Known top-level stage values:

- `missing_outline`
- `empty_outline`
- `invalid_request`
- `render_incomplete`
- `svg_quality_failed`
- `completed`

`completed` may also mean "nothing to patch" when no target indices are missing.

### Page-type-aware regeneration

The patch flow does not use one generic regeneration path. It dispatches by page type:

- cover / thanks pages,
- TOC page,
- separator pages,
- content pages.

That keeps the patch behavior aligned with the full render pipeline.

## 3. `scripts/install/install.py`

This is the local bootstrap and environment-installation CLI.

### Purpose

- create or rebuild the local Python virtual environment,
- install Python dependencies for the default SVG route,
- ensure `.env` exists,
- write `SETUP_COMPLETED=true`,
- print post-install guidance about model configuration.

The installer sets up only what the default SVG route needs. Any additional opt-in route is documented separately in the repository README and is not part of this CLI's default surface.

### Basic usage

```bash
python3 scripts/install/install.py
```

### Arguments

This CLI takes no advertised flags for the default SVG install flow.

### Main step flow

The current implementation runs the following observable steps:

1. check or create the Python virtual environment,
2. install Python dependencies from `requirements.txt`,
3. ensure `.env` exists and write `SETUP_COMPLETED=true`,
4. print post-install guidance for required model service configuration.

### Idempotent behavior

The installer is partially idempotent:

- if `SETUP_COMPLETED=true` and the virtual environment already exists, it skips venv recreation and dependency installation,
- it always re-checks and rewrites the setup marker in `.env`.

### Platform behavior

The default install flow is platform-agnostic for SVG-only setups; no platform-specific helper is invoked.

### Output style

Unlike the generation CLIs, this script does not emit JSON. It prints step-oriented human-readable logs such as:

- `Step N: ...`
- `[INFO] ...`
- `[OK] ...`
- `[WARN] ...`

### Important side effects

It may create or modify:

- `.venv/`
- `.env`

It also requires:

- `requirements.txt`
- a usable system Python bootstrap interpreter
- network access for dependency downloads

## 4. `scripts/export_skill.py`

This is the source-tree skill export CLI.

### Purpose

- assemble a clean skill package from the repository source tree,
- preserve the runtime-first `scripts/` layout in the exported skill package,
- keep install helpers under `scripts/install/`,
- make the exported skill layout match the intended skill-package contract.

### Basic usage

```bash
python3 scripts/export_skill.py --target "<SKILLS_DIR>/slidea"
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--target` | Yes | Final output directory for the exported package |
| `--force` | No | Replace the target directory if it already exists |
| `--bootstrap` | No | After export, run `scripts/install/install.py` inside the exported package |

### Current behavior

The exporter reads `skill/slidea/manifest.json` and assembles a skill package that includes:

- exported `SKILL.md`, and `INSTALL.md`,
- `core/`,
- runtime `scripts/`,
- `scripts/install/install.py`,
- platform helper scripts under `scripts/install/` used only by opt-in flows documented in the repository README.

The exporter intentionally excludes `scripts/export_skill.py` from the exported skill package.

## 5. Developer-only entrypoints

These entrypoints are executable but should be treated as internal utilities rather than supported public CLIs.

### `core/utils/crawl.py`

Purpose:

- manually test `get_content()` against a local file or remote URL

Current `__main__` behavior:

- directly runs `get_content("https://arxiv.org/pdf/2310.08560")`

This is not parameterized and is best understood as a developer smoke hook.

### `core/utils/tavily_search.py`

Purpose:

- manually test Tavily batch search behavior

Current `__main__` behavior:

- directly runs `tavily_search([...])` with hard-coded example queries

This is also not a stable CLI contract and should not be documented as a user-facing command surface.

## Completeness Notes

This document covers:

- every executable entrypoint currently under `scripts/`,
- every repository module outside tests that currently exposes a `__main__` path,
- the distinction between stable CLIs and internal executable helpers.

If a new entrypoint is added later, update this file and keep the distinction between public and developer-only interfaces explicit.

To follow a prepared reference-PPT style pack:

```bash
SESSION_ID=<new-unique-id>
.venv/bin/python scripts/pptx_to_style_pack.py reference.pptx --session-id "$SESSION_ID"
# Agent reads asset-inventory.json plus selected SVG files, then authors style-pack.json.
.venv/bin/python scripts/validate_style_pack.py "/tmp/slidea/style-packs/$SESSION_ID"
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<request>" \
  --session-id "$SESSION_ID" \
  --style-pack "/tmp/slidea/style-packs/$SESSION_ID"
```

The conversion command always writes to `/tmp/slidea/style-packs/<session-id>`. It creates editable reference SVGs, extracted image assets, `reference/conversion-report.json`, and an advisory `asset-inventory.json`; it does not create `style-pack.json` or PNG previews. The inventory reports deterministic candidate signals only. The Agent reads it together with selected SVG source, chooses a small representative subset, and explicitly records `global_style.text_container_usage` as a separate design dimension: whether text is normally unboxed, placed on filled backgrounds, or enclosed by border-only/filled boxes, including the roles that use title bands, labels, callouts, cards and summary strips. Reusable template decorations are authorized through top-level `reusable_assets` and page-level `fixed_image_elements` in `style-pack.json`. The subset must include one genuine cover, TOC, separator, or thanks reference for each such role that exists in the source; special-role completeness takes priority over the normal representative-page count.

At pipeline startup, the validated pack is copied to `output/<run_id>/style_pack/`. Reference ids are selected during outline generation using page type, density, and structure, then saved in `outline/outline.json`. Page type is an exact-match constraint; cross-role assignments such as TOC-to-thanks are rejected. If a pack has no exact-type candidate for one target page, only that page uses the built-in template while the remaining pages retain style-pack references. Before parallel generation, Slidea prepares prompt-safe references under `slides/style_references/` and copies inherited shell images plus Agent-authorized reusable images into `slides/images/style-pack/`; every other `main-content` image remains reference-only. Generated style-mode pages receive the assigned background, master/layout layers, title geometry, header/footer, fixed logos, authorized `back`/`front` decorations, and page-number format through deterministic composition after generation, VLM review, and quality repair. Invalid packs, invalid model assignments after retry, or style-asset preflight errors still fall back to the built-in template workflow for the entire run. Runs without `--style-pack` retain the existing workflow unchanged.
