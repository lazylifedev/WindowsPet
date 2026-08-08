from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from .sanitizer import SharedKnowledgeSanitizer


@dataclass(frozen=True)
class QueuedUpload:
    event_id: str
    kind: str
    payload: dict
    created_at: str
    retry_count: int
    next_attempt_at: str


class SharedKnowledgeUploadQueue:
    """Bounded durable queue containing sanitized abstract records only."""

    def __init__(self, path: str | Path, *, max_items: int = 100, max_age_days: int = 30):
        self.path = Path(path)
        self.max_items = max(1, max_items)
        self.max_age = timedelta(days=max(1, max_age_days))
        self.sanitizer = SharedKnowledgeSanitizer()
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS shared_upload_queue (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, retry_count INTEGER NOT NULL, next_attempt_at TEXT NOT NULL)")
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def enqueue_candidate(self, event_id: str, candidate: Mapping[str, object], *, verified_success: bool, global_eligible: bool) -> bool:
        if not self.available or not verified_success or not global_eligible:
            return False
        decision = self.sanitizer.sanitize(candidate)
        if not decision.eligible or decision.record is None:
            return False
        now = datetime.now(timezone.utc)
        payload = {
            "intent": decision.record.intent,
            "target_type": decision.record.target_type,
            "target": decision.record.target,
            "aliases": list(decision.record.aliases),
            "compatibility": list(decision.record.compatibility),
            "verified_success": True,
        }
        try:
            with sqlite3.connect(self.path) as connection:
                self._prune(connection, now)
                exists = connection.execute("SELECT 1 FROM shared_upload_queue WHERE event_id=?", (event_id,)).fetchone()
                if exists:
                    return False
                count = connection.execute("SELECT COUNT(*) FROM shared_upload_queue").fetchone()[0]
                if count >= self.max_items:
                    return False
                connection.execute("INSERT INTO shared_upload_queue VALUES (?, ?, ?, ?, 0, ?)", (event_id, "candidate", json.dumps(payload), now.isoformat(), now.isoformat()))
            return True
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
            return False

    def list_ready(self, *, now: datetime | None = None, limit: int = 20) -> list[QueuedUpload]:
        if not self.available:
            return []
        current = now or datetime.now(timezone.utc)
        try:
            with sqlite3.connect(self.path) as connection:
                self._prune(connection, current)
                rows = connection.execute("SELECT event_id, kind, payload, created_at, retry_count, next_attempt_at FROM shared_upload_queue WHERE next_attempt_at <= ? ORDER BY created_at LIMIT ?", (current.isoformat(), max(1, min(limit, self.max_items)))).fetchall()
            return [QueuedUpload(row[0], row[1], json.loads(row[2]), row[3], row[4], row[5]) for row in rows]
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def flush(self, sender: Callable[[QueuedUpload], bool], *, now: datetime | None = None, limit: int = 20) -> tuple[int, int]:
        sent = failed = 0
        current = now or datetime.now(timezone.utc)
        for item in self.list_ready(now=current, limit=limit):
            try:
                ok = bool(sender(item))
            except Exception:
                ok = False
            try:
                with sqlite3.connect(self.path) as connection:
                    if ok:
                        connection.execute("DELETE FROM shared_upload_queue WHERE event_id=?", (item.event_id,))
                        sent += 1
                    else:
                        retry = item.retry_count + 1
                        next_attempt = current + timedelta(minutes=min(60, 2 ** min(retry, 6)))
                        connection.execute("UPDATE shared_upload_queue SET retry_count=?, next_attempt_at=? WHERE event_id=?", (retry, next_attempt.isoformat(), item.event_id))
                        failed += 1
            except (OSError, sqlite3.DatabaseError):
                failed += 1
        return sent, failed

    def _prune(self, connection: sqlite3.Connection, now: datetime) -> None:
        cutoff = (now - self.max_age).isoformat()
        connection.execute("DELETE FROM shared_upload_queue WHERE created_at < ?", (cutoff,))
