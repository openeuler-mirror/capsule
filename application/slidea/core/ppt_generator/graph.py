from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from core.ppt_generator.state import GenPPTState, PPTInputSchema
from core.ppt_generator.ppt_thought.graph import thought_app
from core.ppt_generator.node import generate_slides_node


ppt_workflow = StateGraph(state_schema=GenPPTState, input_schema=PPTInputSchema)

ppt_workflow.add_node("generate_thought", thought_app)
ppt_workflow.add_node("thought_to_ppt", generate_slides_node)

ppt_workflow.add_edge(START, "generate_thought")
ppt_workflow.add_edge("generate_thought", "thought_to_ppt")
ppt_workflow.add_edge("thought_to_ppt", END)
