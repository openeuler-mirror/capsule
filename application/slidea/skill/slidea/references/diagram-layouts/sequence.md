# Sequence Diagram Layout

This file covers the layout algorithm specific to sequence diagrams. Apply it on top of the foundations in [diagram-basics.md](../diagram-basics.md) (layering order, component patterns, spacing formulas, slidea SVG constraints).

## 1. Core Elements

| Element | Visual | Meaning |
|---|---|---|
| Actor / Participant | Box at top + dashed vertical line below | Each entity in the interaction |
| Sync message | Solid arrow → | Synchronous call |
| Async message | Open-arrow → | Fire-and-forget event |
| Return message | Dashed arrow ← | Response / return value |
| Activation bar | Narrow filled rect on a lifeline | The actor is processing during this span |
| Self-message | Loop arrow on the same lifeline | Internal processing |
| Note | Rounded rect with a folded corner | Annotation attached to a lifeline |
| Alt / Opt frame | Dashed boundary with a label tab | Conditional block (alt = if/else, opt = if) |
| Loop frame | Dashed boundary with a "loop" tab | Repetition |

## 2. Layout Algorithm

1. **Place actors horizontally across the top**, evenly spaced. Spacing: `drawing-area width / actor_count`, with minimum `font-size × 8` (so labels do not crowd).
2. **Draw lifelines** as vertical dashed lines from each actor box downward, spanning the full drawing-area height minus the bottom margin.
3. **Place messages as horizontal arrows** between lifelines, top-to-bottom in chronological order. Each message sits at a distinct y-coordinate; messages do not share rows.
4. **Vertical spacing between messages**: `font-size × 2.5` minimum, more if any message label wraps to two lines (rare — keep labels short).
5. **Activation bars** sit centered on a lifeline, 10px wide (`font-size × 0.8`), spanning from the incoming message y to the outgoing message y for that actor.

## 3. Actor Box and Lifeline

```xml
<!-- Actor box at top -->
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="6" fill="<TEMPLATE_BACKGROUND_COLOR>"/>
<rect x="<X>" y="<Y>" width="<W>" height="<H>" rx="6"
      fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.4"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1.5"/>
<text x="<CX>" y="<Y + H / 2 + 4>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE>"
      font-weight="600"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">UserService</text>

<!-- Lifeline: dashed vertical line from below the box to the bottom of the drawing area -->
<line x1="<CX>" y1="<Y + H>" x2="<CX>" y2="<drawing_area_bottom>"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"
      stroke-dasharray="6,4"/>
```

Each actor gets a `<TEMPLATE_*>` color identity used consistently across its actor box, activation bars, and outgoing arrows (see §8 for the color-assignment rule).

## 4. Message Arrows

### 4.1 Sync message (solid arrow)

```xml
<line x1="<from_cx>" y1="<msg_y>" x2="<to_cx>" y2="<msg_y>"
      stroke="<TEMPLATE_PRIMARY_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
<text x="<mid_x>" y="<msg_y - 8>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">getUserById(id)</text>
```

### 4.2 Return message (dashed arrow, reversed direction)

Return arrows point from the responder back to the original caller, with a dashed line. The label is typically italicized via a leading `«»` or by being more descriptive (e.g. `200 OK`, `user object`).

```xml
<line x1="<to_cx>" y1="<msg_y>" x2="<from_cx>" y2="<msg_y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1"
      stroke-dasharray="6,3"
      marker-end="url(#arrow)"/>
<text x="<mid_x>" y="<msg_y - 8>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">user</text>
```

### 4.3 Async message (open arrow)

