from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Mapping

from windows_pet.shared_knowledge.sanitizer import SharedKnowledgeSanitizer

from .domain import (
    CompatibilityConstraint,
    EvidenceRecord,
    ExecutionEvidenceAggregate,
    KnowledgeCandidate,
    PromotionDecision,
    SharedAlias,
    SharedSkill,
    TrustState,
    utc_now,
)
from .repositories import GlobalBrainRepository


@dataclass(frozen=True)
class PromotionPolicy:
    minimum_distinct_installations: int = 2
    minimum_verified_successes: int = 3
    maximum_failure_ratio: float = 0.25
    per_installation_contribution_cap: int = 5


@dataclass(frozen=True)
class LookupResult:
    matches: tuple[SharedSkill, ...]
    server_version: str
    stale_client: bool


@dataclass(frozen=True)
class SubmissionResult:
    accepted: bool
    reason: str
    candidate: KnowledgeCandidate | None
    promotion: PromotionDecision | None
    duplicate: bool = False


@dataclass(frozen=True)
class ResultSubmission:
    accepted: bool
    reason: str
    promotion: PromotionDecision | None
    aggregate: ExecutionEvidenceAggregate | None
    duplicate: bool = False


class GlobalBrainService:
    """Domain service for privacy-safe generalized shared knowledge."""

    def __init__(self, repository: GlobalBrainRepository, policy: PromotionPolicy | None = None, sanitizer=None):
        self.repository = repository
        self.policy = policy or PromotionPolicy()
        self.sanitizer = sanitizer or SharedKnowledgeSanitizer()

    def lookup(
        self,
        *,
        intent: str,
        target: str,
        compatibility: CompatibilityConstraint,
        client_knowledge_version: str | None = None,
    ) -> LookupResult:
        version = self.repository.current_version().version
        stale = client_knowledge_version is not None and client_knowledge_version != version
        if stale:
            return LookupResult((), version, True)
        matches = tuple(
            skill
            for skill in self.repository.list_skills()
            if skill.trust_state is TrustState.TRUSTED
            and skill.intent == intent
            and skill.target == target
            and skill.compatibility.matches(compatibility)
        )
        return LookupResult(matches, version, False)

    def submit_candidate(
        self,
        *,
        candidate_id: str,
        installation_evidence_id: str,
        skill_data: Mapping[str, object],
        verified_success: bool,
    ) -> SubmissionResult:
        if not verified_success:
            return SubmissionResult(False, "verified_success_required", None, None)
        existing = self.repository.get_candidate(candidate_id)
        if existing is not None:
            return SubmissionResult(True, "duplicate_candidate", existing, None, True)
        decision = self.sanitizer.sanitize(skill_data)
        if not decision.eligible or decision.record is None:
            return SubmissionResult(False, decision.reason, None, None)
        compatibility = _compatibility_from_record(decision.record.compatibility)
        if not compatibility.known:
            return SubmissionResult(False, "compatibility_required", None, None)
        knowledge_id = f"knowledge-{secrets.token_hex(10)}"
        version = self.repository.current_version().version
        skill = SharedSkill(
            knowledge_id=knowledge_id,
            intent=decision.record.intent,
            target_type=decision.record.target_type,
            target=decision.record.target,
            aliases=tuple(SharedAlias(alias, f"alias-{secrets.token_hex(6)}") for alias in decision.record.aliases),
            compatibility=compatibility,
            trust_state=TrustState.CANDIDATE,
            knowledge_version=version,
        )
        candidate = KnowledgeCandidate(candidate_id, skill, installation_evidence_id, True)
        aggregate = ExecutionEvidenceAggregate(knowledge_id)
        if not self.repository.add_candidate(candidate, aggregate):
            existing = self.repository.get_candidate(candidate_id)
            return SubmissionResult(True, "duplicate_candidate", existing, None, True)
        evidence = EvidenceRecord(candidate_id, knowledge_id, installation_evidence_id, compatibility, True)
        recorded = self.repository.record_evidence(evidence, self.policy.per_installation_contribution_cap)
        if recorded != "recorded":
            return SubmissionResult(False, recorded, candidate, None)
        self._refresh_skill_statistics(knowledge_id)
        promotion = self._promote_if_eligible(knowledge_id)
        return SubmissionResult(True, "candidate_accepted", self.repository.get_candidate(candidate_id), promotion)

    def submit_result(
        self,
        *,
        event_id: str,
        knowledge_id: str,
        knowledge_version: str,
        installation_evidence_id: str,
        compatibility: CompatibilityConstraint,
        verified_success: bool,
        failure_category: str | None,
    ) -> ResultSubmission:
        skill = self.repository.get_skill(knowledge_id)
        if skill is None:
            return ResultSubmission(False, "unknown_knowledge", None, None)
        if knowledge_version != skill.knowledge_version:
            return ResultSubmission(False, "stale_knowledge_version", None, self.repository.get_aggregate(knowledge_id))
        if not compatibility.known:
            return ResultSubmission(False, "compatibility_required", None, self.repository.get_aggregate(knowledge_id))
        if not verified_success and not failure_category:
            return ResultSubmission(False, "failure_category_required", None, self.repository.get_aggregate(knowledge_id))
        evidence = EvidenceRecord(event_id, knowledge_id, installation_evidence_id, compatibility, verified_success, failure_category)
        recorded = self.repository.record_evidence(evidence, self.policy.per_installation_contribution_cap)
        aggregate = self.repository.get_aggregate(knowledge_id)
        if recorded == "duplicate_event":
            return ResultSubmission(True, recorded, None, aggregate, True)
        if recorded != "recorded":
            return ResultSubmission(False, recorded, None, aggregate)
        self._refresh_skill_statistics(knowledge_id)
        promotion = self._promote_if_eligible(knowledge_id)
        return ResultSubmission(True, "result_recorded", promotion, self.repository.get_aggregate(knowledge_id))

    def _promote_if_eligible(self, knowledge_id: str) -> PromotionDecision:
        skill = self.repository.get_skill(knowledge_id)
        aggregate = self.repository.get_aggregate(knowledge_id)
        if skill is None or aggregate is None:
            return PromotionDecision(knowledge_id, False, TrustState.REJECTED, "unknown_knowledge")
        if aggregate.distinct_installations < self.policy.minimum_distinct_installations:
            state = TrustState.OBSERVED if aggregate.success_count else TrustState.CANDIDATE
            return self._set_state(skill, state, "minimum_distinct_installations_not_met")
        if aggregate.success_count < self.policy.minimum_verified_successes:
            return self._set_state(skill, TrustState.OBSERVED, "minimum_verified_successes_not_met")
        if aggregate.failure_ratio > self.policy.maximum_failure_ratio:
            return self._set_state(skill, TrustState.REJECTED, "failure_ratio_too_high")
        if len({key for key in aggregate.compatibility_keys if key}) != 1:
            return self._set_state(skill, TrustState.OBSERVED, "compatibility_not_coherent")
        return self._set_state(skill, TrustState.TRUSTED, "promotion_threshold_met")

    def _refresh_skill_statistics(self, knowledge_id: str) -> None:
        skill = self.repository.get_skill(knowledge_id)
        aggregate = self.repository.get_aggregate(knowledge_id)
        if skill is None or aggregate is None:
            return
        total = aggregate.success_count + aggregate.failure_count
        updated = SharedSkill(
            **{
                **skill.__dict__,
                "success_count": aggregate.success_count,
                "failure_count": aggregate.failure_count,
                "confidence": aggregate.success_count / total if total else 0.0,
                "updated_at": utc_now(),
            }
        )
        self.repository.replace_skill(updated)

    def _set_state(self, skill: SharedSkill, state: TrustState, reason: str) -> PromotionDecision:
        if skill.trust_state is not state:
            version = self.repository.current_version().version
            updated = SharedSkill(
                **{**skill.__dict__, "trust_state": state, "knowledge_version": version, "updated_at": utc_now()}
            )
            self.repository.replace_skill(updated)
        return PromotionDecision(skill.knowledge_id, state is TrustState.TRUSTED, state, reason)


def _compatibility_from_record(values: tuple[str, ...]) -> CompatibilityConstraint:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" in value:
            key, item = value.split("=", 1)
            if key in {"windows_version", "architecture", "application_version", "capability_version"} and item:
                parsed[key] = item
    return CompatibilityConstraint(**parsed)
