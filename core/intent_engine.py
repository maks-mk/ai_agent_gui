from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from langchain_core.messages import BaseMessage

from core.message_context import IsInternalRetry, MessageContextHelper


class TurnIntent(str, Enum):
    CHAT = "chat"
    INSPECT = "inspect"
    MUTATE = "mutate"
    FOLLOW_UP = "followup"


@dataclass(frozen=True)
class IntentSignals:
    normalized_task: str
    had_tool_activity_in_previous_turn: bool = False
    has_recent_tool_context: bool = False
    current_turn_has_tool_evidence: bool = False


@dataclass(frozen=True)
class IntentDecision:
    intent: TurnIntent
    inspect_only: bool
    requires_operational_evidence: bool
    should_force_tools: bool
    prefer_read_only_fallback: bool
    signals: IntentSignals


class IntentEngine(MessageContextHelper):
    """Compatibility shim kept for imports and tests.

    Phrase-lexicon routing was removed from runtime. The live part of this
    component is message-context inspection inherited from MessageContextHelper.
    """

    @staticmethod
    def normalize_task_text(task: str) -> str:
        return " ".join(str(task or "").lower().split())

    def compute_signals(
        self,
        *,
        task: str,
        messages: List[BaseMessage],
        current_turn_id: int,
        is_internal_retry: IsInternalRetry,
    ) -> IntentSignals:
        return IntentSignals(
            normalized_task=self.normalize_task_text(task),
            had_tool_activity_in_previous_turn=self.had_tool_activity_in_previous_turn(
                messages,
                current_turn_id,
                is_internal_retry,
            ),
            has_recent_tool_context=bool(self.recent_tool_context_names(messages, is_internal_retry)),
            current_turn_has_tool_evidence=self.current_turn_has_tool_evidence(messages, is_internal_retry),
        )

    def decide(
        self,
        *,
        task: str,
        messages: List[BaseMessage],
        current_turn_id: int,
        is_internal_retry: IsInternalRetry,
    ) -> IntentDecision:
        return IntentDecision(
            intent=TurnIntent.CHAT,
            inspect_only=False,
            requires_operational_evidence=False,
            should_force_tools=False,
            prefer_read_only_fallback=False,
            signals=self.compute_signals(
                task=task,
                messages=messages,
                current_turn_id=current_turn_id,
                is_internal_retry=is_internal_retry,
            ),
        )
