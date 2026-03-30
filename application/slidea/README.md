[中文](README_CN.md) | English

# Slidea

Slidea is a LangGraph-based slide generation skill for turning a high-level presentation request into structured research material, a writing thought process, a slide outline, and final rendered slide artifacts.

It is not just a single PPT export script. It is a staged generation system with caching, resumability, selective re-rendering, and optional deep research.

## What Slidea Does

Given a request such as "create a PPT about AI agents for product, engineering, and business leaders", Slidea can:

- parse the request into structured requirements,
- collect source material from user input, URLs, and optional search,
- generate a writing thought process for the deck,
- convert that thought process into a slide outline,
- render slide pages as HTML,
- export merged PDF and, when available, PPTX.

The system is designed for agent-driven use cases where you need more than one-shot generation. It supports staged execution, cache reuse. This staged design exists for two reasons:

- generation quality improves when research, planning, outlining, and rendering are separated;
- intermediate outputs can be cached, inspected, edited, resumed, or reused.

## Quick Start

### Recommended: use an agent to install Slidea as a skill

Slidea is primarily intended to be installed as a skill inside an agent environment, rather than used as a standalone Python project that users manage manually. If your agent platform supports local skills, install Slidea through that platform's skill workflow first, then configure the required `.env` values inside the Slidea skill directory.

The Slidea skill is currently adapted for ARM openEuler, Apple Silicon macOS, Windows WSL/PowerShell, and other Linux environments. It can be installed and run directly in mainstream agent environments such as OpenClaw, Codex, and Claude Code.

To install the Slidea skill, you can send the following instruction to your agent:

```text
Please fetch and follow the installation instructions for the Slidea skill here: https://raw.gitcode.com/openeuler/capsule/raw/master/application/slidea/skill/INSTALL.md
```

After installation, restart the agent so it can reload the installed skill. Then invoke Slidea using the skill entry style supported by your agent environment. 

In an environment like OpenClaw, you might invoke it like this:

```text
Use the slidea skill to create a PPT about AI Agents, targeted at product, technical, and business leaders
```

In an environment like Claude Code that supports slash-style skill commands, you might invoke it like this:

```text
/slidea Create a PPT about AI Agents, targeted at product, technical, and business leaders
```

The exact invocation syntax depends on the host agent, but the expected experience is the same: the agent loads the Slidea skill, gathers any missing information if needed, and runs the slide-generation pipeline through to final artifacts.

### Supported Platforms

| Platform | Architecture | Support |
| --- | --- | --- |
| Windows | x86_64 / ARM64 | ✅ |
| macOS | Apple Silicon | ✅ |
| Linux  | x86_64 | Supported for Ubuntu/Debian family only |
| Linux  | ARM64 | Supported for RHEL family only |

### Use from source

If you are contributing to Slidea itself, or if you need to debug the repository locally, you can use Slidea directly from source.

1. Fetch the source code and enter the directory:
   ```bash
   git clone https://gitcode.com/openeuler/capsule.git
   cd capsule/application/slidea
   ```

2. Use the script to automatically create the virtual environment and install the required dependencies:
   This step automatically handles Python dependencies, the Playwright browser, and LibreOffice-related setup.
   ```bash
   python3 scripts/install/install.py
   ```

3. Configure environment variables:
   If the script has not already created `.env`, you can run:
   ```bash
   cp .env.example .env
   ```
   Then configure at least these values in `.env`:
   - `DEFAULT_LLM_MODEL`
   - `DEFAULT_LLM_API_KEY`
   - `DEFAULT_LLM_API_BASE_URL`
   These three settings currently support OpenAI-compatible APIs only.

4. Run an example command:
   For more commands, see `docs/cli.md`.
   ```bash
   .venv/bin/python scripts/run_ppt_pipeline.py \
     --text "<PPT request>" \
     --session-id test
   ```

If you do not want to use the installer in step 2 to prepare the runtime automatically, you can also set it up manually:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

LibreOffice can be downloaded and installed from:
`https://www.libreoffice.org/download/download-libreoffice/`

## Repository Structure

