from __future__ import annotations

from typing import Protocol

from .models import RelationshipState, SpeechPreferences


class PersonalityRepository(Protocol):
    available: bool

    def load_state(self) -> RelationshipState: ...
    def save_state(self, state: RelationshipState) -> RelationshipState: ...
    def load_preferences(self) -> SpeechPreferences: ...
    def save_preferences(self, preferences: SpeechPreferences) -> SpeechPreferences: ...
