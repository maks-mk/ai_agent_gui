from __future__ import annotations

from typing import Callable, List

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage


IsInternalRetry = Callable[[BaseMessage], bool]
_PREVIOUS_TURN_LOOKBACK_MESSAGES = 20


class MessageContextHelper:
    """History-derived context helpers with no phrase or keyword lexicon dependency."""

    def non_internal_human_indexes(
        self,
        messages: List[BaseMessage],
        is_internal_retry: IsInternalRetry,
    ) -> List[int]:
        indexes: List[int] = []
        for idx, message in enumerate(messages):
            if isinstance(message, HumanMessage) and not is_internal_retry(message):
                indexes.append(idx)
        return indexes

    def recent_tool_context_names(
        self,
        messages: List[BaseMessage],
        is_internal_retry: IsInternalRetry,
    ) -> List[str]:
        human_indexes = self.non_internal_human_indexes(messages, is_internal_retry)
        if len(human_indexes) < 2:
            return []

        start_idx = human_indexes[-2] + 1
        end_idx = human_indexes[-1]
        names: List[str] = []
        for message in messages[start_idx:end_idx]:
            if isinstance(message, (AIMessage, AIMessageChunk)):
                for tool_call in getattr(message, "tool_calls", []) or []:
                    tool_name = str(tool_call.get("name") or "").strip()
                    if tool_name and tool_name not in names:
                        names.append(tool_name)
            elif isinstance(message, ToolMessage):
                tool_name = str(message.name or "").strip()
                if tool_name and tool_name not in names:
                    names.append(tool_name)
        return names

    def current_turn_has_tool_evidence(
        self,
        messages: List[BaseMessage],
        is_internal_retry: IsInternalRetry,
    ) -> bool:
        human_indexes = self.non_internal_human_indexes(messages, is_internal_retry)
        if not human_indexes:
            return False
        for message in messages[human_indexes[-1] + 1 :]:
            if isinstance(message, ToolMessage):
                return True
        return False

    def had_tool_activity_in_previous_turn(
        self,
        messages: List[BaseMessage],
        current_turn_id: int,
        is_internal_retry: IsInternalRetry,
    ) -> bool:
        previous_turn_id = max(0, current_turn_id - 1)
        if previous_turn_id <= 0:
            return False

        last_human_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if isinstance(message, HumanMessage) and not is_internal_retry(message):
                last_human_idx = idx
                break

        if last_human_idx <= 0:
            return False

        # Keep a bounded lookback window so previous-turn detection stays cheap.
        lookback_start = max(0, last_human_idx - _PREVIOUS_TURN_LOOKBACK_MESSAGES)
        for message in messages[lookback_start:last_human_idx]:
            if isinstance(message, ToolMessage):
                return True
            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                return True
        return False
