# Flowchart Layout

This file covers the layout algorithm specific to flowcharts. Apply it on top of the foundations in [diagram-basics.md](../diagram-basics.md) (layering order, component patterns, spacing formulas, slidea SVG constraints).

## 1. Shape Vocabulary

Use the right shape for each semantic role. Mixing them up confuses readers.

| Role | Shape | SVG element |
|---|---|---|
| Start / End | Rounded rect (large radius) | `<rect rx="25">` |
| Process / Action | Rectangle (small radius) | `<rect rx="6">` |
| Decision | Diamond (square rotated 45°) | `<polygon points="0,-35 50,0 0,35 -50,0">` |
| Input / Output | Parallelogram | `<polygon>` with horizontal skew |
| Data store | Cylinder | ellipse + rect + ellipse (see [basics §3.3](../diagram-basics.md#33-database-cylinder)) |

Decision diamonds, data stores, and standard boxes are also covered in [diagram-basics.md §3](../diagram-basics.md#3-component-patterns) with full SVG skeletons. Use those patterns directly.

## 2. Flow Direction

**Primary flow is top-to-bottom.** Decision branches go left or right. The diagram has exactly one start node at the top and one or more end nodes at the bottom.

Exception: horizontal flowcharts (LTR) are acceptable when the content represents a pipeline with sequential transforms, but treat that as an architecture diagram instead (see [architecture.md](architecture.md)).

## 3. Layout Algorithm

1. **Lay out the main path first.** The main path is the happy path / most common flow. Place it straight down the horizontal center of the drawing area. Every main-path node sits on the center column (`drawing-area width / 2`).
2. **Branch from decisions.** At each decision diamond, "Yes" continues straight down on the center column. "No" (or the less common exit) branches to the right. Branch to the left only when the right side is full.
3. **Merge branches back.** When a branch re-joins the main path, route it back with an L-shaped connector entering the next main-path node from the side.
4. **Route loop-backs along the edge.** When a branch must return to an earlier node (retry, loop), route it along the far left or far right edge of the diagram with a curved `<path>`, not a straight diagonal.

## 4. Spacing

| Spacing | Formula |
|---|---|
| Vertical gap between sequential steps (main path) | `drawing-area height / step_count` (target), minimum `font-size × 4` |
| Decision diamond height | `font-size × 3.5` |
| Decision diamond width | `font-size × 5` (wider than tall, so labels fit) |
| Horizontal offset for a branch column | `decision_diamond_width × 1.6` from the center |
| Merge connector clearance from any box | `font-size × 1.5` |
| Loop-back curve clearance from diagram edge | `font-size × 1.5` |

## 5. Decision Branches and Labels

Each decision diamond has 2+ exits. By convention, **"Yes" continues down** and **"No" branches right**. Label every exit arrow — unlabeled decision exits are the most common flowchart bug.

```xml
<!-- Decision diamond centered at (400, 200) -->
<g transform="translate(400, 200)">
  <polygon points="0,-35 50,0 0,35 -50,0" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <polygon points="0,-35 50,0 0,35 -50,0"
           fill="<TEMPLATE_ACCENT_COLOR>" fill-opacity="0.3"
           stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
  <text y="4" text-anchor="middle"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.9>"
        font-weight="600"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">Valid input?</text>
</g>

<!-- Yes: straight down to next main-path step -->
<line x1="400" y1="235" x2="400" y2="300"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
<text x="412" y="260"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_SUCCESS_COLOR>">Yes</text>

<!-- No: branch right -->
<line x1="450" y1="200" x2="600" y2="200"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
<text x="480" y="193"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_WARNING_COLOR>">No</text>
```

When the exit names are not "Yes/No" (e.g. "Retry" / "Abort", "Cache hit" / "Cache miss"), use the same convention: the desirable / most-common path goes down, the alternative branches right. Color the down-exit label with `<TEMPLATE_SUCCESS_COLOR>` and the right-exit label with `<TEMPLATE_WARNING_COLOR>` for quick scanning.

## 6. Merging Branches Back

When a branch needs to re-join the main path, route it back with an L-shaped connector that enters the next main-path node from its side, never from below (which would conflict with the next decision's downward exit).

```xml
<!-- Branch off the right side at y=200, needs to merge into main-path node at (400, 400) -->
<path d="M 600,200 L 600,400 L 450,400"
      fill="none"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

The horizontal merge segment should clear any box by `font-size × 1.5`. If it cannot, re-run the branch further right or compress the main path.

## 7. Loop-backs

For retry / redo / loop semantics (the flow returns to an earlier step), route the loop-back along the far-left or far-right edge of the diagram as a curve, never as a straight line back up.

```xml
<!-- Loop from a node at (400, 350) back to a node at (400, 100), routed along the left edge -->
<path d="M 350,350 C 250,350 250,100 350,100"
      fill="none"
      stroke="<TEMPLATE_ACCENT_COLOR>" stroke-width="1.5"
      stroke-dasharray="6,3"
      marker-end="url(#arrow-accent)"/>
```

Use a dashed line (`stroke-dasharray="6,3"`) for loop-backs to visually distinguish them from forward flow. Use `<TEMPLATE_ACCENT_COLOR>` if the loop is a normal control mechanism; use `<TEMPLATE_WARNING_COLOR>` if it represents an error retry.

## 8. Swim Lanes (for 10+ steps)

When a flowchart has 10 or more steps and the steps involve multiple actors (user / system / database / external API), use a **swim lane** layout:

- Each actor gets a vertical lane (column) spanning the full drawing-area height.
- Lane headers at the top (a colored band with the actor name).
- Steps are placed in the lane of the actor that performs them.
- Arrows cross lanes between actors; straight horizontal arrows preferred.

Lanes use the region boundary pattern from [basics §3.4](../diagram-basics.md#34-region-boundary):

```xml
<!-- Lane boundary -->
<rect x="<lane_x>" y="<lane_top>" width="<lane_w>" height="<lane_h>"
      fill="none"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"
      stroke-dasharray="4,4"/>
<!-- Lane header band -->
<rect x="<lane_x>" y="<lane_top>" width="<lane_w>" height="<header_h>"
      fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.15"
      stroke="none"/>
<text x="<lane_cx>" y="<lane_top + header_h / 2 + 4>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
      font-weight="600"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">User</text>
```

Limit to **4 lanes maximum** — beyond that, the diagram is too wide to be useful.

## 9. Pre-write Checklist (Flowchart-specific)

In addition to the general checklist in [diagram-basics.md §7](../diagram-basics.md#7-pre-write-checklist):

- [ ] Exactly one start node at the top, one or more end nodes at the bottom.
- [ ] Main path runs straight down the horizontal center.
- [ ] Every decision diamond has labeled exit arrows (Yes/No or specific labels).
- [ ] Branches either end at an end node or merge back into the main path.
- [ ] Merge connectors enter main-path nodes from the side, not from below.
- [ ] Loop-backs are curved paths along the diagram edge, dashed.
- [ ] Swim lanes (if used) have clear headers and ≤ 4 lanes.

## 10. Common Pitfalls

- **Unlabeled decision exits.** A diamond with two arrows and no labels forces the reader to guess which path is which. Always label both exits.
- **Diagonal arrows.** A diagonal arrow from one box to another visually crosses every box in between. Always bend with L-shape or curve.
- **Loop-backs drawn as straight lines.** A straight line back up the diagram overlaps the forward flow. Loop-backs must be offset to the side as curves.
- **Too many decisions in a row.** Five consecutive diamonds reads as a nested if-else hell. Refactor: collapse related decisions into one with multi-way exits, or split into two flowcharts (high-level + detail).
- **Branching right with no merge.** A right-branch that never returns leaves a "ghost" column hanging. Either end it at an end node (e.g. "Reject") or merge it back into the main path.
- **Decorative color.** As with architecture diagrams, structure should come from position (main path = center) and shape (diamond = decision), not from color. Reserve color for: the happy-path label (`<TEMPLATE_SUCCESS_COLOR>`), the error-path label (`<TEMPLATE_WARNING_COLOR>`), and any genuinely highlighted path.
