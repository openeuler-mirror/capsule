---
name: slidea
description: "Flexible PPT generation via LangGraph with caching, staged execution, and patch rendering by run_id. Use for PPT creation where an agent needs full-run or stage-by-stage control over parse/research/outline/render flows, plus cached reuse and selective re-rendering."
---

# Slidea

Use the directory containing this SKILL.md as the Slidea skill directory (referred to as `<SLIDEA_DIR>`), and run all commands from there.

## Runtime Rule

- This skill must be run exclusively using the Python interpreter located inside `.venv`. Both the `.venv` directory and the skill scripts are located in <SLIDEA_DIR>.
- Do not use system `python` or `python3` for pipeline commands after the environment check.
- Use the Python interpreter inside `.venv` for every script in `scripts/`.
- Unix-like example: `.venv/bin/python`
- Windows example: `.venv/Scripts/python.exe`

## Quick Start

Full pipeline:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --session-id <id> \
  --run-id <run_id>
```

Resume after `input_required`:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "<user reply>" \
  --session-id <same_session_id> \
  --run-id <same_run_id>
```
Always reuse the same `run_id` and `session_id` when resuming an interrupted run.

Staged (file-driven):
```bash
# run only outline
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --stages outline \
  --run-id <run_id>

# run with deep research、outline、render using cached parse results
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --stages research,outline,render \
  --run-id <run_id> \
  --research-mode "deep"

# render using cached outline
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --stages render \
  --run-id <run_id>
```

`parse` / `research` / `outline` can now run without loading render-only browser modules at import time. Playwright/render dependencies are only required when you execute `render` or `all`.
If you run `render` without a cached outline for that `run_id`, the CLI returns a structured `missing_outline` JSON result.
If `parse` or `research` cannot continue because required information is missing, the CLI returns a structured `missing_required_info` JSON result that includes the failed stage.
If the pipeline returns `input_required` or `missing_required_info`, you must stop autonomous execution immediately and ask the user instead of continuing on your own.
When this happens, do not infer the user's intent, do not answer on the user's behalf, do not choose from provided options yourself.
Your only allowed behavior is:
1. show the question, missing information request, or options to the user;
2. wait for the user's explicit answer or selection;
3. run stages using the same `run_id` after the user responds.
If the host agent environment tends to auto-answer tool or skill interactions, treat that behavior as incorrect for this skill and override it by routing the interaction back to the user.
The `run_id` parameter must be obtained from the output of a Full pipeline. For subsequent stages within the same task, the `run_id` must remain consistent throughout all stages across the entire task lifecycle.
`scripts/install/install.py` is a bootstrap CLI with no command-line arguments. It prints step-based human-readable logs rather than JSON.

## Caching & Run ID
- `output/<run_id>/` is the cache/index directory for a run
- Key files:
  - `outline/outline.json` with `run_id`, `topic`, and `outline`
  - `research/research.json`
  - `research/deep_report.md`
  - `references/references_all.txt`
  - `thought/thought.md`
  - `ppt.json` stored at `output/<run_id>/ppt.json` with `run_id`, `topic`, `render_dir`, `pdf_path`, and `pptx_path`

Final HTML/PDF/PPTX files are written to the render output directory referenced by `ppt.json`. That render directory is separate from `output/<run_id>/` and is reused on patch render when available.

## Run Logs
- Logs are stored in `logs/app_{time:YYYY-MM-DD}.log`
- Use `logs/app_{time:YYYY-MM-DD}.log` for debugging when needed. Console output and structured CLI JSON remain the primary runtime signals.

## Outline Editing
For manual cached outline editing, modify `output/<run_id>/outline/outline.json` and rerun render with:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --stages render \
  --run-id <run_id>
