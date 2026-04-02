import asyncio
import shutil
import unittest
from pathlib import Path
from uuid import uuid4
from unittest import mock

import httpx
from langchain_core.messages import RemoveMessage, ToolMessage

from core.text_utils import prepare_markdown_for_render
from core.stream_processor import StreamProcessor
from tools import filesystem, local_shell
from tools.filesystem import FilesystemManager, _DOWNLOAD_HEADERS, _format_download_http_error


class StreamAndFilesystemTests(unittest.TestCase):
    class _FakeReader:
        def __init__(self, chunks: list[bytes]):
            self._chunks = list(chunks)

        async def read(self, _size: int) -> bytes:
            if self._chunks:
                return self._chunks.pop(0)
            return b""

    class _FakeProcess:
        def __init__(self, stdout_chunks: list[bytes], stderr_chunks: list[bytes], returncode: int = 0):
            self.stdout = StreamAndFilesystemTests._FakeReader(stdout_chunks)
            self.stderr = StreamAndFilesystemTests._FakeReader(stderr_chunks)
            self.returncode = returncode
            self.killed = False

        async def wait(self) -> int:
            return self.returncode

        def kill(self) -> None:
            self.killed = True

    def _workspace_tempdir(self) -> Path:
        path = Path.cwd() / ".tmp_tests" / uuid4().hex
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_prepare_markdown_wraps_plain_go_code_block(self):
        source = 'Пример (файл main.go):\npackage main\nimport "fmt"\nfunc main() {\n    fmt.Println("hi")\n}'
        rendered = prepare_markdown_for_render(source)
        self.assertIn("```go", rendered)
        self.assertIn("package main", rendered)
        self.assertIn("fmt.Println", rendered)

    def test_download_headers_request_binary_content(self):
        self.assertEqual(_DOWNLOAD_HEADERS["Accept"], "*/*")

    def test_download_http_errors_are_specific(self):
        forbidden = httpx.HTTPStatusError(
            "forbidden",
            request=httpx.Request("GET", "https://example.com/file.mp4"),
            response=httpx.Response(403, request=httpx.Request("GET", "https://example.com/file.mp4")),
        )
        not_found = httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("GET", "https://example.com/file.mp4"),
            response=httpx.Response(404, request=httpx.Request("GET", "https://example.com/file.mp4")),
        )
        self.assertIn("ACCESS_DENIED", _format_download_http_error(forbidden))
        self.assertIn("browser-only access", _format_download_http_error(forbidden))
        self.assertIn("NOT_FOUND", _format_download_http_error(not_found))
        self.assertIn("direct file", _format_download_http_error(not_found))

    def test_stream_processor_emits_tool_error_and_diff_events(self):
        events = []
        processor = StreamProcessor(events.append)
        processor.tool_buffer["call-1"] = {"name": "edit_file", "args": {"path": "demo.txt"}}

        processor._handle_tool_result(
            ToolMessage(
                tool_call_id="call-1",
                name="edit_file",
                content="Success: File edited.\n\nDiff:\n```diff\n-foo\n+bar\n```",
            )
        )
        processor._handle_tool_result(
            ToolMessage(
                tool_call_id="call-2",
                name="read_file",
                content="ERROR[EXECUTION]: boom",
            )
        )

        event_types = [event.type for event in events]
        self.assertIn("tool_finished", event_types)
        self.assertIn("tool_diff", event_types)
        tool_finished_payloads = [event.payload for event in events if event.type == "tool_finished"]
        self.assertTrue(any(payload["name"] == "edit_file" and payload["diff"] for payload in tool_finished_payloads))
        self.assertTrue(any(payload["is_error"] and "boom" in payload["content"] for payload in tool_finished_payloads))

    def test_stream_processor_injects_preface_before_tool_when_model_text_is_empty(self):
        events = []
        processor = StreamProcessor(events.append)
        processor._remember_tool_call({"id": "call-preface", "name": "read_file", "args": {"path": "demo.txt"}})

        processor._emit_tool_started({"id": "call-preface", "name": "read_file", "args": {"path": "demo.txt"}})

        event_types = [event.type for event in events]
        self.assertIn("assistant_delta", event_types)
        self.assertIn("tool_started", event_types)
        assistant_idx = event_types.index("assistant_delta")
        tool_idx = event_types.index("tool_started")
        self.assertLess(assistant_idx, tool_idx)
        preface_payload = [event.payload for event in events if event.type == "assistant_delta"][0]
        self.assertIn("вызов инструмента", preface_payload["text"].lower())

    def test_stream_processor_emits_summarization_notice(self):
        events = []
        processor = StreamProcessor(events.append)

        processor._handle_updates(
            {
                "summarize": {
                    "summary": "compressed summary",
                    "messages": [RemoveMessage(id="1"), RemoveMessage(id="2")],
                }
            }
        )

        notice_events = [event for event in events if event.type == "summary_notice"]
        self.assertEqual(len(notice_events), 1)
        self.assertIn("Context compressed automatically", notice_events[0].payload["message"])
        self.assertEqual(notice_events[0].payload["count"], 2)
        self.assertEqual(notice_events[0].payload["kind"], "auto_summary")

    def test_stream_processor_merges_tool_args_and_finishes_with_canonical_payload(self):
        events = []
        processor = StreamProcessor(events.append)

        processor._remember_tool_call({"id": "call-merge", "name": "edit_file", "args": {}})
        processor._emit_tool_started({"id": "call-merge", "name": "edit_file", "args": {}})
        processor._remember_tool_call(
            {
                "id": "call-merge",
                "name": "edit_file",
                "args": {"path": "demo.txt", "old_string": "old", "new_string": "new"},
            }
        )
        processor._handle_tool_result(
            ToolMessage(
                tool_call_id="call-merge",
                name="edit_file",
                content="Success: File edited.",
            )
        )

        finished = [event.payload for event in events if event.type == "tool_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(
            finished[0]["args"],
            {"path": "demo.txt", "old_string": "old", "new_string": "new"},
        )
        self.assertIn("demo.txt", finished[0]["display"])

    def test_stream_processor_emits_tool_started_refresh_when_args_arrive_late(self):
        events = []
        processor = StreamProcessor(events.append)

        processor._remember_tool_call({"id": "call-refresh", "name": "edit_file", "args": {}})
        processor._emit_tool_started({"id": "call-refresh", "name": "edit_file", "args": {}})
        processor._remember_tool_call(
            {
                "id": "call-refresh",
                "name": "edit_file",
                "args": {"path": "late.txt", "old_string": "a", "new_string": "b"},
            }
        )

        started = [event.payload for event in events if event.type == "tool_started"]
        self.assertGreaterEqual(len(started), 2)
        self.assertEqual(started[-1]["args"]["path"], "late.txt")
        self.assertTrue(started[-1].get("refresh"))

    def test_stream_processor_tool_display_flattens_multiline_command(self):
        events = []
        processor = StreamProcessor(events.append)
        multiline_command = "python - <<'PY'\nimport sys\nprint(sys.version)\nPY"
        processor._remember_tool_call(
            {
                "id": "call-cmd",
                "name": "cli_exec",
                "args": {"command": multiline_command},
            }
        )

        processor._emit_tool_started(
            {
                "id": "call-cmd",
                "name": "cli_exec",
                "args": {"command": multiline_command},
            }
        )

        started = [event.payload for event in events if event.type == "tool_started"]
        self.assertEqual(len(started), 1)
        self.assertIn("cli_exec", started[0]["display"])
        self.assertNotIn("\n", started[0]["display"])

    def test_stream_processor_finish_before_start_keeps_args_when_buffer_has_tool_call(self):
        events = []
        processor = StreamProcessor(events.append)
        processor.tool_buffer["call-late"] = {
            "name": "tail_file",
            "args": {"path": "service.log", "lines": 20},
        }

        processor._handle_tool_result(
            ToolMessage(
                tool_call_id="call-late",
                name="tail_file",
                content="Last 20 line(s)...",
            )
        )

        started = [event.payload for event in events if event.type == "tool_started"]
        finished = [event.payload for event in events if event.type == "tool_finished"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["args"], {"path": "service.log", "lines": 20})
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["args"], {"path": "service.log", "lines": 20})

    def test_stream_processor_recovers_args_from_tool_message_metadata(self):
        events = []
        processor = StreamProcessor(events.append)

        processor._handle_tool_result(
            ToolMessage(
                tool_call_id="call-meta",
                name="edit_file",
                content="Success: File edited.",
                additional_kwargs={
                    "tool_args": {
                        "path": "parse_yandex_forecast_fixed.py",
                        "old_string": "foo",
                        "new_string": "bar",
                    }
                },
            )
        )

        missing = [event for event in events if event.type == "tool_args_missing"]
        finished = [event.payload for event in events if event.type == "tool_finished"]
        self.assertEqual(missing, [])
        self.assertEqual(len(finished), 1)
        self.assertEqual(
            finished[0]["args"],
            {
                "path": "parse_yandex_forecast_fixed.py",
                "old_string": "foo",
                "new_string": "bar",
            },
        )
        self.assertIn("parse_yandex_forecast_fixed.py", finished[0]["display"])

    def test_filesystem_delete_uses_virtual_mode_path_guard(self):
        tmp = self._workspace_tempdir()
        manager = FilesystemManager(root_dir=tmp, virtual_mode=True)
        result = manager.delete_file("..\\outside.txt")
        self.assertIn("ERROR[EXECUTION]", result)
        self.assertIn("ACCESS DENIED", result)

    def test_filesystem_delete_directory_requires_recursive_for_non_empty(self):
        tmp = self._workspace_tempdir()
        manager = FilesystemManager(root_dir=tmp, virtual_mode=True)
        folder = tmp / "folder"
        folder.mkdir()
        (folder / "child.txt").write_text("data", encoding="utf-8")
        result = manager.delete_directory("folder")
        self.assertIn("recursive=True", result)

    def test_read_file_repairs_trailing_comma_in_existing_path(self):
        tmp = self._workspace_tempdir()
        manager = FilesystemManager(root_dir=tmp, virtual_mode=True)
        file_path = tmp / "model_info.md"
        file_path.write_text("hello", encoding="utf-8")

        result = manager.read_file("model_info.md, ", show_line_numbers=False)

        self.assertEqual(result, "hello")

    def test_module_filesystem_switches_workspace_after_directory_change(self):
        first = self._workspace_tempdir()
        second = self._workspace_tempdir()
        original_cwd = filesystem.fs_manager.cwd
        self.addCleanup(lambda: setattr(filesystem.fs_manager, "cwd", original_cwd))

        (first / "from_first.txt").write_text("first", encoding="utf-8")
        (second / "from_second.txt").write_text("second", encoding="utf-8")

        filesystem.set_working_directory(str(first))
        first_result = filesystem.list_directory_tool.invoke({"path": "."})
        self.assertIn("from_first.txt", first_result)
        self.assertNotIn("from_second.txt", first_result)

        filesystem.set_working_directory(str(second))
        second_result = filesystem.list_directory_tool.invoke({"path": "."})
        self.assertIn("from_second.txt", second_result)
        self.assertNotIn("from_first.txt", second_result)

    def test_edit_file_accepts_legacy_aliases_for_old_and_new(self):
        tmp = self._workspace_tempdir()
        original_cwd = filesystem.fs_manager.cwd
        self.addCleanup(lambda: setattr(filesystem.fs_manager, "cwd", original_cwd))
        filesystem.set_working_directory(str(tmp))

        target = tmp / "demo.txt"
        target.write_text("hello old world", encoding="utf-8")

        result = filesystem.edit_file_tool.invoke(
            {
                "path": "demo.txt",
                "old_text": "old",
                "new_text": "new",
            }
        )

        self.assertIn("Success: File edited.", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello new world")

    def test_edit_file_missing_new_string_returns_friendly_validation_error(self):
        tmp = self._workspace_tempdir()
        original_cwd = filesystem.fs_manager.cwd
        self.addCleanup(lambda: setattr(filesystem.fs_manager, "cwd", original_cwd))
        filesystem.set_working_directory(str(tmp))

        target = tmp / "demo.txt"
        target.write_text("hello old world", encoding="utf-8")

        result = filesystem.edit_file_tool.invoke(
            {
                "path": "demo.txt",
                "old_text": "old",
            }
        )

        self.assertIn("ERROR[VALIDATION]", result)
        self.assertIn("new_string", result)

    def test_edit_file_path_sanitizer_strips_browser_user_agent_tail(self):
        tmp = self._workspace_tempdir()
        original_cwd = filesystem.fs_manager.cwd
        self.addCleanup(lambda: setattr(filesystem.fs_manager, "cwd", original_cwd))
        filesystem.set_working_directory(str(tmp))

        target = tmp / "parse_yandex_forecast_fixed.py"
        target.write_text("x = 1\n", encoding="utf-8")
        noisy_path = "parse_yandex_forecast_fixed.py Mozilla/5.0 AppleWebKit/537.36 Safari/537.36"

        result = filesystem.edit_file_tool.invoke(
            {
                "path": noisy_path,
                "old_string": "x = 1",
                "new_string": "x = 2",
            }
        )

        self.assertIn("Success: File edited.", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 2\n")

    def test_cli_exec_stream_emits_live_chunks_with_tool_id(self):
        process = self._FakeProcess(
            stdout_chunks=[b"line-1\n", b"line-2\n"],
            stderr_chunks=[b"warn-1\n"],
            returncode=0,
        )
        live_events: list[dict[str, str]] = []
        self.addCleanup(lambda: local_shell.set_cli_output_emitter(None))

        async def _fake_create_subprocess_shell(*_args, **_kwargs):
            return process

        with mock.patch.object(local_shell.asyncio, "create_subprocess_shell", side_effect=_fake_create_subprocess_shell):
            local_shell.set_cli_output_emitter(live_events.append)
            with local_shell.cli_output_context("call-cli-1"):
                result = asyncio.run(local_shell.cli_exec.ainvoke({"command": "demo"}))

        self.assertIn("line-1", result)
        self.assertIn("line-2", result)
        self.assertIn("[stderr]", result)
        self.assertTrue(live_events)
        self.assertTrue(all(item.get("tool_id") == "call-cli-1" for item in live_events))
        self.assertTrue(any(item.get("stream") == "stdout" for item in live_events))
        self.assertTrue(any(item.get("stream") == "stderr" for item in live_events))

    def test_cli_exec_stream_preserves_error_result_on_non_zero_exit(self):
        process = self._FakeProcess(
            stdout_chunks=[b"partial-out\n"],
            stderr_chunks=[b"fatal-err\n"],
            returncode=2,
        )
        live_events: list[dict[str, str]] = []
        self.addCleanup(lambda: local_shell.set_cli_output_emitter(None))

        async def _fake_create_subprocess_shell(*_args, **_kwargs):
            return process

        with mock.patch.object(local_shell.asyncio, "create_subprocess_shell", side_effect=_fake_create_subprocess_shell):
            local_shell.set_cli_output_emitter(live_events.append)
            with local_shell.cli_output_context("call-cli-2"):
                result = asyncio.run(local_shell.cli_exec.ainvoke({"command": "demo --fail"}))

        self.assertIn("Exit Code 2", result)
        self.assertIn("partial-out", result)
        self.assertIn("fatal-err", result)
        self.assertGreaterEqual(len(live_events), 2)

    def test_cli_exec_converts_python_heredoc_to_powershell_on_windows(self):
        process = self._FakeProcess(
            stdout_chunks=[b"ok\n"],
            stderr_chunks=[],
            returncode=0,
        )
        captured_commands: list[str] = []

        async def _fake_create_subprocess_shell(*args, **_kwargs):
            if args:
                captured_commands.append(str(args[0]))
            return process

        heredoc_command = "python - <<'PY'\nprint('hello')\nPY"
        with (
            mock.patch.object(local_shell.os, "name", "nt"),
            mock.patch.object(local_shell.asyncio, "create_subprocess_shell", side_effect=_fake_create_subprocess_shell),
        ):
            result = asyncio.run(local_shell.cli_exec.ainvoke({"command": heredoc_command}))

        self.assertIn("ok", result)
        self.assertTrue(captured_commands)
        self.assertIn("powershell -NoProfile -Command", captured_commands[0])
        self.assertIn("@'", captured_commands[0])
        self.assertIn("'@ | python -", captured_commands[0])


if __name__ == "__main__":
    unittest.main()
