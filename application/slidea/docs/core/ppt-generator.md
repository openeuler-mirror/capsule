# PPT Generator App

`ppt_generator/` is the product core of the repository. The most useful way to read it is not as "the code that generates PPT files," but as a system that progressively compresses an open-ended presentation request into a renderable page structure.

## Design Motivation

From the user's point of view, requests usually look like this:

- "Create a presentation about AI agents."
- "The audience is executives."
- "Keep it professional and insight-driven."
- "Use these links and materials."

There is a large semantic gap between that input and a stable deliverable deck. A system cannot jump directly from one natural-language request to reliable slides because at least three transformations are missing:

1. convert a vague request into explicit writing intent,
2. convert writing intent into page-level information structure,
3. convert page-level structure into renderable visual output.

The design motivation of `ppt_generator` is to make those transformations explicit rather than trying to solve everything in one end-to-end generation step.

That avoids several common failure modes:

- unstable content structure,
- poor page granularity,
- weak maintainability,
- no stage-level caching or retry model,
- no ability to patch local rendering failures.

So the core idea is not "one large model writes the whole deck." The core idea is "multi-stage transformation through explicit intermediate representations."

## Core Abstraction Layers

The package structure reflects a clear layered architecture.

### 1. The Intent Layer

The top layer is about what the user is trying to communicate, not what the deck should look like.

Its responsibilities include:

- extracting topic, audience, and goal,
- detecting missing request information,
- deciding whether search or deep research is needed,
- generating the writing thought for the whole presentation.

In the codebase, this is `ppt_thought/`. It is fundamentally a content-planning layer, not a rendering layer.

### 2. The Structure Layer

The second layer converts the writing thought and source material into a page-level structure.

This layer does not care yet about final SVG output. It cares about:

- how many pages the deck should have,
- what type each page is,
- what each page should say,
- which source text and images belong to each page.

In code, this is centered on `thought_to_ppt/outline_generator/` and the `PPTPage` intermediate representation.

This layer matters because it discretizes continuous source text into pages, which are the real operational units of the rendering system.

### 3. The Rendering Layer

The third layer is the visual implementation layer.

Its job is not to rethink content. Its job is to turn an already-defined page structure into stable rendered artifacts. The default backend renders each page as SVG and exports native editable PPTX through the SVG-to-DrawingML converter. An optional HTML backend is preserved in the codebase for users who want HTML/CSS expressiveness; it is not enabled by default and is documented in the repository README's "HTML Render Route (Optional)" section.

In code, this lives in `thought_to_ppt/page_generators/` plus `utils/common.py` and `utils/browser.py`.

### 4. The Runtime-Support Layer

A final layer is easy to overlook but crucial in practice: cache management, templates, browser lifecycle, image handling, output paths, and staged CLI execution.

This layer is not the business logic itself, but it gives the upper layers the properties that make the system usable as software:

- resumability,
- recoverability,
- patchability,
- graceful degradation when optional dependencies are missing.

That is one of the main reasons this project behaves like a real production pipeline rather than a single prompt script.

## Core Algorithmic Ideas

From a principles perspective, the package relies on three major algorithmic ideas.

### 1. Multi-Stage Intermediate Representation Conversion

This is the most important idea in the entire system.

The pipeline does not go directly from user request to final pages. It keeps generating progressively more concrete intermediate representations:

user request -> writing thought -> slide outline -> page SVG -> native PPTX

or, when the optional HTML route is explicitly enabled:

user request -> writing thought -> slide outline -> page HTML -> PDF/PPTX

This has major engineering benefits:

- every layer can be cached and debugged independently,
- local failures can be recomputed locally,
- upper-layer mistakes and lower-layer mistakes are less likely to contaminate each other,
- the codebase becomes easier for multiple maintainers to reason about.

### 2. Page-Type Specialization

The system does not treat all pages as the same generation task. It classifies them first and handles them differently:

- cover / thanks pages,
- table of contents,
- separator pages,
- content pages.

That specialization exists because each page type has very different constraints:

- cover pages care about title presence and visual breathing room,
- TOC pages care about structural listing,
- separator pages care about pacing between sections,
- content pages care about information density, imagery, and layout constraints.

Without page-type specialization, every page would be forced through one averaged prompt strategy, which usually produces weak and unstable results.

### 3. Render-Result Feedback

The system does not assume that model-generated SVG is automatically acceptable. It closes the loop at the rendering level:

1. generate slide SVG,
2. run quality checks (XML well-formedness, fixed canvas, forbidden constructs),
3. attempt deterministic + LLM-driven repair when checks fail,
4. optionally invoke VLM screenshot review when VLM is configured.

That means the system is not merely generating code. It is running a simplified generate-test-repair loop. For layout-heavy tasks, this feedback loop is critical because many failures only become visible after actual rendering.

## Overall Workflow Mechanism

Conceptually, the package has two main stages.

### Stage 1: Intent Convergence

This stage is implemented by `ppt_thought/`.

It answers questions such as:

