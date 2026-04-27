from __future__ import annotations

from typing import Dict, List

from core.state import AgentState


class RecoveryMixin:
    """Recovery node and self-correction budget helpers."""

    async def recovery_node(self, state: AgentState):
        return await self.recovery_turn.run(state)

    def _hard_loop_ceiling(self) -> int:
        configured_ceiling = int(self.config.self_correction_retry_limit or 0)
        max_loops = max(1, int(self.config.max_loops or 1))
        if configured_ceiling <= 0:
            return 0
        return max(1, min(max_loops, configured_ceiling))
