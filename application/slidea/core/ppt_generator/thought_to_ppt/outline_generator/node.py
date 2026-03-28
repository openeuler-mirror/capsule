import json
import re
from typing import List
from pydantic import TypeAdapter

from langchain.messages import HumanMessage
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langgraph.types import StreamWriter

from core.utils.llm import default_llm as llm, llm_invoke
from core.utils.logger import logger
from core.ppt_generator.thought_to_ppt.outline_generator.state import (
    OutlineState,
    ChapterDetail,
    UserQuery,
    Chapter,
    ChapterItem,
    SectionState,
    SlideDetail,
    CoverItem
)

SPLIT_LEN = 81920
SPLIT_PAGE = 15
MAX_PAGE = 100


async def analyze_input_node(state: OutlineState):
    """
    步骤: 确定PPT总页数
    1. 优先从 user_query 提取明确的页数要求。
    2. 如果没有明确要求，则聚合所有章节的 content (总结或原文)，
       让 LLM 根据内容丰富程度自动评估，默认推荐 10-20 页。
    """
    user_query = state["user_query"]
    content_context = state.get("input_text")[:SPLIT_LEN]
    if not content_context:
        content_context = "无详细章节内容"

    logger.info(f"页数分析")

    # 2. 构建 Prompt
    prompt = f"""
你是一个专业的PPT策划专家。请根据【用户指令】和【文章内容概览】，决定这份PPT的目标总页数。
**决策逻辑**：
1. **显式提取**：首先检查【用户指令】。如果用户明确指定了页数（例如'生成30页'、'5页'等），**必须**直接使用该数字。
2. **隐式推断**：如果用户未指定页数，请阅读【文章内容概览】。
   - 根据内容的丰富程度、章节数量和深度进行估算。
   - **默认范围**：请给出 1 到 100 页之间的合理数字。只需要考虑正文页数，不用关注封面目录过渡页等

**输入信息**：
    用户指令: {user_query}
    文章内容概览: \n{content_context}

**输出要求**：
    请仅输出一个JSON格式，包含以下字段：
    - `title`: str (给要制作的PPT生成一个名字)
    - `target_page_count`: int (最终决定的页数)
    - `language`: str (制作PPT使用的语言，限制为中文或者english)
    - `reasoning`: str (简短的决策理由)
"""

    result = await llm_invoke(llm, [HumanMessage(content=prompt)], pydantic_schema=UserQuery)
    if not result:
        logger.warning(f"--- 页数分析出错，使用默认值 15 ---")
        target_pages = 15
        reason = "解析异常"
        title = ""
        language = "中文"
    else:
        target_pages = result.target_page_count
        reason = result.reasoning
        title = result.title
        language = result.language

    target_pages = min(MAX_PAGE, target_pages)
    logger.info(f"【{language}】{title} 页数决策: {target_pages} 页. 理由: {reason}")

    # 更新状态
    return {"target_page_count": target_pages, "title": title, "language": language}


async def split_chapters_by_llm(state):
    """ 使用LLM拆分文档 """

    long_text = state["input_text"][:SPLIT_LEN]
    prompt = f"""
你是一个资深的文学编辑。你的任务是从参考文档中获取和将要制作的PPT主题 {state["title"]} 相关的内容，
将参考文档根据内容和需求首先划分为初步的大章节目录，后续会根据目录进一步拆解为每页PPT内容",

PPT制作的原始需求为：
{state["user_query"]}

对于每一个识别出的章节，请提供：
1. title 章节标题。使用语言：{state["language"]}
3. description 详细说明本章节描述的内容

**输出要求是严格的 JSON list 格式。**:
[
    {{"title": "章节X名称", "description": "..."}},
    {{"title": "章节Y名称", "description": "..."}},
]

**注意事项**：
大纲必须契合用户的写作需求！
章节目录列表需要覆盖用户的重点诉求
章节目录结构要合理
不同章节描述的内容不能重复
如果参考文档有明确的一级章节目录，请根据按照已有的一级文档目录进行划分，否则根据内容归纳为3-7个大章节
章节的数目不是PPT的页数，PPT会基于章节目录进一步拆解，所以章节目录要根据内容进行划分，不能过于细节

**参考文档内容**：
{long_text}
"""

    json_schema = TypeAdapter(List[ChapterDetail]).json_schema()
    chapters_data = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=json_schema)
    if not chapters_data:
        chapters_data = []

    for chap in chapters_data:
        chap["content"] = long_text
    return chapters_data


