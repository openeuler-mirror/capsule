# Research & Speech Script

This document describes the Research & Speech Script phase (Phase 1) of the Slidea workflow. Before generating a PPT, you must first collect information and generate a speech script markdown file, which serves as the reference material for PPT generation.

## Step 1.1: Understand Report Context

Identify the following three key dimensions:

- **Purpose**: Why is this presentation being made? (e.g., inform decision-makers, persuade stakeholders, educate a team, present research findings, support a business proposal)
- **Audience**: Who will receive this presentation? (e.g., executives, technical team, general public, academic peers, investors)
- **Topic**: What is the core subject of the presentation?

Rules for gathering this information:
- If the user has explicitly stated the purpose, audience, or topic, use that information directly
- If any of these are unclear, ask the user to clarify. Combine all unclear dimensions into a single question — **only ask once, do not repeat**
- If the user still does not provide clear answers after being asked, **infer reasonable defaults** from the conversation context, research topic, and reference materials

## Step 1.2: Extract Content from Provided Materials

If user provided local documents or web links, extract content using the crawl tool:

```bash
.venv/bin/python -m core.utils.crawl --file_path [path1] [path2] [dir1]...
```

- `--file_path`: one or more file paths (local documents or URLs or directories)

The tool returns a dictionary:
```python
{'text': "merged text content from all sources", 'images': [image1, image2, ...], 'files': [file1, file2, ...]}
```

**Evaluate sufficiency**: After extraction, assess whether the extracted content fully addresses the user's research needs. If yes, proceed to Step 1.4. If not, proceed to Step 1.3.

## Step 1.3: Web Search (When Needed)

If the provided materials are insufficient or no materials were provided, perform web search using the search tool:

```bash
.venv/bin/python -m core.utils.search [query1] [query2] ... --max-results 3
```

- Positional args: one or more search keywords/queries
- `--max-results`: max results per query (default: 3)

Search workflow:
1. Analyze the user's research needs and identify key aspects that need additional information
2. Generate no more than 5 search keywords/queries that cover different aspects of the research topic
3. Call `search` with all queries in a single batch call (it supports concurrent search internally)
4. Only call `search` once in one iteration

Guidelines for search keywords:
- Each keyword should target a different aspect of the research topic
- Keywords should be specific and targeted, not overly broad
- Use the language appropriate to the research topic
- Prioritize aspects not covered by the provided materials

## Step 1.4: Plan Writing Logic and Generate Speech Script

**1.4.1 Plan the writing logic and structure**

Before writing, think through the overall writing approach based on the report's purpose, audience, and collected reference materials:

1. **Determine the narrative logic**: How should the speech script flow to best achieve its purpose for the target audience?

2. **Define key arguments and evidence**: Based on the reference materials, identify:
   - Core thesis or main message
   - Supporting arguments for each section
   - Specific data, examples, and citations to back each argument
   - Images or figures that can visually strengthen key points

3. **Design the section structure**: Create a detailed outline that:
   - Logically progresses from one section to the next
   - Each section has a clear purpose and takeaway
   - Arguments build upon each other toward the conclusion
   - Is appropriate for the audience's knowledge level and expectations

**1.4.2 Generate the final markdown document**

Generate the markdown document. The document should be comprehensive enough to serve as a presentation or speech script.
The document should explicitly state Audience, Topic, and Purpose and PPT page number evaluation.

**Structure rules**:
- Divide the overall structure into **no more than 7 parts**, each representing a logical thematic block
- **Do not** introduce each slide/page individually — organize content by thematic parts, not by pages
- Adjust the level of detail for each part based on its **importance to the core message**: key arguments should be elaborated with data and examples, while supporting or transitional content can be concise
- Label each part with its importance level (e.g. ★★★ core / ★★ important / ★ supporting), and allocate detail proportionally — core parts get the most depth, supporting parts stay concise

Document quality requirements:
- **Presentation-ready**: Content must be detailed and substantive enough to be used directly for speeches or presentations. Each part should have clear talking points
- **Well-argued**: Every claim should be supported by specific data, examples, or evidence from source materials
- **Audience-appropriate**: Adjust technical depth, terminology, and framing to match the target audience
- **Purpose-driven**: Every section should serve the report's stated purpose — inform, persuade, educate, or recommend
- **Visual support**: Use images strategically to illustrate key points and enhance understanding
- **Logical flow**: Parts must connect smoothly, with arguments building progressively toward the conclusion
- **Write in the same language as the user's request**

**1.4.3 Save the markdown file**

Save the markdown file as `<topic_name>.md` to `<output_root>/speech_script/`, where `<output_root>` is `<SLIDEA_DIR>/output/` by default or the directory configured via `OUTPUT_DIR` in `.env`. Record the absolute file path as `<SPEECH_SCRIPT_MD_PATH>`.


## Step 1.5: Handle User Feedback

After generating the speech script, you must present the content to the user for review:

1. **Display the speech script**: Show the absolute file path of the generated markdown file to the user. **You MUST output the complete, unabridged content of the speech script markdown file verbatim** — do NOT summarize, paraphrase, truncate, or provide a brief overview. The user needs to review every word of the speech script as it will be delivered.
2. **Ask for feedback**: Explicitly ask the user whether they are satisfied with the speech script or have any suggestions for improvement (e.g., content to add, remove, or modify; structural adjustments)

Rules for handling feedback:
- **Understand the feedback**: Carefully read and understand the user's comments, questions, and suggestions.
- **Address the feedback**: If the feedback requires information not in the current materials, perform additional collection using `search` or `crawl` tools, regenerate the speech script based on the whole informations.
- **Iterate**: After applying the feedback, present the updated speech script to the user for another round of review till the user is satisfied.

**Only End the current phase (Step 1.5) after the user confirms the speech script is acceptable**.
