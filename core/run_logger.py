import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class JsonlRunLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def file_path_for(self, session_id: str | None) -> Path:
        safe_session_id = (session_id or "unknown_session").replace("/", "_").replace("\\", "_")
        return self.log_dir / f"{safe_session_id}.jsonl"

    def log_event(self, session_id: str | None, event_type: str, **payload: Any) -> None:
        record: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }
        path = self.file_path_for(session_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
