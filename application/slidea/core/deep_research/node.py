import os
import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, List
from pydantic import TypeAdapter

from core.utils.logger import logger

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.types import StreamWriter

from core.deep_research.context import (
    get_task_reference, add_task_reference,
    MAX_CONTEXT_LEN, WORKSPACE_DIR
)
from core.deep_research.state import (
    ResearchState, TaskStatus, TaskNode, ChapterItem,
    DecomposeItem, DecisionItem, SearchItem
)
from core.utils.llm import default_llm as llm, llm_invoke
from core.utils.tavily_search import tavily_search
from core.utils.crawl import get_content


def generate_todo_list(state: ResearchState, node_id: str = "") -> str:
    """
    接收 TaskNode 列表，返回格式化后的树形 TODO List 字符串。
    """

    if not node_id:
        node_id = state["root_id"]

    task_map = state["task_map"]
    output_lines = []

    def _dfs_build_lines(node_id: str, current_depth: int):
        node = task_map.get(node_id)
        if not node:
            return

        if current_depth >= 0:
            indent = "    " * current_depth
            is_completed = node["status"].lower() in {TaskStatus.COMPLETED}
            symbol = "✓" if is_completed else "✕"
            line = f"{indent}[{symbol}] 【{node['title']}】: {node['description']}"
            output_lines.append(line)

        if node["children_ids"]:
            for child_id in node["children_ids"]:
                _dfs_build_lines(child_id, current_depth + 1)

    _dfs_build_lines(node_id, -1)
    todo = "\n".join(output_lines)

    filepath = os.path.join(WORKSPACE_DIR, f"todo_{node_id}.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(state["research_request"] + "\n\n")
        f.write(todo)
    return todo


def get_root_node(state: ResearchState):
    """get root node of tree"""
    root_id = state["root_id"]
    return state["task_map"][root_id]


async def get_task_context(state: ResearchState, task_node: TaskNode) -> Dict[str, Any]:
    """获取当前任务的上下文信息"""

    todo_list = generate_todo_list(state)
    current = f"{task_node['title']}：{task_node['description']}"

    node_ctx = task_node["context"] if task_node["context"] else ""
    node_ref = await get_task_reference(task_node["references"], current)
    reference = f"{node_ctx}{node_ref}"

    parent = task_node["parent_id"]
    while parent and len(reference) < MAX_CONTEXT_LEN:
        p_node = state["task_map"][parent]
        node_ctx = p_node["context"] if p_node["context"] else ""
        reference = f"{reference}{node_ctx}"
        node_ref = await get_task_reference(
            p_node["references"], current, MAX_CONTEXT_LEN - len(reference)
        )
        reference = f"{reference}{node_ref}"
        parent = p_node["parent_id"]

    context = f"""
## 写作任务原始请求如下：
{state['research_request']}

## 全文写作进度如下：
{todo_list}

## 当前正在写作的章节为：
{current}

## 可参考的内容如下：
{reference[:MAX_CONTEXT_LEN]}
"""
    return context


async def task_write(state: ResearchState, task: TaskNode):
    """write chapter content"""
    context = await get_task_context(state, task)

    title = task["title"]
    description = task["description"]
    chapter = f"{title}：{description}"
    chapter_title = (task["depth"] + 1) * "#" + " " + title
    prompt = f"""
你是一名写作专家，正在编写一篇文档。
你当前的任务是根据写作需求、参考信息以及文档写作的整体情况，进行指定章节的写作。

# 整体文档的写作要求
{state["research_request"]}

# 当前需写作的章节和要求
{chapter}

# 参考信息以及文档写作的整体情况
{context}

# 注意
使用Markdown语法输出文档
使用 '{chapter_title}' 作为章节标题
写作时只专注于本章节主题，无关内容不要考虑
不要和其他章节需要编写的内容出现重复
请尽可能详尽地呈现信息。
准确、公正地呈现事实。
逻辑清晰地组织信息。
加粗标黑显示段落中的关键概念和术语
严格依据所提供的信息，绝不编造或臆想信息，用证据支持观点，避免猜测。
使用合理的段落分配，段落中使用完整的语句描述，减少使用有序/无序列表。。
合理使用链接、列表、行内代码、表格等格式选项，使文档更具可读性。
仅使用输入中明确提供的信息。
如果当前章节的写作要求遗漏了整篇文档的写作要求，请按照要求补全
"""

    logger.info(f"-----write chapter {title} ----")
    result = await llm_invoke(llm, [HumanMessage(content=prompt)])
    if result:
        return result

    logger.warning(f"write chapter {title} failed")
    return ""


async def review_plan(state: ResearchState, task_node: TaskNode):
    """review for chapters"""
    if not task_node["children_ids"]:
        return []

    context = await get_task_reference(
        task_node["references"], task_node["description"]
    )
    todo = generate_todo_list(state, task_node["id"])
    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")
    prompt = f"""
当前时间为 {cur_time}
# 有一个洞察需求，原始要求如下：
{state['research_request']} 

# 已经为当前全文分解章节目录列表如下
{todo}

# 你的主要任务是查漏补缺，要求如下：
审视当前的章节目录大纲是否存在明显缺陷/遗漏
审视列出的重点章节和拆分计划是否存在明显缺陷
审视章节描述是否存在需求丢失
根据文档的洞察需求，明确任务的洞察深度和广度，对于较为简单的任务不需要拆分过细的章节目录
请关注于文档的宏观结构，不要陷于细节
目录中的章节层次必须位于同级别
请给出存在明显缺陷的章节目录描述
注意：请宽容一些，并不需要章节目录百分之百完美
注意：请宽容一些，并不需要章节目录百分之百完美
注意：请宽容一些，并不需要章节目录百分之百完美

# 输出格式要求
请输出一个JSON list格式， list中元素为不合理/错漏的章节目录描述，如果章节目录是合理的，输出[]

# 可参考的背景信息：
{context}
"""

    schema = TypeAdapter(List[Any]).json_schema()
    result = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=schema)
    if not result:
        return []

    title = task_node["title"][:100]
    logger.info(f"🧠 [Review] for: {title} \n{result}")
    return result


