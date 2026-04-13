import json

from pydantic_core.core_schema import no_info_wrap_validator_function
from core.utils.logger import logger
from datetime import datetime

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt, StreamWriter
from langchain_core.runnables import RunnableConfig

from core.ppt_generator.ppt_thought.state import ThoughtState, ParseQuery, ResearchMode
from core.utils.llm import ModelRoute, llm_invoke
from core.utils.crawl import get_content
from core.utils.tavily_search import tavily_search
from core.utils.interrupt import InterruptType
from core.utils.cache import run_dir_from_config, load_json, save_json, save_text, load_text
from core.utils.config import settings, app_base_dir
from core.deep_research.graph import research_app


async def get_reference_node(state: ThoughtState, writer: StreamWriter, config: RunnableConfig | None = None):
    """获取参考资料"""
    run_dir = run_dir_from_config(config, str(app_base_dir))
    if run_dir:
        cached = load_text(f"{run_dir}/references/references.txt")
        if cached is not None:
            return {"raw_content": cached}

    contents = ""
    urls = state["parsed_requirements"].urls
    logger.info(f"read urls: {urls}")
    for url in urls:
        writer({"step": f"阅读资料：{url}"})
        contents = contents + await get_content(url)

    if run_dir:
        save_text(f"{run_dir}/references/references.txt", contents)

    return {"raw_content": contents}


async def parse_query_node(state: ThoughtState, config: RunnableConfig | None = None):
    """解析PPT请求"""

    run_dir = run_dir_from_config(config, str(app_base_dir))
    if run_dir:
        cached = load_json(f"{run_dir}/references/parsed_requirements.json")
        logger.info(f"cached parsed_requirements: {cached}")
        if cached:
            parsed = ParseQuery(**cached)
            if not parsed.missing_info:
                return {"parsed_requirements": parsed}

    request = state["request"]
    nowtime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f"parse ppt query: {request}")
    history = "\n".join(state.get("messages", []))
    format_outputs = PydanticOutputParser(pydantic_object=ParseQuery).get_format_instructions()
    prompt = f"""
当前系统时间为：{nowtime}
请从用户提供的PPT制作需求以及和用户的聊天记录中提取以下关键信息：
是否为合理的PPT制作请求；PPT受众； PPT目的；PPT主题；用户提供的文档和网页链接；

# 用户请求
{request}

# 对话历史
{history}

# 输出要求
请严格按照以下JSON格式返回:
{format_outputs} 
""" 

    result = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)], pydantic_schema=ParseQuery)
    logger.info(f"parsed_requirements: {result}")
    if run_dir and result:
        save_json(f"{run_dir}/references/parsed_requirements.json", result.model_dump())
    return {"parsed_requirements": result}


def ask_user_node(state: ThoughtState):
    """ 获取受众/主题/目标等信息 """

    user_input = interrupt({
        "type": InterruptType.QUESTION,
        "content": state["parsed_requirements"].missing_info,
        "option": {
            "timeout": 30
        }
    })

    logger.info(f"got user input: {user_input}")
    messages = state.get("messages", [])
    messages.append(user_input)

    return {
        "interaction_count": state.get("interaction_count", 0) + 1,
        "messages": messages
    }

async def check_research_mode_node(state: ThoughtState):
    """ 检查是否需要进行深入洞察 """

    forced_mode = settings.RESEARCH_MODE_FORCE.strip().lower()
    if state.get("research_mode") != "deep" or forced_mode == "deep":
        return {}

    user_input = interrupt({
        "type": InterruptType.SELECT,
        "content": "是否进行深入洞察？",
        "option": {
            "items": ['是', '否'],
            "default": '否',
            "timeout": 30
        }
    })
    logger.debug(f"user input: {user_input}")
    user_input = user_input.lower()
    if user_input in ["y", "是", "yes"]:
        logger.debug(f"user need deep research")
        return {}

    if user_input in ["n", "否", "no"]:
        logger.debug(f"user don't need deep research")
        return {"research_mode": "simple"}

    prompt = f"""
目前你正在为用户搜集信息，根据用户的请求，你之前向用户询问过是否需要进行深入洞察。
用户的回复是：{user_input}
请根据用户的回复判断是否需要进行深入洞察。
# 返回要求
yes: 用户确认需要进行深入洞察
no: 用户确认不需要进行深入洞察
"""
    need = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)])
    if need.lower() not in ["y", "yes"]:
        logger.info(f"user don't need deep research")
        return {"research_mode": "simple"}
    else:
        return {}


