import shutil
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
from langchain_core.messages import RemoveMessage, ToolMessage

from core.text_utils import prepare_markdown_for_render
from core.stream_processor import StreamProcessor
from tools import filesystem
from tools.filesystem import FilesystemManager, _DOWNLOAD_HEADERS, _format_download_http_error


class StreamAndFilesystemTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
