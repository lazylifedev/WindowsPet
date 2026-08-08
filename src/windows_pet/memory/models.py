from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MemoryKind(StrEnum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    PROTECTED = "protected"


class MemoryCategory(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    WORKFLOW = "workflow"
    RESOURCE = "resource"
    COMMUNICATION = "communication"


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    category: str
    key: str
    value: str
    created_at: str
    updated_at: str
    last_used_at: str
    expires_at: str | None
    importance: float
    strength: float
    protected: bool
    source: str
    confidence: float
    use_count: int
    reinforcement_count: int

    @property
    def display_value(self) -> str:
        return self.value if len(self.value) <= 240 else self.value[:237] + "..."
