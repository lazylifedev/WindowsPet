from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import datetime, timezone

from .models import HABIT_CONFIDENCE_THRESHOLD, MIN_HABIT_OBSERVATIONS, Habit, HabitObservation


class HabitDetector:
    """Deterministic detector; no LLM or network inference is involved."""

    def __init__(self, *, min_observations: int = MIN_HABIT_OBSERVATIONS):
        self.min_observations = max(2, int(min_observations))

    def detect(self, observations: list[HabitObservation], *, now: datetime | None = None) -> list[Habit]:
        groups: dict[tuple[str, str, str, int], list[HabitObservation]] = defaultdict(list)
        for observation in observations:
            groups[observation.pattern_key].append(observation)
        current = (now or datetime.now(timezone.utc)).isoformat()
        candidates: list[Habit] = []
        for (event_type, target, _weekday_group, bucket), items in groups.items():
            unique_dates = {item.observed_at[:10] for item in items}
            if len(items) < self.min_observations or len(unique_dates) < self.min_observations:
                continue
            positive = sum(item.verified_success for item in items)
            ignored = len(items) - positive
            confidence = min(1.0, 0.45 + min(0.35, len(unique_dates) * 0.08) + (0.2 * positive / len(items)))
            if confidence < HABIT_CONFIDENCE_THRESHOLD:
                continue
            ordered = sorted(items, key=lambda item: item.observed_at)
            first = ordered[0]
            last = ordered[-1]
            start = bucket * 15
            time_window = f"{start // 60:02d}:{start % 60:02d}"
            candidates.append(Habit(
                habit_id=f"habit-{secrets.token_hex(8)}", kind=event_type, target=target,
                time_window=time_window, weekday_mask=tuple(sorted({item.weekday for item in items})),
                observation_count=len(items), positive_count=positive, ignored_count=ignored,
                strength=min(1.0, 0.45 + 0.08 * positive), confidence=confidence,
                last_observed_at=last.observed_at, last_used_at=last.observed_at,
                created_at=first.observed_at or current,
            ))
        return candidates