async def task_planner(state: ResearchState, review=[]):
    """plan for document chapter"""

    request = state["research_request"]
    logger.info(f"🧠 [Planner] Planning for: {request[:100]}")
    root_node = get_root_node(state)
    context = await get_task_reference(root_node["references"], request)
    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")

    prompt = f"""
当前时间为：{cur_time}
你当前的任务是根据写作需求以及参考内容，首先形成文章的一级章节目录大纲。
用户的原始写作需求可能是需要生成PPT或者其他演示文档，你只需要专注于需要写作的内容主题，忽略掉和表现形式有关的部分

# 用户写作需求
{request}

# 当前已分解的章节目录
{generate_todo_list(state)}

# 可能存在问题的章节目录描述
{review}

# 参考信息
{context}

注意：
大纲必须契合用户的写作需求！
章节列表需要覆盖用户的重点诉求
如果当前已有章节列表，通过审视已有章节的问题，输出最新的完整章节目录列表
章节目录要合理，避免只有一个子章节的情况
请关注于文档的宏观结构，不要陷于细节
目录中的章节必须位于同一级别，避免出现子章节和父章节位于相同层级的情况
如果无明确要求，一级章节目录数目不要超过7个

# 输出格式要求
请仅输出一个JSON list格式，list列表中元素如下
- `title`:      章节标题，例如'背景调研与技术选型
- `description`:详细描述本章的写作要求，包含需要编写的内容和重点，注意不要丢失全文写作需求中的要求。
                表明当前章节是否全文的重点，同时分析是否需要进一步拆分，拆分的思路如何，但是拆分思路不能和其他章节出现重复
                采用明确清晰的方式列出需要陈述的内容，避免使用'例如'，'等'语句来进行省略表达
                请在描述中明确标明当前章节或概念的从属及约束关系，避免歧义
                章节的写作要求不能丢失洞察任务的具体需求
- `important`： bool类型，该章节是否为重点章节，注意，综述性的章节不能为重点章节

# 输出示例：
[
    {{"title": "背景调研与技术选型", "description": "调研主流AI Agent框架的历史、现状与关键技术。约2500字", "important": False}},
    {{"title": "核心框架深度分析", "description": "深入分析LangGraph, AutoGen框架的技术细节。针对LangGraph, AutoGen可进一步拆分为子章节，章节一共不少于3000字", "important": True}},
    {{"title": "未来趋势与总结", "description": "总结AI Agent发展趋势并给出选型建议。不多于500字", "important": False}}
]
"""

    json_schema = TypeAdapter(List[ChapterItem]).json_schema()
    result = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=json_schema)
    if result:
        return result

    logger.error("task_planner failed!!")
    return []


