from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RelationshipStage(StrEnum):
    FIRST_MEETING = "first_meeting"
    ACQUAINTED = "acquainted"
    COMFORTABLE = "comfortable"
    PARTNER = "partner"
    LONG_TERM_PARTNER = "long_term_partner"


class CasualPermission(StrEnum):
    UNKNOWN = "unknown"
    ALLOW = "allow"
    KEEP_POLITE = "keep_polite"
    ASK_LATER = "ask_later"


@dataclass(frozen=True)
class SpeechPreferences:
    formality: str = "polite"
    message_length: str = "moderate"
    emoji: str = "low"
    directness: str = "respectful"
    encouragement_frequency: str = "moderate"
    humor_tolerance: str = "low"

    def bounded_context(self) -> dict[str, str]:
        return {
            "formality": self.formality, "verbosity": "short" if self.message_length == "short" else self.message_length,
            "emoji": self.emoji, "directness": self.directness,
            "encouragement_frequency": self.encouragement_frequency, "humor_tolerance": self.humor_tolerance,
        }


@dataclass(frozen=True)
class RelationshipState:
    stage: RelationshipStage = RelationshipStage.FIRST_MEETING
    first_seen_at: str | None = None
    last_interaction_at: str | None = None
    days_used: int = 0
    meaningful_interaction_count: int = 0
    verified_assistance_success: int = 0
    positive_feedback: int = 0
    negative_feedback: int = 0
    explicit_preference_count: int = 0
    familiarity: float = 0.0
    casual_speech_permission: CasualPermission = CasualPermission.UNKNOWN
    casual_permission_asked_at: str | None = None
    casual_permission_cooldown_until: str | None = None

    def bounded_context(self, preferences: SpeechPreferences) -> dict[str, object]:
        return {
            "relationship": self.stage.value,
            "familiarity": round(self.familiarity, 2),
            "casual_speech_permission": self.casual_speech_permission.value,
            "speech_preferences": preferences.bounded_context(),
        }
