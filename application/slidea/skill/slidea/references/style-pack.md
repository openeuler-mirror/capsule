# Reference PPTX Style Pack

Use this phase only when the user supplied a `.pptx` and explicitly asked the new deck to follow its style.

## 1. Convert the reference PPTX to SVG material

Before conversion, choose a new unique `<SESSION_ID>` for the complete Slidea task. The converter fixes `<STYLE_PACK_DIR>` to `/tmp/slidea/style-packs/<SESSION_ID>`; do not choose another output directory. Run from `<SLIDEA_DIR>`:

```bash
.venv/bin/python scripts/pptx_to_style_pack.py \
  <USER_REFERENCE_PPTX> \
  --session-id <SESSION_ID>
```

Use the same `<SESSION_ID>` later with `run_ppt_pipeline.py`. The temporary directory must be new and empty; if it already contains material from another attempt, choose a new session id instead of overwriting it.

The command writes `reference/slideN.svg`, referenced image assets, `reference/conversion-report.json`, and an advisory `asset-inventory.json`. It must not create `style-pack.json` or PNG previews.

Expected material:

```text
/tmp/slidea/style-packs/<SESSION_ID>/
  asset-inventory.json
  reference/
    slide1.svg
    ...
    conversion-report.json
    images/
```

## 2. Agent authors style-pack.json from SVG source

Do not use screenshots or require visual capability. Read SVG source code to understand coordinates, text hierarchy, element groups, shapes, colors, image frames and layout relationships. Read `asset-inventory.json` first to find likely recurring template images. Its `candidate` and `signals` fields only reduce inspection work; they never authorize reuse and must not be copied mechanically into the manifest. Assets with `needs_explicit_authorization: false` already belong to inherited background/master/layout layers and must not be repeated in `reusable_assets`.

Do not describe every converted slide. For a large deck, inspect SVGs in manageable batches and choose only a small, visually distinct set that covers the useful page roles and structures. Normally 4-10 representative pages are enough; use fewer when the source deck has little variation. Unselected SVGs may remain under `reference/` but must not appear in `pages`.

Before assigning `page_type`, verify the selected SVG's actual `<text>` content and `main-content` structure. A cover, TOC or thanks reference must visibly contain that role's real title/content slots. Exclude template license, credits, FAQ, usage instructions, brand-resource and legal notice pages even when their layout looks decorative; never label one of those pages as `cover`, `toc`, `separator` or `thanks`.

Create `<STYLE_PACK_DIR>/style-pack.json` yourself. Use this schema:

```json
{
  "version": 1,
  "source": "reference.pptx",
  "global_style": "Agent description of palette, typography, spacing and recurring motifs",
  "reusable_assets": [
    {
      "id": "bottom-red-brush",
      "path": "reference/images/image12.png",
      "role": "decoration",
      "reason": "Transparent red brush repeated at the bottom edge on several page types"
    }
  ],
  "pages": [
    {
      "id": "content-three-cards",
      "source_slide": 7,
      "svg": "reference/slide7.svg",
      "page_type": "content",
      "density": "medium",
      "structure": "Title band above three equal cards with aligned headings and short body copy",
      "description": "Use for three parallel viewpoints, options or comparisons",
      "fixed_image_elements": [
        {"element_id": "shape-42", "layer": "back"}
      ]
    }
  ]
}
```

Set `source` to the exact basename of the source PPTX (for example `demo.pptx`). The CLI uses it to prevent the same file from being accidentally consumed as both style and content.

Required page fields:

- `id`: unique stable identifier written by the Agent;
- `source_slide`: positive source slide number;
- `svg`: selected SVG path below the style-pack directory;
- `page_type`: `cover`, `toc`, `separator`, `content`, or `thanks`;
- `density`: `sparse`, `medium`, or `dense`;
- `structure`: concrete spatial and component layout description derived from SVG code;
- `description`: what content shape this layout supports.

Optional reusable template image fields:

- top-level `reusable_assets`: the Agent's explicit allow-list. Each object requires a unique `id`, a safe local `path`, a concise `role`, and a concrete `reason` based on inspected SVG structure;
- page-level `fixed_image_elements`: direct child groups of that SVG's `main-content` that code must restore deterministically;
- `element_id`: the exact id of one direct `main-content` child. The element must contain at least one image and no text;
- `layer`: `back` places the element behind generated dynamic content; `front` places it above dynamic content.

