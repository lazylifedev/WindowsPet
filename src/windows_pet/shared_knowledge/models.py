from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ShareDecision:
    eligible: bool
    reason: str
    record: "SharedSkillRecord | None" = None


@dataclass(frozen=True)
class SharedSkillRecord:
    record_id: str
    intent: str
    target_type: str
    target: str
    aliases: tuple[str, ...]
    success_count: int
    failure_count: int
    confidence: float
    source: str
    compatibility: tuple[str, ...] = ()
    trusted: bool = False
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None

    def is_stale(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            return datetime.fromisoformat(self.expires_at) <= (now or datetime.now(timezone.utc))
        except ValueError:
            return True

    @property
    def success_ratio(self) -> float:
        total = max(1, self.success_count + self.failure_count)
        return self.success_count / total
