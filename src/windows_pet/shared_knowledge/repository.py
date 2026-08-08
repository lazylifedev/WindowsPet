from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from .models import SharedSkillRecord


class SharedKnowledgeRepository(Protocol):
    available: bool

    def put(self, record: SharedSkillRecord) -> SharedSkillRecord | None: ...
    def get(self, intent: str, target: str) -> SharedSkillRecord | None: ...
    def list(self) -> list[SharedSkillRecord]: ...


class InMemorySharedKnowledgeRepository:
    def __init__(self):
        self.available = True
        self._records: dict[tuple[str, str], SharedSkillRecord] = {}

    def put(self, record: SharedSkillRecord) -> SharedSkillRecord:
        self._records[(record.intent, record.target)] = record
        return record

    def get(self, intent: str, target: str) -> SharedSkillRecord | None:
        return self._records.get((intent, target))

    def list(self) -> list[SharedSkillRecord]:
        return list(self._records.values())


class SQLiteSharedKnowledgeRepository:
    """Local cache implementation; it never contacts a server."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS shared_knowledge (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def put(self, record: SharedSkillRecord) -> SharedSkillRecord | None:
        if not self.available:
            return None
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute("INSERT OR REPLACE INTO shared_knowledge VALUES (?, ?)", (f"{record.intent}:{record.target}", json.dumps(record.__dict__)))
            return record
        except (OSError, sqlite3.DatabaseError, TypeError):
            return None

    def get(self, intent: str, target: str) -> SharedSkillRecord | None:
        if not self.available:
            return None
        try:
            with sqlite3.connect(self.path) as connection:
                row = connection.execute("SELECT data FROM shared_knowledge WHERE key=?", (f"{intent}:{target}",)).fetchone()
            if not row:
                return None
            data = json.loads(row[0])
            return SharedSkillRecord(**{**data, "aliases": tuple(data["aliases"]), "compatibility": tuple(data["compatibility"])})
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError, json.JSONDecodeError, KeyError):
            return None

    def list(self) -> list[SharedSkillRecord]:
        if not self.available:
            return []
        try:
            with sqlite3.connect(self.path) as connection:
                keys = [row[0].split(":", 1) for row in connection.execute("SELECT key FROM shared_knowledge")]
            return [record for intent, target in keys if (record := self.get(intent, target)) is not None]
        except (OSError, sqlite3.DatabaseError):
            return []
