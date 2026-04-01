from typing import Any

from langchain_core.messages import ToolMessage


ERROR_PREFIXES = ("error", "ошибка", "error[")


def stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def compact_text(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 15] + "... [truncated]"


def is_error_text(text: Any) -> bool:
    normalized = stringify_content(text).strip().lower()
    return (
        normalized.startswith(ERROR_PREFIXES)
        or "error[" in normalized
        or "traceback" in normalized
    )


def is_tool_message_error(message: ToolMessage) -> bool:
    return getattr(message, "status", "") == "error" or is_error_text(message.content)
