from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum


class TriggerKind(StrEnum):
    STARTUP = "startup"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    LUNCH_SOON = "lunch_soon"
    IDLE_RETURN = "idle_return"
    CONTINUOUS_USAGE = "continuous_usage"
    HABIT_OPPORTUNITY = "habit_opportunity"
    CHECK_IN = "check_in"


class ReactionKind(StrEnum):
    REPLY = "reply"
    DISMISS = "dismiss"
    IGNORE = "ignore"
    EXPLICIT_POSITIVE = "explicit_positive"
    EXPLICIT_NEGATIVE = "explicit_negative"


@dataclass(frozen=True)
class ProactiveCandidate:
    category: str
    trigger: TriggerKind
    phrase_family: str
    score: float
    created_at: str
    reason: str
    habit_confidence: float = 0.0


@dataclass(frozen=True)
class ProactiveSettings:
    enabled: bool = True
    minimum_cooldown_minutes: int = 30
    daily_cap: int = 3
    quiet_start: time | None = None
    quiet_end: time | None = None
    suppress_during_focus: bool = True
    suppress_during_critical_operation: bool = True
    recent_interaction_suppression_minutes: int = 10

    def is_quiet(self, current: time) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        if self.quiet_start <= self.quiet_end:
            return self.quiet_start <= current < self.quiet_end
        return current >= self.quiet_start or current < self.quiet_end


@dataclass(frozen=True)
class ProactiveState:
    last_spoken_at: str | None = None
    daily_date: str | None = None
    daily_count: int = 0
    last_trigger: str | None = None
    cooldown_until: str | None = None
    ignored_count_by_category: dict[str, int] = field(default_factory=dict)
    positive_count_by_category: dict[str, int] = field(default_factory=dict)
    disabled_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShouldSpeakDecision:
    should_speak: bool
    score: float
    reason: str
    candidate: ProactiveCandidate
