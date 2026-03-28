# Deep Research App

`deep_research/` is not a small helper that "searches for information and turns it into a document." It is a recursive research engine for complex writing tasks. The right way to understand it is not to start from how many nodes appear in `graph.py`, but from the kind of problem it is trying to solve and the computational model it uses to solve it.

## Design Motivation

This module exists because simple retrieval is not enough when the user is asking for a broad, deep, and well-structured insight document rather than a light summary of existing material.

For this class of task, the system faces several hard problems:

- The user request is usually a high-level objective, not a local instruction that can be written directly into one section.
- Generating a long report in one shot tends to cause structural imbalance, missing coverage, repeated chapters, and factual drift.
- Different chapters do not need the same context, so feeding all references into every section creates context pollution.
- Research is not a single action. It is a loop of decomposition, evidence gathering, local writing, and synthesis.

Because of that, the module is designed to transform "deep writing" from one-shot text generation into an iterative task-solving process built around decomposition, retrieval, and convergence.

## Core Abstraction Layers

From a source-code perspective, `deep_research` is easier to understand as four abstraction layers rather than as a flat collection of functions.

### 1. The Task-Tree Layer

The system first turns "write a deep report" into a task tree.

- The root node represents the overall research request.
- Child nodes represent chapters or sub-problems.
- Leaf nodes represent the smallest units that can be directly researched, analyzed, or written.

This abstraction matters because the system no longer treats the document as one long string. It treats it as a set of work items with hierarchy, priority, and completion state. Planning, retrieval, and writing can then be driven by the current task node.

### 2. The Local-Context Layer

The second abstraction is that each task node should have its own context view.

That is the real reason `context.py` exists. It is not just a utility file. It implements a core design principle:

- Global reference material should not be passed unchanged into every chapter.
- Each chapter should read only the evidence most relevant to itself.
- Parent and ancestor context should act as constraints so local writing stays aligned with the full document.

In other words, this module turns "context" from one global prompt into a dynamic node-level retrieval system.

### 3. The Planning-vs-Execution Layer

The third abstraction is the separation between deciding what to write and actually writing it.

- Planning decides the chapter structure, important sections, and whether further decomposition is needed.
- Execution gathers evidence, fills missing support, and writes concrete chapter content.

This is far more stable than asking the model to think and write everything in one pass. Once the plan exists explicitly, the system can inspect missing coverage, decide which parts need deeper breakdown, and track what is already complete instead of hiding all of that inside a single generation step.

### 4. The Report-Assembly Layer

The final abstraction is that the output is not a bag of isolated chapter fragments. It is a complete markdown report.

That means the end of `deep_research` is not "search is done." The end state is "the task tree has converged into a deliverable document." That is why the control flow behaves more like a document-production pipeline than a question-answering chain.

## Core Algorithmic Idea

If reduced to one sentence, the core algorithm is:

"Iterative planning over a task tree plus node-level retrieval-augmented writing."

That can be broken down into three main mechanisms.

### 1. Layered Decomposition

The system first produces top-level chapters and only then decomposes important chapters further. It does not try to expand the whole tree to maximum depth immediately. That strategy has three benefits:

- It validates the macro structure first.
- It spends more computation on important sections.
- It avoids over-planning trivial sections.

That is why the state carries fields such as `important`, `depth`, and `children_ids`. They are not just metadata. They support the progressive-expansion strategy.

### 2. Retrieval-Driven Writing

When the system writes one node, it does not hand the entire corpus to the model. It first:

1. builds the current chapter task description,
2. retrieves the most relevant content from the node and its ancestors,
3. passes only that local material into the writing prompt.

This effectively turns long-form report generation into a sequence of local RAG writing tasks. That sharply reduces:

- repetition across chapters,
- distraction from irrelevant evidence,
- attention dilution caused by oversized global context.

### 3. Plan Review and Convergence

The system does not fully trust the first plan it generates. It reviews the existing chapter structure, identifies obvious gaps, and may revise the plan before continuing execution.

That means the workflow is not a simple DAG. It is a convergence loop:

- plan,
- review,
- decompose or execute,
- select the next node,
- repeat until the tree satisfies completion conditions.

This "explicit planning, then plan correction during execution" pattern is one of the main reasons the design is more robust than a plain linear chain.

## Overall Workflow Mechanism

At a conceptual level, the whole module can be understood as five phases:

1. Initialize the research request and root task.
2. Plan the top-level chapter structure.
3. Select the most valuable task node to advance next.
4. Gather references, write content, and update state for that node.
5. Assemble the final report once the important parts are complete.

That is the real meaning behind the main line in `graph.py`:

`initializer -> plan -> selector -> processor -> reporter`

The most important point is not the node names themselves. The important point is that control stays in the `selector + processor` loop. The system is not trying to write all chapters sequentially. It keeps asking which task node is most useful to advance now.

## Why This Architecture Helps Humans Read The Code

If you are maintaining the system, this mental model is the shortest path into the source:

- Read `ResearchState` as "a task tree plus a document-production state machine."
- Read `context.py` as "the node-level context retrieval layer."
- Only then read `node.py` to see how planner, selector, processor, and reporter are implemented.

That prevents you from getting stuck at the surface level of "this function builds another prompt." Instead, you understand which abstraction each function is serving.

## Minimal Code Map

Once the architectural model is clear, the file mapping is straightforward:

| File | Architectural role |
| --- | --- |
| `graph.py` | Encodes the plan-select-execute-report loop as a convergent LangGraph |
| `state.py` | Defines the task-tree state, reference items, and final report contract |
| `context.py` | Builds node-local context through summarization, embeddings, and reference filtering |
| `node.py` | Implements planner, reviewer, selector, writer, and reporter behaviors |

## Source-Level Reading Guide

The most effective reading order is:

1. Read `state.py` to understand what the system is actually maintaining.
2. Read `graph.py` to see how control flow loops toward convergence.
3. Read `context.py` to understand how local context is constructed.
4. Read `node.py` last, once the planner / writer / reviewer prompts have a clear architectural purpose.

## Integration With PPT Generation

This module is not a standalone product surface in this repository. It is invoked by `ppt_generator/ppt_thought/node.py` when the system enters deep mode.

Its inputs are intentionally small:

- `research_request`
- `raw_content`

Its outputs are explicit:

- `deep_report`
- `report_file`

That markdown report is then consumed by `ppt_generator` as a high-quality source document for outline generation. In the larger system, `deep_research` does not produce the final user artifact. It produces a structured intermediate representation that the PPT pipeline can trust and reuse.

## Maintenance Focus

When changing this module, the key thing to preserve is not any individual prompt wording but the architectural invariants:

- The task tree must remain convergent rather than expanding or reviewing forever.
- Each chapter should prefer local context instead of collapsing back into one giant prompt.
- Planning and execution should remain separated.
- The output should stay a structured markdown report that downstream PPT generation can consume reliably.
