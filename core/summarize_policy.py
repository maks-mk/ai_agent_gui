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


def _soft_summary_margin(threshold: int, *, has_summary: bool) -> int:
    base = max(800, int(threshold * 0.15))
    if has_summary:
        return max(base, int(threshold * 0.35))
    return base


def should_summarize(
    messages: List[BaseMessage],
    *,
    threshold: int,
    keep_last: int,
    has_summary: bool = False,
) -> bool:
    threshold = int(threshold or 0)
    if threshold <= 0:
        return False

    estimated = estimate_tokens(messages)
    if estimated <= threshold:
        return False

    boundary = choose_summary_boundary(messages, keep_last=keep_last)
    summarizable = messages[:boundary]
    if not summarizable:
        return False

    summarizable_human_turns = sum(1 for message in summarizable if isinstance(message, HumanMessage))
    soft_threshold = threshold + _soft_summary_margin(threshold, has_summary=has_summary)
    min_summarizable_messages = max(6, int(keep_last or 0) + 2)

    if estimated < soft_threshold:
        if len(summarizable) < min_summarizable_messages:
            return False
        if summarizable_human_turns < 2:
            return False

    return True


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
