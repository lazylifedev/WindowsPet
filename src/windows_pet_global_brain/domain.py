from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrustState(str, Enum):
    CANDIDATE = "candidate"
    OBSERVED = "observed"
    TRUSTED = "trusted"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class CompatibilityConstraint:
    windows_version: str | None = None
    architecture: str | None = None
    application_version: str | None = None
    capability_version: str | None = None

    @property
    def known(self) -> bool:
        return any((self.windows_version, self.architecture, self.application_version, self.capability_version))

    def matches(self, facts: "CompatibilityConstraint") -> bool:
        """Return true only when every constraint is explicitly known and equal."""
        if not self.known or not facts.known:
            return False
        for name in ("windows_version", "architecture", "application_version", "capability_version"):
            required = getattr(self, name)
            if required is not None and getattr(facts, name) != required:
                return False
        return True

    def key(self) -> str:
        return "|".join(
            f"{name}={getattr(self, name)}"
            for name in ("windows_version", "architecture", "application_version", "capability_version")
            if getattr(self, name) is not None
        )


@dataclass(frozen=True)
class SharedAlias:
    value: str
    alias_id: str


@dataclass(frozen=True)
class SharedSkill:
    knowledge_id: str
    intent: str
    target_type: str
    target: str
    aliases: tuple[SharedAlias, ...]
    compatibility: CompatibilityConstraint
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.0
    trust_state: TrustState = TrustState.CANDIDATE
    knowledge_version: str = "v1"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @property
    def failure_ratio(self) -> float:
        total = self.success_count + self.failure_count
        return self.failure_count / total if total else 0.0


@dataclass(frozen=True)
class KnowledgeCandidate:
    candidate_id: str
    skill: SharedSkill
    submitted_by_installation: str
    verified_success: bool
    source: str = "local_verified_reflection"
    trust_state: TrustState = TrustState.CANDIDATE
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class ExecutionEvidenceAggregate:
    knowledge_id: str
    success_count: int = 0
    failure_count: int = 0
    installation_evidence_ids: set[str] = field(default_factory=set)
    compatibility_keys: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    per_installation_counts: dict[str, int] = field(default_factory=dict)
    last_observed_at: str = field(default_factory=utc_now)

    @property
    def distinct_installations(self) -> int:
        return len(self.installation_evidence_ids)

    @property
    def failure_ratio(self) -> float:
        total = self.success_count + self.failure_count
        return self.failure_count / total if total else 0.0


@dataclass(frozen=True)
class KnowledgeVersion:
    version: str
    updated_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class PromotionDecision:
    knowledge_id: str
    promoted: bool
    trust_state: TrustState
    reason: str


@dataclass(frozen=True)
class EvidenceRecord:
    event_id: str
    knowledge_id: str
    installation_evidence_id: str
    compatibility: CompatibilityConstraint
    verified_success: bool
    failure_category: str | None = None
    observed_at: str = field(default_factory=utc_now)
