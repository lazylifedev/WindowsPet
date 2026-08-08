from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def normalize_skill_alias(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return " ".join(text.casefold().split())


def _safe_alias(value: str) -> bool:
    normalized = normalize_skill_alias(value)
    if not normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        return False
    return not any(marker in normalized for marker in ("sk-", "api_key", "apikey", "password", "token="))


def _safe_target(value: str) -> bool:
    text = str(value).strip()
    return bool(text) and len(text) <= 200 and not any(marker in text for marker in ("\\", "/", ":", "%"))


@dataclass(frozen=True)
class LocalSkill:
    skill_id: str
    intent: str
    target_type: str
    target: str
    aliases: tuple[str, ...]
    success_count: int
    failure_count: int
    last_used_at: str
    memory_strength: float
    scope: str


class LocalSkillStore:
    """Small local-only store for abstract, verified action skills."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS skills (
                        skill_id TEXT PRIMARY KEY,
                        intent TEXT NOT NULL,
                        target_type TEXT NOT NULL,
                        target TEXT NOT NULL,
                        aliases_json TEXT NOT NULL,
                        success_count INTEGER NOT NULL DEFAULT 0,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        last_used_at TEXT NOT NULL DEFAULT '',
                        memory_strength REAL NOT NULL DEFAULT 0.5,
                        scope TEXT NOT NULL DEFAULT 'local'
                    )
                """)
        except (OSError, sqlite3.DatabaseError):
            self._available = False

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    @staticmethod
    def _row(row) -> LocalSkill:
        return LocalSkill(row[0], row[1], row[2], row[3], tuple(json.loads(row[4])),
                           int(row[5]), int(row[6]), row[7], float(row[8]), row[9])

    def _read(self, query: str, parameters=()) -> list[LocalSkill]:
        if not self._available:
            return []
        try:
            with self._connect() as connection:
                return [self._row(row) for row in connection.execute(query, parameters)]
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return []

    def find_alias(self, alias: str) -> LocalSkill | None:
        normalized = normalize_skill_alias(alias)
        if not _safe_alias(normalized):
            return None
        matches = [skill for skill in self._read("SELECT * FROM skills ORDER BY skill_id")
                   if normalized in skill.aliases]
        return matches[0] if len(matches) == 1 else None

    def record_success(self, *, intent: str, target_type: str, target: str, alias: str) -> bool:
        return self._record(intent=intent, target_type=target_type, target=target, alias=alias, success=True)

    def record_failure(self, *, intent: str, target_type: str, target: str, alias: str) -> bool:
        return self._record(intent=intent, target_type=target_type, target=target, alias=alias, success=False)

    def _record(self, *, intent: str, target_type: str, target: str, alias: str, success: bool) -> bool:
        normalized = normalize_skill_alias(alias)
        if not self._available or not _safe_alias(normalized) or not _safe_target(target) or not intent or not target_type:
            return False
        skill_id = f"{intent}:{target_type}:{normalize_skill_alias(target)}"
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT aliases_json, success_count, failure_count, memory_strength FROM skills WHERE skill_id = ?", (skill_id,)).fetchone()
                if row is None:
                    aliases = [normalized]
                    successes = 1 if success else 0
                    failures = 0 if success else 1
                    strength = 0.55 if success else 0.4
                    connection.execute("INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                       (skill_id, intent, target_type, target, json.dumps(aliases, ensure_ascii=False), successes, failures, now, strength, "local"))
                else:
                    aliases = list(json.loads(row[0]))
                    if normalized not in aliases:
                        aliases.append(normalized)
                    successes = int(row[1]) + (1 if success else 0)
                    failures = int(row[2]) + (0 if success else 1)
                    strength = min(1.0, max(0.0, float(row[3]) + (0.05 if success else -0.1)))
                    connection.execute("UPDATE skills SET aliases_json=?, success_count=?, failure_count=?, last_used_at=?, memory_strength=? WHERE skill_id=?",
                                       (json.dumps(aliases, ensure_ascii=False), successes, failures, now, strength, skill_id))
                return True
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return False
