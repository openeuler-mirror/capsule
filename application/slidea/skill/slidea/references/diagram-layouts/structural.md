# Structural Diagram Layout

This file covers the layout algorithm for structural diagrams: class diagrams, ER diagrams, component diagrams, package diagrams, and org charts. Apply it on top of the foundations in [diagram-basics.md](../diagram-basics.md) (layering order, component patterns, spacing formulas, slidea SVG constraints).

## 1. Class Diagram

### 1.1 Class box (3-compartment)

A class box has three stacked compartments separated by horizontal divider lines: name (top), attributes (middle), methods (bottom).

```xml
<g transform="translate(<X>, <Y>)">
  <!-- Mask + visual layers -->
  <rect width="180" height="120" rx="6" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <rect width="180" height="120" rx="6"
        fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
        stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>

  <!-- Name compartment -->
  <text x="90" y="24" text-anchor="middle"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
        font-weight="700"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">ClassName</text>

  <!-- Divider 1 -->
  <line x1="0" y1="35" x2="180" y2="35"
        stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="0.5"
        stroke-opacity="0.5"/>

  <!-- Attributes (one text per line) -->
  <text x="10" y="52"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">- id: int</text>
  <text x="10" y="64"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">- name: string</text>

  <!-- Divider 2 -->
  <line x1="0" y1="75" x2="180" y2="75"
        stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="0.5"
        stroke-opacity="0.5"/>

  <!-- Methods (one text per line) -->
  <text x="10" y="92"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">+ getName(): string</text>
  <text x="10" y="104"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">+ setName(s: string)</text>
</g>
```

Adjust the compartment heights based on content. Each line of attributes or methods needs `font-size × 1.4` of vertical room.

### 1.2 Abstract class / interface markers

- **Abstract class**: prefix the class name with `«abstract»` on a separate line above, smaller font. Or, if the convention is italics, render the name itself in italics (SVG: `font-style="italic"`).
- **Interface**: prefix with `«interface»` on a separate line above, smaller font.

```xml
<!-- Interface -->
<text x="90" y="14" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.6>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">«interface»</text>
<text x="90" y="30" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
      font-weight="700"
      font-style="italic"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">Repository</text>
```

## 2. Relationship Lines

Each relationship type has a specific line style + end marker. Get the markers right — they are the semantic core of a structural diagram.

| Relationship | Line style | Marker at target end |
|---|---|---|
| Inheritance | Solid | Empty triangle pointing to parent |
| Implementation | Dashed | Empty triangle pointing to interface |
| Composition | Solid | Filled diamond at owner end |
| Aggregation | Solid | Empty diamond at owner end |
| Dependency | Dashed | Open arrowhead at dependency target |
| Association | Solid | Open arrowhead (optional, often none) |

### 2.1 Marker definitions

