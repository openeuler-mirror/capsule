# Slidea Documentation

This directory contains public-facing documentation for the Slidea skill. The codebase is organized around a LangGraph-driven pipeline that turns a slide request into research material, a writing thought process, a PPT outline, and finally HTML/PDF/PPTX outputs.

## Documentation Map

- [Quickstart](quickstart.md): environment setup, required configuration, and the shortest path to a successful local run
- [CLI Reference](cli.md): command entrypoints, stage model, arguments, and output contracts
- [Architecture Overview](architecture.md): end-to-end system design, runtime flow, cache model, and major modules
- [App Overview](core/README.md): index of the `core/` package and how its subgraphs fit together
- [Deep Research App](core/deep-research.md): recursive research/writing graph used for deep insight mode
- [PPT Generator App](core/ppt-generator.md): thought generation, outline generation, and page rendering internals

## Intended Audience

These docs are written for three use cases:

1. New contributors who need to run the project locally.
2. Integrators who only need the CLI contract and output model.
3. Maintainers who need to understand the graph architecture under `core/`.

## Repository Areas

- `scripts/`: public CLIs plus installer/runtime support modules
- `core/`: LangGraph apps, package-local render helpers, and shared core utilities
- `tests/`: regression coverage for CLI contract and portability behavior

## Design Summary

The project is built around one primary workflow:

1. Parse the user request and optionally ask follow-up questions.
   If the graph interrupts for clarification, the caller can later resume the same run with `--resume` and the original `run_id`.
2. Collect references from user-provided URLs and optional search/deep research.
3. Generate a PPT writing thought process.
4. Convert that thought plus source material into a slide outline.
5. Render each slide as HTML, then synthesize PDF and optional PPTX artifacts.

The docs below mirror that runtime model, so reading them in order is the fastest way to understand the system.
