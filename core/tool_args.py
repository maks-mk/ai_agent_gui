from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def canonicalize_tool_args(raw_args: Any) -> dict[str, Any]:
    """Return canonical tool args from dict-like or JSON-string payloads."""
    parsed, _kind = inspect_tool_args_payload(raw_args)
    return parsed


def inspect_tool_args_payload(raw_args: Any) -> tuple[dict[str, Any], str]:
    """Return canonical args plus the original payload shape for diagnostics."""
    value = raw_args
    for _ in range(2):
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}, "mapping"
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}, "empty_string"
            try:
                value = json.loads(text)
            except Exception:
                return {}, "invalid_json_string"
            if isinstance(value, Mapping):
                return {str(key): item for key, item in value.items()}, "json_string"
            continue
        if value is None:
            return {}, "missing"
        return {}, type(value).__name__
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}, "mapping"
    if value is None:
        return {}, "missing"
    return {}, "json_non_object"
