from __future__ import annotations

from typing import Callable, List

from langchain_core.messages import BaseMessage, HumanMessage

from core.message_utils import stringify_content


IsInternalRetry = Callable[[BaseMessage], bool]


def estimate_tokens(messages: List[BaseMessage]) -> int:
    total_chars = 0
    for message in messages:
        total_chars += len(stringify_content(message.content))
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            total_chars += sum(len(str(tool_call)) for tool_call in tool_calls)
    return total_chars // 2


def should_summarize(messages: List[BaseMessage], *, threshold: int) -> bool:
    return estimate_tokens(messages) > int(threshold or 0)


def choose_summary_boundary(messages: List[BaseMessage], *, keep_last: int) -> int:
    idx = max(0, len(messages) - int(keep_last or 0))
    for scan_idx in range(idx, len(messages)):
        if isinstance(messages[scan_idx], HumanMessage):
            return scan_idx
    return idx


def format_history_for_summary(
    messages: List[BaseMessage],
    *,
    is_internal_retry: IsInternalRetry,
) -> str:
    parts: List[str] = []
    for message in messages:
        if isinstance(message, HumanMessage) and is_internal_retry(message):
            continue
        rendered = stringify_content(message.content)
        suffix = "... [truncated]" if len(rendered) > 500 else ""
        parts.append(f"{message.type}: {rendered[:500]}{suffix}")
    return "\n".join(parts)
