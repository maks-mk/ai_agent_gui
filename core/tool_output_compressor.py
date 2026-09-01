"""
Semantic compression and bounded reduction for verbose tool outputs.

File-oriented tools are intentionally excluded because their content must stay
exact for subsequent edit operations. Headroom is optional; deterministic
reducers remain available when it is disabled, unavailable, or returns a
passthrough result.

MCP tool outputs are compressed through headroom's dedicated MCP integration
(``HeadroomMCPCompressor``), which is profile-aware and preserves error items
inside JSON payloads. MCP tools are identified by their tool metadata
(``source == "mcp"``) instead of a name allowlist, so no extra per-server
option in ``mcp.json`` is required.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from core.utils import truncate_output

logger = logging.getLogger(__name__)

COMPRESSIBLE_TOOL_NAMES = frozenset(
    {
        "cli_exec",
        "batch_web_search",
        "fetch_content",
        "crawl_site",
        "list_files",
    }
)

_MIN_COMPRESSION_CHARS = 2000
_CCR_MARKER_RE = re.compile(r"<<ccr:[^>]+>>")
_DIAGNOSTIC_LINE_RE = re.compile(
    r"(?i)\b(error|failed|failure|fatal|warning|warn|traceback|exception|timeout|denied)\b"
)
_SECTION_PREFIXES = {
    "batch_web_search": "Query: ",
    "fetch_content": "=== SOURCE: ",
    "crawl_site": "=== PAGE: ",
}


class ToolOutputCompressor:
    """Lazily-initialised SmartCrusher wrapper with deterministic fallbacks."""

    def __init__(self, enabled: bool, min_chars: int = _MIN_COMPRESSION_CHARS) -> None:
        self._enabled = enabled
        self._min_chars = min_chars
        self._crusher: Any = None
        self._mcp_compressor: Any = None
        self._crusher_init_attempted = False
        self._mcp_init_attempted = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _get_crusher(self) -> Any:
        if self._crusher is not None:
            return self._crusher
        if self._crusher_init_attempted:
            return None
        self._crusher_init_attempted = True
        try:
            from headroom import SmartCrusher

            self._crusher = SmartCrusher()
        except Exception:
            logger.debug("headroom unavailable, tool output compression disabled", exc_info=True)
            return None
        return self._crusher

    def _get_mcp_compressor(self) -> Any:
        if self._mcp_compressor is not None:
            return self._mcp_compressor
        if self._mcp_init_attempted:
            return None
        self._mcp_init_attempted = True
        try:
            from headroom.integrations.mcp.server import HeadroomMCPCompressor

            self._mcp_compressor = HeadroomMCPCompressor()
        except Exception:
            logger.debug("headroom MCP integration unavailable, MCP compression disabled", exc_info=True)
            return None
        return self._mcp_compressor

    def compress(
        self,
        *,
        content: str,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        limit: int,
        is_mcp: bool = False,
    ) -> Optional[str]:
        """Return compressed content, or None when compression is not applicable."""
        if not self._enabled:
            return None
        if not content or len(content) <= max(limit, self._min_chars):
            return None
        if is_mcp:
            return self._compress_mcp(content=content, tool_name=tool_name, tool_args=tool_args)
        if tool_name not in COMPRESSIBLE_TOOL_NAMES:
            return None

        crusher = self._get_crusher()
        if crusher is None:
            return None

        query = self._build_query(tool_name, tool_args)
        try:
            result = crusher.crush(content, query=query)
        except Exception:
            logger.debug("headroom crush failed for tool %s", tool_name, exc_info=True)
            return None

        compressed = getattr(result, "compressed", None)
        if not isinstance(compressed, str) or not compressed.strip():
            return None
        if len(compressed) >= len(content):
            return None

        original_length = len(content)
        header = (
            f"[COMPRESSED by headroom | {original_length} -> {len(compressed)} chars | "
            f"tool={tool_name}]\n"
        )
        return header + compressed

    def _compress_mcp(
        self,
        *,
        content: str,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Compress MCP tool output via headroom's dedicated MCP integration."""
        if not content:
            return None
        compressor = self._get_mcp_compressor()
        if compressor is None:
            return None

        query = self._build_query(tool_name, tool_args)
        try:
            result = compressor.compress(
                content=content,
                tool_name=tool_name,
                tool_args=tool_args if isinstance(tool_args, dict) else None,
                user_query=query,
            )
        except Exception:
            logger.debug("headroom MCP compression failed for tool %s", tool_name, exc_info=True)
            return None

        compressed = getattr(result, "compressed_content", None)
        if not isinstance(compressed, str) or not compressed.strip():
            return None
        if not getattr(result, "was_compressed", False):
            return None
        # CCR markers require Headroom's retrieval tool, which this runtime does not expose.
        if _CCR_MARKER_RE.search(compressed):
            logger.debug("headroom MCP returned an unresolved CCR marker for tool %s", tool_name)
            return None
        if len(compressed) >= len(content):
            return None

        original_length = len(content)
        header = (
            f"[COMPRESSED by headroom MCP | {original_length} -> {len(compressed)} chars | "
            f"tool={tool_name}]\n"
        )
        return header + compressed

    def reduce_to_limit(
        self,
        *,
        content: str,
        tool_name: str,
        limit: int,
        is_mcp: bool = False,
    ) -> str:
        """Fit output while preserving coverage of structured sections when possible."""
        if not content or len(content) <= limit:
            return content
        if limit <= 0:
            return truncate_output(content, limit, source=tool_name)

        prefix = _SECTION_PREFIXES.get(tool_name)
        if prefix:
            reduced = self._reduce_sections(content, prefix=prefix, limit=limit, tool_name=tool_name)
            if reduced is not None:
                return reduced
        if tool_name == "cli_exec":
            return self._reduce_cli(content, limit=limit)
        if is_mcp:
            return self._middle_fit(content, limit)
        return truncate_output(content, limit, source=tool_name)

    @staticmethod
    def _middle_fit(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 0:
            return ""
        omitted = len(text) - limit
        while True:
            marker = f"\n... [OMITTED {omitted} chars] ...\n"
            if len(marker) >= limit:
                return text[:limit]
            kept = limit - len(marker)
            actual_omitted = len(text) - kept
            if actual_omitted == omitted:
                break
            omitted = actual_omitted
        head = (kept + 1) // 2
        tail = kept - head
        return text[:head] + marker + (text[-tail:] if tail else "")

    @classmethod
    def _reduce_sections(cls, content: str, *, prefix: str, limit: int, tool_name: str) -> Optional[str]:
        starts = [match.start() for match in re.finditer(rf"(?m)^{re.escape(prefix)}", content)]
        if not starts:
            return None

        preamble = content[: starts[0]].strip()
        sections = [
            content[start : starts[index + 1] if index + 1 < len(starts) else len(content)].strip()
            for index, start in enumerate(starts)
        ]
        marker = (
            f"[REDUCED preserving {len(sections)} sections | source={tool_name}]\n"
            f"... [TRUNCATED from {len(content)} chars | source={tool_name}]"
        )
        separator = "\n\n"
        preamble_budget = min(len(preamble), max(0, limit // 12)) if preamble else 0
        rendered_count = 1 + len(sections) + (1 if preamble_budget else 0)
        fixed = len(marker) + len(separator) * max(0, rendered_count - 1)
        if fixed >= limit:
            return truncate_output(content, limit, source=tool_name)
        available = limit - fixed
        if preamble_budget:
            preamble_budget = min(preamble_budget, available)
            available -= preamble_budget
        per_section = available // max(1, len(sections))
        remainder = available % max(1, len(sections))

        rendered = [marker]
        if preamble_budget:
            rendered.append(cls._middle_fit(preamble, preamble_budget))
        rendered.extend(
            cls._middle_fit(section, per_section + (1 if index < remainder else 0))
            for index, section in enumerate(sections)
        )
        return separator.join(rendered)

    @classmethod
    def _reduce_cli(cls, content: str, *, limit: int) -> str:
        lines = content.splitlines()
        diagnostics = []
        seen = set()
        for line in lines:
            stripped = line.strip()
            if stripped and _DIAGNOSTIC_LINE_RE.search(stripped) and stripped not in seen:
                seen.add(stripped)
                diagnostics.append(stripped)

        marker = (
            f"[REDUCED shell output | {len(content)} chars | head+diagnostics+tail]\n"
            f"... [TRUNCATED from {len(content)} chars | source=cli_exec]"
        )
        diagnostics_text = "\n".join(diagnostics)
        diagnostics_label = "[diagnostics]\n" if diagnostics_text else ""
        tail_label = "[tail]\n"
        separator_count = 3 if diagnostics_text else 2
        fixed = len(marker) + len(diagnostics_label) + len(tail_label) + separator_count
        if fixed >= limit:
            return truncate_output(content, limit, source="cli_exec")
        available = limit - fixed
        diagnostics_budget = min(len(diagnostics_text), max(0, available // 5))
        remaining = max(0, available - diagnostics_budget)
        head_budget = remaining // 2
        tail_budget = remaining - head_budget

        parts = [marker, content[:head_budget]]
        if diagnostics_budget:
            parts.append(diagnostics_label + cls._middle_fit(diagnostics_text, diagnostics_budget))
        if tail_budget:
            parts.append(tail_label + content[-tail_budget:])
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _build_query(tool_name: str, tool_args: Optional[Dict[str, Any]]) -> str:
        if not tool_args:
            return tool_name
        parts: list[str] = []
        for key in (
            "query",
            "queries",
            "command",
            "path",
            "url",
            "urls",
            "pattern",
            "instructions",
            "select_paths",
            "select_domains",
        ):
            value = tool_args.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, (list, tuple)):
                rendered = " ".join(str(item).strip() for item in value if str(item).strip())
                if rendered:
                    parts.append(rendered)
        query = " ".join(parts) if parts else tool_name
        return query[:4000]
