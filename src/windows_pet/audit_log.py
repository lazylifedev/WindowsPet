from __future__ import annotations
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    result_code: str = "ok"
    task_id: str = ""
    proposal_id: str = ""
    proposal_fingerprint: str = ""
    grant_id: str = ""
    tool_name: str = ""
    tool_version: str = ""
    operation: str = ""
    side_effect: str = ""
    confirmation_type: str = ""
    requires_admin: bool = False
    reversible: bool = False
    timestamp: str = ""


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
    def write(self, event: AuditEvent) -> None:
        self.events.append(event)


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
    def write(self, event: AuditEvent) -> None:
        record = {key: value for key, value in event.__dict__.items() if value != ""}
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
        except OSError:
            return
