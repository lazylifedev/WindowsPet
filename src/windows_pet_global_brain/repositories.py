from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Protocol

from .domain import (
    EvidenceRecord,
    ExecutionEvidenceAggregate,
    KnowledgeCandidate,
    KnowledgeVersion,
    SharedSkill,
    utc_now,
)


class GlobalBrainRepository(Protocol):
    def add_candidate(self, candidate: KnowledgeCandidate, aggregate: ExecutionEvidenceAggregate) -> bool: ...
    def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None: ...
    def list_candidates(self) -> list[KnowledgeCandidate]: ...
    def get_skill(self, knowledge_id: str) -> SharedSkill | None: ...
    def list_skills(self) -> list[SharedSkill]: ...
    def get_aggregate(self, knowledge_id: str) -> ExecutionEvidenceAggregate | None: ...
    def record_evidence(self, evidence: EvidenceRecord, per_installation_cap: int) -> str: ...
    def replace_skill(self, skill: SharedSkill) -> None: ...
    def current_version(self) -> KnowledgeVersion: ...


class InMemoryGlobalBrainRepository:
    """Atomic, network-free repository used by all local service tests."""

    def __init__(self):
        self._lock = RLock()
        self._candidates: dict[str, KnowledgeCandidate] = {}
        self._skills: dict[str, SharedSkill] = {}
        self._aggregates: dict[str, ExecutionEvidenceAggregate] = {}
        self._version = 1

    def add_candidate(self, candidate: KnowledgeCandidate, aggregate: ExecutionEvidenceAggregate) -> bool:
        with self._lock:
            if candidate.candidate_id in self._candidates:
                return False
            self._candidates[candidate.candidate_id] = deepcopy(candidate)
            self._skills[candidate.skill.knowledge_id] = deepcopy(candidate.skill)
            self._aggregates[candidate.skill.knowledge_id] = deepcopy(aggregate)
            return True

    def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        with self._lock:
            return deepcopy(self._candidates.get(candidate_id))

    def list_candidates(self) -> list[KnowledgeCandidate]:
        with self._lock:
            return deepcopy(list(self._candidates.values()))

    def get_skill(self, knowledge_id: str) -> SharedSkill | None:
        with self._lock:
            return deepcopy(self._skills.get(knowledge_id))

    def list_skills(self) -> list[SharedSkill]:
        with self._lock:
            return deepcopy(list(self._skills.values()))

    def get_aggregate(self, knowledge_id: str) -> ExecutionEvidenceAggregate | None:
        with self._lock:
            return deepcopy(self._aggregates.get(knowledge_id))

    def record_evidence(self, evidence: EvidenceRecord, per_installation_cap: int) -> str:
        with self._lock:
            aggregate = self._aggregates.get(evidence.knowledge_id)
            if aggregate is None:
                return "unknown_knowledge"
            if evidence.event_id in aggregate.event_ids:
                return "duplicate_event"
            count = aggregate.per_installation_counts.get(evidence.installation_evidence_id, 0)
            if count >= per_installation_cap:
                return "installation_contribution_cap"
            aggregate.event_ids.add(evidence.event_id)
            aggregate.per_installation_counts[evidence.installation_evidence_id] = count + 1
            aggregate.installation_evidence_ids.add(evidence.installation_evidence_id)
            aggregate.compatibility_keys.add(evidence.compatibility.key())
            if evidence.verified_success:
                aggregate.success_count += 1
            else:
                aggregate.failure_count += 1
            aggregate.last_observed_at = evidence.observed_at
            self._version += 1
            return "recorded"

    def replace_skill(self, skill: SharedSkill) -> None:
        with self._lock:
            self._skills[skill.knowledge_id] = deepcopy(skill)
            candidate = next((item for item in self._candidates.values() if item.skill.knowledge_id == skill.knowledge_id), None)
            if candidate is not None:
                self._candidates[candidate.candidate_id] = KnowledgeCandidate(
                    candidate.candidate_id,
                    skill,
                    candidate.submitted_by_installation,
                    candidate.verified_success,
                    candidate.source,
                    skill.trust_state,
                    candidate.created_at,
                    utc_now(),
                )
            self._version += 1

    def current_version(self) -> KnowledgeVersion:
        with self._lock:
            return KnowledgeVersion(f"v{self._version}")