Only authorize visual identity assets such as repeated transparent ornaments, branded strips, flags, silhouettes, corner marks or fixed photographic cut-outs that are genuinely part of the template. Do not authorize charts, screenshots, portraits, product photos or other source-slide business content. Every image referenced by a fixed element must be present in `reusable_assets`, and every declared reusable asset must be used by at least one fixed element. When uncertain, leave it unlisted.

Describe layout effects, not the source slide's topic. Do not use embeddings, text-overlap scores, generated density formulas, automatic structure classification or skeleton SVGs. Do not modify selected SVG geometry.

## 3. Validate the Agent-authored JSON

Run only the deterministic validator after writing the JSON:

```bash
.venv/bin/python scripts/validate_style_pack.py <STYLE_PACK_DIR>
```

The validator checks JSON syntax, required fields, enum values, duplicate ids, path safety and selected SVG existence. For reusable images it also checks declared files, direct-child element ids, `back/front` layers, absence of text, and exact correspondence between element image paths and the explicit allow-list. It does not generate, authorize or rewrite anything. Fix every validation error before Phase 2.

If conversion or validation fails, report the warning and continue Phase 2 without `--style-pack`; the built-in template workflow is the required fallback.

## 4. Generate with the pack

### Keep style and content channels separate

Phase 0 consumes `<USER_REFERENCE_PPTX>` to prepare style material. Phase 2 consumes `<STYLE_PACK_DIR>` through `--style-pack`. Do not put any of the following in `--text`:

- the reference PPTX path, filename, URL, or `file://` URL;
- `<STYLE_PACK_DIR>` or its `style-pack.json` path;
- converted `reference/slideN.svg` paths;
- instructions such as "read demo.pptx for reference" when only its visual style is wanted.

`--text` is a content request channel. Its document paths and URLs are extracted into `parsed_requirements.urls`, then opened and added to the writing references. Keep only the topic, audience, purpose, page/content requirements, and genuine content sources in it.

Incorrect:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "Explain AI Agent internals; follow /data/demo.pptx" \
  --session-id <SESSION_ID> \
  --style-pack <STYLE_PACK_DIR>
```

Correct:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text "Explain AI Agent internals to software architects in about 12 pages" \
  --session-id <SESSION_ID> \
  --style-pack <STYLE_PACK_DIR>
```

The CLI rejects the command before generation when `--text` contains the PPTX named by the pack's `source`. Only when the user explicitly wants that same PPTX's business content as factual source material may the Agent add `--allow-style-source-content`. Do not use this override merely to bypass the safety check.

Pass the prepared pack when starting the full pipeline or the outline stage:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --text <PPT_REQUEST> \
  --session-id <SESSION_ID> \
  --style-pack <STYLE_PACK_DIR>
```

Slidea validates and copies the pack to `output/<run_id>/style_pack/`. During outline generation, a dedicated style-mode prompt selects `style_reference_id` using only page type, density and structure. Long decks are grouped by the existing chapter `source` and oversized chapters are processed in bounded batches. The selected id is saved directly in `outline/outline.json`; there is no separate `style-plan.json`.

Before parallel page generation, Slidea prepares runtime reference copies under `output/<run_id>/slides/style_references/`. Images inside inherited background/master/layout/title shell layers and images inside validated `fixed_image_elements` are copied to `output/<run_id>/slides/images/style-pack/` with rewritten runtime paths. Authorized `back` elements are injected behind generated content and authorized `front` elements above it. Every other image inside `main-content` is treated as source-deck business content and remains unavailable for reuse.

In style mode, each page Agent generates only dynamic main content. After generation and again after any VLM/quality repair, code deterministically restores the assigned reference page's background, master/layout layers, title geometry, header/footer, fixed logos, authorized reusable decorations and page-number format. Cover, TOC, separator and thanks prompts prohibit creative redesign when a matching reference exists. Do not ask the Agent to redraw or manually reference these fixed elements. This composition path is inactive when `--style-pack` is absent, so the existing built-in template workflow remains unchanged.

Treat `/tmp/slidea/style-packs/<SESSION_ID>` only as Phase 0 working material. After the pipeline returns `completed`, confirm `output/<run_id>/style_pack/` exists before removing the temporary directory. Keep the temporary directory when conversion, validation, preflight or generation has not reached a confirmed run snapshot.

Do not first create an unstyled outline and add `--style-pack` only during a later render-only stage. Reference selection belongs to outline generation. If no pack is supplied, or pack/assignment/fixed-asset preflight fails, Slidea clears every style reference before fan-out and uses the existing built-in template flow for the whole run.
