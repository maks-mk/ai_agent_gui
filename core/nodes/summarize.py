from __future__ import annotations

import logging
from typing import List

from langchain_core.messages import RemoveMessage

from core.state import AgentState
from core.summarize_policy import choose_summary_boundary, estimate_tokens, format_history_for_summary, should_summarize
from core import constants
from core.text_utils import format_exception_friendly
from core.errors import format_error

logger = logging.getLogger("agent")


class SummarizeMixin:
    """Summarize node: compacts message history when it grows beyond the token threshold."""

    def _estimate_tokens(self, messages: List) -> int:
        return estimate_tokens(messages)

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

        if not should_summarize(
            messages,
            threshold=self.config.summary_threshold,
            keep_last=self.config.summary_keep_last,
            has_summary=bool(summary),
        ):
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
        idx = choose_summary_boundary(messages, keep_last=self.config.summary_keep_last)

        to_summarize = messages[:idx]

        # SAFEGUARD: If the last N messages alone exceed the limit,
        # we cannot compress anything without losing recent context.
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

    def _format_history_for_summary(self, messages: List) -> str:
        return format_history_for_summary(messages, is_internal_retry=self._is_internal_retry_message)