async def gather_content_router_node(state: ThoughtState):
    """ determin how to gather content """
    
    logger.info(f"determing gather content method")

    forced_mode = settings.RESEARCH_MODE_FORCE.strip().lower()
    if not settings.has_tavily_search_config():
        logger.warning("TAVILY_API_KEY not found, skip search.")
        forced_mode = "skip"

    if forced_mode == "skip":
        logger.info("forced_mode=skip")
        return {
            "research_mode": forced_mode,
            "research_request": state.get("request", ""),
            "queries": [],
        }

    if forced_mode in {"simple", "deep"}:
        research_str = f"当前要求research mode必须为：{forced_mode}"
    else:
        research_str = ""

    format_outputs = PydanticOutputParser(pydantic_object=ResearchMode).get_format_instructions()
    nowtime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    history = "\n".join(state.get("messages", []))
    prompt = f"""
当前系统时间为：{nowtime}
请根据用户的PPT写作需求和参考信息，判断如果需要满足用户的写作需求，下一步应该如何搜集信息
{research_str}

# 用户请求
{state["request"]}

# 对话历史
{history}

# 参考资料
{state["raw_content"]}

# 输出要求
请严格按照以下JSON格式返回:
{format_outputs}
"""

    # mode为deep和simple时，都让模型返回queries列表，后续如果用户选择并不洞察，可以在simple模式下直接使用，减少模型调用
    result = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)], pydantic_schema=ResearchMode)
    logger.info(f"research mode: {result}")
    if not result:
        result = ResearchMode(mode="skip", queries=[], research_query='', reason='')

    if forced_mode in {"simple", "deep"}:
        logger.info(f"forced_mode={forced_mode}")
        result.mode = forced_mode

    return {
        "research_mode": result.mode,
        "research_request": result.research_query,
        "queries": result.queries
    }


async def simple_search_node(state: ThoughtState, writer: StreamWriter, config: RunnableConfig | None = None):
    """ tavily search """

    run_dir = run_dir_from_config(config, str(app_base_dir))
    if run_dir:
        cached = load_json(f"{run_dir}/research/research.json")
        if cached is not None:
            return {"search_results": json.dumps(cached, indent=2, ensure_ascii=False)}

    writer({"step": "开始搜索"})
    searchs = await tavily_search(state.get("queries", []))
    if run_dir:
        save_json(f"{run_dir}/research/research.json", searchs)
    return {"search_results": json.dumps(searchs, indent=2, ensure_ascii=False)}


async def deep_research_node(state: ThoughtState, writer: StreamWriter, config: RunnableConfig | None = None):
    run_dir = run_dir_from_config(config, str(app_base_dir))
    if run_dir:
        cached = load_text(f"{run_dir}/research/deep_report.md")
        if cached is not None:
            return {"deep_report": cached, "report_file": f"{run_dir}/research/deep_report.md"}

    nowtime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    request = state["research_request"]
    request = f"当前系统时间为{nowtime}，洞察要求：{request}"
    payload = {
        "research_request": request,
        "raw_content": state.get("raw_content", ""),
    }
    result = await research_app.ainvoke(payload, config=config)
    if run_dir and isinstance(result, dict):
        deep_report = result.get("deep_report") or ""
        if deep_report:
            save_text(f"{run_dir}/research/deep_report.md", deep_report)
    return result or {}


async def generate_thought_node(state: ThoughtState, config: RunnableConfig, writer: StreamWriter):
    """ generate final thought """

    writer({"step": "生成PPT写作思路"})
    history = "\n".join(state.get("messages", []))
    references = state.get("raw_content", "") + state.get("search_results", "") + state.get("deep_report", "")
    requirement = state.get("parsed_requirements")
    prompt = f"""
**根据参考资料和历史信息，基于用户PPT需求，请给出PPT的写作思路。**
# 用户请求
{state["request"]}

# 对话历史
{history}

# PPT受众、目标、主题
P受众信息：{requirement.audience}，演讲目标为：{requirement.goal}，演讲主题为：{requirement.topic}
如果上述目标未明确，请根据参考资料和用户需求猜测合理的PPT受众、目标和主题

# 参考资料如下：
{references[:65536]}

要求：
- 如果参考资料为一篇完整的契合主题的洞察报告，PPT写作思路请参考报告文档结构
- 先根据PPT汇报的对象、目的，思考该PPT的撰写逻辑，然后再进行写作思路撰写。只输出写作思路即可。
- 不要使用"引用资料2"等引用相关资料的引用方式，而是将引用内容无缝衔接到写作思路当中。
- 写作思路要突出PPT受众的关注重点。
- 历史信息的内容只作为额外补充，不能偏离用户的PPT主题。
- 不要输出PPT颜色风格的相关内容。
"""

    run_config = config.copy()
    run_config["tags"] = run_config.get("tags", []) + ["user_visible"]
    thought = await llm_invoke(ModelRoute.DEFAULT, [HumanMessage(content=prompt)], config=run_config)

    run_dir = run_dir_from_config(config, str(app_base_dir))
    if run_dir:
        save_text(f"{run_dir}/thought/thought.md", thought or "")
        save_text(f"{run_dir}/references/references_all.txt", references or "")

    return {"thought": thought, "references": references}
