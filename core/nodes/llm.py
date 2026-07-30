from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, List

from langgraph.config import get_stream_writer

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from core.api_key_rotation import ApiKeyRotationExhaustedError, classify_api_key_error
from core.state import AgentState
from core.node_errors import EmptyLLMResponseError
from core.message_utils import compact_text, stringify_content

logger = logging.getLogger("agent")

_TRANSIENT_RETRY_DELAYS = (2, 4, 8)
_TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_TRANSIENT_ERROR_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "resource exhausted",
    "resource_exhausted",
    "timeout",
    "timed out",
    "deadline exceeded",
    "connection",
    "network",
    "reset by peer",
    "connection reset",
    "connection refused",
    "connection aborted",
    "broken pipe",
    "temporary failure",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "server disconnected",
)


class LLMMixin:
    """LLM selection, invocation with retry, and fatal-error classification."""

    def _select_llm_for_active_tools(
        self,
        active_tools: List[Any],
        active_tool_names: List[str],
    ) -> BaseChatModel:
        if not active_tool_names:
            return self.llm

        if active_tool_names == list(self._all_tool_names):
            return self.llm_with_tools

        binder = getattr(self.llm, "bind_tools", None)
        if not callable(binder):
            return self.llm_with_tools

        try:
            return binder(active_tools)
        except Exception as exc:
            logger.warning(
                "Failed to bind active tool subset; falling back to pre-bound tool model: %s",
                exc,
            )
            return self.llm_with_tools

    async def _invoke_llm_with_retry(
        self,
        llm,
        context: List[Any],
        state: AgentState | None = None,
        node_name: str = "",
    ):
        current_llm = llm
        context = list(context)
        configured_max_attempts = max(1, self.config.max_retries)
        retry_delay = max(0, self.config.retry_delay)
        transient_retry_count = 0
        configured_retry_count = 0
        invocation_attempt = 0
        auto_tool_choice_fallback_used = False
        auto_tool_choice_warning = "WARNING: Tools are disabled due to server configuration error."
        self._log_run_event(
            state,
            "llm_invoke_start",
            run_id=None if state is None else state.get("run_id", ""),
            node=node_name,
            max_attempts=configured_max_attempts,
            transient_max_retries=len(_TRANSIENT_RETRY_DELAYS),
            context_messages=len(context),
        )

        while True:
            invocation_attempt += 1
            try:
                normalized_context = self._normalize_system_prefix_for_provider(context)
                response = await current_llm.ainvoke(normalized_context)
                invalid_calls = getattr(response, "invalid_tool_calls", None)
                if not response.content and not response.tool_calls and not invalid_calls:
                    raise EmptyLLMResponseError("Empty response from LLM")
                self._log_run_event(
                    state,
                    "llm_invoke_success",
                    run_id=None if state is None else state.get("run_id", ""),
                    node=node_name,
                    attempt=invocation_attempt,
                    has_content=bool(stringify_content(response.content).strip()),
                    tool_calls=len(getattr(response, "tool_calls", []) or []),
                )
                return response
            except Exception as e:
                err_str = str(e)
                if (
                    "auto" in err_str
                    and "tool choice" in err_str
                    and "requires" in err_str
                    and not auto_tool_choice_fallback_used
                ):
                    logger.warning(
                        "⚠ Server does not support 'auto' tool choice. Falling back to chat-only mode."
                    )
                    auto_tool_choice_fallback_used = True
                    current_llm = self.llm
                    context = list(context)
                    if context and isinstance(context[0], SystemMessage):
                        system_content = str(context[0].content)
                        if auto_tool_choice_warning not in system_content:
                            system_content = f"{system_content}\n\n{auto_tool_choice_warning}"
                        context[0] = SystemMessage(content=system_content)
                    continue

                is_transient = self._is_transient_llm_error(e)
                is_fatal = self._is_fatal_llm_error(e) and not is_transient
                logger.warning("LLM Error (Attempt %s): %s", invocation_attempt, e)

                if is_transient and transient_retry_count < len(_TRANSIENT_RETRY_DELAYS):
                    transient_retry_count += 1
                    backoff_delay = _TRANSIENT_RETRY_DELAYS[transient_retry_count - 1]
                    self._log_run_event(
                        state,
                        "llm_retry",
                        node=node_name,
                        attempt=transient_retry_count,
                        max_attempts=len(_TRANSIENT_RETRY_DELAYS),
                        invocation_attempt=invocation_attempt,
                        retry_kind="transient",
                        backoff_seconds=backoff_delay,
                        fatal=False,
                        error=str(e),
                    )
                    self._emit_reconnecting_status(node_name, transient_retry_count)
                    await asyncio.sleep(backoff_delay)
                    continue

                if is_fatal:
                    logger.error("Fatal LLM error detected. Aborting request: %s", e)
                    self._log_run_event(
                        state,
                        "llm_invoke_fatal",
                        run_id=None if state is None else state.get("run_id", ""),
                        node=node_name,
                        attempt=invocation_attempt,
                        error_type=type(e).__name__,
                        error=compact_text(str(e), 400),
                    )
                    raise

                if is_transient or configured_retry_count >= configured_max_attempts - 1:
                    self._log_run_event(
                        state,
                        "llm_invoke_exhausted",
                        run_id=None if state is None else state.get("run_id", ""),
                        node=node_name,
                        attempt=invocation_attempt,
                        error_type=type(e).__name__,
                        error=compact_text(str(e), 400),
                    )
                    raise

                backoff_delay = retry_delay * (2 ** configured_retry_count) + random.uniform(0, retry_delay)
                configured_retry_count += 1
                self._log_run_event(
                    state,
                    "llm_retry",
                    node=node_name,
                    attempt=configured_retry_count,
                    max_attempts=max(0, configured_max_attempts - 1),
                    invocation_attempt=invocation_attempt,
                    retry_kind="configured",
                    backoff_seconds=backoff_delay,
                    fatal=False,
                    error=str(e),
                )
                await asyncio.sleep(backoff_delay)

    @staticmethod
    def _emit_reconnecting_status(node_name: str, retry_number: int) -> None:
        try:
            writer = get_stream_writer()
            writer(
                {
                    "type": "status_changed",
                    "label": f"Reconnecting... {retry_number}/{len(_TRANSIENT_RETRY_DELAYS)}",
                    "node": node_name or "agent",
                }
            )
        except RuntimeError:
            # Direct unit invocations do not have a LangGraph streaming context.
            return

    @staticmethod
    def _is_transient_llm_error(error: Exception) -> bool:
        if classify_api_key_error(error) == "rate_limit":
            return True

        for candidate in (
            getattr(error, "status_code", None),
            getattr(getattr(error, "response", None), "status_code", None),
        ):
            try:
                if int(candidate) in _TRANSIENT_HTTP_STATUS_CODES:
                    return True
            except (TypeError, ValueError):
                continue

        error_text = " ".join(str(error).lower().split())
        status_pattern = "|".join(str(code) for code in sorted(_TRANSIENT_HTTP_STATUS_CODES))
        if re.search(rf"(?<!\d)(?:{status_pattern})(?!\d)", error_text):
            return True
        return any(marker in error_text for marker in _TRANSIENT_ERROR_MARKERS)

    def _is_fatal_llm_error(self, error: Exception) -> bool:
        if isinstance(error, ApiKeyRotationExhaustedError):
            return True
        error_kind = classify_api_key_error(error)
        if error_kind in {"auth", "billing"}:
            return True
        if error_kind == "rate_limit":
            return False
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
