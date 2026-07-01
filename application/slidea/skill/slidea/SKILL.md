---
name: slidea
description: "AI-Powered PPT generation. Use for PPT creation where an agent needs full-run control over parse/research/outline/render flows. Generates a native editable PPTX from a topic, files, or URLs, with optional research and speech-script phases."
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

**Important**: Before starting, you **must ask user** whether they want to review and refine the PPT speech script first. Based on their answer:

- **User wants to review**: Execute Phase 1 **Research & Speech Script**, then proceed to Phase 2: **PPT Generation**.
- **User does not need to review** (Recommended): Skip Phase 1 and directly execute Phase 2: **PPT Generation**.

---

## Phase 1: Research & Speech Script

Before generating the PPT, produce a speech script markdown file.
**Read [research_speech.md](research_speech.md) for the complete Phase 1 instructions.**

Phase 1 provides built-in tools for extracting content from documents/web pages and searching online information — these tools are ready to use out of the box.

The output of Phase 1 is a saved markdown file at `<SPEECH_SCRIPT_MD_PATH>`.

---

## Phase 2: PPT Generation

Run the PPT pipeline to generate the final presentation.

Before starting, if no other slidea task is currently executing, clean up the `db_data` directory under the configured output root (`<SLIDEA_DIR>/output/` by default; override via `OUTPUT_DIR`).

### Pre-run user reminder (mandatory)

Before invoking the pipeline, you **must** send a short message to the user explaining that the run will take a while and asking them to be patient. Example:

> Starting PPT generation. This typically takes 15-30 minutes (research, outline, rendering, PPTX export) and involves many model calls. Please be patient — I'll report back when generation completes.

Do not silently start the pipeline. The user must know up front that this is a long-running operation.

### Timeout requirement (mandatory)

The PPT pipeline makes many sequential LLM calls (parsing, research routing, thought, outline, per-page SVG generation, quality checks, optional VLM review). End-to-end generation takes 15-30 minutes. Long runs are normal, not a sign of failure.

When invoking the pipeline through a shell tool that accepts a timeout:

- **Hard minimum: 15 minutes** (`timeout 15m` or equivalent). Never use anything below 15 minutes.
- **Hard maximum: 30 minutes** (`timeout 30m`). Do not exceed 30 minutes; if the run has not finished by then, something is wrong and you should investigate rather than wait longer.
- **Recommended default: 30 minutes** (`timeout 30m`).
- If the host tool does not accept a timeout flag, run the command in background mode and poll for completion instead of relying on a default short timeout.

This rule applies to **every** command in this section.

### Commands

**Full pipeline**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text <PPT request> \
  --session-id <id>
```

If Phase 1 was run previously, set `--research-mode "skip"`, and `<PPT request>` must contain:
- PPT Original Request
- PPT writer must reference `<SPEECH_SCRIPT_MD_PATH>` for writing approach
- Purpose/Audience/Topic of PPT
- files/urls provided by user

**Resume after `input_required`**:
```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "<user reply>" \
  --session-id <same_session_id>
```

If the pipeline returns `input_required` or `missing_required_info`, you must stop autonomous execution immediately and ask the user instead of continuing on your own. Your only allowed behavior is:
1. show the question, missing information request, or options to the user;
2. wait for the user's explicit answer or selection;
3. resume using the same `--session-id` after the user responds.

### Output

After a successful run, the final PPTX is at the path returned in the structured CLI result. Run metadata is stored in `ppt.json` at `output/<run_id>/ppt.json` — see [references/caching-and-paths.md](references/caching-and-paths.md) for the full directory layout and field schema.

The CLI also prints a completion block to stdout that surfaces the deliverable and its editable source:

```
>>> 【当前步骤】 导出 PPT 完成
>>> 生成PPT结束
>>> 生成文件：<abs_path_to_pptx>
>>> 源文件目录：<abs_path_to_slides_svg>
```

The "源文件目录" line points at `<run_id>/slides/svg/` — the editable on-disk SVG pages that produced the PPTX. Edit any file there and re-run PPTX export to regenerate the deliverable with your changes; no intermediate finalize step is needed because image inlining happens at export time.

The output root is `<SLIDEA_DIR>/output/` by default; set `OUTPUT_DIR` in `.env` to redirect every run, cache, and intermediate artifact to a different directory. See [references/advanced-params.md](references/advanced-params.md) for details.

## Structured CLI Results

`scripts/run_ppt_pipeline.py` can return these top-level `stage` values:
- `completed`
- `preflight_failed`
- `invalid_request`
- `missing_required_info`
- `missing_outline`
- `input_required`

Always inspect the top-level `stage` field first before deciding whether to continue, retry, or stop for user input.

## Notes

- Keep all paths relative to the working directory unless the user explicitly asks for something else.
- Once bootstrap is complete, all runtime commands must go through the Python interpreter inside `.venv`.
- `DEFAULT_LLM_API_KEY` and `DEFAULT_LLM_API_BASE_URL` must be configured before running the pipeline.
- If `MODEL_INVOKE_HANDOVER` is not `true`, `DEFAULT_LLM_MODEL` must also be configured, and `SLIDEA_MODE` should be `ECONOMIC` or `PREMIUM`.

## Advanced Topics (read on demand)

These are NOT needed for the common "generate a PPT" flow. Read them only when the user explicitly asks for the corresponding capability:

- [references/caching-and-paths.md](references/caching-and-paths.md) — Output directory structure, semantic run_id naming, cache reuse, how to find a previous run.
- [references/staged-execution.md](references/staged-execution.md) — Running individual stages (`parse`, `research`, `outline`, `render`) via `--stages`, useful for debugging or resuming partial runs.
- [references/patch-render.md](references/patch-render.md) — Re-rendering specific pages or fixing missing pages without a full rerun (`scripts/patch_render_missing.py`).
- [references/advanced-params.md](references/advanced-params.md) — `--research-mode`, `--image-search`, `--recursion-limit`, `--dry-run`, and other optional flags.
- [references/update.md](references/update.md) — How to update the skill when source code or dependencies change.
