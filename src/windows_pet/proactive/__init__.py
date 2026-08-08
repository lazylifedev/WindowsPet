"""Local-only proactive speech decisioning with anti-annoyance controls."""

from .engine import ProactiveEngine
from .models import (
    ProactiveCandidate,
    ProactiveSettings,
    ProactiveState,
    ReactionKind,
    ShouldSpeakDecision,
    TriggerKind,
)
from .sqlite_repository import SQLiteProactiveRepository

__all__ = [
    "ProactiveCandidate",
    "ProactiveEngine",
    "ProactiveSettings",
    "ProactiveState",
    "ReactionKind",
    "ShouldSpeakDecision",
    "SQLiteProactiveRepository",
    "TriggerKind",
]
