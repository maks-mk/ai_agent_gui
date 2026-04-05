import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from core.constants import BASE_DIR


class NoisyLogFilter(logging.Filter):
    BLOCKED_PHRASES = [
        "Key 'additionalProperties' is not supported",
        "Key '$schema' is not supported",
        "AFC is enabled",
        "HTTP Request: POST",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(phrase in message for phrase in self.BLOCKED_PHRASES)


def _coerce_log_level(level: int | str | None) -> int:
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    normalized = str(level or "INFO").strip().upper()
    return getattr(logging, normalized, logging.INFO)


def setup_logging(level: int | str | None = None, log_file: str | Path | None = None) -> logging.Logger:
    if level is None:
        level = _coerce_log_level(None)
    else:
        level = _coerce_log_level(level)

    if log_file is None:
        log_file = str(BASE_DIR / os.getenv("LOG_FILE", "logs/agent.log"))
    else:
        log_file = str(log_file)

    handlers: List[logging.Handler] = []

    if level <= logging.DEBUG:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        handlers.append(console_handler)

    if log_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            handlers.append(file_handler)
        except Exception as exc:
            sys.stderr.write(f"Warning: could not create log file: {exc}\n")

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(level=level, handlers=handlers, force=True)

    noise_filter = NoisyLogFilter()
    for handler in handlers:
        handler.addFilter(noise_filter)

    _suppress_library_logs(level)

    agent_logger = logging.getLogger("agent")
    agent_logger.setLevel(logging.DEBUG)
    return agent_logger


def _suppress_library_logs(root_level: int) -> None:
    noisy_modules = [
        "langchain_google_genai",
        "google.ai.generativelanguage",
        "google.auth",
        "openai",
        "httpx",
        "httpcore",
        "urllib3",
        "langchain",
        "langchain_core",
        "langgraph",
        "langchain_mcp_adapters",
        "mcp",
        "pydantic",
        "jsonschema",
        "chromadb",
        "hnswlib",
        "sentence_transformers",
        "filelock",
        "grpc",
        "grpc._cython",
        "multipart",
        "markdown_it",
        "markdown_it.rules_block",
        "markdown_it.rules_inline",
    ]

    library_level = logging.WARNING if root_level == logging.DEBUG else logging.ERROR
    for module_name in noisy_modules:
        logging.getLogger(module_name).setLevel(library_level)
