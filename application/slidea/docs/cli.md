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
| `--resume` | No* | Resume payload for an interrupted full-graph run |
| `--session-id` | No | LangGraph thread/session identifier, default `local` |
| `--stages` | No | Comma-separated stage list, default `all` |
| `--research-mode` | No | Runtime override for research routing: `skip`, `simple`, `deep` |
| `--use-cache` | No | String boolean controlling cache-backed reuse |
| `--image-search` | No | String boolean override for web image search |
| `--run-id` | No | Existing or explicit run id |
| `--recursion-limit` | No | LangGraph recursion limit, default `500` |
| `--dry-run` | No | Run preflight only and skip generation |

At least one of `--text` or `--resume` is required.

### Request validity rules

The command requires at least one of `--text` or `--resume`.

If both are missing, the CLI returns:

```json
{
  "stage": "invalid_request",
  "output": {
    "stage": "invalid_request",
    "message": "missing --text or --resume"
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
- Playwright/browser runtime for HTML-to-PDF during `render` and `all`, including a Chromium launchability smoke check,
- default LLM settings,
- premium LLM settings when `SLIDEA_MODE=PREMIUM`,
- Tavily availability for web search and image search,
- default VLM settings,
- embedding configuration for deep research,
- LibreOffice availability for PDF-to-PPTX during `render` and `all`.

The CLI also prints a human-readable preflight summary with warning/error lines before the terminal JSON payload.

Blocking vs advisory behavior:

- `env_setup` when `.env` is missing, and `default_llm`, are blocking checks.
- `premium_llm`, `tavily`, `default_vlm`, `embedding`, and `libreoffice` are advisory warnings for agents.
- `runtime_python`, `browser`, and incomplete `SETUP_COMPLETED` are warnings in phase 1 and should not stop execution on their own.

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

Known stage values produced by the current code path:

- `completed`
- `preflight_failed`
- `invalid_request`
- `missing_required_info`
- `missing_outline`
- `input_required`

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

When `input_required` is returned, the caller should preserve the current `run_id` and resume the same run after the user responds.

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

The command creates a `run_id` if one is not provided, then writes `output/<run_id>/run.json`.

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
python3 scripts/run_ppt_pipeline.py --text "..." --stages render --run-id <run_id>
```

Resume an interrupted run:

```bash
python3 scripts/run_ppt_pipeline.py \
  --resume "..." \
  --session-id demo \
  --run-id <run_id>
```

Current limitation: `--resume` is consumed only by the `all` stage path that runs the compiled top-level graph. Staged execution does not currently resume LangGraph interrupts.

## 2. `scripts/patch_render_missing.py`

This CLI is for patch rendering after an outline already exists.

### Purpose

- regenerate missing slide HTML files,
- regenerate a selected subset of pages by index,
- rebuild merged PDF and optional PPTX,
- refresh `ppt.json`.

### Basic usage

```bash
python3 scripts/patch_render_missing.py --run-id <run_id>
python3 scripts/patch_render_missing.py --run-id <run_id> --indices "0,3,5"
```

### Arguments

| Argument | Required | Description |
| --- | --- | --- |
| `--run-id` | Yes | Existing run id whose cached outline should be used |
| `--text` | No | Optional original request text reused during render prompts |
| `--indices` | No | Comma-separated slide indices to regenerate |

### Behavior

The command:

1. loads `output/<run_id>/outline/outline.json`,
2. resolves the render directory from `ppt.json` or derives one from the topic,
3. chooses target indices,
4. regenerates only the needed page types,
5. rebuilds merged PDF and optional PPTX,
6. updates `ppt.json`.

If `--indices` is omitted, it computes missing pages by comparing outline indices with existing `*.html` files.

### Structured outcomes

Known top-level stage values:

- `missing_outline`
- `empty_outline`
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
- install Python dependencies,
- install Playwright Chromium,
- install a local LibreOffice copy,
- ensure `.env` exists,
- write `SETUP_COMPLETED=true`,
- print post-install guidance about model configuration.

### Basic usage

```bash
python3 scripts/install/install.py
```

### Arguments

This CLI currently defines no command-line arguments.

### Main step flow

The current implementation runs the following observable steps:

1. check or create the Python virtual environment,
2. install Python dependencies from `requirements.txt`,
3. install Playwright Chromium,
4. check or install local LibreOffice,
5. ensure `.env` exists and write `SETUP_COMPLETED=true`,
6. print post-install guidance for required model service configuration.

### Idempotent behavior

The installer is partially idempotent:

- if `SETUP_COMPLETED=true` and the virtual environment already exists, it skips venv recreation and dependency installation,
- if a usable LibreOffice installation is already available, it reuses it,
- if LibreOffice is missing, installs a local copy on supported platforms, or prints manual installation guidance,
- it always re-checks and rewrites the setup marker in `.env`.

### Platform behavior

LibreOffice installation is platform-specific:

- Linux: download AppImage, or prints manual installation guidance
- macOS: download DMG, mount it, copy `LibreOffice.app`, remove quarantine attributes when possible
- Windows: download the portable installer and run it into the local directory

### Output style

Unlike the generation CLIs, this script does not emit JSON. It prints step-oriented human-readable logs such as:

- `Step N: ...`
- `[INFO] ...`
- `[OK] ...`
- `[WARN] ...`

### Important side effects

It may create or modify:

- `.venv/`
- `libreoffice/`
- `.env`

It also requires:

- `requirements.txt`
- a usable system Python bootstrap interpreter
- network access for dependency downloads and, when needed, bundled LibreOffice downloads

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

The exporter reads `skill/manifest.json` and assembles a skill package that includes:

- exported `SKILL.md`, and `INSTALL.md`,
- `core/`,
- runtime `scripts/`,
- `scripts/install/install.py`,
- the Linux ARM64 LibreOffice helper script under `scripts/install/`.

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