def set_childrens(task_node: TaskNode, subtasks: list, task_map: dict):
    """设置子章节"""
    for task_id in task_node["children_ids"]:
        task_map.pop(task_id, None)

    childrens = []
    for st in subtasks:
        new_id = str(uuid.uuid4())[:8]
        new_node: TaskNode = {
            "id": new_id,
            "title": st["title"],
            "description": st["description"],
            "status": TaskStatus.PENDING,
            "parent_id": task_node["id"],
            "children_ids": [],
            "search_loop": 0,
            "depth": task_node["depth"] + 1,
            "queries": [],
            "content": "",
            "references": [],
            "context": "",
            "important": st.get("important", False),
        }
        task_map[new_id] = new_node
        childrens.append(new_id)

    task_node["children_ids"] = childrens
    task_node["status"] = TaskStatus.IN_PROGRESS


async def plan_node(state: ResearchState, writer: StreamWriter):
    """plan document chapter"""
    root_id = state["root_id"]
    task_map = state["task_map"]
    root_node = task_map[root_id]

    if not root_node["references"]:
        await research_background(state)
    subtasks = await task_planner(state)
    set_childrens(root_node, subtasks, task_map)

    while len(root_node["queries"]) < 15:
        reviews = await review_plan(state, root_node)
        if not reviews:
            break
        if not await research_background(state, reviews):
            break
        subtasks = await task_planner(state, reviews)
        set_childrens(root_node, subtasks, task_map)
        references = root_node.get("references", [])
        if len(references) >= 50:
            break

    todo = generate_todo_list(state)
    writer({"text": f"```markdown\n{todo}\n```", "id": state["root_id"]})
    logger.info(f"[after plan]:\n{todo}")
    return {"task_map": task_map}


async def task_decompose(state: ResearchState, task: TaskNode):
    """decompose chapter"""

    context = await get_task_context(state, task)
    prompt = f"""
请根据用户提供的写作需求，以及当前文章编写情况及背景信息，来决定对当前的章节划分子章节

# 当前写作进度状态和背景信息
{context}

# 注意
章节划分必须契合本章节的写作需求
统一章节划分粒度，不能过于细节
和其他章节内容要做区分，不能重复！
紧扣全文主题和核心诉求，不能发散到和主题关联不大的场景

# 输出格式要求
请仅输出一个JSON list格式，list列表中元素如下
- `title`: 子章节标题，例如'背景调研与技术选型
- `description`: 该子章节的具体描述，包含需要编写的内容和重点
- `reason`：分解为该子章节的理由，是否和全文其他章节出现重复
如果没有合理地划分方式，或者想要拆分的章节在全文中存在重复，请返回[]

# 输出示例：
[
    {{"title": "背景调研与技术选型", "description": "调研主流AI Agent框架的历史、现状与关键技术。", "reason": "..."}},
    {{"title": "核心框架深度分析", "description": "深入分析LangGraph, AutoGen等框架的优缺点。" "reason": "..."}},
    {{"title": "未来趋势与总结", "description": "总结发展趋势并给出选型建议。" "reason": "..."}}
]
"""

    json_schema = TypeAdapter(List[DecomposeItem]).json_schema()
    result = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=json_schema)
    if result:
        return result

    logger.error(f"task decompose failed")
    return []