def mask_markdown_code_blocks(text: str):
    """替换代码块中的#"""

    def replace_hashes(match):
        content = match.group(0)
        return content.replace("#", "@@HASHAIOS@@")

    return re.sub(r'(```.*?```)', replace_hashes, text, flags=re.DOTALL)


def unmask_markdown_code_blocks(text: str):
    """将特殊字符还原"""
    return text.replace("@@HASHAIOS@@", "#")


def get_highest_header_level(state) -> str:
    """ 获取文档中最高层级的标题标记（如"#"、"##"等）"""
    if not state.get("is_markdown_doc"):
        return ""

    text = state["input_text"]
    text = mask_markdown_code_blocks(text)
    matches = re.findall(r"^(#{1,6})(?=\s)", text, re.MULTILINE)
    if len(matches) < 2:
        return ""

    content_matches = matches[1:]
    min_level_len = min(len(m) for m in content_matches)
    return "#" * min_level_len


async def split_chapters(state):
    """
    根据文档结构（Markdown标题或内容分析）分割章节
    """
    text = state["input_text"]
    raw_chapters_data = []

    separator = get_highest_header_level(state)
    logger.info(separator)
    if not separator:
        chap_list = await split_chapters_by_llm(state)
        for i, chapter in enumerate(chap_list):
            raw_chapters_data.append(
                {
                    "header": chapter["title"],
                    "content": chapter["content"],
                    "description": chapter["description"],
                    "idx": i,
                }
            )
    else:
        text = mask_markdown_code_blocks(text)
        splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[(separator, "Header")]
        )
        splits = splitter.split_text(text)

        idx = 0
        for doc in splits:
            header = doc.metadata.get("Header", "")
            if not header:
                continue

            logger.info(header)
            content = unmask_markdown_code_blocks(doc.page_content)
            raw_chapters_data.append({"header": header, "content": content, "idx": idx, "description": ""})
            idx += 1

    return raw_chapters_data


async def get_chapters_node(state: OutlineState):
    """获取PPT目录"""
    text = state["input_text"]
    target_page = state["target_page_count"]

    if target_page <= SPLIT_PAGE:
        logger.info(f"ppt页数要求{target_page}， 不做拆分")
        return {"chapters": [], "summary_text": text[:SPLIT_LEN]}

    chapters: List[Chapter] = []
    for c in await split_chapters(state):
        chapters.append(
            Chapter(
                header=c["header"],
                content=c["content"],
                idx=c["idx"],
                description=c["description"]
            )
        )
    final_chapters = sorted(chapters, key=lambda x: x.idx)
    logger.info(f"处理完成，共生成 {len(final_chapters)} 个章节")
    return {"chapters": final_chapters, "summary_text": text[:SPLIT_LEN]}


