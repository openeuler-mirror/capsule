---
name: deep_research
description: "AI-Powered deep research with automatic web search, content extraction, and structured report generation. Use for comprehensive research tasks that require multi-source investigation and synthesis."
---

# Deep Research

Use the directory containing this SKILL.md as the Deep Research skill directory (referred to as `<DEEP_RESEARCH_DIR>`), and run all commands from there.

## Runtime Rule

- This skill must be run exclusively using the Python interpreter located inside `.venv`. Both the `.venv` directory and the skill scripts are located in `<DEEP_RESEARCH_DIR>`.
- Do not use system `python` or `python3` for pipeline commands after the environment check.
- Use the Python interpreter inside `.venv` for every script in `scripts/`.
- Unix-like example: `.venv/bin/python`
- Windows example: `.venv/Scripts/python.exe`

## Workflow Overview

Deep Research performs multi-source investigation on a given topic, automatically searching the web, extracting content from relevant pages, and synthesizing findings into a structured research report.

---

## Running Deep Research

Each run is identified by `--session-id` (LangGraph thread id) and writes everything (deliverables, planning snapshots, LangGraph checkpointer) under `<output_root>/<run_id>/`, where `<run_id>` is auto-generated as `<timestamp>_<semantic-summary>` (LLM summarizes the research request into a directory-safe suffix). Use a **new** session-id for a fresh start; reuse the **same** session-id to resume an interrupted run — the original `run_id` is recovered automatically.

**Full research run**:
```bash
.venv/bin/python scripts/run_deep_research.py \
  --research-request "<research topic or question>" \
  --session-id <id>
```

---

## Resuming After Failure

If the research run fails due to a runtime exception (network error, API timeout, etc.), you can resume from the last saved checkpoint:

```bash
.venv/bin/python scripts/run_deep_research.py \
  --resume \
  --session-id <same_session_id>
```

**Important**: 
- Always reuse the same `session_id` when resuming an interrupted run — `run_id` is recovered automatically from the session-id ↔ run_id binding in `run.json`.
- The Deep Research pipeline may take a long time(10-30 minutes) to complete. If the runtime environment supports it, execute the pipeline with timeout set to 60 minutes.

---

## Output
- `<output_root>/<run_id>/deep_report.md`: The final synthesized research report in markdown format. `<output_root>` is `<DEEP_RESEARCH_DIR>/output/` by default and can be overridden via `OUTPUT_DIR` in `.env`. The directory is auto-created on first run.
- `<output_root>/<run_id>/run.json`: session_id ↔ run_id binding record (used by `--resume` to recover `run_id`).
- Intermediate planning snapshots (`todo_*.txt`) are written alongside `deep_report.md` in the same `<run_id>/` directory for debugging.
- `checkpointer.sqlite` (and its `-shm`/`-wal` companions) is the LangGraph resume state. It lives in the same `<run_id>/` directory during a run, and is **auto-removed on success**. A failed run leaves it behind so `--resume --session-id <same_id>` can pick up where it stopped; the file is safe to delete manually once you no longer want to resume.

### Session-id Collision Detection

If multiple original runs share the same `--session-id` (typical when users reuse a value across unrelated tasks), `--resume` recovery refuses to guess — it logs a WARNING listing the candidate `run_id`s and exits instead of writing into the wrong run directory. Disambiguate by passing `--run-id <one_of_the_listed>` explicitly, or use a unique `--session-id` per task.

If you omit `--session-id` entirely, the CLI auto-generates a unique value (`auto_<pid>_<ts>`), so unrelated runs can never collide. The trade-off: auto-generated ids cannot be reused for `--resume` — pass an explicit `--session-id` whenever you intend to continue a prior run.

## Run Logs
- Logs are stored in `logs/app_{time:YYYY-MM-DD}.log`
- Use `logs/app_{time:YYYY-MM-DD}.log` for debugging when needed. Console output and structured CLI JSON remain the primary runtime signals.

## Parameters
Parameter selection must be conservative and user-driven.
Only pass CLI parameters that the user explicitly specified in their request or explicitly confirmed during follow-up interaction.
Do not optimize, infer, or personalize parameter values on the user's behalf just because one choice seems faster, cheaper, higher quality, or more appropriate for the task.
If the user did not clearly specify a parameter, do not set it manually. Omit it and let the CLI use its built-in default behavior instead.
