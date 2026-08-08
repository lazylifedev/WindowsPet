from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModelTier(StrEnum):
    LOCAL = "local"
    LUNA = "luna"
    TERRA = "terra"
    SOL = "sol"


@dataclass(frozen=True)
class RoutingRequest:
    task_type: str
    confidence: float = 0.0
    previous_failures: int = 0
    research_depth: int = 0
    expected_quality_gain: float = 0.0
    latency_budget: float = 30.0
    cost_budget: float = 1.0
    known_local_solution: bool = False
    safety_sensitive: bool = False
    hard_case: bool = False


@dataclass(frozen=True)
class RoutingDecision:
    tier: ModelTier
    reason: str
    confirmation_required: bool


class ModelRouter:
    """Deterministic routing policy. It selects one tier and never calls a provider."""

    def choose(self, request: RoutingRequest) -> RoutingDecision:
        if request.known_local_solution or request.confidence >= 0.9:
            return RoutingDecision(ModelTier.LOCAL, "local solution is sufficient", request.safety_sensitive)
        if request.hard_case and request.expected_quality_gain >= 0.55 and request.cost_budget >= 2.0 and request.latency_budget >= 10:
            return RoutingDecision(ModelTier.SOL, "high-value hard case", request.safety_sensitive)
        if request.previous_failures > 0 and request.expected_quality_gain >= 0.25 and request.cost_budget >= 1.5 and request.latency_budget >= 5:
            return RoutingDecision(ModelTier.TERRA, "explicit escalation after failure", request.safety_sensitive)
        return RoutingDecision(ModelTier.LUNA, "default bounded external candidate", request.safety_sensitive)

    def escalate(self, request: RoutingRequest) -> RoutingDecision:
        return self.choose(RoutingRequest(**{**request.__dict__, "previous_failures": request.previous_failures + 1}))
