# Architecture Diagram Layout

This file covers the layout algorithm specific to architecture diagrams. Apply it on top of the foundations in [diagram-basics.md](../diagram-basics.md) (layering order, component patterns, spacing formulas, slidea SVG constraints).

## 1. Flow Direction

Pick one primary direction before placing any component. Do not mix LTR and TTB in the same diagram.

| Direction | When to choose | Typical layer order |
|---|---|---|
| **Left-to-Right (LTR)** | Data pipelines, request flows, ETL, message streams | Clients (left) → Gateways → Services → Data stores (right) |
| **Top-to-Bottom (TTB)** | Layered architectures, deployment topology, org-style ownership | Clients (top) → Edge → App services → Infrastructure (bottom) |

Rule of thumb: if the user's content reads like "X calls Y which writes to Z", prefer LTR. If it reads like "tier 1 / tier 2 / tier 3", prefer TTB.

## 2. Layout Algorithm

1. **Identify layers.** Group components by role: clients, gateways, services, data stores, infrastructure. Each group becomes one layer (one column in LTR, one row in TTB).
2. **Assign layers to positions.** LTR: one layer per column, left to right following data flow. TTB: one layer per row, top to bottom following dependency direction.
3. **Stack within a layer.** LTR: stack vertically with at least `drawing-area height × 5%` between siblings. TTB: arrange horizontally with at least `drawing-area width × 3%` between siblings.
4. **Draw region boundaries.** Enclose groups that share infrastructure (cloud provider, region, cluster, VPC). See §5 for nesting rules.
5. **Route connectors.** Arrows go between layers, never through unrelated components. See §4 for routing rules.

## 3. Typical Layer Structures

### 3.1 LTR layer structure

```
Col 1            Col 2            Col 3            Col 4
[x_start    ]    [x_start+Δx]     [x_start+2Δx]    [x_start+3Δx]

┌────────┐       ┌────────┐       ┌────────┐       ┌────────┐
│ Client │ ────▶ │Gateway │ ────▶ │Service │ ────▶ │  Data  │
│  Layer │       │ Layer  │       │ Layer  │       │ Store  │
└────────┘       └────────┘       └────────┘       └────────┘
```

Column start spacing (`Δx`): `drawing-area width / layer_count`. Adjust upward if any component box is wider than `Δx × 60%`.

### 3.2 TTB layer structure

```
Row 1 (top):     [ Browser ]   [ Mobile App ]   [ API Client ]
Row 2:           [       Load Balancer / API Gateway       ]
Row 3:           [ Auth Svc ]  [ User Svc ]  [ Order Svc ]
Row 4 (bottom):  [  Redis ]   [ PostgreSQL ]   [ S3 Bucket ]
```

Row start spacing (`Δy`): `drawing-area height / row_count`. Reserve extra room (× 1.5) for any row that holds a wide horizontal bar (load balancer, message bus).

## 4. Connection Routing

### 4.1 Straight line (preferred)

Always use a straight horizontal or vertical line when the source and target are on the same row or column.

```xml
<line x1="<src_right_edge>" y1="<src_cy>" x2="<dst_left_edge>" y2="<dst_cy>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

### 4.2 L-shaped path (for off-axis connections)

When source and target are not aligned, route with a two-segment L-shape through a midpoint. Never draw a diagonal line — it visually crosses unrelated components.

```xml
<path d="M <src_x>,<src_y> L <mid_x>,<src_y> L <mid_x>,<dst_y> L <dst_x>,<dst_y>"
      fill="none"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

Pick `<mid_x>` (or `<mid_y>` for vertical routes) at the halfway point between the two layers' edges. Leave at least `font-size × 1.5` of clearance from any unrelated component box.

### 4.3 De-emphasizing secondary connections

For connections that are present but not the focus (e.g. heartbeat, telemetry, fallback paths), lower the stroke opacity instead of removing them.

```xml
<line ... stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-opacity="0.5" stroke-width="1"/>
```

### 4.4 Mid-arrow labels

Label important connections (protocol name, request type, data name) with a `<text>` placed at the midpoint, offset perpendicular to the line by `font-size × 0.8`.

```xml
<!-- Horizontal arrow from (100,50) to (300,50), label above -->
<line x1="100" y1="50" x2="300" y2="50" stroke="..." marker-end="url(#arrow)"/>
<text x="200" y="42" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.7>"
      fill="<TEMPLATE_MUTED_TEXT_COLOR>">HTTPS</text>
```

## 5. Region Boundaries

### 5.1 When to draw a region

Draw a region boundary around components that share a deployment, ownership, or infrastructure boundary:
- Cloud provider boundary (AWS, GCP, Azure)
- Region / VPC
- Kubernetes cluster / namespace
- Team ownership boundary
- Trust zone (public / private network)

### 5.2 Nesting levels

When regions nest, distinguish each level with a different dash pattern. Limit nesting to **3 levels** — anything deeper becomes unreadable.