Async messages use the open arrow marker (defined in [basics §3.6](../diagram-basics.md#36-arrow-markers)) instead of the filled triangle.

```xml
<line x1="<from_cx>" y1="<msg_y>" x2="<to_cx>" y2="<msg_y>"
      stroke="<TEMPLATE_ACCENT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow-open)"/>
<text x="<mid_x>" y="<msg_y - 8>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">publishEvent(payload)</text>
```

### 4.4 Self-message (loop on the same lifeline)

Self-messages loop out and back on the same lifeline. Use a small rectangular detour to the right of the lifeline.

```xml
<path d="M <cx>,<msg_y> L <cx + 40>,<msg_y> L <cx + 40>,<msg_y + 25> L <cx>,<msg_y + 25>"
      fill="none"
      stroke="<TEMPLATE_PRIMARY_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
<text x="<cx + 48>" y="<msg_y + 15>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">validate()</text>
```

## 5. Activation Bar

An activation bar shows when an actor is "busy" — from the moment it receives a message until it sends its response. Center the bar on the lifeline.

```xml
<rect x="<cx - 5>" y="<incoming_msg_y>" width="10"
      height="<outgoing_msg_y - incoming_msg_y>" rx="2"
      fill="<TEMPLATE_PRIMARY_COLOR>" fill-opacity="0.6"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"/>
```

Use the actor's identity color (§8) for the bar fill so the reader can trace activity across the diagram.

## 6. Conditional and Loop Frames

When a sequence of messages is conditional or repeated, wrap it in a frame: a dashed rectangle with a labeled tab in the top-left corner.

```xml
<!-- Frame boundary -->
<rect x="<frame_x>" y="<frame_y>" width="<frame_w>" height="<frame_h>" rx="4"
      fill="none"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"
      stroke-dasharray="4,3"/>
<!-- Label tab -->
<rect x="<frame_x>" y="<frame_y>" width="50" height="18" rx="4"
      fill="<TEMPLATE_BACKGROUND_COLOR>"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"/>
<text x="<frame_x + 25>" y="<frame_y + 13>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      font-weight="600"
      fill="<TEMPLATE_PRIMARY_TEXT_COLOR>">alt</text>
<!-- Condition text next to the tab -->
<text x="<frame_x + 60>" y="<frame_y + 13>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      font-style="italic"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">[user not found]</text>
<!-- Divider for else-branch (alt only) -->
<line x1="<frame_x>" y1="<mid_y>" x2="<frame_x + frame_w>" y2="<mid_y>"
      stroke="<TEMPLATE_BORDER_COLOR>" stroke-width="1"
      stroke-dasharray="4,3"/>
<text x="<frame_x + 10>" y="<mid_y + 13>"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      font-style="italic"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">[else]</text>
```

Frame types:

| Frame | Tab label | Meaning |
|---|---|---|
| `alt` | `alt` | if/else — multiple branches separated by dashed dividers |
| `opt` | `opt` | if — single conditional block |
| `loop` | `loop` | for/while — write the iteration condition in italics next to the tab |
| `par` | `par` | parallel — multiple blocks execute concurrently |

The frame should span all the lifelines it covers and vertically contain every message in the block. Leave `font-size × 1` padding inside the frame.

## 7. Numbering

When a sequence diagram has 8 or more messages, number each message so readers can cite them ("message 5 fails"). Place a small numbered circle at the start of the arrow.

```xml
<circle cx="<from_cx - 15>" cy="<msg_y>" r="8"
        fill="<TEMPLATE_ACCENT_COLOR>" fill-opacity="0.3"
        stroke="<TEMPLATE_ACCENT_COLOR>" stroke-width="1"/>
<text x="<from_cx - 15>" y="<msg_y + 3>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.6>"
      font-weight="600"
      fill="<TEMPLATE_ACCENT_COLOR>">1</text>
```

Number in chronological order, top to bottom. Restart numbering inside each `loop` frame iteration if the loop body has multiple messages.

## 8. Per-Actor Color Consistency

When a diagram has many actors, assign each one a distinct color and use it consistently:

- Actor box stroke color
- Activation bar fill color
- Outgoing arrows from that actor (optional, useful for dense diagrams)

Color source: rotate through `<TEMPLATE_PRIMARY_COLOR>`, `<TEMPLATE_ACCENT_COLOR>`, `<TEMPLATE_SUCCESS_COLOR>`, `<TEMPLATE_WARNING_COLOR>` in order. For 5+ actors, this rotation exhausts; fall back to a single color (`<TEMPLATE_PRIMARY_COLOR>`) for all actor boxes and rely on labels to distinguish them, rather than introducing off-palette colors.

## 9. Pre-write Checklist (Sequence-specific)

In addition to the general checklist in [diagram-basics.md §7](../diagram-basics.md#7-pre-write-checklist):

- [ ] Actors evenly spaced at the top, every actor has a vertical lifeline below it.
- [ ] Messages are horizontal, each at a distinct y-coordinate, top-to-bottom in time order.
- [ ] Sync messages use solid arrows, returns use dashed arrows, async uses open arrows.
- [ ] Self-messages loop to the right of the lifeline (not the left).
- [ ] Activation bars span exactly from incoming to outgoing message for each actor.
- [ ] Frame labels (`alt` / `opt` / `loop` / `par`) are in the top-left tab, conditions in italics next to the tab.
- [ ] Numbered messages (if 8+) follow chronological order.
- [ ] Each actor uses its assigned color consistently across actor box, activation bar, and outgoing arrows.

## 10. Common Pitfalls

- **Diagonal messages.** A message from one actor to another must be a horizontal line. A diagonal line implies one actor "moves" during the interaction, which is never what you want.
- **Crossing lifelines.** If arrows from actor A → D and actor C → B cross, re-order the actors at the top so related actors are adjacent. The optimal ordering minimizes crossings.
- **Activation bar covering the wrong span.** A common bug: drawing the bar from the top of the diagram to the bottom "to be safe". The bar should start exactly at the incoming message y and end at the outgoing message y — that is its semantic meaning.
- **Frame without a tab label.** A dashed rectangle with no `alt` / `opt` / `loop` label is just a confusing region. Always include the labeled tab.
- **Two messages on the same y.** If you run out of vertical space, compress earlier messages rather than stacking two on the same row — readers cannot tell which happened first.
- **Notes floating off-lifeline.** A note must visually attach to a lifeline (typically with a short pointer line) or sit centered on the message it annotates. Floating notes look like they belong to nobody.