async def simple_generate_node(state: OutlineState, writer: StreamWriter):
    """
    针对极少页数需求的快速生成，不进行复杂的章节拆分
    """

    target = state["target_page_count"]
    logger.info(f"一次性生成大纲 {target} 页")

    if target > 5:
        prompt = f"""
你是一位顶级的PPT制作专家。你的核心任务是将文章内容提炼成一个清晰、结构化的PPT大纲。
**用户需求**
{state["user_query"]}
**使用语言**: {state["language"]}
**PPT总页数**: {target}页
**文章内容**: 
{state["summary_text"]}

**输出要求:**
请严格按照以下要求，将上述输入内容转换成一个Python列表（List），列表中每个元素都是一个代表单页PPT的Python字典（Dictionary）。

1.  **整体结构：** 输出必须是一个Python列表，例如 `[ {{slide1}}, {{slide2}}, ... ]`。
2.  **单页结构：** 列表中的每个字典代表一页PPT，且必须包含且仅包含三个键：`title` 、 `abstract` 和 `type`。
    * `title`: 字符串类型。请从输入内容中为每一页提炼出一个最具概括性、最吸引人的核心标题。标题应简短、有力、直击要点。**注意**：该`title`的值会作为最终的PPT标题！
    * `abstract`: 字符串类型。请将该页PPT需要呈现的核心论点、关键数据和支撑信息，总结为几条要点。每条要点前请使用项目符号（如 `•` 或 `-`），并以换行符 `\n` 分隔。
                摘要内容应高度精炼，保留最关键的信息，适合直接展示在PPT页面上。
    * `type`: int类型。如果该页为普通内容页，值为1；如果该页为目录页，值为2；如果该页为章节分割页，值为3；如果该页为封面，值为4。
3.  **转换逻辑：**
    * 忽略输入中的过程性描述（如“这里需要引用xx报告”、“需要用户补充数据”等），只提炼最终应呈现在PPT上的核心内容。
    * 如果要求的PPT总页数小于等于**5**页，不需要考虑封面、目录和分割页，直接总结文章，输出PPT内容页(type=1)即可。
    * 否则
        * 必须包含封面页、目录页，计算在总页数之内。 
        * 你需要理解输入文本的层次结构，将每个相对独立的核心观点或议题，转换成一页PPT（即一个字典）。
        * 对于大章节的标题页，可以单独生成一页，其`abstract`可以概括该章节的主要内容。

4.  **格式约束：**
    *   请直接输出Python列表格式的代码块。
    *   不要在代码块前后添加任何额外的解释、介绍或总结性文字。
5.  **其他要求**： 
    - 遵循用户需求中关于PPT页数与其他内容的要求。
    - 只在每个章节页数均多于2页时或者用户指明的情况下提取章节分割页，其他情况下不提取章节分割页。
    - 封面页、章节分割页、目录页的title和abstract要符合封面页、章节分割页、目录页的常用内容。
    - 注意：目录页为本PPT的目录，不是参考资料的目录
    - 只需直接返回list，不要包含```python ```等内容。
    - 最后不要包含致谢页！。
"""
    else:
        prompt = f"""
你是一个资深的文学编辑。你需要根据用户的原始需求，使用语言 {state["language"]} 从提供的文章内容总结出{target}个核心观点作为文章的大纲。            
**用户的原始需求**
{state["user_query"]}
**文章内容**: 
{state["summary_text"]}

**输出要求:**
请严格按照以下要求，将上述输入内容转换成一个Python列表（List），列表中每个元素都是一个代表单个观点的Python字典（Dictionary）。

1.  **整体结构：** 输出必须是一个Python列表，例如 `[ {{topic1}}, {{topic2}}, ... ]`。
2.  **单页结构：** 列表中的每个字典代表一个观点，且必须包含且仅包含三个键：`title` 、 `abstract` 和 `type`。
    * `title`: 字符串类型。请从输入内容中为每一页提炼出一个最具概括性、最吸引人的核心标题。标题应简短、有力、直击要点。
    * `abstract`: 字符串类型。请将该观点需要呈现的核心论点、关键数据和支撑信息，总结为几条要点。每条要点前请使用项目符号（如 `•` 或 `-`），并以换行符 `\n` 分隔。
                摘要内容应高度精炼，保留最关键的信息。
    * `type`: int类型。必须为1。
3.  **转换逻辑：**
    * 忽略输入中的过程性描述（如“这里需要引用xx报告”、“需要用户补充数据”等），只提炼核心内容。
    * 你需要理解输入文本的层次结构，如果文章的核心观点数量超过数量限制，可以进行合并。
"""

    schema = TypeAdapter(List[SlideDetail]).json_schema()
    slides_data = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=schema)
    if not slides_data:
        slides_data = []

    for s in slides_data:
        s["source"] = -1

    writer(
        {
            "step": "生成PPT大纲",
            "text": json.dumps(slides_data, indent=2, ensure_ascii=False),
        }
    )

    return {"final_output": slides_data}


async def plan_and_allocate_node(state: OutlineState):
    """ 使用LLM进行页数分配 (Allocation) """

    target_pages = state["target_page_count"]
    chapters = state["chapters"]
    chapters_desc = (
        json.dumps(
            [
                {"idx": c.idx, "header": c.header, "description": c.description}
                for c in chapters
            ],
            ensure_ascii=False,
        ),
    )

    prompt = f"""
你是一个专业的课程设计与PPT策划专家。
任务：根据用户提供的章节规划和参考内容，规划其中每一个章节的PPT页数。

用户原始需求为：
{state["user_query"]}
**限制条件**:
1. 用户要求总页数约: {target_pages} 页。
2. 编写的PPT名称为 {state["title"]}
3. 输出必须是JSON列表格式。

**章节编号及名称**:
{chapters_desc}

**决策逻辑**:
- 请根据各章节的重要性和内容丰富程度，分配 'allocated_pages'。
- 确保所有章节分配的 allocated_pages 之和接近 {target_pages}。
- 不要在非核心章节分配过多的页面。

**输出格式示例**:
严格按照json list格式输出，list中每个元素为一个json dict
[
    {{"header": "第一章 市场背景", "allocated_pages": 4, "idx": 0}},
    {{"header": "第二章 技术方案", "allocated_pages": 6, "idx": 1}}
]

** 参考内容**
{state["input_text"][:SPLIT_LEN]}
"""

    schema = TypeAdapter(List[ChapterItem]).json_schema()
    plan_data = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=schema)
    if not plan_data:
        plan_data = []

    for item in plan_data:
        idx = item.get("idx", -1)
        if idx < 0 or idx >= len(chapters):
            continue

        chapters[idx].allocated_pages = item.get("allocated_pages", 2)

    logger.info(f"共 {len(chapters)} 个章节/主题，页数分配: {[c.allocated_pages for c in chapters]}")
    return {"chapters": chapters}


