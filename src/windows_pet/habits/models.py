from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


MIN_HABIT_OBSERVATIONS = 3
TIME_BUCKET_MINUTES = 15
HABIT_CONFIDENCE_THRESHOLD = 0.65
RAW_OBSERVATION_RETENTION_DAYS = 30
MAX_RAW_OBSERVATIONS_PER_PATTERN = 2


@dataclass(frozen=True)
class HabitObservation:
    observation_id: str
    event_type: str
    target: str
    weekday: int
    local_time_bucket: int
    observed_at: str
    verified_success: bool
    source: str

    @property
    def weekday_group(self) -> str:
        return "weekday" if self.weekday < 5 else "weekend"

    @property
    def pattern_key(self) -> tuple[str, str, str, int]:
        return self.event_type, self.target, self.weekday_group, self.local_time_bucket


@dataclass(frozen=True)
class Habit:
    habit_id: str
    kind: str
    target: str
    time_window: str
    weekday_mask: tuple[int, ...]
    observation_count: int
    positive_count: int
    ignored_count: int
    strength: float
    confidence: float
    last_observed_at: str
    last_used_at: str
    created_at: str

    @property
    def is_candidate(self) -> bool:
        return self.confidence >= HABIT_CONFIDENCE_THRESHOLD

    @property
    def weekday_group(self) -> str:
        return "weekday" if any(day < 5 for day in self.weekday_mask) else "weekend"


def time_bucket(value: datetime, bucket_minutes: int = TIME_BUCKET_MINUTES) -> int:
    """Return minutes since midnight rounded down to a deterministic bucket."""
    return (value.hour * 60 + value.minute) // bucket_minutes
