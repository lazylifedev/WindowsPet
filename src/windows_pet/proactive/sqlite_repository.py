from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ProactiveState


class SQLiteProactiveRepository:
    """Small local state store; speech bodies are intentionally not persisted."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS proactive_state (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        last_spoken_at TEXT,
                        daily_date TEXT,
                        daily_count INTEGER NOT NULL,
                        last_trigger TEXT,
                        cooldown_until TEXT,
                        ignored_json TEXT NOT NULL,
                        positive_json TEXT NOT NULL,
                        disabled_json TEXT NOT NULL
                    )
                """)
                connection.execute("INSERT OR IGNORE INTO proactive_state VALUES (1,NULL,NULL,0,NULL,NULL,'{}','{}','[]')")
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def load_state(self) -> ProactiveState:
        if not self.available:
            return ProactiveState()
        try:
            with sqlite3.connect(self.path) as connection:
                row = connection.execute("SELECT * FROM proactive_state WHERE id=1").fetchone()
            if row is None:
                return ProactiveState()
            return ProactiveState(
                last_spoken_at=row[1], daily_date=row[2], daily_count=int(row[3]), last_trigger=row[4],
                cooldown_until=row[5], ignored_count_by_category=dict(json.loads(row[6])),
                positive_count_by_category=dict(json.loads(row[7])), disabled_categories=tuple(json.loads(row[8])),
            )
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError, json.JSONDecodeError):
            return ProactiveState()

    def save_state(self, state: ProactiveState) -> ProactiveState:
        if not self.available:
            return state
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute("""
                    UPDATE proactive_state SET last_spoken_at=?, daily_date=?, daily_count=?, last_trigger=?,
                    cooldown_until=?, ignored_json=?, positive_json=?, disabled_json=? WHERE id=1
                """, (
                    state.last_spoken_at, state.daily_date, state.daily_count, state.last_trigger, state.cooldown_until,
                    json.dumps(state.ignored_count_by_category), json.dumps(state.positive_count_by_category),
                    json.dumps(state.disabled_categories),
                ))
            return state
        except (OSError, sqlite3.DatabaseError, TypeError):
            return state
