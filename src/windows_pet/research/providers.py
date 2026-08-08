from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .capabilities import Capability
from .models import CandidatePlan, Evidence, ResearchGoal


def sanitize_evidence_for_provider(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    """Remove protected/sensitive items before any external reasoning boundary."""
    return tuple(item for item in evidence if not item.sensitive and item.source.value not in {"personal_memory"})


class ResearchProvider(Protocol):
    def investigate(self, goal: ResearchGoal, capabilities: tuple[Capability, ...]) -> Iterable[Evidence]: ...


class ReasoningProvider(Protocol):
    def propose(self, goal: ResearchGoal, evidence: tuple[Evidence, ...], capabilities: tuple[Capability, ...]) -> CandidatePlan: ...


class FakeResearchProvider:
    def __init__(self, evidence: Iterable[Evidence] = ()) -> None:
        self.evidence = tuple(evidence)
        self.calls = 0

    def investigate(self, goal: ResearchGoal, capabilities: tuple[Capability, ...]) -> tuple[Evidence, ...]:
        self.calls += 1
        return self.evidence


class FakeReasoningProvider:
    def __init__(self, plans: CandidatePlan | Iterable[CandidatePlan]) -> None:
        self._plans = (plans,) if isinstance(plans, CandidatePlan) else tuple(plans)
        self.calls = 0
        self.last_evidence: tuple[Evidence, ...] = ()

    def propose(self, goal: ResearchGoal, evidence: tuple[Evidence, ...], capabilities: tuple[Capability, ...]) -> CandidatePlan:
        self.calls += 1
        self.last_evidence = sanitize_evidence_for_provider(evidence)
        if not self._plans:
            raise ValueError("no_fake_plan")
        return self._plans[min(self.calls - 1, len(self._plans) - 1)]