- what presentation the user is really asking for,
- what key information is still missing,
- what references need to be gathered,
- whether simple search or deep research is appropriate,
- what narrative logic should drive the deck.

The output of this stage is not a set of pages. It is a writing thought, which acts as a content blueprint.

### Stage 2: Structural and Visual Realization

This stage is implemented by `thought_to_ppt/`.

It first converts the content blueprint into a slide outline, then turns that outline into SVG pages, and finally exports a native editable PPTX.

It answers questions such as:

- how many pages should exist,
- what each page should cover,
- which page type each page belongs to,
- which images should support the page,
- whether a page is visually overloaded,
- where final artifacts should be written.

From a workflow perspective, the package performs two successive compressions:

- it compresses user intent into narrative structure,
- then compresses narrative structure into visual pages.

## Why SVG Is The Canonical Render Intermediate

This is one of the most important implementation choices to understand.

The default route does not ask the LLM to produce binary PPTX directly. It generates SVG first and converts that to native editable DrawingML inside the PPTX. That is not an accident. It is an architectural choice:

- SVG is vector and declarative, which makes it a clean source for DrawingML conversion.
- SVG is easier to patch locally than a binary PPTX file.
- The SVG → DrawingML path does not require Playwright/Chromium or LibreOffice at runtime.
- The intermediate artifact stays inspectable, which is critical for the generate-check-repair loop.

So in practice, this is closer to "vector-native slide generation" than direct binary presentation generation. PPTX is the export target, not the primary internal representation.

The optional HTML route is a parallel backend for cases where HTML/CSS expressiveness matters more than native PPTX editability. It uses Playwright and LibreOffice at runtime, is documented in the repository README, and is not advertised through `skill/SKILL.md`.

## Main Internal Subsystems

Once that architectural picture is in place, the main subsystems become easy to read.

### `ppt_thought/`

This is the content-planning subsystem. It solves "what should the deck say, and why should it be organized that way."

Its value is not parsing alone. Its value is turning a user request into a consumable content-intent representation and borrowing from search or deep research when necessary.

### `thought_to_ppt/outline_generator/`

This is the structural-planning subsystem. It solves "how should the content be split into pages, and what kind of page is each one."

`PPTPage` is the critical intermediate representation here because it turns downstream rendering from "process a long document" into "process one page object at a time."

### `thought_to_ppt/page_generators/`

This is the visual-generation subsystem. It solves "how do we render each page type and keep the result usable."

The content-page generator is the most complex component because it must handle not only text selection but also image retrieval, optional image generation, image scoring, and page layout.

## Minimal Code Map

With the architectural model established, the file mapping is simple:

| File / Dir | Architectural role |
| --- | --- |
| `graph.py` | Top-level two-stage orchestration: generate thought, then generate slides |
| `state.py` | Top-level input/output state contracts |
| `node.py` | Bridges thought generation into slide-generation entry points |
| `ppt_thought/` | Intent layer and research routing |
| `thought_to_ppt/` | Structure layer and rendering layer |
| `svg_pipeline/` | SVG quality checking, finalization, templates, and spec locking |
| `svg_to_pptx/` | Native editable PPTX export from SVG pages |
| `utils/` | SVG / PPTX helpers, image handling, and render support (HTML-route-only browser helpers are isolated under `utils/browser.py` and `utils/common.py`) |
| `assets/` | templates and shared prompt assets |

## Source-Level Reading Guide

The most efficient reading order is:

1. Read `state.py` and `graph.py` first to see that the top level has only two stages.
2. Read `ppt_thought/` to understand how user intent converges into a content blueprint.
3. Read `thought_to_ppt/state.py` to understand `PPTPage` as the key intermediate representation.
4. Read `outline_generator/` to understand how long-form content becomes page structure.
5. Read `page_generators/` last to understand page-type specialization and render-quality feedback.

If you read the source in that order, the code stops looking like "many nodes and many prompts" and starts looking like a clean layered system.

## Integration With The Rest Of The Project

At the repository level, `ppt_generator` is the main application driven by the CLI.

- Upstream, it is invoked by `scripts/run_ppt_pipeline.py` and adapted by `scripts/utils/pipeline.py`.
- Downstream, it writes cache metadata under `output/<run_id>/` and records the separate render output directory in `ppt.json`.

It consumes high-quality markdown from `deep_research` when needed and decides which intermediate artifacts should be persisted so that staged runs and patch rendering are possible.

At the highest level, its job is:

"Turn an open-ended presentation request into a staged, cacheable, repairable slide-production pipeline."

## Maintenance Focus

When modifying this package, the most important thing to preserve is not any individual prompt but the architectural invariants:

- the content-planning layer and rendering layer should stay separated,
- `PPTPage` should remain the stable boundary between structure and rendering,
- SVG should remain the default rendering intermediate; the HTML backend is opt-in and must not become the default again without explicit user action,
- page-type specialization should not collapse into one generic generation path,
- render-result feedback should stay intact, or output quality will degrade quickly.
