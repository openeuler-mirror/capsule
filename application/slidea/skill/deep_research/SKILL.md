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

Before starting, if no other deep research task is currently executing, clean up the `<DEEP_RESEARCH_DIR>/output/dr_db` directory.

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
- Always reuse the same `session_id` when resuming an interrupted run.
- The Deep Research pipeline may take a long time(10-30 minutes) to complete. If the runtime environment supports it, execute the pipeline with timeout set to 60 minutes.

---

## Output
- `path/to/deep_report.md`: The final synthesized research report in markdown format

## Run Logs
- Logs are stored in `logs/app_{time:YYYY-MM-DD}.log`
- Use `logs/app_{time:YYYY-MM-DD}.log` for debugging when needed. Console output and structured CLI JSON remain the primary runtime signals.

## Parameters
Parameter selection must be conservative and user-driven.
Only pass CLI parameters that the user explicitly specified in their request or explicitly confirmed during follow-up interaction.
Do not optimize, infer, or personalize parameter values on the user's behalf just because one choice seems faster, cheaper, higher quality, or more appropriate for the task.
If the user did not clearly specify a parameter, do not set it manually. Omit it and let the CLI use its built-in default behavior instead.