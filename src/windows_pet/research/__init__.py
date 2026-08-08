"""Local-first, bounded research orchestration for unknown WindowsPet goals."""

from .capabilities import Capability, CapabilityRegistry, default_capability_registry
from .models import (
    CandidatePlan,
    Evidence,
    EvidenceSource,
    ExecutionResult,
    PlanStep,
    PlanStepKind,
    ResearchGoal,
    ResearchOutcome,
    ResearchSession,
    ResearchState,
    ResearchStep,
    ReplanReason,
    RiskClass,
)
from .model_routing import ModelRouter, ModelTier, RoutingDecision, RoutingRequest
from .orchestrator import ResearchLimits, ResearchOrchestrator
from .policy import ResearchPolicy, RequestClassification
from .providers import (
    FakeReasoningProvider,
    FakeResearchProvider,
    ReasoningProvider,
    ResearchProvider,
)

__all__ = [
    "CandidatePlan", "Capability", "CapabilityRegistry", "Evidence", "EvidenceSource",
    "ExecutionResult", "FakeReasoningProvider", "FakeResearchProvider", "ModelRouter",
    "ModelTier", "PlanStep", "PlanStepKind", "ReasoningProvider", "ReplanReason",
    "ResearchGoal", "ResearchLimits", "ResearchOrchestrator", "ResearchOutcome",
    "ResearchPolicy", "ResearchProvider", "ResearchSession", "ResearchState",
    "ResearchStep", "RequestClassification", "RiskClass", "RoutingDecision", "RoutingRequest",
    "default_capability_registry",
]
