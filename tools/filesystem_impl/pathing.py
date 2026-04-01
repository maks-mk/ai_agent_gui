import errno
import shutil
from functools import lru_cache
from pathlib import Path


IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".next", ".nuxt",
    "venv", ".venv", "env", ".env",
    "dist", "build", "out", "target",
    ".idea", ".vscode",
}

_KNOWN_TEXT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".less",
    ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".txt", ".csv", ".log", ".sh", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php",
    ".sql", ".env", ".gitignore", ".dockerignore", ".editorconfig",
}
_KNOWN_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mkv", ".wav", ".flac", ".ogg",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".pyc", ".pyo", ".class", ".o", ".obj",
}


@lru_cache(maxsize=512)
def is_binary_path(path_str: str) -> bool:
    path = Path(path_str)
    ext = path.suffix.lower()
    if ext in _KNOWN_TEXT_EXTS:
        return False
    if ext in _KNOWN_BINARY_EXTS:
        return True
    try:
        with open(path_str, "rb") as file_obj:
            return b"\x00" in file_obj.read(8192)
    except Exception:
        return True


def count_file_lines(path: Path) -> int:
    try:
        if path.stat().st_size == 0:
            return 0

        count = 0
        last_chunk = b""
        with open(path, "rb") as file_obj:
            while chunk := file_obj.read(65536):
                count += chunk.count(b"\n")
                last_chunk = chunk

        if last_chunk and not last_chunk.endswith(b"\n"):
            count += 1
        return count
    except Exception:
        return 0


def candidate_path_inputs(path_str: str) -> list[str]:
    normalized = str(path_str).strip()
    candidates: list[str] = []
    for candidate in (
        normalized,
        normalized.strip("\"'"),
        normalized.rstrip(",;").rstrip(),
        normalized.strip("\"'").rstrip(",;").rstrip(),
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def resolve_path(cwd: Path, virtual_mode: bool, path_str: str) -> Path:
    if not path_str:
        raise ValueError("Path cannot be empty")

    resolved_candidates: list[tuple[str, Path]] = []
    for candidate_input in candidate_path_inputs(path_str):
        clean_path = candidate_input.replace("\\", "/")
        path_obj = Path(clean_path).expanduser()

        if virtual_mode:
            # First, check if it's already an absolute path
            is_abs = path_obj.is_absolute()
            
            # Resolve the full path
            full_path = path_obj.resolve() if is_abs else (cwd / path_obj).resolve()
            
            # Check if it's relative to cwd (this handles traversal attempts like ../../etc)
            if not full_path.is_relative_to(cwd):
                # Only allow absolute paths if they actually point inside the cwd
                if is_abs:
                    raise ValueError(f"ACCESS DENIED: Absolute paths not allowed in virtual mode: {path_str}")
                else:
                    raise ValueError(f"ACCESS DENIED: Path traversal outside working directory: {full_path}")
        else:
            full_path = path_obj.resolve() if path_obj.is_absolute() else (cwd / path_obj).resolve()

        resolved_candidates.append((candidate_input, full_path))

    _, original_path = resolved_candidates[0]
    if original_path.exists():
        return original_path

    for _, candidate_path in resolved_candidates[1:]:
        if candidate_path.exists():
            return candidate_path

    return original_path


def resolve_existing_path(cwd: Path, virtual_mode: bool, path: str, expected: str) -> Path:
    target = resolve_path(cwd, virtual_mode, path)
    if not target.exists():
        raise FileNotFoundError(path)
    if expected == "file" and not target.is_file():
        raise IsADirectoryError(path)
    if expected == "dir" and not target.is_dir():
        raise NotADirectoryError(path)
    return target


def delete_directory_path(target: Path, recursive: bool) -> None:
    if recursive:
        shutil.rmtree(target)
        return
    try:
        next(target.iterdir())
    except StopIteration:
        pass
    else:
        raise OSError(errno.ENOTEMPTY, "Directory is not empty.")
    target.rmdir()
