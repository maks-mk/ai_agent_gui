import logging
import subprocess
import shlex
import platform
import atexit
from pathlib import Path
from typing import Dict, List, Optional, Union

try:
    import psutil
except ImportError:
    psutil = None

from langchain_core.tools import tool

from core.safety_policy import SafetyPolicy
from core.errors import format_error, ErrorType

logger = logging.getLogger(__name__)

# Global registry for background processes
# Format: {pid: Popen_object}
_BACKGROUND_PROCESSES: Dict[int, subprocess.Popen] = {}

# Global safety policy
_SAFETY_POLICY: Optional[SafetyPolicy] = None
_WORKSPACE_ROOT = Path.cwd().resolve()

_SHELL_META_CHARS = ("&&", "||", "|", ";", ">", "<", "$(", "`")

def set_safety_policy(policy: SafetyPolicy):
    global _SAFETY_POLICY
    _SAFETY_POLICY = policy

def set_working_directory(cwd: str):
    global _WORKSPACE_ROOT
    _WORKSPACE_ROOT = Path(cwd).resolve()

def _cleanup_zombies():
    """Removes finished processes from registry."""
    to_remove = []
    for pid, proc in _BACKGROUND_PROCESSES.items():
        if proc.poll() is not None:
            to_remove.append(pid)
    for pid in to_remove:
        del _BACKGROUND_PROCESSES[pid]

def _shutdown_all():
    """Kills all tracked processes on exit."""
    for pid, proc in _BACKGROUND_PROCESSES.items():
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            proc.kill()

atexit.register(_shutdown_all)


def _resolve_cwd(cwd: str) -> Path:
    candidate = Path(cwd or ".")
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (_WORKSPACE_ROOT / candidate).resolve()

    if not resolved.is_relative_to(_WORKSPACE_ROOT):
        raise ValueError(f"ACCESS DENIED: cwd must stay inside workspace root {_WORKSPACE_ROOT}")
    return resolved


def _normalize_command(command: Union[str, List[str]]) -> List[str]:
    if isinstance(command, list):
        parts = [str(part).strip() for part in command if str(part).strip()]
        if not parts:
            raise ValueError("Command list cannot be empty.")
        return parts

    raw_command = str(command or "").strip()
    if not raw_command:
        raise ValueError("Command cannot be empty.")
    if any(token in raw_command for token in _SHELL_META_CHARS):
        raise ValueError(
            "Shell operators are not allowed in run_background_process. Pass an argument list or a simple command only."
        )

    parts = shlex.split(raw_command, posix=platform.system() != "Windows")
    if not parts:
        raise ValueError("Command cannot be empty.")
    return parts

@tool("run_background_process")
def run_background_process(command: Union[str, List[str]], cwd: str = ".") -> str:
    """
    Starts a background process with a validated working directory and argv-like command.

    Args:
        command: Either a list of command arguments or a simple shell-free command string.
        cwd: Working directory inside the current workspace (default ".").
    """
    # Clean up dead processes first
    _cleanup_zombies()
    
    # Check limit
    limit = _SAFETY_POLICY.max_background_processes if _SAFETY_POLICY else 5
    if len(_BACKGROUND_PROCESSES) >= limit:
        return format_error(ErrorType.LIMIT_EXCEEDED, f"Maximum background processes ({limit}) reached.")

    try:
        args = _normalize_command(command)
        safe_cwd = _resolve_cwd(cwd)
        popen_kwargs = {
            "args": args,
            "cwd": str(safe_cwd),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(**popen_kwargs)
        pid = process.pid
        _BACKGROUND_PROCESSES[pid] = process
        
        logger.info("Background process started: %s (PID: %s) cwd=%s", args, pid, safe_cwd)
        return f"Success: Process started with PID {pid}."
        
    except ValueError as e:
        return format_error(ErrorType.VALIDATION, str(e))
    except Exception as e:
        return format_error(ErrorType.EXECUTION, f"Error starting process: {e}")

@tool("stop_background_process")
def stop_background_process(pid: int) -> str:
    """
    Stops a background process by PID.
    """
    _cleanup_zombies()
    
    try:
        if pid in _BACKGROUND_PROCESSES:
            proc = _BACKGROUND_PROCESSES[pid]
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            
            if pid in _BACKGROUND_PROCESSES:
                del _BACKGROUND_PROCESSES[pid]
            return f"Success: Process {pid} stopped."
            
        # Try to kill by PID even if not in our registry only when explicitly allowed.
        if psutil is not None and psutil.pid_exists(pid):
            if not (_SAFETY_POLICY and _SAFETY_POLICY.allow_external_process_control):
                return format_error(
                    ErrorType.ACCESS_DENIED,
                    "Stopping external processes is disabled by SafetyPolicy.",
                )
            try:
                p = psutil.Process(pid)
                p.terminate()
                return f"Success: External process {pid} terminated."
            except psutil.AccessDenied:
                return format_error(ErrorType.ACCESS_DENIED, f"Cannot terminate process {pid}.")
            
        return format_error(ErrorType.NOT_FOUND, f"Process {pid} not found.")
        
    except Exception as e:
        return format_error(ErrorType.EXECUTION, f"Error stopping process: {e}")

@tool("find_process_by_port")
def find_process_by_port(port: int) -> str:
    """
    Finds a process PID listening on a specific port.
    """
    try:
        if psutil is None:
            return format_error(ErrorType.CONFIG, "psutil is required for process inspection tools.")
        for proc in psutil.process_iter(['pid', 'name', 'connections']):
            try:
                for conn in proc.connections(kind='inet'):
                    if conn.laddr.port == port:
                        return f"Found process '{proc.name()}' (PID: {proc.pid}) on port {port}."
            except (psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return format_error(ErrorType.NOT_FOUND, f"No process found listening on port {port}.")
    except Exception as e:
        return format_error(ErrorType.EXECUTION, f"Error searching process: {e}")

