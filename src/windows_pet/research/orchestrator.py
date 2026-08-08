from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Event

from ..audit_log import AuditEvent, AuditEventType, NullAuditSink
from ..cancellation import CancellationToken
from ..local_skill_router import LocalSkillRouter
from ..memory.service import MemoryService
from ..reflection import Experience, ReflectionPipeline
from .capabilities import CapabilityRegistry, default_capability_registry
from .evidence import EvidenceLedger
from .model_routing import ModelRouter, ModelTier, RoutingRequest
from .models import (
    CandidatePlan, Evidence, EvidenceSource, ExecutionResult, ResearchGoal, ResearchOutcome,
    ResearchSession, ResearchState, ReplanReason,
)
from .planner import CandidatePlanner
from .policy import ResearchPolicy, RequestClassification
from .providers import ReasoningProvider, ResearchProvider, sanitize_evidence_for_provider


@dataclass(frozen=True)
class ResearchLimits:
    max_steps: int = 8
    max_replans: int = 2
    max_evidence_items: int = 50
    max_result_chars: int = 12000


def _cancelled(token: CancellationToken | Event | None) -> bool:
    return bool(token and (token.is_cancelled if isinstance(token, CancellationToken) else token.is_set()))


class ResearchOrchestrator:
    """Bounded investigation and planning; action execution is always injected."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        research_provider: ResearchProvider | None = None,
        reasoning_provider: ReasoningProvider | None = None,
        memory_service: MemoryService | None = None,
        skill_router: LocalSkillRouter | None = None,
        reflection: ReflectionPipeline | None = None,
        model_router: ModelRouter | None = None,
        policy: ResearchPolicy | None = None,
        action_executor: Callable[[CandidatePlan], ExecutionResult | bool] | None = None,
        verifier: Callable[[CandidatePlan, ExecutionResult], bool] | None = None,
        audit=None,
        limits: ResearchLimits | None = None,
    ) -> None:
        self.registry = registry or default_capability_registry()
        self.research_provider = research_provider
        self.reasoning_provider = reasoning_provider
        self.memory_service = memory_service
        self.skill_router = skill_router
        self.reflection = reflection
        self.model_router = model_router or ModelRouter()
        self.policy = policy or ResearchPolicy()
        self.action_executor = action_executor
        self.verifier = verifier
        self.audit = audit or NullAuditSink()
        self.limits = limits or ResearchLimits()

    def run_text(self, text: str, **kwargs) -> ResearchOutcome:
        return self.run(ResearchGoal.from_text(text), **kwargs)

    def run(
        self,
        goal: ResearchGoal,
        *,
        confirm: bool | None = None,
        cancel: CancellationToken | Event | None = None,
    ) -> ResearchOutcome:
        session = ResearchSession(secrets.token_urlsafe(12), goal)
        self._audit("research_started", session, "started")
        classification = self.policy.classify(goal)
        if classification is RequestClassification.DENIED:
            return self._finish(session, "denied_by_policy", ResearchState.FAILED, (), None, "local", 0)
        if _cancelled(cancel):
            return self._finish(session, "cancelled", ResearchState.CANCELLED, (), None, "local", 0)

        # Exact built-in/learned skill is the local fast path and never calls a provider.
        if self.skill_router is not None:
            match = self.skill_router.route(goal.abstract_intent)
            if match is not None:
                item = Evidence(secrets.token_urlsafe(8), EvidenceSource.LOCAL_SKILL, f"exact local skill matched: {match.request.target}", 1.0, provenance=match.source, resolves_goal=True)
                plan = CandidatePlanner(self.registry, self.policy).read_only_resolution(goal, (item,))
                return self._finish(session.transition(ResearchState.INVESTIGATING).transition(ResearchState.PLANNING), "local_skill_resolved", ResearchState.SUCCEEDED, (item,), plan, "local", 0)

        session = session.transition(ResearchState.INVESTIGATING)
        ledger = EvidenceLedger(self.limits.max_evidence_items)
        built_in = Evidence(secrets.token_urlsafe(8), EvidenceSource.BUILT_IN, "capability registry discovered", 1.0, provenance="capability_registry")
        ledger.add(built_in)
        self._audit("research_step_started", session, "capability_discovery")

        if self.memory_service is not None:
            for record in self.memory_service.lookup(goal.abstract_intent, limit=5, max_chars=1600):
                try:
                    ledger.add(Evidence(secrets.token_urlsafe(8), EvidenceSource.PERSONAL_MEMORY, f"bounded memory {record.key}: {record.display_value}", record.confidence, provenance="memory_lookup"))
                except ValueError:
                    continue

        if _cancelled(cancel):
            return self._finish(session, "cancelled", ResearchState.CANCELLED, ledger.snapshot(), None, "local", 0)
        try:
            ledger.extend(self.registry.inspect(goal))
        except (OSError, ValueError):
            pass
        evidence = ledger.snapshot()
        if _cancelled(cancel):
            return self._finish(session, "cancelled", ResearchState.CANCELLED, evidence, None, "local", 0)
        if any(item.resolves_goal for item in evidence):
            plan = CandidatePlanner(self.registry, self.policy).read_only_resolution(goal, evidence)
            return self._finish(session.transition(ResearchState.PLANNING), "read_only_resolved", ResearchState.SUCCEEDED, evidence, plan, "local", 0)

        if self.research_provider is not None and len(ledger.snapshot()) < self.limits.max_evidence_items:
            try:
                ledger.extend(self.research_provider.investigate(goal, self.registry.all()))
            except Exception:
                self._audit("research_step_started", session, "provider_failed")
            evidence = ledger.snapshot()

        request = RoutingRequest("research_plan", confidence=max((item.confidence for item in evidence), default=0.0), research_depth=1, expected_quality_gain=0.35)
        decision = self.model_router.choose(request)
        route = decision.tier.value
        try:
            if self.reasoning_provider is None:
                return self._finish(session.transition(ResearchState.PLANNING), "candidate_unavailable", ResearchState.FAILED, evidence, None, route, 0)
            planner = CandidatePlanner(self.registry, self.policy)
            plan = planner.from_provider(goal, sanitize_evidence_for_provider(evidence), self.reasoning_provider)
        except (ValueError, KeyError):
            return self._finish(session.transition(ResearchState.PLANNING), "candidate_rejected", ResearchState.FAILED, evidence, None, route, 0)
        session = session.transition(ResearchState.PLANNING)
        self._audit("research_plan_created", session, "plan_created", item_count=len(plan.steps))

        if not plan.requires_confirmation:
            return self._finish(session, "plan_ready_read_only", ResearchState.SUCCEEDED, evidence, plan, route, 0)
        session = session.transition(ResearchState.WAITING_CONFIRMATION)
        self._audit("research_waiting_confirmation", session, "confirmation_required")
        if confirm is None:
            return ResearchOutcome(session, "waiting_confirmation", evidence, plan, None, route, 0)
        if not confirm:
            return self._finish(session, "confirmation_cancelled", ResearchState.CANCELLED, evidence, plan, route, 0)
        if _cancelled(cancel):
            return self._finish(session, "cancelled", ResearchState.CANCELLED, evidence, plan, route, 0)
        if self.action_executor is None:
            return self._finish(session, "execution_handoff_unavailable", ResearchState.FAILED, evidence, plan, route, 0)

        execution_count = 0
        for attempt in range(self.limits.max_replans + 1):
            execution_count += 1
            session = session.transition(ResearchState.EXECUTING)
            try:
                raw = self.action_executor(plan)
                result = raw if isinstance(raw, ExecutionResult) else ExecutionResult(bool(raw), "execution_succeeded" if raw else "execution_failed")
            except Exception:
                result = ExecutionResult(False, "executor_failed")
            if not result.success:
                reason = ReplanReason.EXECUTION_FAILURE
                if attempt >= self.limits.max_replans:
                    return self._finish(session, reason.value, ResearchState.FAILED, evidence, plan, route, execution_count)
            else:
                session = session.transition(ResearchState.VERIFYING)
                verified = bool(self.verifier and self.verifier(plan, result))
                if verified:
                    return self._finish(session, "verified_success", ResearchState.SUCCEEDED, evidence, plan, route, execution_count)
                reason = ReplanReason.VERIFICATION_FAILURE
                if attempt >= self.limits.max_replans:
                    return self._finish(session, reason.value, ResearchState.FAILED, evidence, plan, route, execution_count)
            try:
                failure = Evidence(secrets.token_urlsafe(8), EvidenceSource.LOCAL_OBSERVATION, f"attempt {attempt + 1} failed: {reason.value}", 1.0, provenance=result.result_code)
                ledger.add(failure)
            except ValueError:
                pass
            session = session.transition(ResearchState.REPLANNING, reason=reason.value)
            self._audit("research_replanned", session, reason.value)
            try:
                plan = CandidatePlanner(self.registry, self.policy).from_provider(goal, sanitize_evidence_for_provider(ledger.snapshot()), self.reasoning_provider)
            except (ValueError, KeyError):
                return self._finish(session, "candidate_unavailable", ResearchState.FAILED, ledger.snapshot(), plan, route, execution_count)
            evidence = ledger.snapshot()
            session = session.transition(ResearchState.PLANNING)
        return self._finish(session, "replan_limit", ResearchState.FAILED, evidence, plan, route, execution_count)

    def _finish(self, session: ResearchSession, result_code: str, state: ResearchState, evidence: tuple[Evidence, ...], plan: CandidatePlan | None, route: str, execution_count: int) -> ResearchOutcome:
        if session.state is not state:
            session = session.transition(state, reason=result_code)
        reflection = None
        if self.reflection is not None:
            success = state is ResearchState.SUCCEEDED
            item = Experience(session.session_id, "research", plan.plan_id if plan else "investigate", session.goal.safe_summary, session.goal.created_at, session.goal.created_at, "succeeded" if success else "failed", "verified" if success else "failed", "" if success else result_code, max(1, execution_count), route, tuple(item.evidence_id for item in evidence))
            if self.reflection.record_experience(item):
                reflection = self.reflection.reflect(item)
        event = "research_succeeded" if state is ResearchState.SUCCEEDED else "research_cancelled" if state is ResearchState.CANCELLED else "research_failed"
        self._audit(event, session, result_code, item_count=len(evidence))
        return ResearchOutcome(session, result_code, evidence, plan, reflection, route, execution_count)

    def _audit(self, event: str, session: ResearchSession, result_code: str, *, item_count: int | None = None) -> None:
        try:
            self.audit.write(AuditEvent(event, result_code=result_code, task_id=session.session_id, operation="research", item_count=item_count))
        except (TypeError, ValueError):
            pass