| Level | Typical meaning | dasharray | stroke source |
|---|---|---|---|
| Outer | Cloud provider | `12,4` | `<TEMPLATE_ACCENT_COLOR>` |
| Middle | Region / VPC | `8,4` | `<TEMPLATE_ACCENT_COLOR>` |
| Inner | Subnet / AZ | `4,4` | `<TEMPLATE_ACCENT_COLOR>` |

The component patterns for region boundaries and security groups are in [diagram-basics.md §3.4 and §3.5](../diagram-basics.md#34-region-boundary).

### 5.3 Region padding

Inside a region boundary, leave at least `font-size × 1.5` between the boundary edge and any contained component. The region name label sits inside this padding band, top-left.

## 6. Message Bus / Event Bus Pattern

When multiple services communicate through a shared bus (Kafka, RabbitMQ, event stream), draw the bus as a thin horizontal bar (for TTB layering) or vertical bar (for LTR layering) between the producer and consumer layers.

```
Services:   [ Svc A ]    [ Svc B ]    [ Svc C ]
               │             │             │
Bus:     ═════╪═════════════╪═════════════╪══════
               │             │             │
Consumers: [ Sink A ]   [ Sink B ]    [ Sink C ]
```

```xml
<!-- Bus bar (full width of the layer above/below) -->
<rect x="<bus_x>" y="<bus_y>" width="<bus_w>" height="<bus_h>" rx="2"
      fill="<TEMPLATE_ACCENT_COLOR>" fill-opacity="0.2"
      stroke="<TEMPLATE_ACCENT_COLOR>" stroke-width="1"/>
<text x="<bus_cx>" y="<bus_y - 8>" text-anchor="middle"
      font-family="<TEMPLATE_FONT_STACK>" font-size="<FONT_SIZE × 0.8>"
      font-weight="600"
      fill="<TEMPLATE_ACCENT_COLOR>">Kafka</text>

<!-- Drop arrows from services to bus (no arrowheads, just connection ticks) -->
<line x1="<svc_cx>" y1="<svc_bottom>" x2="<svc_cx>" y2="<bus_y>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1"/>
<!-- Rise arrows from bus to consumers (with arrowheads) -->
<line x1="<consumer_cx>" y1="<bus_y + bus_h>" x2="<consumer_cx>" y2="<consumer_top>"
      stroke="<TEMPLATE_MUTED_TEXT_COLOR>" stroke-width="1.5"
      marker-end="url(#arrow)"/>
```

The bus bar uses `<TEMPLATE_ACCENT_COLOR>` to mark it as a distinct element type from services and data stores.

## 7. Data Store Placement

Architecture diagrams often have multiple data stores (PostgreSQL, Redis, S3, vector DB, etc.). Conventional placement:

| Position | TTB | LTR |
|---|---|---|
| Default | Bottom row, below the services that own them | Rightmost column |
| Cache (read-heavy) | Same row as the service that uses it | Adjacent column on the left of the data store |
| Shared datastore | Centered in the layer, owned by the platform tier | Centered vertically in the rightmost column |

Use the database cylinder from [diagram-basics.md §3.3](../diagram-basics.md#33-database-cylinder) for SQL/NoSQL stores. Use the standard box (§3.1) for object storage (S3, GCS) and queues (SQS, Pub/Sub).

## 8. Pre-write Checklist (Architecture-specific)

In addition to the general checklist in [diagram-basics.md §7](../diagram-basics.md#7-pre-write-checklist):

- [ ] One primary flow direction chosen (LTR or TTB), not mixed.
- [ ] Every component belongs to exactly one layer; no orphan components between layers.
- [ ] Connectors are straight or L-shaped — no diagonals through other components.
- [ ] Region nesting is at most 3 levels deep, each with a distinct dash pattern.
- [ ] Bus bars (if any) span the full width/height of the layers they connect.
- [ ] Data stores placed per §7 conventions.
- [ ] All cross-layer connections have arrowheads (using markers defined in `<defs>`).
- [ ] Mid-arrow labels (if any) are offset perpendicular to the line, not on top of it.

## 9. Common Pitfalls

- **Diagonal connections.** A diagonal `<line>` between two non-aligned components looks like it should be valid SVG, but it visually slices through every component in between. Always bend with an L-shape (`<path>`).
- **Region inside a region inside a region inside a region.** Four nesting levels is unreadable. If you find yourself wanting a 4th level, refactor — group some components into a single "platform" boundary, or split the diagram into two pages.
- **Bus bar too short.** If the bus only covers half the services, the diagram implies those uncovered services bypass the bus, which is usually wrong. The bus must span every producer and consumer that touches it.
- **Database cylinder in the top tier.** Databases belong to services; placing them above the service layer inverts ownership. Visually it also crowds the entry of the diagram.
- **Same arrow used for sync request and async event.** Distinguish them: sync calls use a filled-triangle `marker-end`, async events often use the open arrow marker, and event-bus drops often have no arrowhead at all (just a connection tick).
- **Color-coding by service type.** Architecture diagrams communicate structure by **position** (layer) and **boundary** (region), not by hue. Reserve color for emphasis (one accent path, one warning zone) — recoloring every box differently just adds noise.
