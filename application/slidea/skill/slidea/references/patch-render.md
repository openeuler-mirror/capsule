# Patch Render (Re-render Specific Pages)

Read this when the user wants to redo specific pages without rerunning the full pipeline. **Not needed for normal full-pipeline generation.**

## When to Use

- Some pages failed quality check or are visually broken.
- The user wants to redo specific pages with adjusted content.
- The run completed but a few pages are missing from the final PPTX.

Patch render regenerates the targeted pages via the same LLM pipeline used during initial generation. It is **not** an in-place SVG editor — each target page is re-generated from scratch using its outline entry and reference material.

## Command

```bash
.venv/bin/python scripts/patch_render_missing.py \
  --session-id <same_session_id> \
  --indices "0,1,2,9"
```

- `--session-id <same_session_id>` (required for normal Agent use): resolves the original internal run directory automatically. Do not search for or expose `--run-id` in the normal workflow.
- `--run-id <run_id>` (advanced alternative): only for manually disambiguating legacy data where multiple original runs share one session id.
- `--text "<PPT request>"` (optional override): normally omit it; the script restores the original request from `run.json`.
- `--indices "0,1,2,9"` (optional): comma-separated 0-based page indices to regenerate. Omit to auto-detect missing pages.

## Behavior

- Resolves the run from `--session-id`, then reads `run.json` for the original request, render mode, and session binding.
- Reads `ppt.json` when available to find `slides_dir` and `template_name`; incomplete runs without `ppt.json` use `<run>/slides` and the render mode from `run.json`.
- Reads `output/<run_id>/outline/outline.json` to get page metadata.
- Reuses the run's immutable `style_pack` snapshot and the `style_reference_id` already assigned in `outline.json`. Style-mode quality checks inspect only generated dynamic content and then restore the fixed reference shell; the legacy no-style-pack path remains unchanged.
- For each target index, regenerates that page's SVG via the LLM (same prompt template as initial generation).
- Re-runs quality check on all pages (including non-targeted ones).
- Refuses PPTX export with `render_incomplete` if any outline page is still missing after the patch.
- Re-exports the PPTX to `<run_id>/<topic>.pptx` at the cache root (overwrites the previous PPTX).
- Updates `output/<run_id>/ppt.json` with new paths.

If no target indices are missing and `--indices` is omitted, returns `stage: completed` with empty `target_indices` and skips regeneration.

## Time Expectation

Each regenerated page is one LLM call plus quality check and PPTX export. Apply the same **timeout ≥ 15 minutes** rule as the full pipeline. Remind the user that patching takes a few minutes before invoking.

## Structured Results

Top-level `stage` values from `patch_render_missing.py`:
- `completed` — regeneration finished (or no missing pages detected)
- `missing_outline` — `outline/outline.json` not found; nothing to patch
- `empty_outline` — outline exists but has zero pages
- `svg_quality_failed` — quality check failed after regeneration; check the message field for details
- `render_incomplete` — one or more outline pages are still absent; PPTX export was skipped
- `invalid_request` — session not found or ambiguous

Always inspect `stage` first before deciding whether to continue, retry, or surface the issue to the user.

## What Patch Render Does NOT Do

- It does not edit existing SVGs in place. For that, follow the Phase 3 agent-led editing workflow in [agent-edit.md](agent-edit.md) — edit SVG files under `slides/` and **defer the PPTX re-export until the user explicitly signals completion** (see agent-edit.md §0 for the deferral rule). The on-disk SVG is the single source of truth — image inlining happens at export time inside a temporary directory, so edits to `slides/` take effect on the next PPTX export without any intermediate step.
- It does not regenerate the outline. If the outline itself is wrong, re-run the `outline` stage instead.
- It does not refresh research material. The regenerated page reuses whatever is in `outline/<idx>_*.reference_doc`.