Define these once in `<defs>` at the top of the SVG (alongside the standard arrow markers from [basics §3.6](../diagram-basics.md#36-arrow-markers)).

```xml
<defs>
  <!-- Inheritance: empty triangle pointing to parent -->
  <marker id="inherit" markerWidth="12" markerHeight="10"
          refX="12" refY="5" orient="auto">
    <polygon points="0 0, 12 5, 0 10"
             fill="<TEMPLATE_BACKGROUND_COLOR>"
             stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
  </marker>

  <!-- Composition: filled diamond at owner end -->
  <!-- refX=0 so the diamond sits at the start of the line -->
  <marker id="composition" markerWidth="12" markerHeight="8"
          refX="0" refY="4" orient="auto">
    <polygon points="0 4, 6 0, 12 4, 6 8"
             fill="<TEMPLATE_MUTED_TEXT_COLOR>"/>
  </marker>

  <!-- Aggregation: empty diamond at owner end -->
  <marker id="aggregation" markerWidth="12" markerHeight="8"
          refX="0" refY="4" orient="auto">
    <polygon points="0 4, 6 0, 12 4, 6 8"
             fill="<TEMPLATE_BACKGROUND_COLOR>"
             stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
  </marker>
</defs>
```

### 2.2 Drawing relationships

```xml
<!-- Inheritance: solid line, inherit marker at parent end -->
<line x1="<child_top_cx>" y1="<child_top>" x2="<parent_bottom_cx>" y2="<parent_bottom>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#inherit)"/>

<!-- Composition: solid line, filled diamond at owner end -->
<line x1="<owner_edge>" y1="<owner_cy>" x2="<part_edge>" y2="<part_cy>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-start="url(#composition)"/>

<!-- Aggregation: solid line, empty diamond at owner end -->
<line x1="<owner_edge>" y1="<owner_cy>" x2="<part_edge>" y2="<part_cy>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-start="url(#aggregation)"/>

<!-- Dependency: dashed line, open arrow at target -->
<line x1="<dep_src>" y1="<y>" x2="<dep_dst>" y2="<y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      stroke-dasharray="6,4"
      marker-end="url(#arrow-open)"/>
```

Note the use of `marker-start` for composition/aggregation (the diamond sits at the owner end, which is the line's start) and `marker-end` for inheritance/dependency (the marker sits at the target end).

## 3. Cardinality Labels

For ER and class relationships, place cardinality (`1`, `0..1`, `1..*`, `*`, `0..*`) at each end of the relationship line, offset `font-size × 0.6` from the connected box edge.

```xml
<!-- One-to-many: "1" at owner end, "1..*" at part end -->
<text x="<owner_edge_x + 8>" y="<owner_cy - 5>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">1</text>
<text x="<part_edge_x - 25>" y="<part_cy - 5>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">1..*</text>
```

Common cardinality values: `1`, `0..1`, `*` (or `0..*`), `1..*`, `n` (specific number).

## 4. ER Diagram

ER diagrams use 2-compartment boxes (entity name + attributes) with crow's-foot notation for cardinality instead of numeric labels.

### 4.1 Entity box (2-compartment)

```xml
<g transform="translate(<X>, <Y>)">
  <rect width="180" height="100" rx="6" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
  <rect width="180" height="100" rx="6"
        fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
        stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>

  <!-- Entity name -->
  <text x="90" y="24" text-anchor="middle"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
        font-weight="700"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">User</text>
  <line x1="0" y1="32" x2="180" y2="32"
        stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="0.5"
        stroke-opacity="0.5"/>

  <!-- Attributes: PK in bold, FK in italic, others regular -->
  <text x="10" y="48"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        font-weight="700"
        fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">PK: id</text>
  <text x="10" y="64"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">name</text>
  <text x="10" y="80"
        font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.75>"
        font-style="italic"
        fill="<TEMPLATE_MUTED_TEXT_COLOR>">FK: org_id</text>
</g>
```

### 4.2 Crow's-foot notation

For one-to-many, draw a single bar at the "one" end and a three-line crow's foot at the "many" end. These are short line segments near the relationship line's endpoints.

```xml
<!-- "One" end at (x1, y): single perpendicular bar -->
<line x1="<x1 + 10>" y1="<y - 8>" x2="<x1 + 10>" y2="<y + 8>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>

<!-- "Many" end at (x2, y): three diverging lines (crow's foot) -->
<line x1="<x2 - 15>" y1="<y - 6>" x2="<x2>" y2="<y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<line x1="<x2 - 15>" y1="<y>" x2="<x2>" y2="<y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<line x1="<x2 - 15>" y1="<y + 6>" x2="<x2>" y2="<y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
```

## 5. Org Chart

Top-down tree layout for organization hierarchies.

### 5.1 Layout rules

- Root node at the top center of the drawing area.
- Each level is on a horizontal band, vertical gap `font-size × 4` between levels.
- Siblings at the same level are evenly distributed horizontally with at least `font-size × 5` between sibling boxes.
- Connection lines: vertical from parent's bottom-center to a horizontal "sibling bar", then vertical from the sibling bar down to each child's top-center.

```xml
<!-- Parent at (300, 50), children at x=100, x=300, x=500, all at y=200 -->
<!-- Vertical drop from parent -->
<line x1="300" y1="100" x2="300" y2="150"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<!-- Horizontal sibling bar -->
<line x1="100" y1="150" x2="500" y2="150"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<!-- Vertical drops to each child -->
<line x1="100" y1="150" x2="100" y2="200"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<line x1="300" y1="150" x2="300" y2="200"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
<line x1="500" y1="150" x2="500" y2="200"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"/>
```

Use a color from the template palette to indicate department or hierarchy level — same level = same color.

### 5.2 Tree width planning

Before placing any nodes, count the **widest level** in the tree (the level with the most nodes). That determines the total width:

```
total_width = (max_nodes_at_any_level × box_width) + (max_nodes_at_any_level - 1) × sibling_gap
```

Center the tree horizontally in the drawing area. If `total_width > drawing-area width`, you have two options:
- Reduce `box_width` (down to `font-size × 8` minimum).
- Convert to a **horizontal layout** (root on the left, children to the right) for any tree deeper than 5 levels.

## 6. Pre-write Checklist (Structural-specific)

In addition to the general checklist in [diagram-basics.md §7](../diagram-basics.md#7-pre-write-checklist):

- [ ] Every class/entity box has its compartments clearly separated by divider lines.
- [ ] `«interface»` and `«abstract»` markers used correctly (in angle brackets, smaller font).
- [ ] Every relationship line has the correct marker at the correct end (start vs end matters for composition/aggregation).
- [ ] Cardinality labels placed at both ends of every relationship, offset `font-size × 0.6` from the boxes.
- [ ] ER diagrams use crow's-foot notation (no numeric labels mixed in).
- [ ] Org charts have a single root, each level on one horizontal band, sibling bars connect parent to children.
- [ ] The widest level of an org chart was planned first to set total width.

## 7. Common Pitfalls

- **Wrong marker end.** Composition and aggregation markers go at the **owner** end (the whole, not the part). If you put them at the part end, the relationship reads backwards.
- **Mixed relationship notation.** Using crow's foot on one side of an ER relationship and a numeric label (`1..*`) on the other confuses readers. Pick one notation per diagram and stick with it.
- **Cardinality label on top of the line.** Place cardinality labels beside the line, not on top of it. Labels on the line overlap with the marker and become unreadable.
- **Sibling bars that skip levels.** In an org chart, every parent-child connection goes through one sibling bar. Drawing a parent's vertical line directly to a child without a sibling bar implies the parent has only one child, which makes the sibling bar for other children look orphaned.
- **Org chart too wide.** A 7+ sibling tree at any level forces the diagram to be too wide. Either compress box sizes, switch to horizontal orientation, or split the org chart into multiple diagrams (per sub-tree).
- **Lines passing through boxes.** A relationship line from one corner of the diagram to another will visually slice through unrelated boxes. Route with L-shapes (see [architecture.md §4.2](architecture.md#42-l-shaped-path-for-off-axis-connections)) when a straight line would cross a box.
- **Treating class members as separate boxes.** Attributes and methods belong inside the class box, not as floating boxes connected by lines. The lines are for relationships between classes, not for class-to-attribute ownership.
