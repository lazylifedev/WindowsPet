from __future__ import annotations

from collections.abc import Iterable

from .models import Evidence, EvidenceSource


_TRUST = {
    EvidenceSource.LOCAL_OBSERVATION: 0,
    EvidenceSource.LOCAL_SKILL: 1,
    EvidenceSource.PERSONAL_MEMORY: 2,
    EvidenceSource.BUILT_IN: 3,
    EvidenceSource.OFFICIAL_WEB: 4,
    EvidenceSource.SHARED_KNOWLEDGE: 5,
    EvidenceSource.GENERAL_WEB: 6,
    EvidenceSource.LLM_INFERENCE: 7,
}


def evidence_trust(source: EvidenceSource) -> int:
    return _TRUST[source]


def rank_evidence(items: Iterable[Evidence]) -> tuple[Evidence, ...]:
    return tuple(sorted(items, key=lambda item: (evidence_trust(item.source), -item.confidence, item.evidence_id)))


class EvidenceLedger:
    def __init__(self, max_items: int = 50) -> None:
        self.max_items = max(1, int(max_items))
        self._items: list[Evidence] = []

    def add(self, item: Evidence) -> bool:
        if len(self._items) >= self.max_items or item.sensitive:
            return False
        if any(existing.evidence_id == item.evidence_id for existing in self._items):
            return False
        self._items.append(item)
        return True

    def extend(self, items: Iterable[Evidence]) -> int:
        return sum(1 for item in items if self.add(item))

    def snapshot(self) -> tuple[Evidence, ...]:
        return rank_evidence(self._items)