async def task_decision(state: ResearchState, task: TaskNode):
    """决定继续拆解/搜索/直接写作"""
    includes = ["decompose", "write", "search"]
    research_depth = state.get("research_depth", 2)
    if task["depth"] >= research_depth or not task["important"]:
        includes.remove("decompose")

    if (
        task["search_loop"] >= 2
        or len(task["queries"]) > 10
        or len(task["references"]) > 10
    ):
        includes.remove("search")

    title = task.get("title", "")
    logger.info(f"🧠 decision start for {title} in {includes}")

    if len(includes) == 1:
        return {"type": "write", "queries": [], "reason": ""}

    desc = "\n"
    query_desc = "queries: list类型， 固定返回[]"
    if "decompose" in includes:
        desc = (
            desc
            + "* 只有正在写作的章节内容庞杂，并且章节描述中明确需要拆分子章节，才选择decompose\n"
        )
    if "write" in includes:
        desc = desc + "* 如果当前章节背景信息足够支撑本章的撰写，请返回write\n"
    if "search" in includes:
        desc = (
            desc
            + "* 如果当前章节参考信息有明显的缺失(小的细节缺失可以容忍)，请返回search\n"
        )
        query_desc = """
queries: list类型，如果type为search，返回下一步需要搜索的不多于3个关键词列表；否则为[]
注意和已经搜索过的关键词不要重复，如果搜索学术论文相关，请优先从arxiv搜索，
搜索关键词需要明确标明具体的从属或约束关系，避免歧义，不要重复搜索已经搜索过的内容\n
"""

    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")
    context = await get_task_context(state, task)
    format_instructions = PydanticOutputParser(pydantic_object=DecisionItem).get_format_instructions()
    prompt = f"""
当前时间为{cur_time}
请从用户提供的写作需求，以及当前任务执行现状及背景信息决定当前任务的下一步决策

# 文档写作需求
{state['research_request']}

# 当前全文的写作状态信息
{context}

# 当前章节已经搜索过的关键词如下：
{task["queries"]}

# 输出格式要求
必须按照如下的JSON dict格式输出：
{format_instructions}
各字段意义如下：
type: str类型，下一步决策，取值范围为：{includes}：{desc}
{query_desc}
reason: str类型，选择当前决策的原因，如果选择decompose，请输出需要分解的子章节描述，看是否全文的写作状态相符
"""

    result = await llm_invoke(llm, [HumanMessage(content=prompt)], json_schema=DecisionItem.model_json_schema())
    if result and result.get("type") in includes:
        return result

    logger.warning("task_decision failed")
    return {"type": "write", "queries": [], "reason": ""}


async def batch_search(queries: list):
    """batch search with queries"""
    searchs = []

    try:
        results = await tavily_search(queries, max_results=3)
        for r in results:
            url = r.get('url', "")
            source = url + '：' + r.get('title', "")
            content = r.get("raw_content", "")
            if url and not content:
                content = await get_content(url)

            if not content:
                content = r.get('content', "")

            logger.debug(f"got {source}:{len(content)} {content[:50]}")
            searchs.append({
                "source": source,
                "content": content,
                "query": queries
            })
    except Exception as e:
        logger.warning(f"web_search failed: {e}")

    return searchs


async def web_search(state: ResearchState, task_node: TaskNode, queries: list, topic: str = ""):
    """search for chapter node"""

    title = task_node["title"]
    logger.debug(f"[Search] {title[:100]} : {queries}")
    searchs = await batch_search(queries)

    references = task_node["references"]
    if not topic:
        topic = state["research_request"]

    new_refs = await add_task_reference(references, searchs, topic)
    task_node["references"] = references + new_refs

    return searchs


