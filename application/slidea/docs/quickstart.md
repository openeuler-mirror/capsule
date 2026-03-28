# Quickstart

This guide explains the fastest way to start using Slidea, with the recommended skill-based path first and direct source execution second.

## Recommended Path: Install Slidea as a Skill

Slidea is primarily designed to run as an installed skill inside an agent environment.

If your agent supports local skills, use that platform's skill installation workflow first. For the skill-installation steps, see [skill/INSTALL.md](../skill/INSTALL.md).

After the skill has been installed:

1. configure the required `.env` values inside the installed Slidea skill directory;
2. restart the agent so it can reload the new skill;
3. invoke Slidea through the host agent's skill entry syntax.

Example in a slash-style environment:

```text
/slidea create a PPT about AI agents for product, engineering, and business leaders
```

The exact command syntax depends on the host agent. The important part is the workflow: the agent loads Slidea, asks for any missing information when necessary, and runs the slide-generation pipeline through to final artifacts.

## Develop or Run from Source

If you are contributing to Slidea itself or debugging the repository locally, run it directly from this checkout.

## Prerequisites

- Python 3.11+
- Git
- a configured default LLM API

Optional but commonly useful:

- Tavily API keys for search
- embedding model settings, or `DISABLE_EMBEDDING=true`
- VLM settings for image scoring and distribution

## Bootstrap the Local Runtime

From the project root:

```bash
python3 scripts/install/install.py
```

The installer creates `.venv`, installs Python dependencies, installs Playwright Chromium, prepares local LibreOffice support, creates `.env` when needed, and writes the setup marker.

## Configure Environment

Fill at least these fields in `.env`:

```env
DEFAULT_LLM_MODEL=...
DEFAULT_LLM_API_KEY=...
DEFAULT_LLM_API_BASE_URL=...
```

Recommended minimum for a friction-free local setup:

```env
DISABLE_EMBEDDING=true
USE_CACHE=true
USE_WEB_IMG_SEARCH=false
USE_IMG_GEN=false
```

## Dry Run First

Before invoking the real pipeline, validate runtime prerequisites:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py --text "test request" --dry-run
```

Expected behavior:

- the CLI returns a JSON payload,
- top-level `stage` is `completed`,
- the payload includes `preflight`,
- missing optional capabilities are reported as warnings rather than hard failures.

## Run the Full Pipeline

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "Create a Chinese PPT about AI agent opportunities for product and engineering leaders" \
  --session-id local-demo
```

The default `--stages all` path runs:

1. request parsing and clarification
2. reference gathering and optional research
3. writing-thought generation
4. outline generation
5. HTML rendering
6. PDF synthesis
7. optional PPTX conversion

## Resume After User Input

If the full pipeline pauses with `stage: "input_required"`, keep the returned `run_id` and ask the user the requested question.

Then continue with:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "<user response>" \
  --session-id local-demo \
  --run-id <run_id>
```

Use the same `session-id` and `run-id` from the interrupted run.

Resume payload mapping:

- `question` -> `answer`
- `select` -> `selection`
- `edit_text` -> `text`

For simple CLI usage, passing the user response as `--resume "<text>"` is sufficient for question and edit-text flows.

## Run by Stage

When iterating on a cached run, staged execution is faster:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py --text "..." --stages outline --run-id <run_id>
.venv/bin/python scripts/run_ppt_pipeline.py --text "..." --stages render --run-id <run_id>
```

Supported stages:

- `parse`
- `research`
- `outline`
- `render`
- `all`

Current limitation: `--resume` is supported by the full-graph `all` path. Staged execution is still cache-based and does not directly resume LangGraph interrupts.

## Output Layout

Each run creates `output/<run_id>/` for cached state and metadata.

Typical contents:

- `run.json`: original request and runtime flags
- `references/parsed_requirements.json`: structured parse result
- `references/references.txt`: URL and file content fetched from user inputs
- `references/references_all.txt`: aggregated reference text reused by later thought/outline stages
- `research/research.json`: simple Tavily search output
- `research/deep_report.md`: deep research report when deep mode is used
- `thought/thought.md`: generated PPT writing thought
- `outline/outline.json`: normalized outline cache
- `ppt.json`: render result metadata

Important distinction:

- `output/<run_id>/` stores cache files and metadata,
- rendered slide HTML plus final PDF/PPTX are written into the separate render directory referenced by `ppt.json`.

## Common Local Modes

Fastest local content-only setup:

```env
DISABLE_EMBEDDING=true
USE_WEB_IMG_SEARCH=false
USE_IMG_GEN=false
```

Search-assisted setup:

```env
TAVILY_API_KEYS=key1,key2
USE_WEB_IMG_SEARCH=true
```

Deep-research capable setup:

```env
TAVILY_API_KEYS=...
DISABLE_EMBEDDING=false
EMBEDDING_MODEL=...
EMBEDDING_API_KEY=...
EMBEDDING_API_BASE_URL=...
```

## Troubleshooting

If `preflight_failed` is returned:

- check the three default LLM settings first,
- make sure the local runtime has been bootstrapped with `scripts/install/install.py`,
- ensure Playwright Chromium is available when `render` or `all` is used.

If search silently skips:

- confirm `TAVILY_API_KEYS` is configured,
- otherwise the code intentionally falls back to `skip`.

If embedding work fails:

- either configure embedding settings fully,
- or set `DISABLE_EMBEDDING=true`.

If PPTX is missing but PDF exists:

- the render pipeline likely succeeded,
- local LibreOffice conversion was skipped or failed,
- inspect the render/export path in `core/ppt_generator/utils/common.py`.
