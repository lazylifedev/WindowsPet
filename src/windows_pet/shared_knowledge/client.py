from __future__ import annotations

from typing import Protocol

from .models import SharedSkillRecord


class GlobalBrainClient(Protocol):
    """Future transport boundary. Implementations must not grant execution authority."""

    def fetch(self, intent: str, target: str) -> SharedSkillRecord | None: ...
    def publish(self, record: SharedSkillRecord) -> bool: ...


class LocalOnlyGlobalBrainClient:
    """Explicit no-network implementation used by the current product baseline."""

    def fetch(self, intent: str, target: str) -> SharedSkillRecord | None:
        return None

    def publish(self, record: SharedSkillRecord) -> bool:
        return False
