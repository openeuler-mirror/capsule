# SVG Render Route (Default)

Slidea renders slides as SVG and exports native editable PPTX. The SVG route is the default render route — no `--render-mode` flag is required.

```bash
python3 scripts/run_ppt_pipeline.py --text "<request>"
```

This is the only render route advertised through `skill/SKILL.md`. Any alternative render route is opt-in and documented in the repository README; it is not part of this doc.

## Runtime Flow

```text
request -> thought -> outline -> slides/*.svg -> quality repair -> PPTX export (images inlined in a temp dir)
```

The route reuses the existing request parsing, research, thought generation, outline generation, image download, cache, and patch-render infrastructure. Only the page-render and export backend changes.

Key output paths are recorded in `output/<run_id>/ppt.json`:

- `render_mode`: `svg`
- `slides_dir`: directory holding slide source files (SVG pages directly inside, plus `prompts/` and `images/` subdirs)
- `svg_dir`: editable on-disk SVG pages (relative image references)
- `template_name`: persisted so patch-render reuses the same visual template
- `pptx_path`: generated native PPTX path (at the cache directory root)

## Cache And Patch Rendering

Render from a cached outline (default SVG route):

```bash
python3 scripts/run_ppt_pipeline.py \
  --text "<request>" \
  --stages render \
  --session-id demo
```

Patch render missing or selected pages:

```bash
python3 scripts/patch_render_missing.py --session-id <session_id>
python3 scripts/patch_render_missing.py --session-id <session_id> --indices 0,3,5
```

`patch_render_missing.py` resolves the internal run from `session-id`, restores the original request and render mode from `run.json`, and reuses the immutable style-pack snapshot when present.

## Quality Gates

Before export, the SVG route checks:

- XML well-formedness
- fixed `1280x720` canvas and `viewBox`
- forbidden SVG constructs such as `foreignObject`, `style`, scripts, masks, and animations
- unsupported references that would break native PPTX conversion

Repair happens in layers:

1. deterministic SVG cleanup during extraction,
2. LLM repair for quality-check failures,
3. optional VLM screenshot review and repair when `ENABLE_VLM_VISUAL_REVIEW=true` (defaults to `false`) and VLM settings are configured.

If VLM review is disabled (the default) or the VLM is unavailable, the SVG route continues without visual review and skips generating `vlm_screenshots/`, `vlm_svg_candidates/`, and `<page>_vlm_review.json`.

## Manual QA Checklist

Use this checklist before treating a new SVG-route change as production-ready:

1. Generate a small deck with the default SVG route (no `--render-mode` flag needed).
2. Confirm `ppt.json` contains `render_mode: "svg"` and non-empty `slides_dir`, `svg_dir`, and `pptx_path`.
3. Inspect `slides/`; page count should match the outline.
4. Open the generated PPTX in PowerPoint or another PPTX viewer.
5. Confirm text boxes and basic shapes are editable, not flattened screenshots.
6. Confirm images display and keep reasonable aspect ratios.
7. Compare several PPTX pages against matching `slides/*.svg` files.
8. Run `patch_render_missing.py --session-id <session_id> --indices <page>` and confirm the PPTX is rebuilt.

Fast regression suite used during implementation:

```bash
PYTHONPATH=application/slidea application/slidea/.venv/bin/python -m unittest \
  application.slidea.tests.test_svg_vlm_review \
  application.slidea.tests.test_svg_quality_repair \
  application.slidea.tests.test_svg_templates \
  application.slidea.tests.test_svg_export \
  application.slidea.tests.test_patch_render_cli_smoke \
  application.slidea.tests.test_svg_utils \
  application.slidea.tests.test_svg_finalize \
  application.slidea.tests.test_svg_quality_checker \
  application.slidea.tests.test_cli_stage_smoke \
  application.slidea.tests.test_common \
  application.slidea.tests.test_pipeline_contracts
```
