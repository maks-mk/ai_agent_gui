import uuid
import asyncio
import logging
import time
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.types import interrupt

from core.state import AgentState
from core.config import AgentConfig
from core import constants
from core.run_logger import JsonlRunLogger
from core.tool_policy import ToolMetadata, default_tool_metadata
from core.tool_results import parse_tool_execution_result
from core.validation import validate_tool_result
from core.utils import truncate_output
from core.errors import format_error, ErrorType
from core.message_utils import compact_text, is_error_text, stringify_content
from core.text_utils import format_exception_friendly


class ProviderContextError(RuntimeError):
    """Raised when the agent context violates provider message-ordering constraints."""
    pass

logger = logging.getLogger("agent")


class AgentNodes:
    __slots__ = (
        "config",
        "llm",
        "tools",
        "llm_with_tools",
        "tools_map",
        "tool_metadata",
        "run_logger",
        "_cached_base_prompt",
    )

    # Only these tools are allowed to run in parallel in a single tool-call batch.
    # Any unknown or mutating tool keeps sequential execution for safety.
    PARALLEL_SAFE_TOOL_NAMES = frozenset(
        {
            "file_info",
            "read_file",
            "list_directory",
            "search_in_file",
            "search_in_directory",
            "tail_file",
            "find_file",
            "web_search",
            "fetch_content",
            "batch_web_search",
            "get_public_ip",
            "lookup_ip_info",
            "get_system_info",
            "get_local_network_info",
            "find_process_by_port",
        }
    )
    # Read-only tools can be called repeatedly while an agent verifies edits/results.
    READ_ONLY_LOOP_TOLERANT_TOOL_NAMES = frozenset(
        {
            "file_info",
            "read_file",
            "search_in_file",
            "search_in_directory",
            "tail_file",
            "find_file",
            "list_directory",
            "web_search",
            "fetch_content",
            "batch_web_search",
        }
    )
    # Planning/reasoning tools are helpful, but can easily create oscillation when
    # the model keeps "thinking" instead of switching to concrete actions.
    PLANNING_TOOL_NAMES = frozenset({"sequentialthinking", "sequential-thinking", "sequential_thinking"})
    PLANNING_TOOL_MAX_CALLS_PER_TURN = 2

    def __init__(
        self,
        config: AgentConfig,
        llm: BaseChatModel,
        tools: List[BaseTool],
        llm_with_tools: Optional[BaseChatModel] = None,
        tool_metadata: Optional[Dict[str, ToolMetadata]] = None,
        run_logger: Optional[JsonlRunLogger] = None,
    ):
        self.config = config
        self.llm = llm
        self.tools = tools
        self.llm_with_tools = llm_with_tools or llm

        # Оптимизация: O(1) доступ к инструментам вместо O(N) перебора списка
        self.tools_map = {t.name: t for t in tools}
        self.tool_metadata = tool_metadata or {}
        self.run_logger = run_logger

        # Оптимизация: кэширование базового промпта (чтобы не читать с диска на каждый шаг)
        self._cached_base_prompt: Optional[str] = None

    def _log_run_event(self, state: AgentState | None, event_type: str, **payload: Any) -> None:
        if not self.run_logger:
            return
        # Guard against accidental duplicate keyword with the positional session_id
        # argument of JsonlRunLogger.log_event(...).
        payload.pop("session_id", None)
        session_id = None if state is None else state.get("session_id")
        self.run_logger.log_event(session_id, event_type, **payload)

    def _state_log_context(self, state: AgentState | None) -> Dict[str, Any]:
        if not state:
            return {}
        return {
            "run_id": state.get("run_id", ""),
            "step": state.get("steps", 0),
            "turn_id": state.get("turn_id", 0),
            "state_session_id": state.get("session_id", ""),
        }

    def _log_node_start(self, state: AgentState | None, node: str, **payload: Any) -> float:
        event_payload: Dict[str, Any] = {"node": node, **self._state_log_context(state)}
        event_payload.update(payload)
        self._log_run_event(state, "node_start", **event_payload)
        return time.perf_counter()

    def _log_node_end(self, state: AgentState | None, node: str, started_at: float, **payload: Any) -> None:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        event_payload: Dict[str, Any] = {
            "node": node,
            "duration_ms": duration_ms,
            **self._state_log_context(state),
        }
        event_payload.update(payload)
        self._log_run_event(state, "node_end", **event_payload)

    def _log_node_error(
        self,
        state: AgentState | None,
        node: str,
        started_at: float,
        error: Exception,
        **payload: Any,
    ) -> None:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        event_payload: Dict[str, Any] = {
            "node": node,
            "duration_ms": duration_ms,
            "error_type": type(error).__name__,
            "error": compact_text(str(error), 400),
            **self._state_log_context(state),
        }
        event_payload.update(payload)
        self._log_run_event(state, "node_error", **event_payload)

    def _metadata_for_tool(self, tool_name: str) -> ToolMetadata:
        return self.tool_metadata.get(tool_name, default_tool_metadata(tool_name))

    def _normalize_tool_name(self, tool_name: str) -> str:
        return str(tool_name or "").strip().lower()

    def _is_planning_tool(self, tool_name: str) -> bool:
        normalized = self._normalize_tool_name(tool_name)
        condensed = normalized.replace("-", "").replace("_", "")
        return normalized in self.PLANNING_TOOL_NAMES or condensed == "sequentialthinking"

    def _required_tool_fields(self, tool_name: str) -> List[str]:
        tool = self.tools_map.get(tool_name)
        if not tool:
            return []
        try:
            schema = tool.get_input_schema()
        except Exception:
            return []

        fields = getattr(schema, "model_fields", {}) or {}
        required: List[str] = []
        for field_name, field_info in fields.items():
            try:
                if field_info.is_required():
                    required.append(str(field_name))
            except Exception:
                continue
        return required

    def _missing_required_tool_fields(self, tool_name: str, tool_args: Dict[str, Any]) -> List[str]:
        required = self._required_tool_fields(tool_name)
        if not required:
            return []
        missing: List[str] = []
        for field_name in required:
            value = tool_args.get(field_name)
            if value is None:
                missing.append(field_name)
                continue
            if isinstance(value, str) and not value.strip():
                missing.append(field_name)
        return missing

    def _tool_is_read_only(self, tool_name: str) -> bool:
        metadata = self._metadata_for_tool(tool_name)
        return metadata.read_only and not metadata.mutating and not metadata.destructive

    def _tool_requires_approval(self, tool_name: str) -> bool:
        if not self.config.enable_approvals:
            return False
        metadata = self._metadata_for_tool(tool_name)
        return metadata.requires_approval or metadata.destructive or metadata.mutating

    def tool_calls_require_approval(self, tool_calls: List[Dict[str, Any]]) -> bool:
        return any(self._tool_requires_approval((tool_call.get("name") or "unknown_tool")) for tool_call in tool_calls)

    # --- NODE: SUMMARIZE ---

    def _estimate_tokens(self, messages: List[BaseMessage]) -> int:
        """Грубая оценка токенов входящего контекста: сумма символов / 2.
        Учитывает как текстовый контент, так и аргументы вызовов инструментов."""
        total_chars = 0
        for m in messages:
            # 1. Текстовый контент (строка или мультимодальный список)
            content = m.content
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            total_chars += len(str(content))

            # 2. Вызовы инструментов (JSON аргументы от LLM могут быть огромными)
            if hasattr(m, "tool_calls") and m.tool_calls:
                total_chars += sum(len(str(tc)) for tc in m.tool_calls)

        return total_chars // 2

    async def summarize_node(self, state: AgentState):
        messages = state["messages"]
        summary = state.get("summary", "")

        estimated_tokens = self._estimate_tokens(messages)
        node_timer = self._log_node_start(
            state,
            "summarize",
            message_count=len(messages),
            estimated_tokens=estimated_tokens,
            threshold=self.config.summary_threshold,
            keep_last=self.config.summary_keep_last,
            has_summary=bool(summary),
        )

        if estimated_tokens <= self.config.summary_threshold:
            self._log_node_end(
                state,
                "summarize",
                node_timer,
                outcome="skipped",
                reason="below_threshold",
            )
            return {}

        logger.debug(f"📊 Context size: ~{estimated_tokens} tokens. Summarizing...")

        # Determine cut-off point
        idx = max(0, len(messages) - self.config.summary_keep_last)

        # Try to find a clean break at a HumanMessage
        for scan_idx in range(idx, len(messages)):
            if isinstance(messages[scan_idx], HumanMessage):
                idx = scan_idx
                break

        to_summarize = messages[:idx]

        # ЗАЩИТА: Если последние N сообщений сами по себе весят больше лимита,
        # мы не можем ничего сжать без потери недавнего контекста.
        if not to_summarize:
            logger.warning(
                f"⚠ Context (~{estimated_tokens} tokens) exceeds threshold, "
                "but cannot summarize further without deleting the most recent active messages. "
                "Expanding context dynamically for this turn."
            )
            self._log_node_end(
                state,
                "summarize",
                node_timer,
                outcome="skipped",
                reason="no_summarizable_messages",
            )
            return {}

        history_text = self._format_history_for_summary(to_summarize)

        prompt = constants.SUMMARY_PROMPT_TEMPLATE.format(summary=summary, history_text=history_text)

        try:
            res = await self.llm.ainvoke(prompt)

            delete_msgs = [RemoveMessage(id=m.id) for m in to_summarize if m.id]
            logger.info(f"🧹 Summary: Removed {len(delete_msgs)} messages. Generated new summary.")
            self._log_run_event(
                state,
                "summary_compacted",
                estimated_tokens=estimated_tokens,
                removed_messages=len(delete_msgs),
                summarized_messages=len(to_summarize),
            )
            self._log_node_end(
                state,
                "summarize",
                node_timer,
                outcome="compacted",
                removed_messages=len(delete_msgs),
                summarized_messages=len(to_summarize),
            )

            return {"summary": res.content, "messages": delete_msgs}
        except Exception as e:
            err_str = str(e)
            if "content_filter" in err_str or "Moderation Block" in err_str:
                logger.warning(
                    "🧹 Summarization skipped due to Content Filter (False Positive). Continuing with full history."
                )
            else:
                logger.error(f"Summarization Error: {format_exception_friendly(e)}")
            self._log_node_error(
                state,
                "summarize",
                node_timer,
                e,
                outcome="failed",
                estimated_tokens=estimated_tokens,
            )
            return {}

    def _format_history_for_summary(self, messages: List[BaseMessage]) -> str:
        return "\n".join(
            f"{m.type}: {str(m.content)[:500]}{'... [truncated]' if len(str(m.content)) > 500 else ''}"
            for m in messages
            if not self._is_internal_retry_message(m)
        )

    def _is_internal_retry_message(self, message: BaseMessage) -> bool:
        if not isinstance(message, HumanMessage):
            return False
        metadata = getattr(message, "additional_kwargs", {}) or {}
        internal = metadata.get("agent_internal")
        return isinstance(internal, dict) and internal.get("kind") == "retry_instruction"

    def _current_turn_id(self, state: AgentState, messages: List[BaseMessage]) -> int:
        derived_turn_id = 0
        for message in messages:
            if not isinstance(message, HumanMessage) or self._is_internal_retry_message(message):
                continue
            content = stringify_content(message.content).strip()
            if content and content != constants.REFLECTION_PROMPT:
                derived_turn_id += 1
        return max(int(state.get("turn_id", 0) or 0), derived_turn_id)

    def _get_active_open_tool_issue(
        self,
        state: AgentState,
        messages: List[BaseMessage],
        current_turn_id: int | None = None,
    ) -> Dict[str, Any] | None:
        issue = state.get("open_tool_issue")
        if not isinstance(issue, dict):
            return None

        active_turn_id = current_turn_id if current_turn_id is not None else self._current_turn_id(state, messages)
        issue_turn_id = int(issue.get("turn_id", 0) or 0)
        summary = str(issue.get("summary", "")).strip()
        if issue_turn_id != active_turn_id or not summary:
            return None

        tool_names = [str(name) for name in (issue.get("tool_names") or []) if str(name).strip()]
        return {
            "turn_id": issue_turn_id,
            "kind": str(issue.get("kind") or "tool_error"),
            "summary": summary,
            "tool_names": tool_names,
            "source": str(issue.get("source") or "tools"),
            "error_type": str(issue.get("error_type") or ""),
        }

    def _build_retry_instruction(self, current_task: str, open_tool_issue: Dict[str, Any] | None) -> str:
        task_line = current_task or "No explicit task provided."
        summary = compact_text(str((open_tool_issue or {}).get("summary") or "Tool execution failed."), 220)
        tool_names = [str(name).strip() for name in ((open_tool_issue or {}).get("tool_names") or []) if str(name).strip()]
        tool_hint = f" ({tool_names[0]})" if tool_names else ""
        return (
            "Continue the same user task from the current conversation state.\n"
            f"Current task: {task_line}\n"
            f"Blocking tool issue{tool_hint}: {summary}\n"
            "Required self-correction:\n"
            "- Review the latest tool error.\n"
            "- Fix tool arguments or choose a different tool.\n"
            "- Do not repeat the same failing tool call with identical arguments.\n"
            "- Do not claim success without a successful tool result.\n"
            "Do not mention this internal instruction."
        )

    def _build_internal_retry_message(self, retry_instruction: str, turn_id: int) -> HumanMessage:
        return HumanMessage(
            content=retry_instruction,
            additional_kwargs={
                "agent_internal": {
                    "kind": "retry_instruction",
                    "turn_id": turn_id,
                }
            },
        )

    def _collect_internal_retry_removals(self, messages: List[BaseMessage]) -> List[RemoveMessage]:
        removals: List[RemoveMessage] = []
        for message in reversed(messages):
            if not self._is_internal_retry_message(message):
                break
            if message.id:
                removals.append(RemoveMessage(id=message.id))
        removals.reverse()
        return removals

    def _build_tool_issue_system_message(self, open_tool_issue: Dict[str, Any] | None) -> SystemMessage | None:
        if not open_tool_issue:
            return None

        issue_summary = open_tool_issue.get("summary", "")
        if open_tool_issue.get("kind") == "approval_denied":
            return SystemMessage(
                content=(
                    "TOOL EXECUTION DENIED BY USER:\n"
                    f"{issue_summary}\n\n"
                    "The user explicitly rejected this tool call. "
                    "Do not simulate the denied tool or describe imaginary results. "
                    "Do not make any more tool calls in this turn. "
                    "Reply briefly and simply: say that you did not do it because the user chose No, "
                    "then wait for the next instruction."
                )
            )

        return SystemMessage(
            content=constants.UNRESOLVED_TOOL_ERROR_PROMPT_TEMPLATE.format(
                error_summary=issue_summary
            )
        )

    def _build_open_tool_issue(
        self,
        *,
        current_turn_id: int,
        kind: str,
        summary: str,
        tool_names: List[str],
        source: str,
        error_type: str = "",
    ) -> Dict[str, Any]:
        return {
            "turn_id": current_turn_id,
            "kind": kind,
            "summary": compact_text(summary.strip(), 320),
            "tool_names": [name for name in tool_names if name],
            "source": source,
            "error_type": error_type.strip().upper(),
        }

    def _merge_open_tool_issues(
        self,
        issues: List[Dict[str, Any]],
        current_turn_id: int,
    ) -> Dict[str, Any] | None:
        if not issues:
            return None

        summaries: List[str] = []
        tool_names: List[str] = []
        kind = "tool_error"
        source = "tools"
        error_type = ""
        for issue in issues:
            summary = str(issue.get("summary", "")).strip()
            if summary and summary not in summaries:
                summaries.append(summary)
            for tool_name in issue.get("tool_names") or []:
                tool_name = str(tool_name).strip()
                if tool_name and tool_name not in tool_names:
                    tool_names.append(tool_name)
            issue_error_type = str(issue.get("error_type") or "").strip().upper()
            if issue_error_type and not error_type:
                error_type = issue_error_type
            if issue.get("kind") == "approval_denied":
                kind = "approval_denied"
                source = "approval"

        return self._build_open_tool_issue(
            current_turn_id=current_turn_id,
            kind=kind,
            summary=" | ".join(summaries[:3]),
            tool_names=tool_names,
            source=source,
            error_type=error_type,
        )

    def _build_agent_context(
        self,
        messages: List[BaseMessage],
        summary: str,
        current_task: str,
        tools_available: bool,
        open_tool_issue: Dict[str, Any] | None,
        state: AgentState | None = None,
    ) -> List[BaseMessage]:
        sanitized_messages = self._sanitize_messages_for_model(messages, state=state)
        full_context: List[BaseMessage] = [
            self._build_system_message(summary, tools_available=tools_available)
        ]
        safety_overlay = self._build_safety_overlay(tools_available=tools_available)
        if safety_overlay:
            full_context.append(SystemMessage(content=safety_overlay))
        issue_message = self._build_tool_issue_system_message(open_tool_issue)
        if issue_message:
            full_context.append(issue_message)
        full_context.extend(sanitized_messages)
        return full_context

    def _sanitize_messages_for_model(
        self,
        messages: List[BaseMessage],
        state: AgentState | None = None,
    ) -> List[BaseMessage]:
        sanitized: List[BaseMessage] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                content = stringify_content(message.content).strip()
                if content == constants.REFLECTION_PROMPT:
                    continue
                last_visible = self._get_last_model_visible_message(sanitized)
                if isinstance(last_visible, ToolMessage):
                    # Some OpenAI-compatible providers reject `tool -> user` transitions.
                    # Insert a lightweight assistant bridge before the user turn.
                    sanitized.append(
                        AIMessage(
                            content=(
                                "Acknowledged previous tool output. "
                                "Continuing with the next user instruction."
                            )
                        )
                    )
                    self._log_run_event(
                        state,
                        "provider_role_order_bridge",
                        run_id=None if state is None else state.get("run_id", ""),
                        reason="user_after_tool",
                    )
            sanitized.append(message)
        return sanitized

    def _build_safety_overlay(self, tools_available: bool) -> str:
        if not tools_available:
            return ""
        overlay_lines: List[str] = []
        if self.config.enable_approvals:
            overlay_lines.append(
                "SAFETY POLICY: Mutating or destructive tools may require explicit user approval before execution."
            )
        if self.config.enable_shell_tool:
            overlay_lines.append(
                "SAFETY POLICY: Shell execution is high risk. Prefer safer project-local tools whenever possible."
            )
        return "\n".join(overlay_lines).strip()

    def _build_agent_result(
        self,
        response: AIMessage,
        current_task: str,
        tools_available: bool,
        turn_id: int,
        messages: List[BaseMessage],
        open_tool_issue: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        token_usage_update = {}
        if getattr(response, "usage_metadata", None):
            token_usage_update = {"token_usage": response.usage_metadata}

        has_tool_calls = False
        protocol_error = ""
        outbound_messages: List[BaseMessage] = self._collect_internal_retry_removals(messages)

        if isinstance(response, AIMessage):
            t_calls = list(getattr(response, "tool_calls", []))
            invalid_calls = list(getattr(response, "invalid_tool_calls", []))

            missing_fields = [
                tc for tc in t_calls
                if not tc.get("id") or not tc.get("name")
            ]
            if missing_fields or invalid_calls:
                protocol_error = self._build_tool_protocol_error(missing_fields, invalid_calls)
                response = AIMessage(
                    content=self._merge_protocol_error_into_content(response.content, protocol_error),
                    additional_kwargs=response.additional_kwargs,
                    response_metadata=response.response_metadata,
                    usage_metadata=response.usage_metadata,
                    id=response.id,
                )
                t_calls = []

            has_tool_calls = bool(tools_available and t_calls)
            if has_tool_calls and open_tool_issue and open_tool_issue.get("kind") == "approval_denied":
                response = AIMessage(
                    content="Okay, I did not do that because you chose No. Tell me what you want to do instead.",
                    additional_kwargs=response.additional_kwargs,
                    response_metadata=response.response_metadata,
                    usage_metadata=response.usage_metadata,
                    id=response.id,
                )
                has_tool_calls = False
            elif has_tool_calls and self._open_tool_issue_requires_user_input(open_tool_issue):
                response = AIMessage(
                    content=self._build_tool_issue_handoff_text(open_tool_issue),
                    additional_kwargs=response.additional_kwargs,
                    response_metadata=response.response_metadata,
                    usage_metadata=response.usage_metadata,
                    id=response.id,
                )
                has_tool_calls = False

        outbound_messages.append(response)

        return {
            "messages": outbound_messages,
            "turn_id": turn_id,
            "current_task": current_task,
            "turn_outcome": "run_tools" if has_tool_calls else "",
            "retry_instruction": "",
            "pending_approval": None,
            "open_tool_issue": open_tool_issue,
            "has_protocol_error": bool(protocol_error),
            "last_tool_error": "",
            "last_tool_result": "",
            **token_usage_update,
        }

    def _merge_protocol_error_into_content(self, content: Any, protocol_error: str) -> str:
        base_text = stringify_content(content).strip()
        if not protocol_error:
            return base_text
        if not base_text:
            return protocol_error
        return f"{base_text}\n\n{protocol_error}"

    def _build_tool_protocol_error(
        self,
        missing_fields: List[Dict[str, Any]],
        invalid_calls: List[Dict[str, Any]],
    ) -> str:
        details = []
        if missing_fields:
            details.append(
                f"{len(missing_fields)} tool call(s) were missing required 'name' or 'id' fields"
            )
        if invalid_calls:
            details.append(
                f"{len(invalid_calls)} tool call(s) had invalid arguments and could not be parsed"
            )
        joined = "; ".join(details) if details else "Malformed tool call payload received."
        return (
            "INTERNAL TOOL PROTOCOL ERROR: "
            f"{joined}. Do not invent tool names or IDs. "
            "If tools are still needed, issue a fresh valid tool call."
        )
    
    # --- NODE: AGENT ---

    async def agent_node(self, state: AgentState):
        node_timer = self._log_node_start(
            state,
            "agent",
            message_count=len(state.get("messages") or []),
            has_summary=bool(state.get("summary")),
        )
        messages = state["messages"]
        summary = state.get("summary", "")
        current_task = state.get("current_task") or self._derive_current_task(messages)
        current_turn_id = self._current_turn_id(state, messages)
        open_tool_issue = self._get_active_open_tool_issue(state, messages, current_turn_id)
        self._log_run_event(
            state,
            "agent_node_start",
            run_id=state.get("run_id", ""),
            step=state.get("steps", 0),
            current_task=current_task,
        )

        tools_available = bool(self.tools) and self.config.model_supports_tools
        try:
            full_context = self._build_agent_context(
                messages,
                summary,
                current_task,
                tools_available,
                open_tool_issue,
                state=state,
            )
            self._assert_provider_safe_agent_context(full_context, state)
            response = await self._invoke_llm_with_retry(
                self.llm_with_tools,
                full_context,
                state=state,
                node_name="agent",
            )
            result = self._build_agent_result(
                response,
                current_task,
                tools_available,
                current_turn_id,
                messages,
                open_tool_issue=open_tool_issue,
            )
            tool_calls_count = len(getattr(response, "tool_calls", []) or [])
            self._log_run_event(
                state,
                "agent_node_end",
                run_id=state.get("run_id", ""),
                tool_calls=tool_calls_count,
                content_preview=compact_text(stringify_content(response.content), 240),
            )
            self._log_node_end(
                state,
                "agent",
                node_timer,
                tool_calls=tool_calls_count,
                tools_available=tools_available,
                has_open_tool_issue=bool(open_tool_issue),
            )
            return result
        except Exception as e:
            self._log_node_error(
                state,
                "agent",
                node_timer,
                e,
                tools_available=tools_available,
                has_open_tool_issue=bool(open_tool_issue),
            )
            raise

    # --- NODE: STABILITY GUARD ---

    async def stability_guard_node(self, state: AgentState):
        node_timer = self._log_node_start(
            state,
            "stability_guard",
            message_count=len(state.get("messages") or []),
            has_open_tool_issue=bool(state.get("open_tool_issue")),
        )
        messages = state.get("messages", [])
        current_turn_id = self._current_turn_id(state, messages)
        current_task = (state.get("current_task") or self._derive_current_task(messages)).strip()
        open_tool_issue = self._get_active_open_tool_issue(state, messages, current_turn_id)
        last_ai = self._get_last_ai_message(messages)
        loop_budget_reached = int(state.get("steps", 0) or 0) >= int(self.config.max_loops or 0)
        pending_tool_calls = bool(last_ai and getattr(last_ai, "tool_calls", None))

        retry_turn_id = int(state.get("self_correction_retry_turn_id", 0) or 0)
        retry_count = int(state.get("self_correction_retry_count", 0) or 0)
        if retry_turn_id != current_turn_id:
            retry_count = 0
            retry_turn_id = current_turn_id

        turn_outcome = "finish_turn"
        retry_instruction = ""
        next_retry_count = retry_count
        next_retry_turn_id = retry_turn_id
        next_open_tool_issue = open_tool_issue
        completion_reason = "no_open_tool_issue"
        handoff_message = ""
        drop_trailing_tool_call = False

        try:
            if loop_budget_reached:
                completion_reason = "loop_budget_exhausted"
                next_open_tool_issue = None
                if open_tool_issue:
                    handoff_message = self._build_tool_issue_handoff_text(open_tool_issue)
                elif pending_tool_calls:
                    completion_reason = "loop_budget_exhausted_pending_tool_call"
                    handoff_message = self._build_loop_budget_handoff_text(
                        current_task=current_task,
                        tool_names=[
                            str(tool_call.get("name") or "").strip()
                            for tool_call in (getattr(last_ai, "tool_calls", []) or [])
                            if str(tool_call.get("name") or "").strip()
                        ],
                    )
                    drop_trailing_tool_call = True
            elif not open_tool_issue:
                completion_reason = "no_open_tool_issue"
                next_open_tool_issue = None
            elif self._assistant_handed_off_open_tool_issue(last_ai, open_tool_issue):
                completion_reason = "assistant_handoff"
                next_open_tool_issue = None
            elif self._assistant_requests_user_input(last_ai):
                completion_reason = "assistant_waits_for_user_input"
                next_open_tool_issue = None
            elif self._open_tool_issue_requires_user_input(open_tool_issue):
                completion_reason = "input_required"
                next_open_tool_issue = None
                handoff_message = self._build_tool_issue_handoff_text(open_tool_issue)
            elif retry_count < 1:
                completion_reason = "auto_retry"
                turn_outcome = "retry_agent"
                retry_instruction = self._build_retry_instruction(current_task, open_tool_issue)
                next_retry_count = retry_count + 1
                next_retry_turn_id = current_turn_id
                next_open_tool_issue = open_tool_issue
            else:
                completion_reason = "retry_budget_exhausted"
                next_open_tool_issue = None
                handoff_message = self._build_tool_issue_handoff_text(open_tool_issue)

            outbound_messages: List[BaseMessage] = []
            if drop_trailing_tool_call and last_ai and getattr(last_ai, "tool_calls", None) and getattr(last_ai, "id", None):
                outbound_messages.append(RemoveMessage(id=last_ai.id))
            if (
                turn_outcome == "finish_turn"
                and handoff_message
                and not self._assistant_handed_off_open_tool_issue(last_ai, open_tool_issue)
                and not self._assistant_requests_user_input(last_ai)
            ):
                handoff_metadata: Dict[str, Any] = {}
                if completion_reason.startswith("loop_budget_exhausted"):
                    handoff_metadata = {
                        "agent_internal": {
                            "kind": "loop_budget_handoff",
                            "turn_id": current_turn_id,
                        }
                    }
                outbound_messages.append(
                    AIMessage(
                        content=handoff_message,
                        additional_kwargs=handoff_metadata,
                    )
                )

            self._log_run_event(
                state,
                "stability_guard_verdict",
                run_id=state.get("run_id", ""),
                outcome=turn_outcome,
                reason=completion_reason,
                retry_count_before=retry_count,
                retry_count_after=next_retry_count,
                has_open_tool_issue=bool(open_tool_issue),
                loop_budget_reached=loop_budget_reached,
                had_pending_tool_calls=pending_tool_calls,
            )
            self._log_node_end(
                state,
                "stability_guard",
                node_timer,
                turn_outcome=turn_outcome,
                reason=completion_reason,
                retry_count=next_retry_count,
                has_open_tool_issue=bool(open_tool_issue),
                loop_budget_reached=loop_budget_reached,
                had_pending_tool_calls=pending_tool_calls,
            )
            result: Dict[str, Any] = {
                "turn_id": current_turn_id,
                "current_task": current_task,
                "turn_outcome": turn_outcome,
                "retry_instruction": retry_instruction,
                "open_tool_issue": next_open_tool_issue,
                "has_protocol_error": False,
                "self_correction_retry_count": next_retry_count,
                "self_correction_retry_turn_id": next_retry_turn_id,
            }
            if outbound_messages:
                result["messages"] = outbound_messages
            return result
        except Exception as e:
            self._log_node_error(
                state,
                "stability_guard",
                node_timer,
                e,
                has_open_tool_issue=bool(open_tool_issue),
                retry_count=retry_count,
            )
            raise

    async def prepare_retry_node(self, state: AgentState):
        node_timer = self._log_node_start(
            state,
            "prepare_retry",
            has_retry_instruction=bool((state.get("retry_instruction") or "").strip()),
        )
        retry_instruction = (state.get("retry_instruction") or "").strip()
        if not retry_instruction:
            self._log_node_end(
                state,
                "prepare_retry",
                node_timer,
                outcome="finish_turn",
                reason="empty_retry_instruction",
            )
            return {"turn_outcome": "finish_turn"}

        messages = state.get("messages", [])
        current_turn_id = self._current_turn_id(state, messages)
        retry_message = self._build_internal_retry_message(retry_instruction, current_turn_id)
        self._log_run_event(
            state,
            "retry_prepared",
            run_id=state.get("run_id", ""),
            turn_id=current_turn_id,
            instruction_preview=compact_text(retry_instruction, 240),
        )
        self._log_node_end(
            state,
            "prepare_retry",
            node_timer,
            outcome="retry_prepared",
            turn_id=current_turn_id,
        )
        return {
            "messages": [retry_message],
            "turn_outcome": "",
            "retry_instruction": "",
        }

    async def approval_node(self, state: AgentState):
        node_timer = self._log_node_start(
            state,
            "approval",
            message_count=len(state.get("messages") or []),
        )
        messages = state.get("messages", [])
        if not messages:
            self._log_node_end(
                state,
                "approval",
                node_timer,
                outcome="skipped",
                reason="no_messages",
            )
            return {"pending_approval": None}

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
            self._log_node_end(
                state,
                "approval",
                node_timer,
                outcome="skipped",
                reason="no_protected_tool_calls",
            )
            return {"pending_approval": None}

        protected_calls = []
        for tool_call in last_msg.tool_calls:
            tool_name = tool_call.get("name") or "unknown_tool"
            if not self._tool_requires_approval(tool_name):
                continue
            metadata = self._metadata_for_tool(tool_name)
            protected_calls.append(
                {
                    "id": tool_call.get("id") or "",
                    "name": tool_name,
                    "args": tool_call.get("args") or {},
                    "policy": metadata.to_dict(),
                }
            )

        if not protected_calls:
            self._log_node_end(
                state,
                "approval",
                node_timer,
                outcome="skipped",
                reason="all_tools_readonly",
            )
            return {"pending_approval": None}

        payload = {
            "kind": "tool_approval",
            "message": "Approve protected tool execution?",
            "tools": protected_calls,
            "run_id": state.get("run_id", ""),
            "session_id": state.get("session_id", ""),
        }
        self._log_run_event(
            state,
            "approval_requested",
            run_id=state.get("run_id", ""),
            tool_names=[tool["name"] for tool in protected_calls],
        )
        decision = interrupt(payload)
        approved = self._approval_decision_is_approved(decision)
        approval_state = {
            "approved": approved,
            "decision": decision,
            "tool_call_ids": [tool["id"] for tool in protected_calls if tool["id"]],
            "tool_names": [tool["name"] for tool in protected_calls],
        }
        self._log_run_event(
            state,
            "approval_resolved",
            run_id=state.get("run_id", ""),
            approved=approved,
            tool_names=approval_state["tool_names"],
        )
        self._log_node_end(
            state,
            "approval",
            node_timer,
            outcome="resolved",
            approved=approved,
            protected_count=len(protected_calls),
        )
        return {"pending_approval": approval_state}

    def _approval_decision_is_approved(self, decision: Any) -> bool:
        if isinstance(decision, bool):
            return decision
        if isinstance(decision, dict):
            if "approved" in decision:
                return bool(decision.get("approved"))
            action = str(decision.get("action", "")).strip().lower()
            return action in {"approve", "approved", "yes", "y"}
        return bool(decision)

    # --- NODE: TOOLS ---

    async def tools_node(self, state: AgentState):
        node_timer = self._log_node_start(
            state,
            "tools",
            message_count=len(state.get("messages") or []),
            has_pending_approval=bool(state.get("pending_approval")),
        )
        self._check_invariants(state)

        messages = state["messages"]
        last_msg = messages[-1]
        current_turn_id = self._current_turn_id(state, messages)

        if not isinstance(last_msg, AIMessage) or not last_msg.tool_calls:
            self._log_node_end(
                state,
                "tools",
                node_timer,
                outcome="skipped",
                reason="no_tool_calls",
            )
            return {}

        final_messages: List[ToolMessage] = []
        has_error = False
        last_error = ""
        last_result = ""
        tool_issues: List[Dict[str, Any]] = []
        approval_state = state.get("pending_approval") or {}

        # Оптимизация: собираем историю вызовов один раз, а не для каждого инструмента.
        # ВАЖНО: исключаем последний AI message, чтобы текущий вызов не считался "повтором".
        recent_calls = []
        history_window = self.config.effective_tool_loop_window
        history_slice = messages[-(history_window + 1):-1] if history_window > 0 else messages[:-1]
        for m in reversed(history_slice):
            if isinstance(m, AIMessage) and m.tool_calls:
                recent_calls.extend(m.tool_calls)

        tool_calls = list(last_msg.tool_calls)

        parallel_mode = self._can_parallelize_tool_calls(tool_calls)
        self._log_run_event(
            state,
            "tools_node_start",
            run_id=state.get("run_id", ""),
            tool_call_count=len(tool_calls),
            tool_names=[(tc.get("name") or "unknown_tool") for tc in tool_calls],
            parallel_mode=parallel_mode,
        )
        try:
            if parallel_mode:
                processed = await asyncio.gather(
                    *(
                        self._process_tool_call(tool_call, recent_calls, state, approval_state, current_turn_id)
                        for tool_call in tool_calls
                    )
                )
                for tool_msg, had_error, issue in processed:
                    final_messages.append(tool_msg)
                    has_error = has_error or had_error
                    if issue:
                        tool_issues.append(issue)
                    parsed = parse_tool_execution_result(tool_msg.content)
                    if parsed.ok:
                        last_result = parsed.message
                    else:
                        last_error = parsed.message
            else:
                for tool_call in tool_calls:
                    tool_msg, had_error, issue = await self._process_tool_call(
                        tool_call,
                        recent_calls,
                        state,
                        approval_state,
                        current_turn_id,
                    )
                    final_messages.append(tool_msg)
                    has_error = has_error or had_error
                    if issue:
                        tool_issues.append(issue)
                    parsed = parse_tool_execution_result(tool_msg.content)
                    if parsed.ok:
                        last_result = parsed.message
                    else:
                        last_error = parsed.message

            merged_issue = self._merge_open_tool_issues(tool_issues, current_turn_id)
            self._log_run_event(
                state,
                "tools_node_end",
                run_id=state.get("run_id", ""),
                tool_result_count=len(final_messages),
                has_error=has_error,
                issue_kind="" if not merged_issue else merged_issue.get("kind", ""),
                issue_source="" if not merged_issue else merged_issue.get("source", ""),
            )
            self._log_node_end(
                state,
                "tools",
                node_timer,
                tool_call_count=len(tool_calls),
                tool_result_count=len(final_messages),
                parallel_mode=parallel_mode,
                has_error=has_error,
                has_open_tool_issue=bool(merged_issue),
            )
            return {
                "messages": final_messages,
                "turn_id": current_turn_id,
                "turn_outcome": "run_tools",
                "retry_instruction": "",
                "pending_approval": None,
                "open_tool_issue": merged_issue,
                "has_protocol_error": False,
                "last_tool_error": last_error,
                "last_tool_result": last_result,
            }
        except Exception as e:
            self._log_node_error(
                state,
                "tools",
                node_timer,
                e,
                tool_call_count=len(tool_calls),
                parallel_mode=parallel_mode,
            )
            raise

    def _can_parallelize_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> bool:
        if len(tool_calls) < 2:
            return False

        # Both conditions required: metadata says read-only AND name is on the explicit whitelist.
        # The whitelist acts as a second safety gate — unknown / newly-added tools default to sequential.
        return all(
            self._tool_is_read_only(tc.get("name") or "unknown_tool")
            and (tc.get("name") or "") in self.PARALLEL_SAFE_TOOL_NAMES
            for tc in tool_calls
        )

    async def _process_tool_call(
        self,
        tool_call: Dict[str, Any],
        recent_calls: List[Dict[str, Any]],
        state: AgentState,
        approval_state: Dict[str, Any],
        current_turn_id: int,
    ) -> Tuple[ToolMessage, bool, Dict[str, Any] | None]:
        # Безопасное извлечение с фоллбеками
        t_name = tool_call.get("name") or "unknown_tool"
        t_args = tool_call.get("args") or {}
    
        # Генерируем фейковый ID, если LLM забыла его указать, чтобы Pydantic не упал
        t_id = tool_call.get("id")
        if not t_id:
            t_id = f"call_missing_{uuid.uuid4().hex[:8]}"

        had_error = False
        metadata = self._metadata_for_tool(t_name)

        if self._tool_requires_approval(t_name) and not self._tool_call_is_approved(t_id, approval_state):
            content = format_error(
                ErrorType.ACCESS_DENIED,
                f"Execution of '{t_name}' was cancelled by approval policy.",
            )
            self._log_run_event(
                state,
                "tool_call_denied",
                run_id=state.get("run_id", ""),
                tool_name=t_name,
                tool_args=t_args,
                policy=metadata.to_dict(),
            )
            limit = self.config.safety.max_tool_output
            parsed_result = parse_tool_execution_result(content)
            issue = self._build_open_tool_issue(
                current_turn_id=current_turn_id,
                kind="approval_denied",
                summary=parsed_result.message or content,
                tool_names=[t_name],
                source="approval",
                error_type=parsed_result.error_type,
            )
            return (
                self._build_tool_message(
                    content=truncate_output(content, limit, source=t_name),
                    tool_call_id=t_id,
                    tool_name=t_name,
                    tool_args=t_args,
                ),
                True,
                issue,
            )

        missing_required = self._missing_required_tool_fields(t_name, t_args)
        if missing_required:
            content = format_error(
                ErrorType.VALIDATION,
                f"Missing required field(s): {', '.join(missing_required)}.",
            )
            self._log_run_event(
                state,
                "tool_call_preflight_validation_failed",
                run_id=state.get("run_id", ""),
                tool_name=t_name,
                tool_args=t_args,
                missing_required=missing_required,
            )
            limit = self.config.safety.max_tool_output
            parsed_result = parse_tool_execution_result(content)
            issue = self._build_open_tool_issue(
                current_turn_id=current_turn_id,
                kind="tool_error",
                summary=parsed_result.message or content,
                tool_names=[t_name],
                source="tools",
                error_type=parsed_result.error_type,
            )
            return (
                self._build_tool_message(
                    content=truncate_output(content, limit, source=t_name),
                    tool_call_id=t_id,
                    tool_name=t_name,
                    tool_args=t_args,
                ),
                True,
                issue,
            )

        # Проверка на зацикливание
        loop_count = sum(
            1 for tc in recent_calls if tc.get("name") == t_name and tc.get("args") == t_args
        )
        same_tool_count = sum(
            1 for tc in recent_calls
            if self._normalize_tool_name(tc.get("name") or "") == self._normalize_tool_name(t_name)
        )

        loop_limit = (
            self.config.effective_tool_loop_limit_readonly
            if t_name in self.READ_ONLY_LOOP_TOLERANT_TOOL_NAMES
            else self.config.effective_tool_loop_limit_mutating
        )
        planning_limit_reached = self._is_planning_tool(t_name) and (
            same_tool_count >= self.PLANNING_TOOL_MAX_CALLS_PER_TURN
        )

        if planning_limit_reached:
            content = format_error(
                ErrorType.LOOP_DETECTED,
                (
                    f"Planning tool budget reached for '{t_name}'. "
                    "Stop further planning tool calls in this turn and proceed with concrete action tools."
                ),
            )
            had_error = True
            self._log_run_event(
                state,
                "tool_call_planning_budget_blocked",
                run_id=state.get("run_id", ""),
                tool_name=t_name,
                tool_args=t_args,
                same_tool_count=same_tool_count,
                planning_limit=self.PLANNING_TOOL_MAX_CALLS_PER_TURN,
            )
        elif loop_count >= loop_limit:
            content = format_error(
                ErrorType.LOOP_DETECTED,
                f"Loop detected. You have called '{t_name}' with these exact arguments {loop_limit} times in the recent history. Please try a different approach.",
            )
            had_error = True
            self._log_run_event(
                state,
                "tool_call_loop_blocked",
                run_id=state.get("run_id", ""),
                tool_name=t_name,
                tool_args=t_args,
                loop_count=loop_count,
                loop_limit=loop_limit,
            )
        else:
            self._log_run_event(
                state,
                "tool_call_start",
                run_id=state.get("run_id", ""),
                tool_name=t_name,
                tool_args=t_args,
                policy=metadata.to_dict(),
            )
            content = await self._execute_tool(t_name, t_args, state=state, tool_call_id=t_id)

        # Post-Tool Validation Layer
        validation_error = validate_tool_result(t_name, t_args, content)
        if validation_error:
            content = f"{content}\n\n{validation_error}"
            had_error = True

        if is_error_text(content):
            had_error = True

        limit = self.config.safety.max_tool_output
        content = truncate_output(content, limit, source=t_name)
        parsed_result = parse_tool_execution_result(content)
        self._log_run_event(
            state,
            "tool_call_end",
            run_id=state.get("run_id", ""),
            tool_name=t_name,
            tool_args=t_args,
            result=parsed_result.to_event_payload(),
        )

        issue = None
        if not parsed_result.ok:
            issue_kind = "approval_denied" if parsed_result.error_type == "ACCESS_DENIED" else "tool_error"
            issue_source = "approval" if issue_kind == "approval_denied" else "tools"
            issue = self._build_open_tool_issue(
                current_turn_id=current_turn_id,
                kind=issue_kind,
                summary=parsed_result.message or content,
                tool_names=[t_name],
                source=issue_source,
                error_type=parsed_result.error_type,
            )

        return (
            self._build_tool_message(
                content=content,
                tool_call_id=t_id,
                tool_name=t_name,
                tool_args=t_args,
            ),
            had_error,
            issue,
        )

    def _build_tool_message(
        self,
        *,
        content: str,
        tool_call_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> ToolMessage:
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            additional_kwargs={"tool_args": deepcopy(tool_args) if isinstance(tool_args, dict) else {}},
        )

    def _tool_call_is_approved(self, tool_call_id: str, approval_state: Dict[str, Any]) -> bool:
        if not self.config.enable_approvals:
            return True
        if not approval_state:
            return False
        if not approval_state.get("approved"):
            return False
        approved_ids = set(approval_state.get("tool_call_ids") or [])
        return not approved_ids or tool_call_id in approved_ids

    def _check_invariants(self, state: AgentState):
        if not self.config.debug:
            return
        steps = state.get("steps", 0)
        if steps < 0:
            logger.error(f"INVARIANT VIOLATION: steps ({steps}) < 0")

    def _get_last_model_visible_message(self, context: List[BaseMessage]) -> BaseMessage | None:
        for message in reversed(context):
            if isinstance(message, SystemMessage):
                continue
            return message
        return None

    def _assert_provider_safe_agent_context(
        self,
        context: List[BaseMessage],
        state: AgentState | None = None,
    ) -> None:
        last_visible = self._get_last_model_visible_message(context)
        valid = isinstance(last_visible, (HumanMessage, ToolMessage))
        self._log_run_event(
            state,
            "provider_context_valid",
            run_id=None if state is None else state.get("run_id", ""),
            valid=valid,
            last_visible_type=type(last_visible).__name__ if last_visible else "",
        )
        if valid:
            return

        raise ProviderContextError(
            "Provider-unsafe agent context: the last model-visible message must be HumanMessage or ToolMessage."
        )

    async def _execute_tool(
        self,
        name: str,
        args: dict,
        state: AgentState | None = None,
        tool_call_id: str = "",
    ) -> str:
        # Быстрый поиск за O(1)
        tool = self.tools_map.get(name)
        if not tool:
            self._log_run_event(
                state,
                "tool_call_missing",
                run_id=None if state is None else state.get("run_id", ""),
                tool_name=name,
                tool_args=args,
            )
            return format_error(ErrorType.NOT_FOUND, f"Tool '{name}' not found.")
        try:
            invoke_scope = nullcontext()
            if name == "cli_exec" and tool_call_id:
                try:
                    from tools.local_shell import cli_output_context

                    invoke_scope = cli_output_context(tool_call_id)
                except Exception:
                    invoke_scope = nullcontext()

            with invoke_scope:
                raw_result = await tool.ainvoke(args)
            content = str(raw_result)
            if not content.strip():
                self._log_run_event(
                    state,
                    "tool_call_empty_result",
                    run_id=None if state is None else state.get("run_id", ""),
                    tool_name=name,
                    tool_args=args,
                )
                return format_error(ErrorType.EXECUTION, "Tool returned empty response.")
            return content
        except Exception as e:
            self._log_run_event(
                state,
                "tool_call_exception",
                run_id=None if state is None else state.get("run_id", ""),
                tool_name=name,
                tool_args=args,
                error_type=type(e).__name__,
                error=compact_text(str(e), 400),
            )
            return format_error(ErrorType.EXECUTION, str(e))

    # --- HELPERS ---

    def _derive_current_task(self, messages: List[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage) and not self._is_internal_retry_message(message):
                content = stringify_content(message.content).strip()
                if content and content != constants.REFLECTION_PROMPT:
                    return content
        return ""

    def _open_tool_issue_requires_user_input(self, open_tool_issue: Dict[str, Any] | None) -> bool:
        if not isinstance(open_tool_issue, dict):
            return False
        if str(open_tool_issue.get("kind") or "").strip().lower() != "tool_error":
            return False

        error_type = str(open_tool_issue.get("error_type") or "").strip().upper()
        tool_names = [str(name).strip() for name in (open_tool_issue.get("tool_names") or []) if str(name).strip()]
        only_planning_tools = bool(tool_names) and all(self._is_planning_tool(name) for name in tool_names)
        summary = str(open_tool_issue.get("summary") or "").strip().lower()
        input_required_markers = (
            "укажите путь",
            "укажите правильный путь",
            "предоставьте путь",
            "provide path",
            "specify the path",
            "provide file",
            "предоставьте файл",
        )
        if error_type == "VALIDATION":
            if only_planning_tools:
                return False
            # Validation issues for edit operations (missing old/new string, ambiguous matches, aliases)
            # are usually recoverable by another tool pass and should not force immediate handoff.
            recoverable_validation_markers = (
                "accepted aliases",
                "identical occurrences",
                "exact target text",
                "old_string",
                "new_string",
                "search_text",
                "find_text",
                "replace_text",
                "replacement",
            )
            if any(marker in summary for marker in recoverable_validation_markers):
                return False
            # Missing path/file fields are often model-side argument issues and can be auto-corrected
            # by one guarded retry; do not force immediate handoff on the first failure.
            return any(marker in summary for marker in input_required_markers)

        user_input_markers = (
            "missing required field",
            "required field",
            "accepted aliases",
            *input_required_markers,
        )
        return any(marker in summary for marker in user_input_markers)

    def _build_tool_issue_handoff_text(self, open_tool_issue: Dict[str, Any] | None) -> str:
        summary = ""
        tool_names: List[str] = []
        if isinstance(open_tool_issue, dict):
            summary = compact_text(str(open_tool_issue.get("summary", "")).strip(), 220)
            tool_names = [str(name).strip() for name in (open_tool_issue.get("tool_names") or []) if str(name).strip()]

        tool_hint = f" (`{tool_names[0]}`)" if tool_names else ""
        summary_line = f"\nДетали ошибки: {summary}" if summary else ""
        return (
            f"Не могу продолжить автоматический вызов инструмента{tool_hint}: не хватает корректных входных данных."
            f"{summary_line}\n"
            "Пожалуйста, укажите путь к файлу и недостающие параметры (что искать и на что заменить), "
            "и я продолжу без повторного цикла."
        )

    def _build_loop_budget_handoff_text(self, current_task: str, tool_names: List[str]) -> str:
        tool_hint = f" (последний: `{tool_names[0]}`)" if tool_names else ""
        task_hint = compact_text(current_task.strip(), 180) if current_task else "текущей задачи"
        return (
            f"Остановился по лимиту внутренних шагов до завершения {task_hint}{tool_hint}.\n"
            "Чтобы не терять корректность, я не выполнял новые вызовы инструмента после достижения лимита.\n"
            "Напишите «продолжай» (или уточните следующий конкретный шаг), и я продолжу с текущего состояния."
        )

    def _assistant_handed_off_open_tool_issue(
        self,
        last_ai: AIMessage | None,
        open_tool_issue: Dict[str, Any] | None,
    ) -> bool:
        if not open_tool_issue or not last_ai or getattr(last_ai, "tool_calls", None):
            return False

        content = stringify_content(last_ai.content).strip().lower()
        if not content:
            return False

        if open_tool_issue.get("kind") == "approval_denied":
            return True

        blocker_terms = (
            "не удалось",
            "не найден",
            "не найдено",
            "ошиб",
            "не смог",
            "не получилось",
            "не могу",
            "укажите путь",
            "укажи путь",
            "предоставьте файл",
            "предоставь файл",
            "предоставьте код",
            "предоставь код",
            "cannot",
            "unable",
            "not found",
            "failed",
            "failure",
            "error",
            "blocked",
            "denied",
            "cancelled",
            "provide path",
            "provide the path",
            "specify the path",
            "provide file",
            "provide the file",
            "provide code",
        )
        success_terms = (
            "успеш",
            "готово",
            "сделал",
            "сделано",
            "создан",
            "заверш",
            "completed",
            "success",
            "done",
            "finished",
        )

        if any(term in content for term in blocker_terms):
            return True
        if any(term in content for term in success_terms):
            return False
        return False

    def _assistant_requests_user_input(self, last_ai: AIMessage | None) -> bool:
        if not last_ai or getattr(last_ai, "tool_calls", None):
            return False

        content = stringify_content(last_ai.content).strip().lower()
        if not content:
            return False

        decision_terms = (
            "какой вариант",
            "какой способ",
            "какой из вариантов",
            "выберите",
            "укажите номер",
            "укажите путь",
            "укажите правильный путь",
            "предоставьте путь",
            "предоставьте файл",
            "предоставьте код",
            "подтвердите",
            "как поступить",
            "нужно ваше подтверждение",
            "нужно ваше решение",
            "which option",
            "which approach",
            "choose",
            "select",
            "confirm",
            "please confirm",
            "provide path",
            "provide the path",
            "specify the path",
            "provide file",
            "provide code",
            "let me know your choice",
        )
        waiting_terms = (
            "жду вашего ответа",
            "жду вашего решения",
            "ожидаю вашего ответа",
            "wait for your input",
            "waiting for your input",
            "waiting for your confirmation",
        )

        if any(term in content for term in decision_terms):
            return True
        if any(term in content for term in waiting_terms):
            return True
        return content.endswith("?")

    def _get_last_ai_message(self, messages: List[BaseMessage]) -> Optional[AIMessage]:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                return message
        return None

    def _get_base_prompt(self) -> str:
        """Ленивая загрузка и кэширование промпта для устранения дискового I/O"""
        if self._cached_base_prompt is None:
            prompt_path = self.config.prompt_path.absolute()
            if self.config.prompt_path.exists():
                try:
                    self._cached_base_prompt = self.config.prompt_path.read_text("utf-8")
                    logger.info("Loaded prompt from file: %s", prompt_path)
                except Exception as e:
                    logger.warning(
                        "Failed to read prompt file %s: %s. Using built-in prompt.",
                        prompt_path,
                        e,
                    )
                    self._cached_base_prompt = (
                        "You are an autonomous AI agent.\n"
                        "Reason in English, Reply in Russian.\n"
                        "Date: {{current_date}}"
                    )
            else:
                logger.info("Prompt file not found at %s. Using built-in prompt.", prompt_path)
                self._cached_base_prompt = (
                    "You are an autonomous AI agent.\n"
                    "Reason in English, Reply in Russian.\n"
                    "Date: {{current_date}}"
                )
        return self._cached_base_prompt

    def _build_system_message(self, summary: str, tools_available: bool = True) -> SystemMessage:
        raw_prompt = self._get_base_prompt()

        prompt = raw_prompt.replace("{{current_date}}", datetime.now().strftime("%Y-%m-%d"))
        prompt = prompt.replace("{{cwd}}", str(Path.cwd()))

        if self.config.strict_mode:
            prompt += "\nNOTE: STRICT MODE ENABLED. Be precise. No guessing."

        if not tools_available:
            prompt += "\nNOTE: You are in CHAT-ONLY mode. Tools are disabled."

        if summary:
            prompt += f"\n\n<memory>\n{summary}\n</memory>"

        return SystemMessage(content=prompt)

    async def _invoke_llm_with_retry(
        self,
        llm,
        context: List[BaseMessage],
        state: AgentState | None = None,
        node_name: str = "",
    ) -> AIMessage:
        current_llm = llm
        max_attempts = max(1, self.config.max_retries)
        retry_delay = max(0, self.config.retry_delay)
        self._log_run_event(
            state,
            "llm_invoke_start",
            run_id=None if state is None else state.get("run_id", ""),
            node=node_name,
            max_attempts=max_attempts,
            context_messages=len(context),
        )

        for attempt in range(max_attempts):
            try:
                response = await current_llm.ainvoke(context)
                invalid_calls = getattr(response, "invalid_tool_calls", None)
                if not response.content and not response.tool_calls and not invalid_calls:
                    raise ValueError("Empty response from LLM")
                self._log_run_event(
                    state,
                    "llm_invoke_success",
                    run_id=None if state is None else state.get("run_id", ""),
                    node=node_name,
                    attempt=attempt + 1,
                    has_content=bool(stringify_content(response.content).strip()),
                    tool_calls=len(getattr(response, "tool_calls", []) or []),
                )
                return response
            except Exception as e:
                err_str = str(e)
                if "auto" in err_str and "tool choice" in err_str and "requires" in err_str:
                    logger.warning(
                        "⚠ Server does not support 'auto' tool choice. Falling back to chat-only mode."
                    )
                    current_llm = self.llm
                    # Безопасное копирование контекста
                    context = list(context)
                    if isinstance(context[0], SystemMessage):
                        context[0] = SystemMessage(
                            content=str(context[0].content)
                            + "\n\nWARNING: Tools are disabled due to server configuration error."
                        )
                    continue

                is_fatal = self._is_fatal_llm_error(e)
                logger.warning(f"LLM Error (Attempt {attempt+1}/{max_attempts}): {e}")
                self._log_run_event(
                    state,
                    "llm_retry",
                    node=node_name,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    fatal=is_fatal,
                    error=str(e),
                )

                if is_fatal:
                    logger.error(f"Fatal LLM error detected. Aborting request: {e}")
                    self._log_run_event(
                        state,
                        "llm_invoke_fatal",
                        run_id=None if state is None else state.get("run_id", ""),
                        node=node_name,
                        attempt=attempt + 1,
                        error_type=type(e).__name__,
                        error=compact_text(str(e), 400),
                    )
                    raise

                if attempt == max_attempts - 1:
                    self._log_run_event(
                        state,
                        "llm_invoke_exhausted",
                        run_id=None if state is None else state.get("run_id", ""),
                        node=node_name,
                        attempt=attempt + 1,
                        error_type=type(e).__name__,
                        error=compact_text(str(e), 400),
                    )
                    raise

                await asyncio.sleep(retry_delay)

        raise RuntimeError("LLM retry loop exited unexpectedly without a response.")

    def _is_fatal_llm_error(self, error: Exception) -> bool:
        err_str = " ".join(str(error).lower().split())
        fatal_markers = (
            "insufficient_balance",
            "insufficient account balance",
            "invalid_api_key",
            "incorrect api key",
            "authentication failed",
            "unauthorized",
            "forbidden",
            "permission denied",
            "billing",
            "payment required",
            "error code: 401",
            "error code: 402",
            "error code: 403",
        )
        return any(marker in err_str for marker in fatal_markers)
