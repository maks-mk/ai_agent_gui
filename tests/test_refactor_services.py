import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.config import AgentConfig
from core.context_builder import ContextBuilder
from core.recovery_manager import RecoveryManager
from core.self_correction_engine import RepairPlan
from core.tool_executor import ToolExecutor
from core.tool_issues import build_tool_issue
from core.tool_policy import ToolMetadata


class RefactorServicesTests(unittest.TestCase):
    def _make_config(self, **overrides) -> AgentConfig:
        defaults = {
            "PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "PROMPT_PATH": Path(__file__).resolve().parents[1] / "prompt.txt",
            "MCP_CONFIG_PATH": Path(__file__).resolve().parents[1] / "tests" / "missing_mcp.json",
            "ENABLE_SEARCH_TOOLS": False,
            "ENABLE_SYSTEM_TOOLS": False,
            "ENABLE_PROCESS_TOOLS": False,
            "ENABLE_SHELL_TOOL": False,
        }
        defaults.update(overrides)
        return AgentConfig(**defaults)

    def test_context_builder_uses_compact_tool_notice_for_large_catalog(self):
        builder = ContextBuilder(
            config=self._make_config(),
            prompt_loader=lambda: "Base prompt {{current_date}}",
            is_internal_retry=lambda _msg: False,
            log_run_event=lambda *_args, **_kwargs: None,
            recovery_message_builder=lambda _state: None,
            provider_safe_tool_call_id_re=__import__("re").compile(r"^[A-Za-z0-9]{9}$"),
        )

        context = builder.build(
            [],
            None,
            summary="",
            current_task="Проверь задачу",
            tools_available=True,
            active_tool_names=[f"tool_{i}" for i in range(8)],
            open_tool_issue=None,
            recovery_state=None,
        )

        self.assertIsInstance(context[0], SystemMessage)
        system_text = str(context[0].content)
        self.assertIn("use only tools bound in this request", system_text)
        self.assertNotIn("tool_0, tool_1", system_text)

    def test_context_builder_inserts_short_bridge_after_tool_before_user(self):
        builder = ContextBuilder(
            config=self._make_config(),
            prompt_loader=lambda: "Base prompt {{current_date}}",
            is_internal_retry=lambda _msg: False,
            log_run_event=lambda *_args, **_kwargs: None,
            recovery_message_builder=lambda _state: None,
            provider_safe_tool_call_id_re=__import__("re").compile(r"^[A-Za-z0-9]{9}$"),
        )

        sanitized = builder.sanitize_messages(
            [
                ToolMessage(content="ok", tool_call_id="tool-1", name="read_file"),
                HumanMessage(content="Продолжай"),
            ]
        )

        self.assertEqual(len(sanitized), 3)
        self.assertIsInstance(sanitized[1], AIMessage)
        self.assertEqual(str(sanitized[1].content), "Continuing.")

    def test_context_builder_stringifies_openai_assistant_content_lists(self):
        builder = ContextBuilder(
            config=self._make_config(),
            prompt_loader=lambda: "Base prompt {{current_date}}",
            is_internal_retry=lambda _msg: False,
            log_run_event=lambda *_args, **_kwargs: None,
            recovery_message_builder=lambda _state: None,
            provider_safe_tool_call_id_re=__import__("re").compile(r"^[A-Za-z0-9]{9}$"),
        )

        sanitized = builder.sanitize_messages(
            [
                HumanMessage(content="Проверь историю"),
                AIMessage(content=["Первый фрагмент. ", "Второй фрагмент."]),
                ToolMessage(content=[{"type": "text", "text": "ok"}], tool_call_id="tool-1", name="read_file"),
            ]
        )

        self.assertEqual(sanitized[1].content, "Первый фрагмент. Второй фрагмент.")
        self.assertEqual(sanitized[2].content, "ok")

    def test_tool_executor_readonly_error_stays_visible_to_agent_without_issue(self):
        executor = ToolExecutor(
            config=self._make_config(),
            metadata_for_tool=lambda name: ToolMetadata(name=name, read_only=True),
            log_run_event=lambda *_args, **_kwargs: None,
            workspace_boundary_violated=lambda *_args, **_kwargs: False,
        )

        outcome = executor.handle_result(
            state={"run_id": "run"},
            current_turn_id=1,
            tool_name="read_file",
            tool_args={"path": "README.md"},
            tool_call_id="call-1",
            content="ERROR[EXECUTION]: boom",
            apply_validation=False,
            had_error=True,
        )

        self.assertTrue(outcome.had_error)
        self.assertIsNone(outcome.issue)
        self.assertEqual(outcome.tool_message.status, "error")

    def test_tool_executor_merges_multiple_issues(self):
        executor = ToolExecutor(
            config=self._make_config(),
            metadata_for_tool=lambda name: ToolMetadata(name=name, mutating=True),
            log_run_event=lambda *_args, **_kwargs: None,
            workspace_boundary_violated=lambda *_args, **_kwargs: False,
        )

        merged = executor.merge_issues(
            [
                build_tool_issue(
                    current_turn_id=2,
                    kind="tool_error",
                    summary="Missing path",
                    tool_names=["edit_file"],
                    tool_args={"path": "a.txt"},
                    source="tools",
                    error_type="VALIDATION",
                    fingerprint="fp-1",
                    details={"missing_required_fields": ["path"]},
                    progress_fingerprint="fp-1",
                ),
                build_tool_issue(
                    current_turn_id=2,
                    kind="tool_error",
                    summary="Loop detected",
                    tool_names=["edit_file"],
                    tool_args={"path": "a.txt"},
                    source="tools",
                    error_type="LOOP_DETECTED",
                    fingerprint="fp-2",
                    details={"loop_detected": True},
                    progress_fingerprint="fp-2",
                ),
            ],
            current_turn_id=2,
        )

        self.assertIsNotNone(merged)
        self.assertIn("Missing path", merged["summary"])
        self.assertIn("edit_file", merged["tool_names"])
        self.assertTrue(merged["details"]["loop_detected"])

    def test_recovery_manager_builds_compact_recovery_message(self):
        manager = RecoveryManager()
        message = manager.build_recovery_system_message(
            {
                "active_issue": {"summary": "Port must be integer"},
                "active_strategy": {
                    "strategy": "normalize_args",
                    "strategy_kind": "fix_args",
                    "llm_guidance": "Retry with normalized arguments.",
                    "suggested_tool_name": "find_process_by_port",
                    "patched_args": {"port": 8080, "extra": "x" * 200},
                    "notes": "Normalize the port before retry.",
                },
            }
        )

        self.assertIsNotNone(message)
        text = str(message.content)
        self.assertIn("Recovery strategy: fix_args", text)
        self.assertIn("Prepared arguments:", text)
        self.assertNotIn("Structured issue details:", text)

    def test_recovery_manager_handoff_text_hides_internal_recovery_hints(self):
        manager = RecoveryManager()
        text = manager.build_tool_issue_handoff_text(
            {
                "kind": "tool_error",
                "summary": "Command failed with Exit Code 1",
                "tool_names": ["cli_exec"],
                "details": {},
            },
            repair_plan=RepairPlan(
                strategy="llm_replan",
                reason="recovery_stagnated",
                fingerprint="fp-1",
                tool_name="cli_exec",
                suggested_tool_name="cli_exec",
                original_args={"command": "rm bad.txt"},
                patched_args={"command": "rm bad.txt"},
                notes="No deterministic auto-repair available.",
            ),
        )

        self.assertIn("Не удалось завершить задачу", text)
        self.assertIn("стагнац", text.lower())
        self.assertNotIn("Prepared arguments:", text)
        self.assertNotIn("Suggested next tool:", text)
        self.assertNotIn("Hint:", text)

    def test_recovery_manager_builds_soft_internal_ui_notices_by_reason(self):
        manager = RecoveryManager()

        loop_notice = manager.build_internal_ui_notice("loop_budget_exhausted_pending_tool_call")
        stagnation_notice = manager.build_internal_ui_notice("successful_tool_stagnation")
        fallback_notice = manager.build_internal_ui_notice("recovery_stagnated")

        self.assertIn("внутренний лимит", loop_notice.lower())
        self.assertIn("по кругу", stagnation_notice.lower())
        self.assertIn("пау", fallback_notice.lower())


if __name__ == "__main__":
    unittest.main()
