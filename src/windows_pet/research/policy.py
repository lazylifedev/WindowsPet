from __future__ import annotations

from enum import StrEnum

from ..action_models import SideEffect
from .capabilities import CapabilityRegistry
from .models import CandidatePlan, PlanStepKind, ResearchGoal, RiskClass


class RequestClassification(StrEnum):
    UNKNOWN = "unknown"
    RESEARCHABLE = "researchable"
    DENIED = "denied"


_DENIED_MARKERS = (
    "bypass confirmation", "skip confirmation", "disable app check", "download and execute",
    "download-and-execute", "arbitrary shell", "任意シェル", "確認を bypass", "確認を省略",
    "api key", "password", "credential", "secret", "private key",
)


class ResearchPolicy:
    def classify(self, goal: ResearchGoal) -> RequestClassification:
        text = f"{goal.abstract_intent} {goal.desired_state}".casefold()
        return RequestClassification.DENIED if any(marker in text for marker in _DENIED_MARKERS) else RequestClassification.RESEARCHABLE

    def validate_plan(self, plan: CandidatePlan, registry: CapabilityRegistry) -> tuple[bool, str]:
        if not plan.steps or len(plan.steps) > 20:
            return False, "invalid_plan_steps"
        requires_confirmation = False
        requires_admin = False
        risk = RiskClass.NONE
        for step in plan.steps:
            try:
                capability = registry.get(step.capability_id)
            except KeyError:
                return False, "unknown_capability"
            if step.operation not in capability.operations:
                return False, "unknown_operation"
            if step.kind is PlanStepKind.READ_ONLY and not capability.read_only:
                return False, "read_only_step_not_read_only"
            if step.kind is PlanStepKind.ACTION_PROPOSAL and capability.read_only:
                return False, "action_step_is_read_only"
            if step.side_effect != capability.side_effect.value:
                return False, "effect_class_mismatch"
            if step.requires_confirmation != capability.requires_confirmation:
                return False, "confirmation_policy_mismatch"
            if step.requires_admin != capability.requires_admin:
                return False, "admin_policy_mismatch"
            if step.kind is PlanStepKind.ACTION_PROPOSAL:
                requires_confirmation = True
                risk = RiskClass.ADMIN if capability.requires_admin else RiskClass.STATE_CHANGE
                requires_admin = requires_admin or capability.requires_admin
            if step.kind is PlanStepKind.VERIFY and not step.verification:
                return False, "verification_required"
            if any(marker in f"{step.operation} {step.description}".casefold() for marker in ("popen", "shell", "powershell", "download", "execute arbitrary")):
                return False, "arbitrary_execution_rejected"
        if plan.requires_confirmation != requires_confirmation or plan.requires_admin != requires_admin:
            return False, "plan_boundary_mismatch"
        if plan.risk_class != risk:
            return False, "plan_risk_mismatch"
        return True, "ok"

    @staticmethod
    def external_evidence_allowed(source: str) -> bool:
        return source in {"official_web", "general_web"}
