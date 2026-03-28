# Core Package Overview

The `core/` directory contains the two core business applications in this repository:

- [`deep_research`](deep-research.md): produces a structured markdown report for complex research requests
- [`ppt_generator`](ppt-generator.md): turns a slide request into thought, outline, HTML slides, PDF, and optional PPTX

## Package Layout

```text
core/
├── deep_research/
├── ppt_generator/
├── utils/
└── __init__.py
```

## How They Interact

`ppt_generator` is the top-level product graph. In normal runs it owns the complete PPT workflow.

`deep_research` is not a separate product surface for users in this repository. It is an internal capability invoked by `ppt_generator.ppt_thought` when the request needs deeper research and writing.

## Shared Architectural Pattern

Both apps follow the same broad structure:

- `graph.py`: builds the LangGraph workflow
- `node.py`: contains node implementations
- `state.py`: declares TypedDict/Pydantic state contracts

This makes graph wiring, business logic, and state schema easy to inspect independently.

## Reading Order

If you are new to the codebase, read in this order:

1. [`ppt-generator.md`](ppt-generator.md)
2. [`deep-research.md`](deep-research.md)

That matches the normal top-down runtime dependency path.
