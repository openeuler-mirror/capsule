# Advanced CLI Parameters

The default invocation in the main SKILL.md needs no flags beyond `--text` and `--session-id`. Read this only when the user explicitly asks for one of the capabilities below.

## Core Rule (Always Applies)

Parameter selection must be conservative and user-driven. Only pass CLI parameters that the user explicitly specified or explicitly confirmed during follow-up interaction. Do not optimize, infer, or personalize parameter values on the user's behalf. If the user did not clearly specify a parameter, omit it and let the CLI use its built-in default.

## Output Root (`.env`)

`OUTPUT_DIR` in `<SLIDEA_DIR>/.env` redirects every run, cache, and intermediate artifact slidea writes — PPT runs, deep research workspace, sqlite checkpoints, downloaded documents — to the configured directory.

- **Empty (default)**: output goes to `<SLIDEA_DIR>/output/` (auto-created).
- **Absolute path** (e.g. `OUTPUT_DIR=/data/slidea-output`): that directory becomes the output root; slidea treats it exactly like `<SLIDEA_DIR>/output`.
- **Relative path** (e.g. `OUTPUT_DIR=./my_output`): resolves against `<SLIDEA_DIR>`.

This is an environment-level setting, not a CLI flag. The agent does not pass it; the user edits `.env` directly. When helping the user configure it, ensure the directory is writable and has enough disk space (a single PPT run is ~1–10 MB; deep research workspaces can grow larger).

## `scripts/run_ppt_pipeline.py` Flags

| Flag | Values | When to use |
|---|---|---|
| `--text "<request>"` | string | Supply the PPT request. Exactly one of `--text`, `--resume`, or `--continue` is required. With default `--stages all`, this starts a fresh run and an existing session is rejected. With non-default `--stages`, the same session may be reused intentionally to read cached stage outputs. Preserve the user's original input as much as possible. Assemble per SKILL.md "Assemble `--text`": the understood PPT request text, and if Phase 1 (Document Processing) ran, append `参考文档: <abs_path_to_structured.md>` on a new line — use the doc-processing output, **not** the original document paths. |
| `--resume "<user reply>"` | string | Supply the user's answer, selection, or edited text after `stage: input_required`. This flag takes the reply directly; do not also pass `--text`. Not used for process timeout recovery. |
| `--continue` | flag | Continue unfinished checkpoint tasks after timeout/process termination. Pass no `--text`, `--resume`, non-default `--stages`, or replacement runtime settings. |
| `--session-id <id>` | string | Public task/session id. If omitted for a new run, a unique id (`auto_<pid>_<ts>`) is generated. The auto-generated default changes every invocation and cannot be recovered, so **reuse the same explicit value** for continuation, interrupt replies, patch rendering, or staged execution. |
| `--stages <csv>` | `all` (default), `parse`, `research`, `outline`, `render` | Stage selection. See [staged-execution.md](staged-execution.md). |
| `--render-mode` | `svg` (default) | **Do not pass this flag.** The default SVG route is what this skill advertises. |
| `--research-mode` | `skip`, `simple`, `deep`, `''` (default) | Force research mode. **High-impact parameter** — see rule below. |
| `--image-search` | `on`, `off` | Toggle web image search. |
| `--run-id <id>` | string | Advanced internal override. Normal Agent workflows must use `--session-id`; use this only to disambiguate legacy session collisions or for low-level debugging. |
| `--recursion-limit <int>` | integer | Override LangGraph recursion limit. |
| `--dry-run` | flag | Run preflight only and skip generation. |

## The `--research-mode` Rule

`--research-mode` materially changes runtime length, generation depth, and overall behavior. **You must explicitly ask the user** which mode they want before setting this flag. Do not choose on the user's behalf, even if one mode seems more appropriate based on the request.

Only set `--research-mode` after the user has clearly confirmed that exact choice. Otherwise:
- You may set `--research-mode skip` without asking (skip is the safe default).
- Never set `--research-mode simple` or `--research-mode deep` without explicit user confirmation.

## `scripts/patch_render_missing.py` Flags

See [patch-render.md](patch-render.md) for full usage. Flags:

| Flag | Required | Purpose |
|---|---|---|
| `--session-id <id>` | yes* | Normal Agent-facing task id; resolves the internal run automatically. |
| `--run-id <id>` | yes* | Advanced mutually exclusive alternative for legacy collision debugging. |
| `--text "<request>"` | no | Explicit override; normally omit so the original request is restored from `run.json`. |
| `--indices "<csv>"` | no | 0-based page indices to regenerate. Omit to auto-detect missing. |

`*` Exactly one of `--session-id` or `--run-id` is required.

## Distinguishing Explicit Intent from Task Content

When reading the user's request, distinguish between:
- **Explicit parameter intent**: the user directly asked for a research mode, image toggle, specific session, or similar execution control.
- **Task content**: the user only described the presentation topic, audience, style, or desired outcome.

Task content alone is **not** permission to set optional CLI parameters.

Example: a user saying "做一份给高管看的技术洞察 PPT" is task content. It does NOT authorize you to set `--research-mode deep` or `--image-search on` — those still require explicit confirmation.
