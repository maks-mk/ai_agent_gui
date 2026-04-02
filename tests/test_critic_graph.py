import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent import create_agent_workflow
from core.config import AgentConfig
from core.nodes import AgentNodes
from core.tool_policy import ToolMetadata


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.invocations = []

    async def ainvoke(self, context):
        self.invocations.append(context)
        if not self.responses:
            return AIMessage(content="Готово.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ProviderSafeFakeLLM(FakeLLM):
    async def ainvoke(self, context):
        last_visible = next(
            (message for message in reversed(context) if not isinstance(message, SystemMessage)),
            None,
        )
        if isinstance(last_visible, AIMessage):
            raise AssertionError("provider-unsafe assistant-last context")
        return await super().ainvoke(context)


class FakeTool:
    def __init__(self, name, result):
        self.name = name
        self.description = f"Fake tool {name}"
        self.result = result
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        if callable(self.result):
            return self.result(args)
        return self.result


class StabilityGraphTests(unittest.IsolatedAsyncioTestCase):
    def _make_config(
        self,
        *,
        model_supports_tools=True,
        max_loops=8,
        max_retries=3,
        retry_delay=0,
        enable_approvals=False,
    ):
        return AgentConfig(
            provider="openai",
            openai_api_key="test-key",
            model_supports_tools=model_supports_tools,
            max_loops=max_loops,
            max_retries=max_retries,
            retry_delay=retry_delay,
            enable_approvals=enable_approvals,
            prompt_path=Path(__file__).resolve().parents[1] / "prompt.txt",
        )

    def _build_app(
        self,
        *,
        agent_responses,
        tools=None,
        model_supports_tools=True,
        enable_approvals=False,
        agent_llm_cls=FakeLLM,
        tool_metadata=None,
        max_loops=8,
    ):
        config = self._make_config(
            model_supports_tools=model_supports_tools,
            enable_approvals=enable_approvals,
            max_loops=max_loops,
        )
        agent_llm = agent_llm_cls(agent_responses)
        nodes = AgentNodes(
            config=config,
            llm=FakeLLM([]),
            tools=tools or [],
            llm_with_tools=agent_llm,
            tool_metadata=tool_metadata or {},
        )
        workflow = create_agent_workflow(
            nodes,
            config,
            tools_enabled=bool(tools) and model_supports_tools,
        )
        app = workflow.compile(checkpointer=MemorySaver())
        return app, agent_llm

    def _initial_state(self, task="Проверь задачу"):
        return {
            "messages": [HumanMessage(content=task)],
            "steps": 0,
            "token_usage": {},
            "current_task": task,
            "turn_outcome": "",
            "retry_instruction": "",
            "self_correction_retry_count": 0,
            "self_correction_retry_turn_id": 0,
            "turn_id": 1,
            "open_tool_issue": None,
            "pending_approval": None,
            "has_protocol_error": False,
            "last_tool_error": "",
            "last_tool_result": "",
        }

    async def test_chat_only_turn_finishes_without_retry(self):
        app, agent_llm = self._build_app(
            agent_responses=[AIMessage(content="Задача выполнена.")],
            tools=[],
            model_supports_tools=False,
        )

        result = await app.ainvoke(
            self._initial_state("Скажи готово"),
            config={"configurable": {"thread_id": "chat-only"}, "recursion_limit": 24},
        )

        self.assertEqual(result["messages"][-1].content, "Задача выполнена.")
        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertEqual(len(agent_llm.invocations), 1)
        self.assertIsNone(result["open_tool_issue"])

    async def test_tool_error_gets_single_auto_retry_then_finishes_after_success(self):
        call_idx = {"value": 0}

        def tool_result(_args):
            call_idx["value"] += 1
            if call_idx["value"] == 1:
                return "ERROR[EXECUTION]: boom"
            return "Success: done"

        tool = FakeTool("demo_tool", tool_result)
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"step": 1}, "id": "tc-1"}]),
                AIMessage(content="Выполняю повторную попытку."),
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"step": 2}, "id": "tc-2"}]),
                AIMessage(content="Готово."),
            ],
            tools=[tool],
        )

        result = await app.ainvoke(
            self._initial_state("Сделай задачу"),
            config={"configurable": {"thread_id": "single-retry-success"}, "recursion_limit": 48},
        )

        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(len(agent_llm.invocations), 4)
        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertIsNone(result["open_tool_issue"])

        retry_messages = [
            message
            for message in agent_llm.invocations[2]
            if isinstance(message, HumanMessage)
            and isinstance(getattr(message, "additional_kwargs", {}), dict)
            and isinstance(message.additional_kwargs.get("agent_internal"), dict)
            and message.additional_kwargs["agent_internal"].get("kind") == "retry_instruction"
        ]
        self.assertTrue(retry_messages)

    async def test_second_unresolved_error_stops_after_single_retry_and_handoffs(self):
        tool = FakeTool("demo_tool", "ERROR[EXECUTION]: boom")
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"action": "a"}, "id": "tc-a"}]),
                AIMessage(content="Попробую еще раз."),
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"action": "b"}, "id": "tc-b"}]),
                AIMessage(content="Похоже, все получилось."),
            ],
            tools=[tool],
        )

        result = await app.ainvoke(
            self._initial_state("Обнови проект"),
            config={"configurable": {"thread_id": "retry-budget-exhausted"}, "recursion_limit": 48},
        )

        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(len(agent_llm.invocations), 4)
        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertIsNone(result["open_tool_issue"])
        self.assertIn("не могу продолжить", str(result["messages"][-1].content).lower())

    async def test_validation_missing_path_gets_single_auto_retry_then_handoff(self):
        tool = FakeTool("edit_file", "ERROR[VALIDATION]: Missing required field: path.")
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "edit_file", "args": {"old_string": "x", "new_string": "y"}, "id": "tc-v1"}],
                ),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "edit_file", "args": {"old_string": "x", "new_string": "y"}, "id": "tc-v2"}],
                ),
            ],
            tools=[tool],
        )

        result = await app.ainvoke(
            self._initial_state("Исправь файл"),
            config={"configurable": {"thread_id": "validation-path"}, "recursion_limit": 36},
        )

        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(len(agent_llm.invocations), 4)
        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertIsNone(result["open_tool_issue"])
        self.assertIn("укажите путь", str(result["messages"][-1].content).lower())

    async def test_approval_denied_finishes_without_retry_and_without_tool_execution(self):
        tool = FakeTool("danger_tool", "Изменение применено.")
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(content="", tool_calls=[{"name": "danger_tool", "args": {"action": "apply"}, "id": "tc-d1"}]),
                AIMessage(content="", tool_calls=[{"name": "danger_tool", "args": {"action": "again"}, "id": "tc-d2"}]),
            ],
            tools=[tool],
            enable_approvals=True,
            tool_metadata={
                "danger_tool": ToolMetadata(
                    name="danger_tool",
                    mutating=True,
                    destructive=True,
                    requires_approval=True,
                )
            },
        )

        thread_config = {"configurable": {"thread_id": "approval-thread"}, "recursion_limit": 36}
        interrupted = await app.ainvoke(self._initial_state("Сделай изменение"), config=thread_config)
        self.assertIn("__interrupt__", interrupted)

        resumed = await app.ainvoke(Command(resume={"approved": False}), config=thread_config)

        self.assertEqual(tool.calls, [])
        self.assertEqual(len(agent_llm.invocations), 2)
        self.assertEqual(resumed["turn_outcome"], "finish_turn")
        self.assertIsNone(resumed["open_tool_issue"])
        self.assertIn("you chose no", str(resumed["messages"][-1].content).lower())

    async def test_provider_safe_order_is_kept_during_internal_retry(self):
        call_idx = {"value": 0}

        def tool_result(_args):
            call_idx["value"] += 1
            if call_idx["value"] == 1:
                return "ERROR[EXECUTION]: fail"
            return "Success: done"

        tool = FakeTool("demo_tool", tool_result)
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"x": 1}, "id": "tc-1"}]),
                AIMessage(content="Попробую исправить и повторить."),
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"x": 2}, "id": "tc-2"}]),
                AIMessage(content="Готово после повтора."),
            ],
            tools=[tool],
            agent_llm_cls=ProviderSafeFakeLLM,
        )

        result = await app.ainvoke(
            self._initial_state("Сделай шаги"),
            config={"configurable": {"thread_id": "provider-safe"}, "recursion_limit": 48},
        )

        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(len(agent_llm.invocations), 4)

    async def test_loop_budget_with_pending_tool_call_finishes_with_handoff_without_dangling_call(self):
        tool = FakeTool("demo_tool", "Success: done")
        app, agent_llm = self._build_app(
            agent_responses=[
                AIMessage(content="", tool_calls=[{"name": "demo_tool", "args": {"x": 1}, "id": "tc-loop"}]),
            ],
            tools=[tool],
            max_loops=1,
        )

        result = await app.ainvoke(
            self._initial_state("Сделай задачу"),
            config={"configurable": {"thread_id": "loop-budget-pending-call"}, "recursion_limit": 24},
        )

        self.assertEqual(len(agent_llm.invocations), 1)
        self.assertEqual(tool.calls, [])
        self.assertEqual(result["turn_outcome"], "finish_turn")
        self.assertIsNone(result["open_tool_issue"])

        messages = result.get("messages", [])
        self.assertTrue(messages)
        self.assertIn("лимиту внутренних шагов", str(messages[-1].content).lower())
        self.assertFalse(any(isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None) for msg in messages))


if __name__ == "__main__":
    unittest.main()
