import difflib
import json
import logging
from pathlib import Path

from core.errors import ErrorType, format_error

logger = logging.getLogger(__name__)


def edit_text_file(target: Path, path_label: str, old_string: str, new_string: str) -> str:
    try:
        if not target.exists():
            return format_error(ErrorType.NOT_FOUND, f"File '{path_label}' not found.")

        content = target.read_text(encoding="utf-8")
        original_newline = "\r\n" if "\r\n" in content else "\n"
        content_norm = content.replace("\r\n", "\n")
        old_string_norm = old_string.replace("\r\n", "\n")
        new_string_norm = new_string.replace("\r\n", "\n")

        count = content_norm.count(old_string_norm)
        if count == 1:
            new_content = content_norm.replace(old_string_norm, new_string_norm, 1)
        elif count > 1:
            return format_error(
                ErrorType.VALIDATION,
                f"Found {count} identical occurrences of the exact target text. "
                "Please provide more context (e.g., surrounding lines) to uniquely identify the block.",
            )
        else:
            file_lines = content_norm.split("\n")
            search_lines = old_string_norm.split("\n")

            while search_lines and not search_lines[0].strip():
                search_lines.pop(0)
            while search_lines and not search_lines[-1].strip():
                search_lines.pop()

            if not search_lines:
                return format_error(ErrorType.VALIDATION, "old_string is empty or only contains whitespaces.")

            search_len = len(search_lines)

            def find_matches(normalize_line):
                search_norm = [normalize_line(line) for line in search_lines]
                file_norm = [normalize_line(line) for line in file_lines]
                return [
                    index
                    for index in range(len(file_lines) - search_len + 1)
                    if file_norm[index:index + search_len] == search_norm
                ]

            match_mode = "trim"
            matches = find_matches(lambda value: value.strip())
            if not matches and search_len >= 3:
                match_mode = "aggressive"
                matches = find_matches(lambda value: "".join(value.split()))
            elif not matches and search_len < 3:
                snippet = old_string_norm[:80].replace("\n", "\\n")
                return format_error(
                    ErrorType.VALIDATION,
                    f"Could not find an exact/indentation-safe match for short old_string snippet '{snippet}...'. "
                    "For 1-2 line replacements, provide exact text or include more surrounding lines.",
                )

            if len(matches) == 0:
                snippet = old_string_norm[:50].replace("\n", "\\n")
                return format_error(
                    ErrorType.VALIDATION,
                    f"Could not find a match for 'old_string'.\n"
                    f"Snippet: '{snippet}...'.\n"
                    "Make sure you are replacing existing lines and DID NOT include line numbers in old_string.",
                )
            if len(matches) > 1:
                return format_error(
                    ErrorType.VALIDATION,
                    f"Found {len(matches)} occurrences of the target block. "
                    "Please include more surrounding lines to uniquely identify the block.",
                )

            match_idx = matches[0]
            if match_mode == "aggressive":
                logger.warning("edit_file: aggressive whitespace-insensitive match used for '%s'.", path_label)
            new_file_lines = (
                file_lines[:match_idx]
                + new_string_norm.split("\n")
                + file_lines[match_idx + len(search_lines):]
            )
            new_content = "\n".join(new_file_lines)

        final_content = new_content.replace("\n", original_newline)
        if target.suffix.lower() == ".json":
            try:
                json.loads(final_content)
            except json.JSONDecodeError as exc:
                return format_error(
                    ErrorType.VALIDATION,
                    f"Edit would produce invalid JSON in '{path_label}' (line {exc.lineno}, column {exc.colno}): {exc.msg}",
                )

        with open(target, "w", encoding="utf-8", newline="") as file_obj:
            file_obj.write(final_content)

        diff = difflib.unified_diff(
            content_norm.splitlines(),
            new_content.splitlines(),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            lineterm="",
        )
        diff_text = "\n".join(diff)
        return f"Success: File edited.\n\nDiff:\n```diff\n{diff_text}\n```"
    except Exception as exc:
        return format_error(ErrorType.EXECUTION, f"Error editing file: {exc}")
