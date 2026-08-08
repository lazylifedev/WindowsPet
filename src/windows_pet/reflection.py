from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol

from .local_skill_store import LocalSkillStore


MAX_ABSTRACT_LENGTH = 200
_UNSAFE = re.compile(r"(?:api[_ ]?key|password|token|credential|cookie|private key|stdout|stderr|screenshot|raw conversation|[A-Za-z]:\\|\\\\)", re.I)


def _safe(value: str | None, limit: int = MAX_ABSTRACT_LENGTH) -> bool:
    text = str(value or "").strip()
    return bool(text) and len(text) <= limit and not _UNSAFE.search(text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Experience:
    task_id: str
    intent: str
    operation: str
    abstract_target: str
    started_at: str
    finished_at: str
    outcome: str
    verification_result: str
    failure_reason: str = ""
    attempt_count: int = 1
    routing_tier: str = "local"
    provenance_ids: tuple[str, ...] = ()

    def is_safe(self) -> bool:
        return all((
            _safe(self.task_id), _safe(self.intent), _safe(self.operation),
            _safe(self.abstract_target), _safe(self.outcome),
            _safe(self.verification_result),
            _safe(self.failure_reason) if self.failure_reason else True,
            self.attempt_count > 0,
            all(_safe(provenance, 120) for provenance in self.provenance_ids),
        ))


@dataclass(frozen=True)
class Reflection:
    task_id: str
    goal_achieved: bool
    verified: bool
    reusable: bool
    failure_evidence: str
    next_step: str
    local_only: bool = True
    global_eligible: bool = False


@dataclass(frozen=True)
class LearningCandidate:
    intent: str
    target_type: str
    abstract_target: str
    alias: str
    provenance: str
    verified: bool
    local_only: bool = True


@dataclass(frozen=True)
class RevalidationResult:
    valid: bool
    reason: str
    current_identity: str | None = None


@dataclass(frozen=True)
class ReflectionContext:
    """Bounded structured context; raw conversation, paths, and secrets are excluded."""

    task_id: str
    outcome: str
    verification_result: str
    failure_reason: str = ""
    routing_tier: str = "local"
    provenance_ids: tuple[str, ...] = ()

    def is_safe(self) -> bool:
        return all((_safe(self.task_id), _safe(self.outcome), _safe(self.verification_result),
                    not self.failure_reason or _safe(self.failure_reason),
                    _safe(self.routing_tier), all(_safe(item, 120) for item in self.provenance_ids)))


@dataclass(frozen=True)
class ReflectionEnrichment:
    summary: str
    reusable_signal: bool = False

    def __post_init__(self):
        if not _safe(self.summary, 240):
            raise ValueError("unsafe_reflection_enrichment")


class LLMReflectionProvider(Protocol):
    def reflect(self, context: ReflectionContext) -> ReflectionEnrichment: ...


class FakeLLMReflectionProvider:
    def __init__(self, enrichment: ReflectionEnrichment | None = None):
        self.enrichment = enrichment or ReflectionEnrichment("no additional local insight")
        self.calls = 0

    def reflect(self, context: ReflectionContext) -> ReflectionEnrichment:
        if not context.is_safe():
            raise ValueError("unsafe_reflection_context")
        self.calls += 1
        return self.enrichment


class OptionalLLMReflection:
    def __init__(self, provider: LLMReflectionProvider):
        self.provider = provider

    def enrich(self, experience: Experience) -> ReflectionEnrichment | None:
        context = ReflectionContext(experience.task_id, experience.outcome, experience.verification_result, experience.failure_reason, experience.routing_tier, experience.provenance_ids)
        return self.provider.reflect(context) if context.is_safe() else None


class ReflectionPipeline:
    """Deterministic reflection; no LLM, Qt, network, raw logs, or secrets."""

    def __init__(self, skill_store: LocalSkillStore | None = None):
        self.skill_store = skill_store
        self.experiences: list[Experience] = []

    def record_experience(self, experience: Experience) -> bool:
        if not experience.is_safe():
            return False
        self.experiences.append(experience)
        return True

    def reflect(self, experience: Experience) -> Reflection | None:
        if not experience.is_safe():
            return None
        verified = experience.outcome.casefold() in {"success", "succeeded", "started"} and experience.verification_result.casefold() in {"verified", "success", "succeeded"}
        return Reflection(experience.task_id, verified, verified, verified and _safe(experience.abstract_target), experience.failure_reason, "reuse verified abstract procedure" if verified else "do not promote; investigate again", True, False)

    def candidate(self, experience: Experience, *, alias: str, target_type: str = "application", provenance: str = "local_observation") -> LearningCandidate | None:
        reflection = self.reflect(experience)
        if reflection is None or not reflection.reusable or not reflection.verified or not _safe(alias) or not _safe(provenance):
            return None
        return LearningCandidate(experience.intent, target_type, experience.abstract_target, alias.strip(), provenance.strip(), True)

    def promote(self, candidate: LearningCandidate) -> bool:
        if (self.skill_store is None or not candidate.verified or not candidate.local_only
                or not _safe(candidate.intent) or not _safe(candidate.target_type)
                or not _safe(candidate.abstract_target) or not _safe(candidate.alias)
                or not _safe(candidate.provenance)):
            return False
        return self.skill_store.record_success(intent=candidate.intent, target_type=candidate.target_type, target=candidate.abstract_target, alias=candidate.alias)


class DeterministicReflection(ReflectionPipeline):
    """Named deterministic boundary retained alongside the future LLM provider."""


def revalidate_abstract_target(target_type: str, abstract_target: str, resolver: Callable[[str, str], str | None]) -> RevalidationResult:
    """Resolve the current local identity; never execute a stored path directly."""
    if not _safe(target_type) or not _safe(abstract_target):
        return RevalidationResult(False, "unsafe abstract target")
    try:
        current = resolver(target_type, abstract_target)
    except Exception:
        return RevalidationResult(False, "resolver failed")
    return RevalidationResult(bool(current), "current identity resolved" if current else "current identity not found", current)
