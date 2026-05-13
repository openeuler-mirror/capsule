# SVG Render Route

Slidea now has an optional SVG-to-native-PPTX route alongside the default HTML route.

The HTML route remains the default. The SVG route is selected explicitly with:

```bash
python3 scripts/run_ppt_pipeline.py --text "<request>" --render-mode svg
```

## Runtime Flow

```text
request -> thought -> outline -> svg_output/*.svg -> quality repair -> svg_final/*.svg -> native PPTX
```

The route reuses the existing request parsing, research, thought generation, outline generation, image download, cache, and patch-render infrastructure. Only the page-render and export backend changes.

Key output paths are recorded in `output/<run_id>/ppt.json`:

- `render_mode`: `svg`
- `render_dir`: deck render directory
- `svg_output_dir`: raw LLM-generated SVG pages
- `svg_final_dir`: finalized SVG pages used for export
- `pptx_path`: generated native PPTX path

## Cache And Patch Rendering

Render from a cached outline:

```bash
python3 scripts/run_ppt_pipeline.py \
  --text "<request>" \
  --stages render \
  --run-id <run_id> \
  --render-mode svg
```

Patch render missing or selected pages:

```bash
python3 scripts/patch_render_missing.py --run-id <run_id>
python3 scripts/patch_render_missing.py --run-id <run_id> --indices 0,3,5
```

`patch_render_missing.py` reads `ppt.json` and chooses the HTML or SVG patch path automatically.

## Quality Gates

Before export, the SVG route checks:

- XML well-formedness
- fixed `1280x720` canvas and `viewBox`
- forbidden SVG constructs such as `foreignObject`, `style`, scripts, masks, and animations
- unsupported references that would break native PPTX conversion

Repair happens in layers:

1. deterministic SVG cleanup during extraction,
2. LLM repair for quality-check failures,
3. optional VLM screenshot review and repair when VLM settings are configured.

If VLM is unavailable, the SVG route continues without visual review.

## Manual QA Checklist

Use this checklist before treating a new SVG-route change as production-ready:

1. Generate a small deck with `--render-mode svg`.
2. Confirm `ppt.json` contains `render_mode: "svg"` and non-empty `svg_output_dir`, `svg_final_dir`, and `pptx_path`.
3. Inspect `svg_output/` and `svg_final/`; page count should match the outline.
4. Open the generated PPTX in PowerPoint or LibreOffice.
5. Confirm text boxes and basic shapes are editable, not flattened screenshots.
6. Confirm images display and keep reasonable aspect ratios.
7. Compare several PPTX pages against matching `svg_final/*.svg` files.
8. Run `patch_render_missing.py --run-id <run_id> --indices <page>` and confirm the PPTX is rebuilt.

Fast regression suite used during implementation:

```bash
PYTHONPATH=application/slidea application/slidea/.venv/bin/python -m unittest \
  application.slidea.tests.test_svg_vlm_review \
  application.slidea.tests.test_svg_quality_repair \
  application.slidea.tests.test_svg_templates \
  application.slidea.tests.test_svg_spec_lock \
  application.slidea.tests.test_svg_export \
  application.slidea.tests.test_patch_render_cli_smoke \
  application.slidea.tests.test_svg_utils \
  application.slidea.tests.test_svg_finalize \
  application.slidea.tests.test_svg_quality_checker \
  application.slidea.tests.test_cli_stage_smoke \
  application.slidea.tests.test_common \
  application.slidea.tests.test_pipeline_contracts
```