class FirestoreDocumentClient(Protocol):
    def collection(self, name: str) -> Any: ...


class FirestoreGlobalBrainRepository(InMemoryGlobalBrainRepository):
    """Firestore adapter boundary with dependency-injected document client.

    The local MVP keeps an atomic in-memory mirror so domain tests need no SDK
    or credentials. When a real client is supplied, writes are mirrored to the
    named collections; transaction/query policy remains isolated here.
    """

    COLLECTIONS = {
        "skills": "global_skills",
        "candidates": "knowledge_candidates",
        "aggregates": "evidence_aggregates",
        "versions": "knowledge_versions",
    }

    def __init__(self, client: FirestoreDocumentClient | None = None):
        super().__init__()
        self.client = client

    def _write(self, collection: str, document_id: str, value: object) -> None:
        if self.client is None:
            return
        collection_ref = self.client.collection(collection)
        document_ref = collection_ref.document(document_id)
        payload = value if isinstance(value, dict) else {"value": repr(value)}
        document_ref.set(payload)

    def add_candidate(self, candidate: KnowledgeCandidate, aggregate: ExecutionEvidenceAggregate) -> bool:
        added = super().add_candidate(candidate, aggregate)
        if added:
            self._write(self.COLLECTIONS["skills"], candidate.skill.knowledge_id, _skill_dict(candidate.skill))
            self._write(self.COLLECTIONS["candidates"], candidate.candidate_id, _candidate_dict(candidate))
            self._write(self.COLLECTIONS["aggregates"], candidate.skill.knowledge_id, _aggregate_dict(aggregate))
        return added

    def record_evidence(self, evidence: EvidenceRecord, per_installation_cap: int) -> str:
        result = super().record_evidence(evidence, per_installation_cap)
        if result == "recorded":
            aggregate = self.get_aggregate(evidence.knowledge_id)
            if aggregate:
                self._write(self.COLLECTIONS["aggregates"], evidence.knowledge_id, _aggregate_dict(aggregate))
            self._write(self.COLLECTIONS["versions"], "current", {"version": self.current_version().version})
        return result

    def replace_skill(self, skill: SharedSkill) -> None:
        super().replace_skill(skill)
        self._write(self.COLLECTIONS["skills"], skill.knowledge_id, _skill_dict(skill))
        self._write(self.COLLECTIONS["versions"], "current", {"version": self.current_version().version})


def _skill_dict(skill: SharedSkill) -> dict[str, object]:
    return {
        "knowledge_id": skill.knowledge_id,
        "intent": skill.intent,
        "target_type": skill.target_type,
        "target": skill.target,
        "aliases": [alias.value for alias in skill.aliases],
        "compatibility": skill.compatibility.__dict__,
        "success_count": skill.success_count,
        "failure_count": skill.failure_count,
        "confidence": skill.confidence,
        "trust_state": skill.trust_state.value,
        "knowledge_version": skill.knowledge_version,
        "updated_at": skill.updated_at,
    }


def _candidate_dict(candidate: KnowledgeCandidate) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "knowledge_id": candidate.skill.knowledge_id,
        "installation_evidence_id": candidate.submitted_by_installation,
        "verified_success": candidate.verified_success,
        "trust_state": candidate.trust_state.value,
        "created_at": candidate.created_at,
    }


def _aggregate_dict(aggregate: ExecutionEvidenceAggregate) -> dict[str, object]:
    return {
        "knowledge_id": aggregate.knowledge_id,
        "success_count": aggregate.success_count,
        "failure_count": aggregate.failure_count,
        "distinct_installations": aggregate.distinct_installations,
        "compatibility_keys": sorted(aggregate.compatibility_keys),
        "last_observed_at": aggregate.last_observed_at,
    }
