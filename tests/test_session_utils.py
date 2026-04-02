import types
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.session_utils import repair_session_if_needed


class _FakeAgentApp:
    def __init__(self, messages):
        self.values = {"messages": list(messages)}
        self.update_calls = []

    async def aget_state(self, _config):
        return types.SimpleNamespace(values=self.values)

    async def aupdate_state(self, _config, update, as_node=None):
        self.update_calls.append({"update": update, "as_node": as_node})
        self.values.setdefault("messages", []).extend(update.get("messages", []))


class SessionRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_inserts_interrupted_tool_message_for_missing_tool_output(self):
        app = _FakeAgentApp(
            [
                HumanMessage(content="Сделай шаг"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tc-1", "name": "cli_exec", "args": {"command": "echo 1"}}],
                ),
            ]
        )

        notices = await repair_session_if_needed(app, "thread-a")

        self.assertEqual(len(app.update_calls), 1)
        self.assertGreaterEqual(len(notices), 2)
        tool_messages = [message for message in app.values["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].tool_call_id, "tc-1")
        self.assertIn("Execution interrupted", str(tool_messages[0].content))

    async def test_repair_skips_when_loop_budget_handoff_exists_after_pending_tool_call(self):
        app = _FakeAgentApp(
            [
                HumanMessage(content="Сделай шаг"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tc-loop", "name": "cli_exec", "args": {"command": "echo 1"}}],
                    id="ai-loop",
                ),
                AIMessage(
                    content="Остановился по лимиту шагов.",
                    additional_kwargs={"agent_internal": {"kind": "loop_budget_handoff", "turn_id": 1}},
                ),
            ]
        )

        notices = await repair_session_if_needed(app, "thread-b")

        self.assertEqual(notices, [])
        self.assertEqual(app.update_calls, [])
        self.assertFalse(any(isinstance(message, ToolMessage) for message in app.values["messages"]))


if __name__ == "__main__":
    unittest.main()

