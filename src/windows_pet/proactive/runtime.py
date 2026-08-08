from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .engine import ProactiveEngine
from .models import ProactiveCandidate, ProactiveSettings, TriggerKind


class ProactiveRuntime:
    """Production wiring for bounded proactive candidates; content is callback-only."""

    def __init__(self, engine: ProactiveEngine, speak: Callable[[str], object], *, now: Callable[[], datetime] | None = None,
                 last_activity: Callable[[], datetime | None] | None = None,
                 lunch_start: Callable[[], datetime | None] | None = None,
                 focus_mode: Callable[[], bool] | None = None,
                 critical_operation: Callable[[], bool] | None = None):
        self.engine = engine
        self.speak = speak
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_activity = last_activity or (lambda: None)
        self.lunch_start = lunch_start or (lambda: None)
        self.focus_mode = focus_mode or (lambda: False)
        self.critical_operation = critical_operation or (lambda: False)

    def update_settings(self, settings: ProactiveSettings) -> None:
        self.engine.settings = settings

    def startup(self) -> bool:
        return self.consider(self.engine.startup_candidate(self.now()))

    def tick(self) -> bool:
        now = self.now()
        candidate = self.engine.idle_return_candidate(now, last_activity=self.last_activity())
        if candidate is not None and self.consider(candidate):
            return True
        lunch = self.engine.lunch_candidate(now, lunch_start=self.lunch_start())
        return lunch is not None and self.consider(lunch)

    def consider(self, candidate: ProactiveCandidate) -> bool:
        now = self.now()
        decision = self.engine.decide(candidate, now=now, focus_mode=bool(self.focus_mode()),
                                      critical_operation=bool(self.critical_operation()),
                                      recent_user_interaction_at=None if candidate.trigger is TriggerKind.STARTUP else self.last_activity())
        if not decision.should_speak:
            return False
        result = self.speak(self.engine.phrase(candidate))
        if result is False:
            return False
        self.engine.record_spoken(candidate, now=now)
        return True

    def maybe_offer_casual_permission(self, relationship, *, at: datetime | None = None) -> bool:
        at = at or self.now()
        if not relationship.casual_speech_candidate(at=at):
            return False
        return self.speak("もう少しくだけた話し方でも大丈夫でしょうか？") is not False
