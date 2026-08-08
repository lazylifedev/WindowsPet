from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .models import SharedSkillRecord
from .repository import SharedKnowledgeRepository
from .sanitizer import SharedKnowledgeSanitizer


@dataclass(frozen=True)
class SharedKnowledgeMatch:
    record: SharedSkillRecord
    requires_local_revalidation: bool
    stale: bool


class SharedKnowledgeCache:
    def __init__(self, repository: SharedKnowledgeRepository, sanitizer: SharedKnowledgeSanitizer | None = None):
        self.repository = repository
        self.sanitizer = sanitizer or SharedKnowledgeSanitizer()

    def store_candidate(self, candidate: dict | SharedSkillRecord) -> SharedSkillRecord | None:
        decision = self.sanitizer.sanitize(candidate)
        if not decision.eligible or decision.record is None:
            return None
        return self.repository.put(decision.record)

    def resolve(self, intent: str, target: str, *, now: datetime | None = None) -> SharedKnowledgeMatch | None:
        record = self.repository.get(intent, target)
        if record is None:
            return None
        stale = record.is_stale(now or datetime.now(timezone.utc))
        return SharedKnowledgeMatch(record, requires_local_revalidation=True, stale=stale)

    def revalidate(self, match: SharedKnowledgeMatch, verifier: Callable[[SharedSkillRecord], bool]) -> bool:
        """Only a caller's local resolver/policy/confirmation path can validate use."""
        if match.stale or not match.requires_local_revalidation:
            return False
        return bool(verifier(match.record))
