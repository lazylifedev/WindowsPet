from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from .consolidation import HabitConsolidator
from .models import TIME_BUCKET_MINUTES, Habit, HabitObservation, time_bucket
from .repository import HabitRepository

_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_SECRET = re.compile(r"(?:api[_ -]?key|password|token|credential|private key)", re.I)


def _safe_abstract(value: str, *, field: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 120 or "\n" in text or "\r" in text:
        raise ValueError(f"unsafe habit {field}")
    if field in {"event_type", "source"} and text.casefold() in {"conversation", "raw conversation", "stdout", "stderr", "raw stdout", "raw stderr"}:
        raise ValueError(f"unsafe habit {field}")
    if field == "target" and ("\\" in text or "/" in text or _EMAIL.search(text) or _SECRET.search(text)):
        raise ValueError("habit target must be abstract and local-only")
    return text


class HabitService:
    """Public API for structured habit signals; raw conversations are never accepted."""

    def __init__(self, repository: HabitRepository, consolidator: HabitConsolidator | None = None):
        self.repository = repository
        self.consolidator = consolidator or HabitConsolidator(repository)

    def observe(self, *, event_type: str, target: str, observed_at: datetime | None = None,
                verified_success: bool = True, source: str = "local_event") -> HabitObservation:
        timestamp = observed_at or datetime.now().astimezone()
        observation = HabitObservation(
            observation_id=f"observation-{secrets.token_hex(8)}",
            event_type=_safe_abstract(event_type, field="event_type"), target=_safe_abstract(target, field="target"),
            weekday=timestamp.weekday(), local_time_bucket=time_bucket(timestamp), observed_at=timestamp.isoformat(),
            verified_success=bool(verified_success), source=_safe_abstract(source, field="source"),
        )
        if self.repository.add_observation(observation) is None:
            raise RuntimeError("habit repository unavailable")
        return observation

    def consolidate(self, *, now: datetime | None = None) -> list[Habit]:
        return self.consolidator.run(now=now)

    def decay_stale(self, *, now: datetime | None = None, stale_days: int = 30) -> list[Habit]:
        return self.consolidator.decay_stale(now=now, stale_days=stale_days)

    def list_habits(self) -> list[Habit]:
        return self.repository.list_habits()

    def record_feedback(self, habit_id: str, *, positive: bool) -> Habit | None:
        habit = next((item for item in self.repository.list_habits() if item.habit_id == habit_id), None)
        if habit is None:
            return None
        positive_count = habit.positive_count + int(bool(positive))
        ignored_count = habit.ignored_count + int(not positive)
        strength = habit.strength + (0.08 if positive else -0.35)
        confidence = habit.confidence + (0.04 if positive else -0.30)
        updated = Habit(
            habit_id=habit.habit_id, kind=habit.kind, target=habit.target, time_window=habit.time_window,
            weekday_mask=habit.weekday_mask, observation_count=habit.observation_count,
            positive_count=positive_count, ignored_count=ignored_count,
            strength=max(0.0, min(1.0, strength)), confidence=max(0.0, min(1.0, confidence)),
            last_observed_at=habit.last_observed_at, last_used_at=habit.last_used_at, created_at=habit.created_at,
        )
        return self.repository.upsert_habit(updated)
