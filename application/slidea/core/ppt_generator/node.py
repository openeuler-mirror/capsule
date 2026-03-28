from core.ppt_generator.state import GenPPTState
from core.ppt_generator.thought_to_ppt.graph import generate_slides_app


async def generate_slides_node(state: GenPPTState):
    """ generate ppt by thought and content """
    deep_report = state.get("deep_report", "")
    if deep_report:
        is_markdown_doc = True
        ori_doc = deep_report
    else:
        is_markdown_doc = False
        ori_doc = state["references"]

    ppt_query = f"{state['request']}\nPPT写作思路如下：\n{state['thought']}"
    task_payload = {
        "query": ppt_query,
        "ori_doc": ori_doc,
        "is_markdown_doc": is_markdown_doc
    }

    return await generate_slides_app.ainvoke(task_payload)
