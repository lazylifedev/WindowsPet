from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .models import CasualPermission, RelationshipStage, RelationshipState, SpeechPreferences
from .repository import PersonalityRepository


def _dt(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


class RelationshipService:
    """Slow, local relationship progression; chat volume alone is insufficient."""

    def __init__(self, repository: PersonalityRepository):
        self.repository = repository

    def state(self) -> RelationshipState:
        return self.repository.load_state()

    def preferences(self) -> SpeechPreferences:
        return self.repository.load_preferences()

    def record_interaction(self, *, at: datetime, meaningful: bool = False, verified_assistance_success: bool = False,
                           positive: bool = False, negative: bool = False, explicit_preference: bool = False) -> RelationshipState:
        state = self.state()
        first_seen = state.first_seen_at or at.isoformat()
        previous = _dt(state.last_interaction_at)
        days_used = state.days_used + int(previous is None or previous.date() != at.date())
        familiarity = state.familiarity + 0.015 * int(meaningful) + 0.02 * int(verified_assistance_success) + 0.01 * int(positive) - 0.06 * int(negative)
        familiarity = max(0.0, min(1.0, familiarity))
        updated = replace(state, first_seen_at=first_seen, last_interaction_at=at.isoformat(), days_used=days_used,
                          meaningful_interaction_count=state.meaningful_interaction_count + int(meaningful),
                          verified_assistance_success=state.verified_assistance_success + int(verified_assistance_success),
                          positive_feedback=state.positive_feedback + int(positive), negative_feedback=state.negative_feedback + int(negative),
                          explicit_preference_count=state.explicit_preference_count + int(explicit_preference), familiarity=familiarity)
        return self.repository.save_state(replace(updated, stage=self._stage(updated)))

    def update_preferences(self, **changes: str) -> SpeechPreferences:
        current = self.preferences()
        allowed = set(SpeechPreferences().__dict__)
        if set(changes) - allowed:
            raise ValueError("unsupported speech preference")
        return self.repository.save_preferences(replace(current, **{key: str(value)[:40] for key, value in changes.items()}))

    def casual_speech_candidate(self, *, at: datetime) -> bool:
        state = self.state()
        if state.casual_speech_permission not in {CasualPermission.UNKNOWN, CasualPermission.ASK_LATER}:
            return False
        if state.casual_speech_permission is CasualPermission.UNKNOWN and state.casual_permission_asked_at is not None and state.casual_permission_cooldown_until is None:
            return False
        cooldown = _dt(state.casual_permission_cooldown_until)
        if cooldown and at < cooldown:
            return False
        if state.stage not in {RelationshipStage.COMFORTABLE, RelationshipStage.PARTNER, RelationshipStage.LONG_TERM_PARTNER}:
            return False
        candidate = replace(state, casual_permission_asked_at=at.isoformat(), casual_permission_cooldown_until=None)
        self.repository.save_state(candidate)
        return True

    def respond_to_casual_permission(self, result: CasualPermission, *, at: datetime) -> RelationshipState:
        if result not in set(CasualPermission):
            raise ValueError("invalid casual permission")
        state = self.state()
        cooldown = at + timedelta(days=30) if result is CasualPermission.ASK_LATER else None
        return self.repository.save_state(replace(state, casual_speech_permission=result, casual_permission_cooldown_until=cooldown.isoformat() if cooldown else None))

    def bounded_context(self) -> dict[str, object]:
        return self.state().bounded_context(self.preferences())

    @staticmethod
    def _stage(state: RelationshipState) -> RelationshipStage:
        if state.days_used >= 180 and state.meaningful_interaction_count >= 40 and state.familiarity >= 0.85:
            return RelationshipStage.LONG_TERM_PARTNER
        if state.days_used >= 60 and state.meaningful_interaction_count >= 20 and state.verified_assistance_success >= 10 and state.familiarity >= 0.65:
            return RelationshipStage.PARTNER
        if state.days_used >= 14 and state.meaningful_interaction_count >= 8 and state.familiarity >= 0.35:
            return RelationshipStage.COMFORTABLE
        if state.days_used >= 2 or state.meaningful_interaction_count >= 1:
            return RelationshipStage.ACQUAINTED
        return RelationshipStage.FIRST_MEETING