async def research_background(state: ResearchState, queries=[]):
    """收集主题背景"""

    root_node = get_root_node(state)

    context = await get_task_reference(
        root_node["references"], state["research_request"]
    )
    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")
    format_instructions = PydanticOutputParser(pydantic_object=SearchItem).get_format_instructions()

    prompt = f"""
当前时间为 {cur_time}
当前有一个洞察需求，要求如下：
{state['research_request']}
请根据已经搜集到的信息理解需求，判断搜集信息是否已经完全覆盖了需要洞察的内容主题，可以支撑编写洞察文章的大纲。
如果搜集到的信息还不够，请列出3-5个还需要进一步获取的内容描述
内容描述需要明确标明具体的从属或约束关系
要求严格参考搜集到的信息

# 已经搜索过的关键词如下
{root_node["queries"]}

# 还需要补充如下内容：
{queries}

# 已经搜集到的相关信息有：
{context}

# 输出格式要求
{format_instructions}
"""

    result = await llm_invoke(llm, [HumanMessage(content=prompt)], pydantic_schema=SearchItem)
    if not result:
        return []

    queries = result.queries
    if queries and isinstance(queries, list):
        queries = queries[:5]
        root_node["queries"].extend(queries)
        await web_search(state, root_node, queries)
        return queries
    else:
        return []


async def initializer_node(state: ResearchState, writer: StreamWriter):
    """初始化：接收用户请求，创建根节点"""

    logger.info(f"start deep research, using model {llm.model_name}")
    writer({"step": "开始洞察"})

    research_request = state["research_request"]
    references = state.get("references", [])

    raw_content = state.get("raw_content", "")
    if raw_content:
        datas = []
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=5000,
            chunk_overlap=500,
            length_function=len,
        )
        chunks = splitter.split_text(raw_content)
        for trunk in chunks:
            datas.append({
                "content": trunk,
                "source": "",
                "query": "",
                "raw_content": True
            })

        new_refs = await add_task_reference(references, datas, research_request)
        references = new_refs + references

    root_id = str(uuid.uuid4())[:8]
    root_node: TaskNode = {
        "id": root_id,
        "title": "全文",
        "description": research_request,
        "status": TaskStatus.PENDING,
        "parent_id": None,
        "children_ids": [],
        "search_loop": 0,
        "depth": 0,
        "context": "",
        "content": "",
        "references": references,
        "queries": state.get("queries", []),
        "important": True,
    }

    return {"root_id": root_id, "task_map": {root_id: root_node}}


def tree_selector_node(state: ResearchState):
    """树遍历选择器，决定下一批要处理的任务"""
    task_map = state["task_map"]
    root_id = state["root_id"]

    state_changed = True
    while state_changed:
        state_changed = False
        for _, node in task_map.items():
            if node["status"] == TaskStatus.IN_PROGRESS:
                children = node["children_ids"]
                if children and all(
                    task_map[cid]["status"] == TaskStatus.COMPLETED for cid in children
                ):
                    node["status"] = TaskStatus.COMPLETED
                    state_changed = True

    if task_map[root_id]["status"] == TaskStatus.COMPLETED:
        return {"current_task_ids": []}

    executable_ids = []
    for node_id, node in task_map.items():
        if node["status"] == TaskStatus.PENDING:
            parent_id = node["parent_id"]
            if (
                parent_id is None
                or task_map[parent_id]["status"] == TaskStatus.IN_PROGRESS
            ):
                executable_ids.append(node_id)

    if not executable_ids:
        logger.warning("当前没有 Pending 任务，可能所有分支都在进行中或已完成。")

    return {"task_map": task_map, "current_task_ids": executable_ids}


async def preprocess_node(state: ResearchState, task_node: TaskNode):
    """process chapter node"""
    if not state.get("preprocess"):
        return

    if task_node["depth"] == 0 or task_node["context"]:
        return

    title = task_node["title"]
    description = task_node["description"]
    logger.info(f"预处理章节：{title}")
    root_node = get_root_node(state)
    topic = f"{title}：{description}"
    context = await get_task_reference(root_node["references"], topic)
    cur_time = datetime.now().strftime("%Y%m%d%H%M%S")
    prompt = f"""
当前时间为 {cur_time}
# 你需要编写一篇文档的章节内容，章节主题为：
{topic}
# 整篇文档的完整要求是：
{state["research_request"]}
# 输出要求
保留详尽的观点、细节和数据、示例
不要遗漏关键概念和数据
必须完全来自于参考内容，不要编造或虚构内容
要求是从参考内容总结相关详细事实，并非生成完整的洞察报告
和章节不相关的内容不用回答，不用推断，直接略过即可
# 参考内容
{context}
"""

    result = await llm_invoke(llm, [HumanMessage(content=prompt)])
    if result:
        task_node["context"] = result


