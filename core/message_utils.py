from typing import Any

from langchain_core.messages import ToolMessage


ERROR_PREFIXES = ("error", "ошибка", "error[")


def _stringify_content_item(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        if "text" in item:
            return str(item.get("text") or "")
        if "refusal" in item:
            return str(item.get("refusal") or "")
        if "content" in item:
            return _stringify_content_item(item.get("content"))
        return ""
    if isinstance(item, list):
        return "".join(_stringify_content_item(part) for part in item)
    return str(item)


def stringify_content(content: Any) -> str:
    return _stringify_content_item(content)


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