```

## Parameters
Parameter selection must be conservative and user-driven.
Only pass CLI parameters that the user explicitly specified in their request or explicitly confirmed during follow-up interaction.
Do not optimize, infer, or personalize parameter values on the user's behalf just because one choice seems faster, cheaper, higher quality, or more appropriate for the task.
If the user did not clearly specify a parameter, do not set it manually. Omit it and let the CLI use its built-in default behavior instead.

This rule applies to all optional parameters, including but not limited to:
- `--research-mode`
- `--use-cache`
- `--image-search`
- `--stages`
- `--session-id`
- `--run-id`
- `--recursion-limit`

When reading the user's request, distinguish between:
- explicit parameter intent: the user directly asked for a mode, stage, cache behavior, or similar execution control;
- task content: the user only described the presentation topic, audience, style, or desired outcome.

Task content alone is not permission to set optional CLI parameters.
Unless the user explicitly expressed a parameter preference, keep the parameter unset and rely on the default value.

`--research-mode` is a high-impact parameter because it can materially change runtime length, generation depth, and overall end-to-end behavior.
If you want to set `--research-mode` to `simple` or `deep`, you must explicitly ask the user which mode they want. Do not choose the mode on the user's behalf, even if one mode seems more appropriate based on the request. Only set `--research-mode` after the user has clearly confirmed that exact choice. Otherwise, you may set `--research-mode` to `skip` without asking the user.

`scripts/run_ppt_pipeline.py`:
- `--text "<PPT request>"`: new PPT request text; `--text` or `--resume` must be provided; Preserve user original input as much as possible.
- `--resume "<user reply>"`: continue an interrupted `all`-stage LangGraph run using the user's answer, selection, or edited text
- `--session-id <id>`: session / thread id, default `local`
- `--stages <comma-separated>`: stage selection, default `all`; supported values are `all`, `parse`, `research`, `outline`, `render`
- `--research-mode {skip|simple|deep}`: force research mode, skip means no research, simple means shallow research, deep means deep research, default is ''
- `--use-cache {true|false}`: toggle cached reuse
- `--image-search {on|off}`: toggle web image search
- `--run-id <run_id>`: reuse or pin a run id
- `--recursion-limit <int>`: override LangGraph recursion limit
- `--dry-run`: run preflight only and skip generation

`scripts/patch_render_missing.py`:
- `--run-id <run_id>`: required
- `--text "<PPT request>"`: optional request text reused in render prompts
- `--indices "0,1,2"`: optional comma-separated slide indices to regenerate

## Patch render (missing/target pages)
Use when HTML pages are missing or you want to re-render specific page indices without full rerun.
```bash
.venv/bin/python scripts/patch_render_missing.py \
  --run-id <run_id> \
  --text "<PPT request>" \
  --indices "0,1,2,9"
```
- Omit `--indices` to auto-detect missing pages.
- Re-exports PDF/PPTX after patching.
- If no target indices are missing, the CLI returns `completed` with an empty `target_indices` list and skips regeneration.
- Returns structured JSON with `completed`, `missing_outline`, or `empty_outline` stage values.
- Shares the same JSON payload framing helper as `run_ppt_pipeline.py`.

## Structured CLI Results

`scripts/run_ppt_pipeline.py` can return these top-level `stage` values:
- `completed`
- `preflight_failed`
- `invalid_request`
- `missing_required_info`
- `missing_outline`
- `input_required`

Resume values are interpreted tolerantly. Upstream callers may resume with `payload.selection`, `payload.answer`, `payload.text`, or `payload.message`. The runtime consumes them in that order.

`--resume` currently applies to the compiled top-level graph path (`--stages all`). It is not a substitute for staged cache re-entry such as `outline` or `render`.

`scripts/patch_render_missing.py` can return these top-level `stage` values:
- `completed`
- `missing_outline`
- `empty_outline`

Always inspect the top-level `stage` field first before deciding whether to continue, retry, or stop for user input.

## Update

When the skill code or dependencies change, follow the update process in `UPDATE.md`:

1. Clone the latest code to a temporary directory
2. Export the updated skill package using `export_skill.py --update`
3. Delete the temporary directory
4. Switch to the skill directory and run `python scripts/install/update.py`

The update script only reinstalls dependencies if `requirements.txt` has changed.

## Notes
- Keep all paths relative to the working directory unless the user explicitly asks for something else.
- Once bootstrap is complete, all runtime commands must go through the Python interpreter inside `.venv`.
- If `DEFAULT_LLM_MODEL`, `DEFAULT_LLM_API_KEY`, or `DEFAULT_LLM_API_BASE_URL` is empty, do not attempt to run the pipeline.