- `scripts/`: user-facing CLI entrypoints for skill export, full pipeline runs, staged runs, patch rendering, and nested install helpers
- `skill/`: the exported skill-package definition, including `SKILL.md`, `INSTALL.md`, and the skill manifest
- `core/`: the main LangGraph applications, including deep research, PPT generation, and shared core utilities
- `docs/`: public documentation for quickstart, CLI, architecture, and app internals
- `tests/`: regression coverage for portability, CLI contracts, and runtime behavior

## Core Subsystems

### Deep Research

`core/deep_research/` is responsible for recursive research and long-form synthesis.

Its job is not slide rendering. Its job is to expand a broad request into a structured research process: decomposing questions, collecting evidence, reviewing gaps, and writing research output that can later feed the presentation pipeline.

Use this path when the task needs insight generation before slide planning.

### PPT Generator

`core/ppt_generator/` is responsible for presentation-oriented generation.

It takes source material and turns it into:

- a presentation thought process,
- a slide outline,
- page-level HTML renders,
- and final PDF / PPTX artifacts.

This subsystem is split so that "thinking about the deck" and "rendering the deck" are separate concerns.

## CLI Overview

Slidea exposes three primary script entrypoints:

- `scripts/run_ppt_pipeline.py`: main generation pipeline, including staged execution
- `scripts/patch_render_missing.py`: selective re-rendering for missing or targeted pages
- `scripts/install/install.py`: bootstrap local runtime dependencies for the source tree or exported skill package
- `scripts/export_skill.py`: export the skill package from the source tree

For full argument documentation and JSON result contracts, see [CLI Reference](docs/cli.md).

## Resume Interrupted Runs

The main pipeline CLI supports resuming an interrupted LangGraph run.

When `scripts/run_ppt_pipeline.py` returns `stage: "input_required"`, the caller should:

1. show the question or options to the user,
2. wait for the user's explicit response,
3. call the CLI again with the same `run_id`, `session_id`, and `--resume`.

Example:

```bash
.venv/bin/python scripts/run_ppt_pipeline.py \
  --resume "Product and engineering leaders" \
  --session-id local-demo \
  --run-id <run_id>
```

For select-style interactions, upstream callers can also resume with structured payloads. Internally the runtime accepts resume input in this order: `selection`, `answer`, `text`, then `message`.

Current limitation: `--resume` is handled by the full-graph `--stages all` path. Staged execution remains cache-oriented and does not resume LangGraph interrupts directly.

## Outputs and Caching

Each run is tracked by a `run_id`.

Cached intermediates live under:

- `output/<run_id>/`

Typical files include:

- `run.json`
- `references/`
- `research/`
- `thought/thought.md`
- `outline/outline.json`
- `ppt.json`

Important distinction:

- `output/<run_id>/` is the run cache and metadata directory
- final rendered artifacts are written to the render directory recorded in `ppt.json`

That separation allows staged re-entry and patch rendering without rerunning the full pipeline.

## Runtime Behavior and Degradation

The runtime is configuration-driven.

If optional services are missing, the system degrades rather than failing wholesale:

- no Tavily config: skip web search
- embedding disabled or unconfigured: skip embedding-based ranking
- no LibreOffice conversion available: keep HTML/PDF outputs and skip PPTX conversion
- no VLM config: skip VLM-backed image scoring and distribution features

This makes the project usable across different local and remote environments with different capability levels.

## Documentation

Start here depending on what you need:

- [Documentation Index](docs/README.md)
- [Quickstart](docs/quickstart.md)
- [CLI Reference](docs/cli.md)
- [Architecture Overview](docs/architecture.md)
- [App Overview](docs/core/README.md)
- [Deep Research App](docs/core/deep-research.md)
- [PPT Generator App](docs/core/ppt-generator.md)

## Verification

Run the regression suite with:

```bash
python3 -m unittest tests.test_image_config -v
python3 -m unittest tests.test_runtime_config -v
python3 -m unittest tests.test_preflight -v
python3 -m unittest tests.test_runtime_options -v
python3 -m unittest tests.test_portability_polish -v
python3 -m unittest tests.test_pipeline_contracts -v
python3 -m unittest tests.test_cli_stage_smoke -v
python3 -m unittest tests.test_patch_render_cli_smoke -v
```

## Contributing

Contributions are most useful when they improve one of these areas clearly:

- CLI contract and runtime stability
- research graph quality
- outline or render quality
- portability and environment handling
- public documentation

If you change behavior, update the corresponding docs under `docs/` in the same change.
