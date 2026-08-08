from __future__ import annotations

import secrets

from .capabilities import CapabilityRegistry
from .models import CandidatePlan, Evidence, PlanStep, PlanStepKind, ResearchGoal, RiskClass
from .policy import ResearchPolicy
from .providers import ReasoningProvider


class CandidatePlanner:
    def __init__(self, registry: CapabilityRegistry, policy: ResearchPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or ResearchPolicy()

    def from_provider(self, goal: ResearchGoal, evidence: tuple[Evidence, ...], provider: ReasoningProvider) -> CandidatePlan:
        plan = provider.propose(goal, evidence, self.registry.all())
        valid, reason = self.policy.validate_plan(plan, self.registry)
        if not valid:
            raise ValueError(reason)
        return plan
    def read_only_resolution(self, goal: ResearchGoal, evidence: tuple[Evidence, ...]) -> CandidatePlan:
        step = PlanStep(secrets.token_urlsafe(8), PlanStepKind.READ_ONLY, "reflection", "reflect_verified_result", "record the bounded local resolution", "read_only", False, False, (), "local evidence remains current")
        plan = CandidatePlan(secrets.token_urlsafe(10), goal.goal_id, (step,), "request resolved by local read-only evidence", False, False, RiskClass.NONE, "local observation is current", evidence_ids=tuple(item.evidence_id for item in evidence))
        valid, reason = self.policy.validate_plan(plan, self.registry)
        if not valid:
            raise ValueError(reason)
        return plan