async def generate_chapter_slides_node(state: SectionState):
    """ 为单章节分配页数 """

    chapter = state["chapter"]
    alloc = chapter.allocated_pages

    logger.info(f"正在为章节  {chapter.header}  生成大纲")
    if alloc <= 0:
        alloc = 1

    prompt = f"""
你是一个PPT内容设计师，目前需要将某个章节的内容拆分为PPT页面列表。
**制作PPT的原始诉求是：{state["query"]}
**当前章节**: {chapter.header}
**当前章节描述内容为**: {chapter.description}
**需要拆分的PPT页数**: {alloc} 页 (请严格控制页数，不要多也不要少)
**内容素材**: 
{chapter.content}

**输出结构要求**:
必须输出一个 Python 列表，列表元素为字典，字典包含：
- `title`: 字符串类型。请从输入内容中为每一页提炼出一个最具概括性、最吸引人的核心标题。标题应简短、有力、直击要点，不要生成页面序号描述。
            注意：该`title`的值会作为最终的PPT页面标题！，使用的语言必须和章节名称保持一致！！！
- `abstract`: 字符串类型。请将该页PPT需要呈现的核心论点、关键数据和支撑信息，总结为几条要点。每条要点前请使用项目符号（如 `•` 或 `-`），
                并以换行符 `\n` 分隔。摘要内容应高度精炼，保留最关键的信息，适合直接展示在PPT页面上。
"""

    schema = TypeAdapter(List[SlideDetail]).json_schema()
    slides = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=schema)
    if not slides:
        slides = []

    for s in slides:
        s["type"] = 1
        s["source"] = chapter.idx

    logger.info(slides)
    return {"generated_slides_map": {chapter.idx: slides}}


async def assemble_chapters_node(state: OutlineState, writer: StreamWriter):
    """
    组装最终PPT大纲
    """
    slides_map = state["generated_slides_map"]

    final_list = []
    logger.info("正在根据内容生成封面与目录文案")
    chap_list = [c.header for c in state["chapters"]]
    prompt = f"""
你是一个PPT策划专家。请根据用户的【制作需求】和文章的【内容概要】/【章节列表】，为这份PPT设计封面和目录页的文案。

**输入信息**:
    1. 用户制作需求:
    {state["user_query"]}
    3. 参考章节列表
    {chap_list}
    3. 参考内容概要:
    {state["summary_text"]}

**输出格式**:
    仅输出JSON对象，包含 keys: `cover_title`, `cover_abstract`, `toc_intro`"
    1. **封面标题 (cover_title)**: 提炼一个有吸引力、概括性强的主标题。不要直接使用'分析报告'这种泛泛的词，要结合内容。
    2. **封面摘要 (cover_abstract)**: 字符串类型，用3-4个短句总结PPT的核心价值或汇报人、汇报时间等（可用占位符）。
    3. **目录列表 (toc_intro)**: list格式，返回当前PPT的目录列表。和章节列表信息保持一致。
)
"""

    metadata = await llm_invoke(llm, [HumanMessage(content=prompt)], pydantic_schema=CoverItem)
    if metadata:
        logger.info(f"封面与目录: {metadata}")
        final_list.extend(
            [
                {
                    "title": metadata.cover_title,
                    "abstract": metadata.cover_abstract,
                    "type": 4,
                    "source": -1,
                },
                {
                    "title": "目录",
                    "abstract": metadata.toc_intro,
                    "type": 2,
                    "source": -1,
                },
            ]
        )

    for chap in state["chapters"]:
        slides = slides_map.get(chap.idx, [])
        if not slides:
            continue

        # 插入章节过渡页 (如果该章内容>1页)
        if len(slides) > 1:
            final_list.append(
                {
                    "title": chap.header,
                    "abstract": "章节详情",
                    "type": 3,
                    "source": chap.idx,
                }
            )
        final_list.extend(slides)

    writer(
        {
            "step": "生成PPT大纲",
            "text": json.dumps(final_list, indent=2, ensure_ascii=False),
        }
    )

    logger.info(f"组装完成，总页数: {len(final_list)}")
    return {"final_output": final_list}
