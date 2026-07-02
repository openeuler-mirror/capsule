# Common SVG Drawing Basics

This is the shared foundation for any Phase 3 edit that involves drawing a diagram on a slide. Layering rules, reusable component skeletons, and spacing formulas all live here. Layout-specific algorithms (architecture / flowchart / sequence / structural) live in their own files under [diagram-layouts/](diagram-layouts/).

## Table of Contents

- [1. When to Read Which Layout Doc](#1-when-to-read-which-layout-doc)
- [2. SVG Layering Order](#2-svg-layering-order)
- [3. Component Patterns](#3-component-patterns)
  - [3.1 Standard box](#31-standard-box-service--process--module)
  - [3.2 Decision diamond](#32-decision-diamond)
  - [3.3 Database cylinder](#33-database-cylinder)
  - [3.4 Region boundary](#34-region-boundary)
  - [3.5 Security group / restricted boundary](#35-security-group--restricted-boundary)
  - [3.6 Arrow markers](#36-arrow-markers)
- [4. Spacing Formulas](#4-spacing-formulas)
  - [4.1 First, determine the drawing-area size](#41-first-determine-the-drawing-area-size)
  - [4.2 Spacing formula table](#42-spacing-formula-table)
  - [4.3 Font-size suggestions](#43-font-size-suggestions-relative-to-drawing-area)
- [5. slidea SVG Compatibility Constraints](#5-slidea-svg-compatibility-constraints)
- [6. Other Diagram Types](#6-other-diagram-types)
  - [6.1 Mind map](#61-mind-map)
  - [6.2 Timeline](#62-timeline)
  - [6.3 State machine](#63-state-machine)
  - [6.4 Data flow diagram](#64-data-flow-diagram)
  - [6.5 Illustrative / conceptual](#65-illustrative--conceptual)
- [7. Pre-write Checklist](#7-pre-write-checklist)

## 1. When to Read Which Layout Doc

Pick the diagram type by the **semantic content** of the area being edited, then read the matching file. Do not draw "every kind of diagram at once" — one diagram solves one problem.

| Content semantic | Diagram type | Read |
|---|---|---|
| System composition / call chain / data flow / service relationships / deployment topology | Architecture | [diagram-layouts/architecture.md](diagram-layouts/architecture.md) |
| Decision logic / process steps / algorithm flow / approval flow | Flowchart | [diagram-layouts/flowchart.md](diagram-layouts/flowchart.md) |
| Time-ordered interactions / call order / protocol handshake / multi-role dialogue | Sequence | [diagram-layouts/sequence.md](diagram-layouts/sequence.md) |
| Class hierarchy / ER / org chart / package structure | Structural | [diagram-layouts/structural.md](diagram-layouts/structural.md) |
| Mind map / timeline / state machine / data flow / illustrative | Other | §6 of this file |

If the diagram does not match the first four rows, §2–§4 of this file together with §6 already cover everything you need.

## 2. SVG Layering Order

SVG stacks by paint order — later elements paint on top of earlier ones. A page that mixes components and connectors **must be layered in this order**:

1. **Background fill** — the slide template already covers the full canvas in `<g id="background">`, so usually you do not redraw it.
2. **Region / group boundaries** — dashed rectangles enclosing related components.
3. **Connector arrows and lines** — paint before component boxes so the line endpoints get covered by the boxes that follow.
4. **Opaque mask rect** — same x/y/width/height as the component box, fill = `<TEMPLATE_BACKGROUND_COLOR>`.
5. **Component box** — semi-transparent fill (`<TEMPLATE_PRIMARY_COLOR>` + `fill-opacity`) with stroke.
6. **Text labels** — always on top, never covered.
7. **Legend** — placed below all diagram elements, in the bottom-right or along the bottom.
8. **Title block** — top-left. But the slide template already provides `<g id="header">`, so do not paint a second title.

### Why the mask rect (step 4) is mandatory

The slidea SVG contract forbids the `<mask>` tag and forbids `rgba()`. When a component box uses a semi-transparent fill (`fill-opacity < 1`), **connector lines that pass underneath will show through the box** and look noisy. The fix is to paint an fully opaque rect with the template background color just before the component box, hiding the lines behind it.

```xml
<!-- Step 4: mask rect, hides arrows passing behind the box -->
<rect x="100" y="100" width="160" height="60" rx="6" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
<!-- Step 5: component box, semi-transparent fill -->
<rect x="100" y="100" width="160" height="60" rx="6"
      fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
<!-- Step 6: text -->
<text x="180" y="124" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="11" font-weight="600"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">API Gateway</text>
<text x="180" y="140" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="9"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">Kong / Nginx</text>
```

The same trick is already used by the existing slides for card shadows — two stacked rects — and is fully compatible with the export pipeline.

## 3. Component Patterns

The six skeletons below cover ~90% of diagram needs. All colors are placeholders. Resolve every placeholder from the slide template's `data-description` attributes (see §5 for where to find them).

### 3.1 Standard box (service / process / module)

The most common component. Used in architecture diagrams, flowchart process steps, and sequence-diagram actor boxes.

```xml
<!-- Two stacked rects: mask + visual -->
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="6" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="6"
      fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
<!-- Name (primary label) -->
<text x="<CX>" y="<Y + 24>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>" font-weight="600"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>"><Component Name></text>
<!-- Sublabel (optional, description / tech stack) -->
<text x="<CX>" y="<Y + 40>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.8>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>"><Sublabel></text>
```

`<CX>` = `<X> + <W>/2` (horizontally centered).

### 3.2 Decision diamond

Used in flowcharts for branching.

```xml
<g transform="translate(<CX>, <CY>)">
  <!-- mask -->
  <polygon points="0,-35 50,0 0,35 -50,0" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <!-- visual -->
  <polygon points="0,-35 50,0 0,35 -50,0"
           fill="<TEMPLATE_ACCENT_COLOR>" fill-opacity="0.3"
           stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <!-- label -->
  <text y="4" text-anchor="middle"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.9>"
        font-weight="600"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>"><Condition>?</text>
</g>
```

### 3.3 Database cylinder

Used in ER diagrams and architecture diagrams for data stores.

```xml
<g transform="translate(<X>, <Y>)">
  <!-- mask: body rect + top ellipse + bottom ellipse -->
  <rect x="0" y="10" width="120" height="50" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <ellipse cx="60" cy="10" rx="60" ry="12" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <ellipse cx="60" cy="60" rx="60" ry="12" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <!-- visual -->
  <rect x="0" y="10" width="120" height="50"
        fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"/>
  <ellipse cx="60" cy="10" rx="60" ry="12"
           fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
           stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <ellipse cx="60" cy="60" rx="60" ry="12"
           fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
           stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <!-- side lines -->
  <line x1="0" y1="10" x2="0" y2="60" stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <line x1="120" y1="10" x2="120" y2="60" stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <!-- name -->
  <text x="60" y="40" text-anchor="middle"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
        font-weight="600"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>"><DB Name></text>
</g>
```

### 3.4 Region boundary

Used in architecture diagrams to enclose components that share infrastructure (e.g. "AWS us-east-1", "Kubernetes Cluster").

```xml
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="12"
      fill="none"
      stroke="<TEMPLATE_ACCENT_COLOR>" stroke-width="1"
      stroke-dasharray="8,4"/>
<text x="<X + 12>" y="<Y + 16>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      font-weight="600"
      fill="<TEMPLATE_ACCENT_COLOR>"><Region Name></text>
```

**Nested regions** use different dash arrays to distinguish hierarchy:

| Level | dasharray |
|---|---|
| Outer (cloud provider) | `12,4` |
| Middle (region / VPC) | `8,4` |
| Inner (subnet / AZ) | `4,4` |

### 3.5 Security group / restricted boundary

A finer permission boundary inside a region.

```xml
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="8"
      fill="none"
      stroke="<TEMPLATE_WARNING_COLOR>" stroke-width="1"
      stroke-dasharray="4,4"/>
<text x="<X + 10>" y="<Y + 14>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.65>"
      font-weight="500"
      fill="<TEMPLATE_WARNING_COLOR>">VPC / Security Group</text>
```

### 3.6 Arrow markers

The slidea SVG contract allows `<marker>` definitions inside `<defs>`. Place `<defs>` near the top of the SVG (right after the background group) and reference markers with `marker-end="url(#<id>)"`.

```xml
<defs>
  <!-- Standard arrow (filled triangle) -->
  <marker id="arrow" markerWidth="10" markerHeight="7"
          refX="9" refY="3.5" orient="auto">
    <polygon points="0 0, 10 3.5, 0 7" fill="<TEMPLATE_MUTED_TEXT_COLOR>"/>
  </marker>

  <!-- Open arrow (for async / return messages) -->
  <marker id="arrow-open" markerWidth="10" markerHeight="7"
          refX="9" refY="3.5" orient="auto">
    <polyline points="0 0, 10 3.5, 0 7"
              fill="none"
              stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
  </marker>

  <!-- Accent arrow (for highlighted paths); one marker per color -->
  <marker id="arrow-accent" markerWidth="10" markerHeight="7"
          refX="9" refY="3.5" orient="auto">
    <polygon points="0 0, 10 3.5, 0 7" fill="<TEMPLATE_ACCENT_COLOR>"/>
  </marker>
</defs>

<!-- Usage -->
<line x1="100" y1="50" x2="200" y2="50"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

## 4. Spacing Formulas

Do not hardcode absolute pixel values (`150px`, `40px`). Express every spacing relative to either the **drawing-area size** or the **font size**. Reason: the slide canvas is fixed at 1280×720, but the actual drawing area might be the full content safe area (~1140×520), a single card inside it (~540×235), or a small corner (~300×200). The same `200px` value can be perfect in one and catastrophic in another.

### 4.1 First, determine the drawing-area size

When editing an SVG, first locate the area being changed — typically a `<g id="...">` group:

- Replacing the whole content area → use the `data-description` of `main-content-safe-area` in the slide template (e.g. `x=70 y=145 width=1140 height=520`).
- Replacing a specific card → use that `<g>`'s bounding box.
- Adding a new sub-area → pick coordinates yourself, but stay inside `main-content-safe-area`.

Record the area's `width` and `height`, then plug them into the formulas below.

### 4.2 Spacing formula table

| Spacing | Formula |
|---|---|
| Standard component box height | `drawing-area height / 8` to `/ 6` |
| Large / complex component box height | `drawing-area height / 4` |
| Minimum horizontal gap between sibling components | `drawing-area width × 3%` |
| Minimum vertical gap between layers | `drawing-area height × 5%` |
| Arrow label clearance from a box edge | `font-size × 0.8` |
| Region boundary internal padding | `font-size × 1.5` |
| Legend clearance below the lowest diagram element | `font-size × 1.5` |
| Text clearance from the canvas edge | ≥ `24px` (hard requirement of the slidea SVG contract) |
| Multi-line text line height | `font-size × 1.4` (also a hard requirement) |

### 4.3 Font-size suggestions (relative to drawing area)

| Role | Font size |
|---|---|
| Diagram-internal title / large component name | `drawing-area height × 2.5%` (e.g. 520-tall area → 13px) |
| Component name / primary label | `drawing-area height × 2%` (520 → ~10px) |
| Sublabel / description | `primary-label font-size × 0.8` |
| Arrow label / annotation | `primary-label font-size × 0.7` |

Note: the slide title bar (`<g id="header">`) has its font size fixed by the template (e.g. 34px). It is **outside** the drawing area — do not change it.

## 5. slidea SVG Compatibility Constraints

When editing SVG inside a slidea run, the result must still pass the same constraints that the original generation pipeline enforced. **For the full, authoritative constraint list, read `slides/prompts/<idx>_<title>.txt` for the page you are editing — it contains the complete contract the page was generated against.** The table below lists the constraints most commonly tripped over when adding a diagram.

| Constraint | Detail |
|---|---|
| Canvas | `width="1280" height="720" viewBox="0 0 1280 720"` |
| Color | HEX + `fill-opacity` / `stroke-opacity` / `stop-opacity`. `rgba()` is forbidden. |
| Fonts | Use the template font stack. `@import`, `@font-face`, `<link>`, `<style>` are all forbidden. |
| Tags | `<style>`, `class`, `foreignObject`, `mask`, `<tspan>` (for line breaks), animation tags are forbidden. |
| Font whitelist | Microsoft YaHei / SimHei / SimSun / Arial / Calibri / Times New Roman / Georgia / Consolas / sans-serif / serif / monospace |
| Multi-line text | Split into multiple independent `<text>` elements, each with its own `x` / `y`. Never use `<tspan>` for line breaks. |
| Images | `<image href="images/xxx.png">` (relative path); the file must exist under `slides/images/`. Data URIs are also accepted. |
| XML escaping | `&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`, `"` → `&quot;`, `'` → `&apos;` |
| Typographic symbols | Use Unicode directly (→ ← ⇒ ©). HTML entities (`&nbsp;`, `&mdash;`) and LaTeX (`$\rightarrow$`) are forbidden. |
| Template protection | Do not move, scale, delete, or redraw elements whose id is `background` / `slide-background` / `header` / `page-title-text` / `top-accent-bar` / `bottom-accent-bar` / `template-*` / `main-content-frame` / `content-frame-*`. |

**Resolving color placeholders.** Every `<TEMPLATE_*>` placeholder in this doc must be resolved from the slide template's `data-description` attributes. The template SVG for the current page is appended to `slides/prompts/<idx>_<title>.txt`, and is also available at `assets/svg_templates/<template_name>.svg` (read `template_name` from `ppt.json` in the run root). Each color-carrying element in the template has a `data-description` like `主色：#3F3933；使用要求：...` (`Primary color: #3F3933; use: ...`) — copy the hex value from there.

| Placeholder | Typical template role |
|---|---|
| `<TEMPLATE_BACKGROUND_COLOR>` | Page background, also the mask-rect fill |
| `<TEMPLATE_PRIMARY_COLOR>` | Component fills, primary visual elements |
| `<TEMPLATE_BORDER_COLOR>` | Strokes, dividers, frame outlines |
| `<TEMPLATE_ACCENT_COLOR>` | Highlights, tags, region boundaries |
| `<TEMPLATE_PRIMARY_TEXT_COLOR>` | Headings, primary body text |
| `<TEMPLATE_MUTED_TEXT_COLOR>` | Sublabels, annotations, captions |
| `<TEMPLATE_WARNING_COLOR>` | Risks, errors, restricted boundaries |
| `<TEMPLATE_SUCCESS_COLOR>` | Growth, completion, positive results |
| `<TEMPLATE_FONT_STACK>` | Whatever `font-family` the template uses for body text |

## 6. Other Diagram Types

The five diagram types below are fully covered by the foundations in §2–§4 (layering, component patterns, spacing formulas). Apply those foundations together with the per-type hints below.

### 6.1 Mind map

- Center node at the drawing-area center, with the largest font (primary label × 1.5).
- Branches are organic curves (`<path d="M ... C ...">` cubic Béziers), not straight lines.
- Each branch uses a different template color (rotate primary / accent / success / warning).
- Child nodes radiate outward; font size decreases by depth.
- Siblings at the same depth are evenly distributed radially.

### 6.2 Timeline

- Main axis is one `<line>` (horizontal or vertical).
- Event markers on the axis are `<circle>` or `<polygon>` (diamond / star).
- Event descriptions alternate above/below the axis to avoid overlap.
- Use color to categorize event types (milestone = accent, risk = warning, completed = success).
- Time labels sit directly under or beside their event marker.

### 6.3 State machine

- States are rounded rects with `<rect rx="20">` (rounder than standard boxes).
- Initial state: filled circle. Final state: filled circle inside an unfilled circle.
- Self-transitions are `<path>` curves looping back to the same state, with an arrow marker.
- Transition label format: `event [guard] / action` (use plain Unicode `[]` and `/`, not LaTeX).
- Composite states use a double-line border (outer rect + inner rect).

### 6.4 Data flow diagram

- Processes are circles (`<circle>`) or rounded rects.
- Data stores use the §3.3 database cylinder, or open-topped rectangles with a "double horizontal line" marker.
- External entities are rectangles (with shadow or double-line border).
- Data flows are curved or straight lines with arrowheads; label each flow with the data name.
- Difference from flowcharts: a data flow diagram emphasizes **how data moves between system boundaries**, not decision branches.

### 6.5 Illustrative / conceptual

- Free-form layout, no fixed topology.
- Use icon-like `<path>` elements (simple line-art icons drawn by hand).
- Use annotations — short text plus a `<line>` pointer to the element being explained.
- Visual metaphors are welcome (gears for "mechanism", funnel for "filter", etc.).
- Stay on the template color palette; do not introduce decorative colors.

## 7. Pre-write Checklist

Before finalizing the SVG, walk through this list:

- [ ] All colors come from the template palette (resolved from `data-description`); no decorative defaults.
- [ ] All fonts use `<TEMPLATE_FONT_STACK>`; no hardcoded font names.
- [ ] No `<style>`, `class`, `@import`, `@font-face`, `rgba()`, `<tspan>`, `<mask>`, or animation tags.
- [ ] Every semi-transparent component box is preceded by an opaque mask rect.
- [ ] All spacing was computed from the formulas (relative to drawing-area size or font-size), not copied from fixed-pixel references.
- [ ] Image `href` values are `images/xxx.png` relative paths, and the files exist under `slides/images/`.
- [ ] No template-protected element was moved, scaled, deleted, or redrawn.
- [ ] Multi-line text is split into independent `<text>` elements, line spacing = `font-size × 1.4`.
- [ ] Text keeps ≥ 24px clearance from the canvas edge.
- [ ] XML-reserved characters are escaped (`&` → `&amp;`, etc.).

For the authoritative and complete constraint list, always cross-check with `slides/prompts/<idx>_<title>.txt` for the page being edited.
