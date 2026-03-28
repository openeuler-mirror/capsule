from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Callable
from typing import Optional

from langchain_core.messages import AIMessageChunk
from langgraph.types import Command

from core.utils.interrupt import InterruptType
from core.utils.logger import logger


JsonObject = dict[str, Any]
EmitEvent = Callable[[str, JsonObject], None]


def _normalize_interrupt_type(value: Any) -> str:
    if isinstance(value, InterruptType):
        return value.value
    if isinstance(value, Enum) and isinstance(value.value, str):
        return value.value
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _extract_resume_value(payload: JsonObject, *, key: str) -> Any:
    value = payload.get(key)
    if value is not None:
        return value
    if key == "selection":
        return payload.get("answer")
    if key == "answer":
        for fallback_key in ("message", "text"):
            fallback = payload.get(fallback_key)
            if fallback is not None:
                return fallback
    return None


def extract_resume_input(payload: JsonObject) -> Any:
    """Extract resume value from invoke payload in a tolerant order."""
    for key in ("selection", "answer", "text", "message"):
        value = _extract_resume_value(payload, key=key)
        if value is not None:
            return value
    return None


def _build_options_from_interrupt(interrupt_info: JsonObject) -> list[JsonObject]:
    option = interrupt_info.get("option")
    if not isinstance(option, dict):
        option = {}
    items = option.get("items")
    if not isinstance(items, list):
        return []

    options: list[JsonObject] = []
    for idx, item in enumerate(items, start=1):
        label = str(item).strip()
        if not label:
            continue
        options.append(
            {
                "id": str(idx),
                "label": label,
                "value": label,
            }
        )
    return options


def _default_input_message(interrupt_info: JsonObject) -> JsonObject:
    content = str(interrupt_info.get("content") or "Please provide input to continue.")
    return {
        "messageId": "langgraph-interrupt",
        "role": "agent",
        "parts": [{"kind": "text", "text": content}],
    }


def prepare_input_required(
    interrupt_info: JsonObject,
    *,
    emit_event: EmitEvent,
) -> JsonObject:
    interrupt_type = _normalize_interrupt_type(interrupt_info.get("type"))
    content = str(interrupt_info.get("content") or "Please provide input to continue.")

    if interrupt_type == InterruptType.SELECT.value:
        options = _build_options_from_interrupt(interrupt_info)
        if options:
            emit_event(
                "interaction.options_requested",  # 使用直接字符串代替常量导入
                {
                    "title": content,
                    "message": "Select one option and continue.",
                    "response_key": "selection",
                    "options": options,
                },
            )
        return {
            "reason": "Ask User to select one option",
            "message": _default_input_message(interrupt_info),
            "schema": {
                "type": "object",
                "properties": {
                    "selection": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "value": {},
                        },
                    }
                },
            },
            "output": {
                "stage": "input_required",
                "interaction": "select",
                "hint": "resume with payload.selection",
            },
        }

    if interrupt_type == InterruptType.EDIT_TEXT.value:
        return {
            "reason": "edit_text",
            "message": _default_input_message(interrupt_info),
            "schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                },
            },
            "output": {
                "stage": "input_required",
                "interaction": "edit_text",
                "hint": "resume with payload.text",
            },
        }

    return {
        "reason": "answer_question",
        "message": _default_input_message(interrupt_info),
        "schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string"},
            },
        },
        "output": {
            "stage": "input_required",
            "interaction": "question",
            "hint": "resume with payload.answer",
        },
    }


def _chunk_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


async def process_event_stream(event_stream: Any, *, emit_event: EmitEvent) -> None:
    """Relay LangGraph stream events to Thinkflow events."""
    async for _, mode, payload in event_stream:
        if mode == "messages":
            message, metadata = payload
            tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
            if isinstance(message, AIMessageChunk) and "user_visible" in tags:
                text = _chunk_to_text(message.content)
                if text:
                    emit_event("output.delta", {"text": text})
        elif mode == "custom" and isinstance(payload, dict):
            step = payload.get("step")
            files = payload.get("files")
            text = payload.get("text")

            if isinstance(step, str) and step:
                emit_event("output.delta", {"text": f"\n>>> 【当前步骤】 {step}\n"})
            if isinstance(text, str) and text:
                emit_event("output.delta", {"text": f"\n>>> {text}\n"})
            if isinstance(files, list) and files:
                file_text = ",".join(str(file) for file in files)
                emit_event("output.delta", {"text": f">>> 生成文件：{file_text}\n"})


async def run_thinkflow_app(
    app: Any,
    graph_input: Optional[JsonObject | Command],
    config: JsonObject,
    *,
    emit_ctx,
) -> JsonObject:
    """
    Run a LangGraph app inside Thinkflow invoke flow.

    Returns:
      - {"stage": "completed", "output": {...}}
      - {"stage": "input_required", "interrupt": {...}}
    """

    event_stream = app.astream(
        graph_input,
        config=config,
        stream_mode=["messages", "custom"],
        subgraphs=True,
    )
    await process_event_stream(event_stream, emit_event=emit_ctx.emit)

    state = await app.aget_state(config)
    if not state.next:
        values = state.values if isinstance(getattr(state, "values", None), dict) else {}
        return {
            "stage": "completed",
            "output": {
                "stage": "completed",
                "report_file": values.get("report_file"),
                "title": values.get("title"),
                "deep_report": values.get("deep_report"),
            },
        }

    if not state.tasks or not state.tasks[0].interrupts:
        return {
            "stage": "completed",
            "output": {"stage": "completed"},
        }

    interrupt_info = state.tasks[0].interrupts[0].value
    if not isinstance(interrupt_info, dict):
        logger.error("invalid interrupt payload: %s", interrupt_info)
        return {
            "stage": "completed",
            "output": {"stage": "failed", "message": "invalid interrupt payload"},
        }

    required = prepare_input_required(interrupt_info, emit_event=emit_ctx.emit)
    emit_ctx.require_input(
        message=required["message"],
        reason=required["reason"],
        schema=required["schema"],
        output=required["output"],
    )

    return {"stage": "input_required"}
