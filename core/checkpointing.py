import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph.checkpoint.memory import MemorySaver

from core.config import AgentConfig

logger = logging.getLogger("agent")


@dataclass
class CheckpointRuntime:
    backend: str
    resolved_backend: str
    target: str
    checkpointer: Any
    warnings: list[str] = field(default_factory=list)
    close_callback: Optional[Callable[[], Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "resolved_backend": self.resolved_backend,
            "target": self.target,
            "warnings": list(self.warnings),
        }

    async def aclose(self) -> None:
        if self.close_callback is None:
            return
        result = self.close_callback()
        if inspect.isawaitable(result):
            await result


async def _maybe_setup(checkpointer: Any) -> None:
    setup = getattr(checkpointer, "setup", None)
    if not callable(setup):
        return
    result = setup()
    if inspect.isawaitable(result):
        await result


async def create_checkpoint_runtime(config: AgentConfig) -> CheckpointRuntime:
    backend = config.checkpoint_backend

    if backend == "memory":
        return CheckpointRuntime(
            backend=backend,
            resolved_backend="memory",
            target="in-memory",
            checkpointer=MemorySaver(),
        )

    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError:
            warning = (
                "SQLite checkpointer is unavailable because 'langgraph-checkpoint-sqlite' is not installed. "
                "Falling back to MemorySaver."
            )
            logger.warning(warning)
            return CheckpointRuntime(
                backend=backend,
                resolved_backend="memory",
                target="in-memory",
                checkpointer=MemorySaver(),
                warnings=[warning],
            )

        db_path = Path(config.checkpoint_sqlite_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        context_manager = AsyncSqliteSaver.from_conn_string(str(db_path))
        checkpointer = await context_manager.__aenter__()
        await _maybe_setup(checkpointer)
        return CheckpointRuntime(
            backend=backend,
            resolved_backend="sqlite",
            target=str(db_path),
            checkpointer=checkpointer,
            close_callback=lambda: context_manager.__aexit__(None, None, None),
        )

    if backend == "postgres":
        if not config.checkpoint_postgres_url:
            raise ValueError("CHECKPOINT_POSTGRES_URL is required when CHECKPOINT_BACKEND=postgres.")
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError:
            warning = (
                "Postgres checkpointer is unavailable because 'langgraph-checkpoint-postgres' is not installed. "
                "Falling back to MemorySaver."
            )
            logger.warning(warning)
            return CheckpointRuntime(
                backend=backend,
                resolved_backend="memory",
                target="in-memory",
                checkpointer=MemorySaver(),
                warnings=[warning],
            )

        context_manager = AsyncPostgresSaver.from_conn_string(config.checkpoint_postgres_url)
        checkpointer = await context_manager.__aenter__()
        await _maybe_setup(checkpointer)
        return CheckpointRuntime(
            backend=backend,
            resolved_backend="postgres",
            target=config.checkpoint_postgres_url,
            checkpointer=checkpointer,
            close_callback=lambda: context_manager.__aexit__(None, None, None),
        )

    raise ValueError(f"Unsupported checkpoint backend: {backend}")
