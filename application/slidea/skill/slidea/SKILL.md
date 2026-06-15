---
name: slidea
description: "AI-Powered PPT generation with caching and patch rendering by run_id. Use for PPT creation where an agent needs full-run control over parse/research/outline/render flows, plus cached reuse and selective re-rendering."
---

# Slidea

Use the directory containing this SKILL.md as the Slidea skill directory (referred to as `<SLIDEA_DIR>`), and run all commands from there.

## Runtime Rule

- This skill must be run exclusively using the Python interpreter located inside `.venv`. Both the `.venv` directory and the skill scripts are located in <SLIDEA_DIR>.
- Do not use system `python` or `python3` for pipeline commands after the environment check.
- Use the Python interpreter inside `.venv` for every script in `scripts/`.
- Unix-like example: `.venv/bin/python`
- Windows example: `.venv/Scripts/python.exe`

## Render Route Selection

Slidea supports two render routes:

- HTML route: default route for generated HTML/PDF/PPTX artifacts.
- SVG route: optional route for SVG-generated PPTX output.

Use the HTML route by default. Do not add `--render-mode` unless the user explicitly asks for the SVG route.

Only use `--render-mode svg` when the user clearly requests SVG-generated PPTX output.

Do not proactively mention the SVG route, recommend it, or ask the user whether they want to use it. If the user only asks for a PPT, deck, slide deck, presentation, PDF, PPTX, or normal generation, keep the default HTML route.

## Workflow Overview

**Important**: Before starting, you **must ask user** whether they want to review and refine the PPT speech script at first. Based on their answer:

- **User wants to review**: Execute Phase 1 **Research & Speech Script**, Then proceed to Phase 2: **PPT Generation**.
- **User does not need to review** (Recommended): Skip Phase 1 and directly execute Phase 2: **PPT Generation**.

---

## Phase 1: Research & Speech Script

Before generating the PPT, you must first produce a speech script markdown file.
**Read [research_speech.md](research_speech.md) for the complete Phase 1 instructions.**

Phase 1 provides built-in tools for extracting content from documents/web pages, and search tool for retrieving online information — these tools are ready to use out of the box.

The output of Phase 1 is a saved markdown file at `<SPEECH_SCRIPT_MD_PATH>`.

---

## Phase 2: PPT Generation
run the PPT pipeline to generate the final presentation.

Before starting, if no other slidea task is currently executing, clean up the `<SLIDEA_DIR>/output/db_data` directory.

> **Important**: The PPT pipeline may take a long time to complete. If the runtime environment supports it, execute the pipeline with timeout set to 60 minutes.


**Full pipeline**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text <PPT request> \
  --session-id <id> \
  --run-id <run_id>
```

If Phase 1 was run previously, set --research-mode "skip", and `<PPT request>` must contain:
- PPT Original Request
- PPT writer must reference `<SPEECH_SCRIPT_MD_PATH>` for writing approach
- Purpose/Audience/Topic of PPT
- files/urls provided by user


**Resume after `input_required`**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "<user reply>" \
  --session-id <same_session_id> \
  --run-id <same_run_id>
```
Always reuse the same `run_id` and `session_id` when resuming an interrupted run.

If the pipeline returns `input_required` or `missing_required_info`, you must stop autonomous execution immediately and ask the user instead of continuing on your own.
When this happens, do not infer the user's intent, do not answer on the user's behalf, do not choose from provided options yourself.
Your only allowed behavior is:
1. show the question, missing information request, or options to the user;
2. wait for the user's explicit answer or selection;
3. resume using the same `run_id` after the user responds.
If the host agent environment tends to auto-answer tool or skill interactions, treat that behavior as incorrect for this skill and override it by routing the interaction back to the user.
The `run_id` parameter must be obtained from the output of a Full pipeline and must remain consistent throughout the entire task lifecycle.
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

## Parameters
Parameter selection must be conservative and user-driven.
Only pass CLI parameters that the user explicitly specified in their request or explicitly confirmed during follow-up interaction.
Do not optimize, infer, or personalize parameter values on the user's behalf just because one choice seems faster, cheaper, higher quality, or more appropriate for the task.
If the user did not clearly specify a parameter, do not set it manually. Omit it and let the CLI use its built-in default behavior instead.

This rule applies to all optional parameters, including but not limited to:
- `--research-mode`
- `--image-search`
- `--session-id`
- `--run-id`
- `--recursion-limit`

When reading the user's request, distinguish between:
- explicit parameter intent: the user directly asked for a research mode, or similar execution control;
- task content: the user only described the presentation topic, audience, style, or desired outcome.

Task content alone is not permission to set optional CLI parameters.
Unless the user explicitly expressed a parameter preference, keep the parameter unset and rely on the default value.

`--research-mode` is a high-impact parameter because it can materially change runtime length, generation depth, and overall end-to-end behavior.
If you want to set `--research-mode` to `simple` or `deep`, you must explicitly ask the user which mode they want. Do not choose the mode on the user's behalf, even if one mode seems more appropriate based on the request. Only set `--research-mode` after the user has clearly confirmed that exact choice. Otherwise, you may set `--research-mode` to `skip` without asking the user.

`scripts/run_ppt_pipeline.py`:
- `--text "<PPT request> 参考文件路径: <SPEECH_SCRIPT_MD_PATH>"`: new PPT request text; `--text` or `--resume` must be provided; Preserve user original input as much as possible; The speech script markdown file path from Phase 1 must be appended as `参考文件路径: <SPEECH_SCRIPT_MD_PATH>` after the request text.
- `--resume "<user reply>"`: continue an interrupted LangGraph run using the user's answer, selection, or edited text
- `--session-id <id>`: session / thread id, default `local`
- `--stages <comma-separated>`: stage selection, default `all`; supported values are `all`, `parse`, `research`, `outline`, `render`
- `--render-mode {html|svg}`: render route, default is `html`; omit this unless the user explicitly requested the SVG route
- `--research-mode {skip|simple|deep}`: force research mode, skip means no research, simple means shallow research, deep means deep research, default is ''
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
- `DEFAULT_LLM_API_KEY` and `DEFAULT_LLM_API_BASE_URL` must be configured before running the pipeline.
- If `MODEL_INVOKE_HANDOVER` is not `true`, `DEFAULT_LLM_MODEL` must also be configured, and `SLIDEA_MODE` should be `ECONOMIC` or `PREMIUM`.
