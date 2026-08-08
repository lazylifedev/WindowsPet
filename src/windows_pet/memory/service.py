from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .models import MemoryCategory, MemoryKind, MemoryRecord
from .privacy import PrivacyDecision, validate_memory_input
from .repository import MemoryRepository
from .retention import cleanup_candidates


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryService:
    """Validated Personal Memory API. Callers never write SQLite directly."""

    def __init__(self, repository: MemoryRepository):
        self.repository = repository

    def validate(self, key: str, value: str) -> PrivacyDecision:
        return validate_memory_input(key, value)

    def remember(self, *, category: str, key: str, value: str, protected: bool = False, short_term_ttl: int | None = None, source: str = "user_explicit", importance: float = 0.7, confidence: float = 1.0) -> MemoryRecord | None:
        decision = self.validate(key, value)
        if not decision.accepted:
            return None
        kind = MemoryKind.PROTECTED if protected else (MemoryKind.SHORT_TERM if short_term_ttl is not None else MemoryKind.LONG_TERM)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=short_term_ttl) if short_term_ttl is not None else None
        record = MemoryRecord(secrets.token_hex(12), kind, str(category or MemoryCategory.FACT.value), str(key).strip(), str(value).strip(), _now(), _now(), _now(), expires.isoformat() if expires else None, min(1.0, max(0.0, float(importance))), 0.8 if protected else 0.55, protected, str(source)[:80], min(1.0, max(0.0, float(confidence))), 0, 1)
        return self.repository.upsert(record)

    def request_memory_store(self, *, category: str, key: str, value: str, protected: bool = False) -> MemoryRecord | None:
        """Structured entry point for a future tool; still passes local validation."""
        return self.remember(category=category, key=key, value=value, protected=protected)

    def lookup(self, query: str | None = None, *, limit: int = 20, max_chars: int = 4000) -> list[MemoryRecord]:
        return self.repository.lookup(query, limit=min(max(0, limit), 50), max_chars=min(max(0, max_chars), 12000))

    def list(self, *, category: str | None = None, kind: str | None = None) -> list[MemoryRecord]:
        return self.repository.list(category=category, kind=kind)

    def forget(self, memory_id: str) -> bool:
        return self.repository.delete(memory_id)

    def forget_by_key(self, key: str, *, category: str | None = None) -> tuple[str, list[MemoryRecord]]:
        candidates = [record for record in self.repository.list(category=category) if record.key.casefold() == str(key).strip().casefold()]
        if len(candidates) != 1:
            return ("not_found" if not candidates else "ambiguous", candidates)
        return ("deleted" if self.forget(candidates[0].memory_id) else "failed", candidates)

    def request_memory_forget(self, memory_id: str) -> bool:
        return self.forget(memory_id)

    def cleanup_candidates(self, *, now: datetime | None = None, stale_days: int = 90, min_strength: float = 0.2) -> list[MemoryRecord]:
        return cleanup_candidates(self.repository, now=now, stale_days=stale_days, min_strength=min_strength)
