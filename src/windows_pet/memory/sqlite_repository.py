from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import MemoryKind, MemoryRecord


def _utc(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


class SQLiteMemoryRepository:
    """Fault-tolerant local store; it never performs network I/O."""

    available: bool

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS personal_memories (
                        memory_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        category TEXT NOT NULL,
                        memory_key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        expires_at TEXT,
                        importance REAL NOT NULL,
                        strength REAL NOT NULL,
                        protected INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        use_count INTEGER NOT NULL,
                        reinforcement_count INTEGER NOT NULL,
                        UNIQUE(kind, category, memory_key)
                    )
                """)
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    @staticmethod
    def _row(row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row[0], kind=MemoryKind(row[1]), category=row[2], key=row[3], value=row[4],
            created_at=row[5], updated_at=row[6], last_used_at=row[7], expires_at=row[8],
            importance=float(row[9]), strength=float(row[10]), protected=bool(row[11]),
            source=row[12], confidence=float(row[13]), use_count=int(row[14]),
            reinforcement_count=int(row[15]),
        )

    def _read(self, query: str, parameters=()) -> list[MemoryRecord]:
        if not self.available:
            return []
        try:
            with self._connect() as connection:
                return [self._row(row) for row in connection.execute(query, parameters)]
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return []

    def upsert(self, record: MemoryRecord) -> MemoryRecord | None:
        if not self.available:
            return None
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT memory_id, created_at, reinforcement_count, strength, use_count FROM personal_memories WHERE kind=? AND category=? AND memory_key=?",
                    (record.kind.value, record.category, record.key),
                ).fetchone()
                if existing:
                    record = MemoryRecord(
                        memory_id=existing[0], kind=record.kind, category=record.category, key=record.key,
                        value=record.value, created_at=existing[1], updated_at=record.updated_at,
                        last_used_at=record.last_used_at, expires_at=record.expires_at,
                        importance=record.importance, strength=min(1.0, max(record.strength, float(existing[3]) + 0.05)),
                        protected=record.protected, source=record.source, confidence=record.confidence,
                        use_count=int(existing[4]), reinforcement_count=int(existing[2]) + 1,
                    )
                    connection.execute("""UPDATE personal_memories SET value=?, updated_at=?, last_used_at=?, expires_at=?, importance=?, strength=?, protected=?, source=?, confidence=?, reinforcement_count=? WHERE memory_id=?""",
                                       (record.value, record.updated_at, record.last_used_at, record.expires_at, record.importance, record.strength, int(record.protected), record.source, record.confidence, record.reinforcement_count, record.memory_id))
                else:
                    connection.execute("""INSERT INTO personal_memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                       (record.memory_id, record.kind.value, record.category, record.key, record.value, record.created_at, record.updated_at, record.last_used_at, record.expires_at, record.importance, record.strength, int(record.protected), record.source, record.confidence, record.use_count, record.reinforcement_count))
                return record
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return None

    def list(self, *, category: str | None = None, kind: str | None = None) -> list[MemoryRecord]:
        clauses, params = [], []
        if category:
            clauses.append("category=?"); params.append(category)
        if kind:
            clauses.append("kind=?"); params.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._read(f"SELECT * FROM personal_memories{where} ORDER BY updated_at DESC, memory_id", params)

    def lookup(self, query: str | None, *, limit: int, max_chars: int) -> list[MemoryRecord]:
        records = self.list()
        now = _utc()
        words = [word.casefold() for word in str(query or "").split() if word.strip()]
        matches = [record for record in records if not record.expires_at or record.expires_at > now]
        if words:
            matches = [record for record in matches if all(word in f"{record.key} {record.value}".casefold() for word in words)]
        result, chars = [], 0
        for record in matches[:max(0, int(limit))]:
            cost = len(record.key) + len(record.value)
            if result and chars + cost > max(0, int(max_chars)):
                break
            if not result and cost > max(0, int(max_chars)):
                continue
            result.append(record); chars += cost
        return result

    def delete(self, memory_id: str) -> bool:
        if not self.available:
            return False
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM personal_memories WHERE memory_id=?", (memory_id,))
                return cursor.rowcount == 1
        except (OSError, sqlite3.DatabaseError):
            return False

    def delete_expired(self, now: datetime) -> int:
        if not self.available:
            return 0
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM personal_memories WHERE expires_at IS NOT NULL AND expires_at <= ? AND protected=0", (_utc(now),))
                return cursor.rowcount
        except (OSError, sqlite3.DatabaseError):
            return 0

    def cleanup_candidates(self, now: datetime, *, stale_before: datetime, min_strength: float) -> list[MemoryRecord]:
        return self._read("SELECT * FROM personal_memories WHERE protected=0 AND last_used_at < ? AND strength <= ? AND (expires_at IS NULL OR expires_at > ?) ORDER BY strength, last_used_at",
                          (_utc(stale_before), float(min_strength), _utc(now)))
