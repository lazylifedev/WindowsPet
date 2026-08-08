from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import MemoryRecord
from .repository import MemoryRepository


def cleanup_candidates(repository: MemoryRepository, *, now: datetime | None = None, stale_days: int = 90, min_strength: float = 0.2) -> list[MemoryRecord]:
    current = now or datetime.now(timezone.utc)
    repository.delete_expired(current)
    return repository.cleanup_candidates(current, stale_before=current - timedelta(days=stale_days), min_strength=min_strength)
