from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import CasualPermission, RelationshipStage, RelationshipState, SpeechPreferences


class SQLitePersonalityRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS relationship_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS speech_preferences (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
                connection.execute("INSERT OR IGNORE INTO relationship_state VALUES (1, ?)", (json.dumps({}),))
                connection.execute("INSERT OR IGNORE INTO speech_preferences VALUES (1, ?)", (json.dumps({}),))
        except (OSError, sqlite3.DatabaseError):
            self.available = False

    def _read(self, table: str) -> dict:
        if not self.available:
            return {}
        try:
            with sqlite3.connect(self.path) as connection:
                row = connection.execute(f"SELECT data FROM {table} WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {}
        except (OSError, sqlite3.DatabaseError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def load_state(self) -> RelationshipState:
        data = self._read("relationship_state")
        try:
            return RelationshipState(
                stage=RelationshipStage(data.get("stage", RelationshipStage.FIRST_MEETING.value)),
                first_seen_at=data.get("first_seen_at"), last_interaction_at=data.get("last_interaction_at"),
                days_used=int(data.get("days_used", 0)), meaningful_interaction_count=int(data.get("meaningful_interaction_count", 0)),
                verified_assistance_success=int(data.get("verified_assistance_success", 0)), positive_feedback=int(data.get("positive_feedback", 0)),
                negative_feedback=int(data.get("negative_feedback", 0)), explicit_preference_count=int(data.get("explicit_preference_count", 0)),
                familiarity=float(data.get("familiarity", 0.0)),
                casual_speech_permission=CasualPermission(data.get("casual_speech_permission", CasualPermission.UNKNOWN.value)),
                casual_permission_asked_at=data.get("casual_permission_asked_at"), casual_permission_cooldown_until=data.get("casual_permission_cooldown_until"),
            )
        except (ValueError, TypeError):
            return RelationshipState()

    def save_state(self, state: RelationshipState) -> RelationshipState:
        data = {"stage": state.stage.value, "first_seen_at": state.first_seen_at, "last_interaction_at": state.last_interaction_at,
                "days_used": state.days_used, "meaningful_interaction_count": state.meaningful_interaction_count,
                "verified_assistance_success": state.verified_assistance_success, "positive_feedback": state.positive_feedback,
                "negative_feedback": state.negative_feedback, "explicit_preference_count": state.explicit_preference_count,
                "familiarity": state.familiarity, "casual_speech_permission": state.casual_speech_permission.value,
                "casual_permission_asked_at": state.casual_permission_asked_at, "casual_permission_cooldown_until": state.casual_permission_cooldown_until}
        self._write("relationship_state", data)
        return state

    def load_preferences(self) -> SpeechPreferences:
        data = self._read("speech_preferences")
        return SpeechPreferences(**{key: str(data.get(key, value)) for key, value in SpeechPreferences().__dict__.items()})

    def save_preferences(self, preferences: SpeechPreferences) -> SpeechPreferences:
        self._write("speech_preferences", preferences.__dict__)
        return preferences

    def _write(self, table: str, data: dict) -> None:
        if not self.available:
            return
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute(f"UPDATE {table} SET data=? WHERE id=1", (json.dumps(data),))
        except (OSError, sqlite3.DatabaseError, TypeError):
            return
