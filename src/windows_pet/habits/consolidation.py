from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .detector import HabitDetector
from .models import MAX_RAW_OBSERVATIONS_PER_PATTERN, RAW_OBSERVATION_RETENTION_DAYS, Habit
from .repository import HabitRepository


class HabitConsolidator:
    def __init__(self, repository: HabitRepository, detector: HabitDetector | None = None):
        self.repository = repository
        self.detector = detector or HabitDetector()

    def run(self, *, now: datetime | None = None, max_raw_per_pattern: int = MAX_RAW_OBSERVATIONS_PER_PATTERN) -> list[Habit]:
        current = now or datetime.now(timezone.utc)
        candidates = self.detector.detect(self.repository.list_observations(), now=current)
        stored = [habit for candidate in candidates if (habit := self.repository.upsert_habit(candidate)) is not None]
        self.repository.delete_observations_before(current - timedelta(days=RAW_OBSERVATION_RETENTION_DAYS))
        self.repository.compact_observations(max_per_pattern=max(1, int(max_raw_per_pattern)))
        return stored

    def decay_stale(self, *, now: datetime | None = None, stale_days: int = 30, factor: float = 0.85) -> list[Habit]:
        current = now or datetime.now(timezone.utc)
        threshold = current - timedelta(days=max(1, int(stale_days)))
        decayed: list[Habit] = []
        for habit in self.repository.list_habits():
            try:
                stale = datetime.fromisoformat(habit.last_used_at) < threshold
            except ValueError:
                stale = True
            if not stale:
                continue
            updated = Habit(
                habit_id=habit.habit_id, kind=habit.kind, target=habit.target, time_window=habit.time_window,
                weekday_mask=habit.weekday_mask, observation_count=habit.observation_count,
                positive_count=habit.positive_count, ignored_count=habit.ignored_count,
                strength=max(0.0, min(1.0, habit.strength * max(0.0, min(1.0, factor)))),
                confidence=max(0.0, min(1.0, habit.confidence * max(0.0, min(1.0, factor)))),
                last_observed_at=habit.last_observed_at, last_used_at=habit.last_used_at, created_at=habit.created_at,
            )
            saved = self.repository.upsert_habit(updated)
            if saved is not None:
                decayed.append(saved)
        return decayed
