from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage


def extract_tool_args(message: ToolMessage) -> dict[str, Any]:
    metadata = getattr(message, "additional_kwargs", {}) or {}
    if not isinstance(metadata, dict):
        return {}

    candidates: list[Any] = [
        metadata.get("tool_args"),
        metadata.get("args"),
    ]
    tool_call_obj = metadata.get("tool_call")
    if isinstance(tool_call_obj, dict):
        candidates.append(tool_call_obj.get("args"))

    for candidate in candidates:
        if isinstance(candidate, dict):
            return dict(candidate)
        if isinstance(candidate, str):
            raw = candidate.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def extract_tool_duration(message: ToolMessage) -> float | None:
    metadata = getattr(message, "additional_kwargs", {}) or {}
    if not isinstance(metadata, dict):
        return None

    raw = metadata.get("tool_duration_seconds")
    if raw is None:
        return None

    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None
