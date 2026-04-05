import re
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict

try:
    import psutil
except ImportError:
    psutil = None

from core.errors import ErrorType, format_error

_PID_RE = re.compile(r"PID[:\s]+(\d+)")
_FILE_ARG_NAMES = ("path", "file_path", "dir_path")


class ValidationContext(TypedDict, total=False):
    tool_name: str
    args: Dict[str, Any]


def _extract_path(args: Dict[str, Any]) -> Optional[str]:
    for name in _FILE_ARG_NAMES:
        value = args.get(name)
        if value:
            return str(value)
    return None


def _resolve_workspace_path(path: str) -> Path:
    try:
        from tools.filesystem import fs_manager

        return fs_manager._resolve_path(path)
    except Exception:
        return Path(path).resolve()


def validate(result: str, context: ValidationContext) -> Optional[str]:
    """Validates the side effects of tool execution."""
    tool_name = str(context.get("tool_name") or "").strip()
    args = context.get("args") or {}
    if result.startswith("ERROR"):
        return None

    try:
        if tool_name in ("write_file", "edit_file"):
            path = _extract_path(args)
            if not path:
                return None

            target = _resolve_workspace_path(path)
            if not target.exists():
                return format_error(ErrorType.VALIDATION, f"File {path} not found or was not created.")
            if tool_name == "write_file" and target.stat().st_size == 0 and args.get("content"):
                return format_error(ErrorType.VALIDATION, f"File {path} is empty after write.")

        elif tool_name in ("safe_delete_file", "safe_delete_directory"):
            path = _extract_path(args)
            if path and _resolve_workspace_path(path).exists():
                target_type = "File" if "file" in tool_name else "Directory"
                return format_error(ErrorType.VALIDATION, f"{target_type} {path} still exists after deletion.")

        elif tool_name == "run_background_process":
            match = _PID_RE.search(result)
            if match and psutil is not None and not psutil.pid_exists(int(match.group(1))):
                return format_error(ErrorType.VALIDATION, f"Process PID {match.group(1)} not found immediately after start.")

    except Exception as e:
        return format_error(ErrorType.VALIDATION, f"Validation check failed: {e}")

    return None


def validate_tool_result(tool_name: str, args: Dict[str, Any], result: str) -> Optional[str]:
    return validate(result, {"tool_name": tool_name, "args": args})
