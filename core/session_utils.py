import logging
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

logger = logging.getLogger("agent")
HANDOFF_MARKERS_SKIP_REPAIR = frozenset({"loop_budget_handoff"})


async def repair_session_if_needed(
    agent_app: Any,
    thread_id: str,
    notifier: Callable[[str], None] | None = None,
) -> list[str]:
    notices: list[str] = []

    def _notify(message: str) -> None:
        notices.append(message)
        if notifier:
            notifier(message)

    try:
        config = {"configurable": {"thread_id": thread_id}}
        async_get_state = getattr(agent_app, "aget_state", None)
        if callable(async_get_state):
            current_state = await async_get_state(config)
        else:
            current_state = agent_app.get_state(config)

        if not current_state or not current_state.values:
            return notices

        messages = current_state.values.get("messages", [])
        if not messages:
            return notices

        last_ai_msg = None
        last_ai_idx = -1
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, (AIMessage, AIMessageChunk)) and message.tool_calls:
                last_ai_msg = message
                last_ai_idx = index
                break

        if not last_ai_msg:
            return notices

        existing_tool_outputs = set()
        for index in range(last_ai_idx + 1, len(messages)):
            message = messages[index]
            if isinstance(message, ToolMessage):
                existing_tool_outputs.add(message.tool_call_id)

        missing_tool_calls = []
        for tool_call in last_ai_msg.tool_calls:
            tool_call_id = tool_call.get("id")
            if not tool_call_id:
                logger.warning(
                    "Session repair skipped malformed tool call without id in thread %s",
                    thread_id,
                )
                continue
            if tool_call_id not in existing_tool_outputs:
                missing_tool_calls.append(tool_call)

        if not missing_tool_calls:
            return notices

        # If the run already produced an explicit internal handoff after the
        # dangling tool-call message, do not inject synthetic tool outputs.
        handoff_after_tool_call = False
        for index in range(last_ai_idx + 1, len(messages)):
            message = messages[index]
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue
            metadata = getattr(message, "additional_kwargs", {}) or {}
            internal = metadata.get("agent_internal") if isinstance(metadata, dict) else None
            if isinstance(internal, dict) and str(internal.get("kind") or "") in HANDOFF_MARKERS_SKIP_REPAIR:
                handoff_after_tool_call = True
                break
        if handoff_after_tool_call:
            return notices

        _notify(
            f"Detected {len(missing_tool_calls)} interrupted tool execution(s). Filling gaps automatically."
        )
        tool_messages = [
            ToolMessage(
                tool_call_id=tool_call["id"],
                content="Error: Execution interrupted (system limit reached or user stop). Please retry.",
                name=tool_call["name"],
            )
            for tool_call in missing_tool_calls
        ]

        async_update_state = getattr(agent_app, "aupdate_state", None)
        if callable(async_update_state):
            await async_update_state(config, {"messages": tool_messages}, as_node="tools")
        else:
            agent_app.update_state(config, {"messages": tool_messages}, as_node="tools")

        _notify("History repaired. The restored session is ready for a new request.")
    except Exception as exc:
        logger.debug("Session repair skipped due to error: %s", exc)

    return notices
