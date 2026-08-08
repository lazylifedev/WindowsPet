from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .models import (
    ProactiveCandidate,
    ProactiveSettings,
    ProactiveState,
    ReactionKind,
    ShouldSpeakDecision,
    TriggerKind,
)
from .repository import ProactiveRepository

PHRASE_FAMILIES: dict[str, tuple[str, ...]] = {
    "morning": ("おはようございます。今日もよろしくお願いします。", "おはようございます。無理のないペースでいきましょう。"),
    "afternoon": ("こんにちは。ひと息つけていますか？", "こんにちは。午後も落ち着いて進めましょう。"),
    "evening": ("こんばんは。今日もお疲れさまでした。", "こんばんは。そろそろひと息つく時間ですね。"),
    "welcome_back": ("おかえりなさい。", "お戻りですね。お疲れさまです。"),
    "lunch": ("もうすぐお昼休みですね。あと少しです。", "お昼休みが近づいてきました。無理せず進めましょう。"),
    "check_in": ("少し休憩してみませんか？", "必要なら、ひと息つく時間にしましょう。"),
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class ProactiveEngine:
    """Creates local candidates and applies all anti-annoyance gates before speech."""

    def __init__(self, repository: ProactiveRepository, settings: ProactiveSettings | None = None):
        self.repository = repository
        self.settings = settings or ProactiveSettings()

    def candidate(self, *, trigger: TriggerKind, category: str, phrase_family: str, now: datetime,
                  score: float = 0.7, reason: str = "", habit_confidence: float = 0.0) -> ProactiveCandidate:
        return ProactiveCandidate(category, trigger, phrase_family, max(0.0, min(1.0, score)), now.isoformat(), reason, max(0.0, min(1.0, habit_confidence)))

    def startup_candidate(self, now: datetime) -> ProactiveCandidate:
        family = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
        trigger = TriggerKind.MORNING if family == "morning" else TriggerKind.AFTERNOON if family == "afternoon" else TriggerKind.EVENING
        return self.candidate(trigger=TriggerKind.STARTUP, category="startup", phrase_family=family, now=now, score=0.65, reason="application startup")

    def lunch_candidate(self, now: datetime, *, lunch_start: datetime | None) -> ProactiveCandidate | None:
        if lunch_start is None:
            return None
        window_start = lunch_start - timedelta(minutes=10)
        window_end = lunch_start - timedelta(minutes=5)
        if not window_start <= now <= window_end:
            return None
        return self.candidate(trigger=TriggerKind.LUNCH_SOON, category="lunch", phrase_family="lunch", now=now, score=0.7, reason="explicit lunch_start memory")

    def idle_return_candidate(self, now: datetime, *, last_activity: datetime | None, threshold_minutes: int = 60) -> ProactiveCandidate | None:
        if last_activity is None or now - last_activity < timedelta(minutes=max(1, threshold_minutes)):
            return None
        return self.candidate(trigger=TriggerKind.IDLE_RETURN, category="idle_return", phrase_family="welcome_back", now=now, score=0.72, reason="long idle return")

    def decide(self, candidate: ProactiveCandidate, *, now: datetime, focus_mode: bool = False,
               critical_operation: bool = False, recent_user_interaction_at: datetime | None = None) -> ShouldSpeakDecision:
        state = self._reset_daily_if_needed(self.repository.load_state(), now)
        settings = self.settings
        if not settings.enabled or getattr(settings, "speech_level", "normal") == "off":
            return ShouldSpeakDecision(False, 0.0, "disabled_by_user", candidate)
        if candidate.category in state.disabled_categories:
            return ShouldSpeakDecision(False, 0.0, "category_disabled", candidate)
        if settings.is_quiet(now.timetz().replace(tzinfo=None)):
            return ShouldSpeakDecision(False, 0.0, "quiet_hours", candidate)
        if settings.suppress_during_focus and focus_mode:
            return ShouldSpeakDecision(False, 0.0, "focus_suppression", candidate)
        if settings.suppress_during_critical_operation and critical_operation:
            return ShouldSpeakDecision(False, 0.0, "critical_operation", candidate)
        if recent_user_interaction_at and now - recent_user_interaction_at < timedelta(minutes=settings.recent_interaction_suppression_minutes):
            return ShouldSpeakDecision(False, 0.0, "recent_interaction", candidate)
        cooldown = _parse(state.cooldown_until)
        if cooldown and now < cooldown:
            return ShouldSpeakDecision(False, 0.0, "cooldown", candidate)
        daily_cap = min(settings.daily_cap, 1) if getattr(settings, "speech_level", "normal") == "low" else settings.daily_cap
        if state.daily_count >= max(0, daily_cap):
            return ShouldSpeakDecision(False, 0.0, "daily_cap", candidate)
        ignored = state.ignored_count_by_category.get(candidate.category, 0)
        score = max(0.0, candidate.score - min(0.5, ignored * 0.12) + candidate.habit_confidence * 0.1)
        if score < 0.5:
            return ShouldSpeakDecision(False, score, "ignored_history_penalty", candidate)
        return ShouldSpeakDecision(True, score, "eligible", candidate)

    def record_spoken(self, candidate: ProactiveCandidate, *, now: datetime) -> ProactiveState:
        state = self._reset_daily_if_needed(self.repository.load_state(), now)
        updated = replace(state, last_spoken_at=now.isoformat(), daily_date=now.date().isoformat(), daily_count=state.daily_count + 1,
                          last_trigger=candidate.trigger.value, cooldown_until=(now + timedelta(minutes=self.settings.minimum_cooldown_minutes)).isoformat())
        return self.repository.save_state(updated)

    def record_reaction(self, candidate: ProactiveCandidate, reaction: ReactionKind) -> ProactiveState:
        state = self.repository.load_state()
        ignored = dict(state.ignored_count_by_category)
        positive = dict(state.positive_count_by_category)
        disabled = set(state.disabled_categories)
        if reaction in {ReactionKind.IGNORE, ReactionKind.DISMISS, ReactionKind.EXPLICIT_NEGATIVE}:
            ignored[candidate.category] = ignored.get(candidate.category, 0) + 1
        if reaction in {ReactionKind.REPLY, ReactionKind.EXPLICIT_POSITIVE}:
            positive[candidate.category] = positive.get(candidate.category, 0) + 1
        if reaction is ReactionKind.EXPLICIT_NEGATIVE:
            disabled.add(candidate.category)
        return self.repository.save_state(replace(state, ignored_count_by_category=ignored, positive_count_by_category=positive, disabled_categories=tuple(sorted(disabled))))

    def phrase(self, candidate: ProactiveCandidate) -> str:
        family = PHRASE_FAMILIES.get(candidate.phrase_family, PHRASE_FAMILIES["check_in"])
        index = int(hashlib.sha256(f"{candidate.phrase_family}:{candidate.created_at}".encode()).hexdigest(), 16) % len(family)
        return family[index]

    @staticmethod
    def _reset_daily_if_needed(state: ProactiveState, now: datetime) -> ProactiveState:
        if state.daily_date == now.date().isoformat():
            return state
        return replace(state, daily_date=now.date().isoformat(), daily_count=0)
