from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Habit, HabitObservation


def _utc(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


class SQLiteHabitRepository:
    """Fault-tolerant local store for structured habit metadata only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS habit_observations (
                        observation_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        target TEXT NOT NULL,
                        weekday INTEGER NOT NULL,
                        local_time_bucket INTEGER NOT NULL,
                        observed_at TEXT NOT NULL,
                        verified_success INTEGER NOT NULL,
                        source TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS habits (
                        habit_id TEXT PRIMARY KEY,
                        kind TEXT NOT NULL,
                        target TEXT NOT NULL,
                        time_window TEXT NOT NULL,
                        weekday_mask TEXT NOT NULL,
                        observation_count INTEGER NOT NULL,
                        positive_count INTEGER NOT NULL,
                        ignored_count INTEGER NOT NULL,
                        strength REAL NOT NULL,
                        confidence REAL NOT NULL,
                        last_observed_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    @staticmethod
    def _observation(row) -> HabitObservation:
        return HabitObservation(
            observation_id=row[0], event_type=row[1], target=row[2], weekday=int(row[3]),
            local_time_bucket=int(row[4]), observed_at=row[5], verified_success=bool(row[6]), source=row[7],
        )

    @staticmethod
    def _habit(row) -> Habit:
        import json
        return Habit(
            habit_id=row[0], kind=row[1], target=row[2], time_window=row[3],
            weekday_mask=tuple(int(item) for item in json.loads(row[4])), observation_count=int(row[5]),
            positive_count=int(row[6]), ignored_count=int(row[7]), strength=float(row[8]),
            confidence=float(row[9]), last_observed_at=row[10], last_used_at=row[11], created_at=row[12],
        )

    def add_observation(self, observation: HabitObservation) -> HabitObservation | None:
        if not self.available:
            return None
        try:
            with self._connect() as connection:
                connection.execute("INSERT OR IGNORE INTO habit_observations VALUES (?,?,?,?,?,?,?,?)", (
                    observation.observation_id, observation.event_type, observation.target, observation.weekday,
                    observation.local_time_bucket, observation.observed_at, int(observation.verified_success), observation.source,
                ))
            return observation
        except (OSError, sqlite3.DatabaseError):
            return None

    def list_observations(self) -> list[HabitObservation]:
        if not self.available:
            return []
        try:
            with self._connect() as connection:
                return [self._observation(row) for row in connection.execute(
                    "SELECT * FROM habit_observations ORDER BY observed_at, observation_id")]
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return []

    def upsert_habit(self, habit: Habit) -> Habit | None:
        if not self.available:
            return None
        import json
        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO habits VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(habit_id) DO UPDATE SET
                    kind=excluded.kind, target=excluded.target, time_window=excluded.time_window,
                    weekday_mask=excluded.weekday_mask, observation_count=excluded.observation_count,
                    positive_count=excluded.positive_count, ignored_count=excluded.ignored_count,
                    strength=excluded.strength, confidence=excluded.confidence,
                    last_observed_at=excluded.last_observed_at, last_used_at=excluded.last_used_at
                """, (
                    habit.habit_id, habit.kind, habit.target, habit.time_window, json.dumps(habit.weekday_mask),
                    habit.observation_count, habit.positive_count, habit.ignored_count, habit.strength,
                    habit.confidence, habit.last_observed_at, habit.last_used_at, habit.created_at,
                ))
            return habit
        except (OSError, sqlite3.DatabaseError, TypeError):
            return None

    def list_habits(self) -> list[Habit]:
        if not self.available:
            return []
        try:
            with self._connect() as connection:
                return [self._habit(row) for row in connection.execute("SELECT * FROM habits ORDER BY strength DESC, habit_id")]
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
            return []

    def delete_observations_before(self, cutoff: datetime) -> int:
        if not self.available:
            return 0
        try:
            with self._connect() as connection:
                return connection.execute("DELETE FROM habit_observations WHERE observed_at < ?", (_utc(cutoff),)).rowcount
        except (OSError, sqlite3.DatabaseError):
            return 0

    def compact_observations(self, *, max_per_pattern: int) -> int:
        if not self.available:
            return 0
        removed = 0
        try:
            with self._connect() as connection:
                groups = connection.execute("SELECT DISTINCT event_type, target, CASE WHEN weekday < 5 THEN 0 ELSE 1 END FROM habit_observations").fetchall()
                for event_type, target, weekday_group in groups:
                    rows = connection.execute(
                        "SELECT observation_id FROM habit_observations WHERE event_type=? AND target=? AND CASE WHEN weekday < 5 THEN 0 ELSE 1 END=? ORDER BY observed_at DESC, observation_id DESC",
                        (event_type, target, weekday_group),
                    ).fetchall()
                    for (observation_id,) in rows[max(0, int(max_per_pattern)):]:
                        removed += connection.execute("DELETE FROM habit_observations WHERE observation_id=?", (observation_id,)).rowcount
            return removed
        except (OSError, sqlite3.DatabaseError):
            return removed

    def delete_habit(self, habit_id: str) -> bool:
        if not self.available:
            return False
        try:
            with self._connect() as connection:
                return connection.execute("DELETE FROM habits WHERE habit_id=?", (habit_id,)).rowcount == 1
        except (OSError, sqlite3.DatabaseError):
            return False
