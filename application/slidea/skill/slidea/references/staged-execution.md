# Staged Execution

Read this when debugging a partial run, resuming from a failed stage, or running stages individually for any other reason. **Not needed for normal full-pipeline PPT generation.**

## Stages

The pipeline has four stages, executed in this order:

| Stage | What it does | Output |
|---|---|---|
| `parse` | Parse user request into structured requirements (audience, topic, goal, urls, missing_info) | `references/parsed_requirements.json` |
| `research` | Run Tavily search / deep research based on parsed requirements; download reference images | `research/research.json` (simple) or `research/deep_report.md` (deep), `references/images.json`, `references/references_all.txt` |
| `outline` | Generate the PPT outline (title, abstract, type, index for each page) using research material + thought strategy | `outline/outline.json`, `thought/thought.md` |
| `render` | Generate per-page SVG (LLM calls, possibly parallel), run quality checks, export PPTX | `slides/`, `<run_id>/<topic>.pptx` |

## Running Specific Stages

Pass `--stages` with a comma-separated list:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "<PPT request>" \
  --session-id <id> \
  --stages parse,research
```

Supported values: `all` (default), `parse`, `research`, `outline`, `render`. You can list multiple stages; they run in the canonical order above regardless of the order you list them in.

## When to Use Staged Execution

- **Debugging**: run only `parse` to inspect what the parser extracted, then decide whether to continue.
- **Resume after failure**: if `render` failed but `outline` succeeded, re-run with `--stages render` and the same `--session-id`. The original run_id is recovered automatically from session-id and all artifacts stay in one directory.
- **Iteration**: edit `outline/outline.json` manually, then re-run `--stages outline,render` (same `--session-id`) to use the edited outline as input to rendering.

## Stage Dependencies

| Stage | Depends on |
|---|---|
| `parse` | nothing |
| `research` | `parse` outputs (uses parsed requirements) |
| `outline` | `research` outputs + `parse` outputs |
| `render` | `outline` outputs |

If you skip a stage's prerequisite, the pipeline reads whatever cached output exists from a prior run with the same `run_id`. If no cache exists, the stage will fail with a clear error.

## Cache Reuse Between Stages

Cache is keyed on `run_id`, and for staged runs the `run_id` is automatically recovered from `--session-id` (same mechanism as `--resume`). As long as you reuse the same `--session-id`, each stage reads prior stage outputs from `output/<run_id>/` and writes its own outputs there. This is what makes staged execution work — see [caching-and-paths.md](caching-and-paths.md) for the on-disk layout.

## Same Timeout Rules Apply

Staged runs still make many LLM calls. The same `timeout ≥ 15 minutes` rule from the main SKILL.md applies — never shorten it for staged runs.
