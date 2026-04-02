import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
from typing import Callable, Iterator, Optional
from langchain_core.tools import tool

from core.utils import truncate_output
from core.errors import format_error, ErrorType
from core.safety_policy import SafetyPolicy

# Константы
DEFAULT_TIMEOUT = 120

# Глобальные настройки
_SAFETY_POLICY: Optional[SafetyPolicy] = None
_WORKING_DIRECTORY: str = os.getcwd()  # По умолчанию текущая папка процесса
_CLI_OUTPUT_EMITTER: Optional[Callable[[dict[str, str]], None]] = None
_CLI_TOOL_ID: ContextVar[str] = ContextVar("cli_tool_id", default="")

_WINDOWS_COMMAND_HINTS = {
    "cat": "type <file> (или Get-Content <file>)",
    "ls": "dir (или Get-ChildItem)",
    "pwd": "cd (или Get-Location)",
    "cp": "copy (или Copy-Item)",
    "mv": "move (или Move-Item)",
    "rm": "del (или Remove-Item)",
    "grep": "findstr <pattern> <file> (или Select-String ...)",
    "head": "Get-Content <file> -TotalCount N",
    "tail": "Get-Content <file> -Tail N",
    "which": "where <command> (или Get-Command <name>)",
    "clear": "cls (или Clear-Host)",
}

_WINDOWS_PYTHON_HEREDOC_RE = re.compile(
    r"^\s*(?P<exe>python(?:3(?:\.\d+)?)?)\s*-\s*<<\s*['\"]?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\r?\n(?P<body>[\s\S]*?)\r?\n(?P=tag)\s*$",
    re.IGNORECASE,
)


def _get_windows_command_hint(command: str, stderr: str) -> str:
    """Return a friendly Windows-specific hint for common Unix commands."""
    if os.name != "nt":
        return ""

    lower_stderr = stderr.lower()
    if "is not recognized as an internal or external command" not in lower_stderr:
        return ""

    parts = command.strip().split()
    if not parts:
        return ""

    first_token = parts[0].strip("\"'").lower()
    suggestion = _WINDOWS_COMMAND_HINTS.get(first_token)
    if not suggestion:
        return ""
    return f"\nHint (Windows): команда '{first_token}' не найдена. Попробуйте: {suggestion}."


def _normalize_windows_python_heredoc(command: str) -> str:
    """Converts bash-style python heredoc into a PowerShell-compatible invocation on Windows."""
    if os.name != "nt":
        return command

    match = _WINDOWS_PYTHON_HEREDOC_RE.match(command.strip())
    if not match:
        return command

    exe = match.group("exe")
    body = match.group("body").replace("\r\n", "\n")
    # PowerShell single-quoted here-string terminator must be at line start.
    # Indent-breaking sequences are extremely unlikely for generated scripts; if present,
    # fallback to original command so the model sees a direct error and can correct manually.
    if "\n'@\n" in f"\n{body}\n":
        return command

    return f'powershell -NoProfile -Command "@\'\n{body}\n\'@ | {exe} -"'


def set_safety_policy(policy: SafetyPolicy):
    """Sets the global safety policy for shell execution."""
    global _SAFETY_POLICY
    _SAFETY_POLICY = policy

def set_working_directory(cwd: str):
    """
    Syncs the shell's working directory with the FilesystemManager's workspace.
    Call this when initializing the agent to ensure tools look at the same folders.
    """
    global _WORKING_DIRECTORY
    _WORKING_DIRECTORY = cwd


def set_cli_output_emitter(emitter: Optional[Callable[[dict[str, str]], None]]) -> None:
    """Registers callback that receives streaming CLI chunks for UI rendering."""
    global _CLI_OUTPUT_EMITTER
    _CLI_OUTPUT_EMITTER = emitter


@contextmanager
def cli_output_context(tool_id: str) -> Iterator[None]:
    """Binds current tool call id so streaming chunks can be routed to the right widget."""
    token = _CLI_TOOL_ID.set(str(tool_id or "").strip())
    try:
        yield
    finally:
        _CLI_TOOL_ID.reset(token)


def _emit_cli_output(data: str, stream: str) -> None:
    if not data:
        return
    emitter = _CLI_OUTPUT_EMITTER
    tool_id = _CLI_TOOL_ID.get()
    if emitter is None or not tool_id:
        return
    try:
        emitter({"tool_id": tool_id, "data": data, "stream": stream})
    except Exception:
        # Streaming is best-effort and must never fail tool execution.
        return

