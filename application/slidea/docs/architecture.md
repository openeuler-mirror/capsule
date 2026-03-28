# Architecture Overview

Slidea is a graph-driven slide generation system built around LangGraph. The codebase is intentionally separated into orchestration, domain graphs, runtime adapters, and shared infrastructure.

## End-to-End Flow

At the highest level, the system does this:

1. Accept a natural-language slide request from the CLI.
2. Parse the request into structured requirements.
3. Gather user-provided references and optionally perform web/deep research.
4. Generate a writing thought process for the presentation.
5. Produce a normalized slide outline.
6. Render each page as HTML.
7. Convert HTML pages to a merged PDF and optionally to PPTX.

## Top-Level Module Map

| Module | Role |
| --- | --- |
| `scripts/` | CLI entrypoints, installer package, and CLI-side runtime helpers |
| `core/` | Business logic graphs for research and PPT generation |
| `tests/` | regression tests for CLI payloads, runtime config, and portability |

## Runtime Layers

### 1. CLI Layer

`scripts/run_ppt_pipeline.py` is the public execution surface.

Responsibilities:

- parse CLI arguments
- accept either a fresh request (`--text`) or a resume payload (`--resume`) for an interrupted full-graph run
- run environment preflight
- create `run_id` and output directories
- persist run metadata
- choose between full-graph mode and staged mode
- emit machine-readable JSON results

### 2. Pipeline Adapter Layer

`scripts/utils/pipeline.py` bridges LangGraph runtime behavior and CLI-friendly output.

Responsibilities:

- consume LangGraph event streams
- forward user-visible streamed tokens
- normalize interrupt types
- normalize resume payloads from upstream callers
- convert interrupts into a stable `input_required` stage signal
- return terminal `completed` payloads when the graph finishes

Current behavior note:

- interaction-specific details are prepared inside the adapter,
- but the terminal JSON emitted by the CLI currently only exposes the top-level `input_required` stage,
- richer interaction hints are surfaced through runtime events and console output rather than the final JSON payload.

`core/utils/interrupt.py` defines the enum used across the project for interrupt semantics.

Resume payloads are accepted in a tolerant order: `selection`, `answer`, `text`, then `message`. The adapter converts the resolved value into `Command(resume=...)` input for LangGraph continuation.

### 3. Application Graph Layer

This is the core product logic under `core/`.

- `core/ppt_generator/`: full presentation generation graph
- `core/deep_research/`: recursive document research/writing graph used in deep mode

### 4. Infrastructure Layer

`core/utils/` provides reusable services shared by graph code and script entrypoints:

- `config.py`: environment-backed runtime settings
- `cache.py`: output path and JSON/text cache persistence
- `llm.py`: LLM/VLM invocation wrappers
- `crawl.py`: URL/file content acquisition
- `tavily_search.py`: search integration
- `logger.py`: project logging
- `interrupt.py`: shared interrupt enum for graph-to-CLI interaction

`scripts/utils/` provides CLI-only runtime helpers:

- `preflight.py`: dependency/config readiness checks
- `cli_output.py`: centralized JSON payload emission
- `pipeline.py`: LangGraph stream and interrupt adaptation for CLI consumers

## Main Graph Composition

The top-level graph builder is `core.ppt_generator.graph.ppt_workflow`.

It has two stages:

1. `generate_thought`
2. `thought_to_ppt`

This division is important because the code treats content planning and visual rendering as separate concerns.

### Content Planning

The `ppt_thought` subgraph:

- parses the request
- asks follow-up questions when key fields are missing
- reads user-supplied references
- decides whether to skip, do simple search, or deep research
- generates the final writing thought for the presentation

### Slide Production

The `thought_to_ppt` subgraph:

- transforms thought + source material into an outline
- classifies pages by type
- renders cover / toc / separator / content pages
- synthesizes HTML outputs into PDF/PPTX deliverables

## Data Persistence Model

The project uses file-based run caches under `output/<run_id>/`.

This cache is not just a by-product. It is part of the architecture because:

- staged CLI execution depends on it
- patch rendering depends on it
- long-running research/render steps can be re-entered from cached artifacts
- debugging is much easier with explicit intermediate artifacts

Key artifacts:

- `run.json`
- `references/*.json|txt`
- `research/*.json|md`
- `thought/thought.md`
- `outline/outline.json`
- `ppt.json`

Caching is controlled by `settings.USE_CACHE`.

## Research Routing Model

Research mode is chosen in `core/ppt_generator/ppt_thought/node.py`.

Possible modes:

- `skip`: no external search
- `simple`: Tavily search only
- `deep`: invoke `core.deep_research`

Routing depends on:

- explicit `RESEARCH_MODE_FORCE`
- whether Tavily is configured
- inferred complexity of the request
- embedding availability
- optional user confirmation for deep mode

## Rendering Model

Rendering uses HTML as the intermediate canonical artifact.

Why HTML first:

- the LLM can directly produce layout code
- Playwright can render deterministic slide-sized pages
- HTML is easier to inspect and patch than binary presentation formats

Render pipeline:

1. choose template YAML
2. load shared PPT prompt
3. prepare output directory
4. generate each page HTML
5. evaluate scaling quality in a browser
6. retry or modify pages when scaling is poor
7. convert HTML pages to PDF
8. optionally convert PDF to PPTX via local LibreOffice

## Reliability and Degradation Strategy

The code is intentionally tolerant of missing optional services.

Examples:

- no Tavily: search is skipped
- no embeddings: ranking falls back or can be disabled explicitly
- no VLM: image scoring/distribution features are limited
- no local LibreOffice: PDF can still be produced even if PPTX is absent

This makes the project portable across environments with very different capabilities.

## Concurrency Model

Several subgraphs use LangGraph fan-out/fan-in:

- chapter-level outline slide generation
- content page worker execution
- image scoring workers
- separator page workers

The goal is to parallelize expensive LLM and asset-processing steps while still aggregating structured outputs back into the shared state.

## Testing Focus

The checked-in tests emphasize runtime contract stability rather than exhaustive visual correctness.

Covered areas include:

- CLI stage behavior
- preflight/config behavior
- cache/runtime option semantics
- patch render smoke flow
- portability-oriented regression handling

For maintainers, this means public CLI and environment behavior are treated as important compatibility surfaces.