async def processor_node(state: ResearchState, writer: StreamWriter):
    """process chapter"""
    current_ids = state.get("current_task_ids", [])
    if not current_ids:
        return {}

    task_map = state["task_map"]
    sem = asyncio.Semaphore(3)

    async def process_single_task(task_id):
        async with sem:
            current_task = task_map.get(task_id)
            if not current_task:
                return

            title = current_task["title"]
            queries = []
            action = "write"
            if current_task["depth"] == 0:
                action = "decompose"
            else:
                try:
                    await preprocess_node(state, current_task)
                    decision = await task_decision(state, current_task)
                    action = decision["type"]
                    queries = decision["queries"]
                    if queries and isinstance(queries, list):
                        queries = queries[:5]
                    else:
                        queries = []
                except Exception as e:
                    logger.warning(f"Decision failed for {title}: {e}")

            if action == "decompose":
                writer({"step": f"【分解】：{title}"})
                subtasks_data = []
                if current_task["depth"] != 0:
                    subtasks_data = await task_decompose(state, current_task)
                else:
                    # won't here
                    subtasks_data = await task_planner(state)

                if subtasks_data:
                    set_childrens(current_task, subtasks_data, task_map)
                    logger.info(
                        f"🔪 Task '{title}' decomposed into {len(subtasks_data)} subtasks."
                    )
                else:
                    # Fallback to write if decompose returns empty
                    current_task["content"] = await task_write(state, current_task)
                    current_task["status"] = TaskStatus.COMPLETED

            elif action == "search":
                current_task["search_loop"] += 1
                if queries:
                    writer({"step": f"【搜索】：{queries} "})
                    current_task["queries"].extend(queries)
                    await web_search(
                        state, current_task, queries, current_task["description"]
                    )

            else:
                writer({"step": f"【撰写】：{title}"})
                current_task["content"] = await task_write(state, current_task)
                current_task["status"] = TaskStatus.COMPLETED

            if action != "search":
                todo = generate_todo_list(state)
                writer({"text": f"```markdown\n{todo}\n```", "id": state["root_id"]})
                logger.debug(f"\n\n------TODO LIST-----\n{todo}\n\n")

    await asyncio.gather(*[process_single_task(tid) for tid in current_ids])
    return {"task_map": task_map}


async def reporter_node(state: ResearchState, writer: StreamWriter):
    """报告生成"""
    logger.info("📝 Generating final report...")
    task_map = state["task_map"]
    root_id = state["root_id"]
    lines = []

    def traverse_print(node_id):
        node = task_map[node_id]
        if node["depth"] > 0:
            if node["content"]:
                lines.append(node["content"])
            else:
                lines.append(f"{'#' * (node['depth'] + 1)} {node['title']}")
        for child_id in node.get("children_ids", []):
            traverse_print(child_id)

    traverse_print(root_id)
    full_report = "\n".join(lines)

    prompt = f"""
基于用户写作要求生成了一篇文档，但是该文档还没有标题，请为该文档生成一个专业的标题

# 写作要求如下：
{state["research_request"]}

# 文档如下
{full_report}

# 输出要求
仅输出文章标题本身，不超过50个字
不要包含其他内容
"""

    title = await llm_invoke(llm, [HumanMessage(content=prompt)])
    full_report = f"# {title[:50]}\n" + full_report

    logger.debug(f"\n\n{full_report}\n\n")
    filepath = os.path.join(WORKSPACE_DIR, f"{title[:30]}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_report)

    writer({"step": "生成报告", "files": [filepath]})
    return {"deep_report": full_report, "title": title, "report_file": filepath}


def main_router(state: ResearchState):
    """主路由器，决定流程走向"""
    current_ids = state.get("current_task_ids", [])
    if current_ids:
        return "processor"

    root_node = get_root_node(state)
    if root_node["status"] == TaskStatus.COMPLETED:
        return "reporter"

    return "selector"