@tool("cli_exec")
async def cli_exec(command: str) -> str:
    """
    Executes a shell command on the host machine.
    
    IMPORTANT RULES FOR LLM:
    1. STATELESSNESS: Commands are stateless. `cd folder` in one call will NOT affect the next call. 
       If you need to change directories, chain commands: e.g., `cd folder && npm install`.
    2. NO INTERACTIVE COMMANDS: DO NOT run commands that require user input (e.g., `nano`, `vim`, `python` without args, `less`, `top`). 
       They will hang until timeout!
    3. BACKGROUND TASKS: Do not run blocking servers (e.g., `npm start`, `python -m http.server`) unless you background them or use an appropriate tool.
    4. LONG SCRIPTS: For complex logic, write a script file using `write_file` and then execute it.
    
    Supports pipe (|), redirects (>), and chain operators (&&).
    
    Args:
        command: The shell command to execute (e.g., 'ls -la', 'git status').
    """
    if _SAFETY_POLICY and not _SAFETY_POLICY.allow_shell:
        return format_error(ErrorType.ACCESS_DENIED, "Shell execution is disabled by SafetyPolicy.")

    if not command.strip():
        return format_error(ErrorType.VALIDATION, "Command cannot be empty.")

    normalized_command = _normalize_windows_python_heredoc(command)

    try:
        process = await asyncio.create_subprocess_shell(
            normalized_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_WORKING_DIRECTORY
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        chunk_queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

        async def _read_stream(stream_name: str, reader: asyncio.StreamReader | None) -> None:
            if reader is None:
                await chunk_queue.put((stream_name, None))
                return
            try:
                while True:
                    chunk = await reader.read(1024)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    if not text:
                        continue
                    await chunk_queue.put((stream_name, text))
            finally:
                await chunk_queue.put((stream_name, None))

        async def _collect_stream_output() -> None:
            completed_readers = 0
            while completed_readers < 2:
                stream_name, chunk = await chunk_queue.get()
                if chunk is None:
                    completed_readers += 1
                    continue
                if stream_name == "stdout":
                    stdout_chunks.append(chunk)
                else:
                    stderr_chunks.append(chunk)
                _emit_cli_output(chunk, stream_name)

        stdout_reader_task = asyncio.create_task(_read_stream("stdout", process.stdout))
        stderr_reader_task = asyncio.create_task(_read_stream("stderr", process.stderr))
        collector_task = asyncio.create_task(_collect_stream_output())

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                process.kill()
            except OSError:
                pass
            await process.wait()
        finally:
            await asyncio.gather(stdout_reader_task, stderr_reader_task, return_exceptions=True)
            await collector_task

        stdout = "".join(stdout_chunks).strip()
        stderr = "".join(stderr_chunks).strip()

        output_parts =[]
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"[stderr]\n{stderr}")

        output = "\n".join(output_parts)

        if timed_out:
            details = f"\nPartial output:\n{output}" if output else ""
            return format_error(
                ErrorType.TIMEOUT,
                f"Command timed out after {DEFAULT_TIMEOUT} seconds. Did you run an interactive command (like nano/vim) or a blocking server?{details}"
            )
        
        if process.returncode != 0:
            error_msg = f"Command failed with Exit Code {process.returncode}."
            cmd_hint = _get_windows_command_hint(command, stderr)
            if os.name == "nt" and "<< was unexpected at this time." in stderr:
                cmd_hint += (
                    "\nHint (Windows): bash-style heredoc (`python - <<'PY'`) не поддерживается через cmd.exe. "
                    "Используйте PowerShell here-string: @' ... '@ | python -"
                )
            if output:
                error_msg += f"\nOutput:\n{output}"
            else:
                error_msg += " (No output)"
            if cmd_hint:
                error_msg += cmd_hint
            return format_error(ErrorType.EXECUTION, error_msg)

        if not output:
            output = "Command executed successfully (no output)."
        
        limit = _SAFETY_POLICY.max_tool_output if _SAFETY_POLICY else 5000
        return truncate_output(output, limit, source="shell")

    except Exception as e:
        return format_error(ErrorType.EXECUTION, f"Error executing command: {str(e)}")
