from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum


_SECRET_OR_RAW = re.compile(
    r"api[_ -]?key|password|passwd|token|credential|cookie|private key|secret|"
    r"raw conversation|stdout|stderr|screenshot|full document|[A-Za-z]:\\|\\\\",
    re.IGNORECASE,
)
MAX_TEXT = 240


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: str, *, limit: int = MAX_TEXT) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or _SECRET_OR_RAW.search(text):
        raise ValueError("unsafe_research_text")
    if any(ord(char) < 32 and char not in "\t" for char in text):
        raise ValueError("invalid_research_text")
    return text


class ResearchState(StrEnum):
    CREATED = "created"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchStepKind(StrEnum):
    DISCOVER = "discover"
    READ_ONLY = "read_only"
    PLAN = "plan"
    ACTION_PROPOSAL = "action_proposal"
    VERIFY = "verify"
    REFLECT = "reflect"


class PlanStepKind(StrEnum):
    READ_ONLY = "read_only"
    ACTION_PROPOSAL = "action_proposal"
    VERIFY = "verify"


class EvidenceSource(StrEnum):
    LOCAL_OBSERVATION = "local_observation"
    PERSONAL_MEMORY = "personal_memory"
    LOCAL_SKILL = "local_skill"
    BUILT_IN = "built_in"
    OFFICIAL_WEB = "official_web"
    GENERAL_WEB = "general_web"
    LLM_INFERENCE = "llm_inference"
    SHARED_KNOWLEDGE = "shared_knowledge"


class ReplanReason(StrEnum):
    EXECUTION_FAILURE = "execution_failure"
    VERIFICATION_FAILURE = "verification_failure"
    STALE_EVIDENCE = "stale_evidence"
    CANDIDATE_UNAVAILABLE = "candidate_unavailable"
    IDENTITY_CHANGED = "identity_changed"
    LIMIT_REACHED = "limit_reached"


class RiskClass(StrEnum):
    NONE = "none"
    LOW = "low"
    STATE_CHANGE = "state_change"
    ADMIN = "admin"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ResearchGoal:
    goal_id: str
    abstract_intent: str
    safe_summary: str
    desired_state: str
    constraints: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        for value in (self.goal_id, self.abstract_intent, self.safe_summary, self.desired_state):
            safe_text(value)
        if len(self.constraints) > 20 or any(not safe_text(item, limit=160) for item in self.constraints):
            raise ValueError("invalid_goal_constraints")

    @classmethod
    def from_text(cls, text: str, *, desired_state: str = "resolve the request", constraints: tuple[str, ...] = ()) -> "ResearchGoal":
        intent = safe_text(text)
        return cls(secrets.token_urlsafe(12), intent, intent, safe_text(desired_state), tuple(constraints))


@dataclass(frozen=True)
class ResearchStep:
    step_id: str
    kind: ResearchStepKind
    operation: str
    status: str = "pending"
    evidence_ids: tuple[str, ...] = ()
    result_code: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: EvidenceSource
    claim: str
    confidence: float
    observed_at: str = field(default_factory=_now)
    freshness: str = "current"
    provenance: str = ""
    sensitive: bool = False
    resolves_goal: bool = False

    def __post_init__(self) -> None:
        if not safe_text(self.evidence_id, limit=120) or not safe_text(self.claim, limit=1000):
            raise ValueError("unsafe_evidence")
        if self.provenance:
            safe_text(self.provenance, limit=240)
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("invalid_evidence_confidence")


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    kind: PlanStepKind
    capability_id: str
    operation: str
    description: str
    side_effect: str = "read_only"
    requires_confirmation: bool = False
    requires_admin: bool = False
    parameters: tuple[tuple[str, str], ...] = ()
    verification: str = ""


@dataclass(frozen=True)
class CandidatePlan:
    plan_id: str
    goal_id: str
    steps: tuple[PlanStep, ...]
    expected_outcome: str
    requires_confirmation: bool
    requires_admin: bool
    risk_class: RiskClass
    verification_plan: str
    rollback_metadata: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    result_code: str
    verification_hint: str = ""


@dataclass(frozen=True)
class ResearchSession:
    session_id: str
    goal: ResearchGoal
    state: ResearchState = ResearchState.CREATED
    steps: tuple[ResearchStep, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    plan: CandidatePlan | None = None
    replan_count: int = 0
    reason: str = ""

    def transition(self, state: ResearchState, *, reason: str = "") -> "ResearchSession":
        allowed = {
            ResearchState.CREATED: {ResearchState.INVESTIGATING, ResearchState.FAILED, ResearchState.CANCELLED},
            ResearchState.INVESTIGATING: {ResearchState.PLANNING, ResearchState.CANCELLED, ResearchState.FAILED},
            ResearchState.PLANNING: {ResearchState.WAITING_CONFIRMATION, ResearchState.EXECUTING, ResearchState.SUCCEEDED, ResearchState.REPLANNING, ResearchState.FAILED, ResearchState.CANCELLED},
            ResearchState.WAITING_CONFIRMATION: {ResearchState.EXECUTING, ResearchState.CANCELLED, ResearchState.REPLANNING},
            ResearchState.EXECUTING: {ResearchState.VERIFYING, ResearchState.REPLANNING, ResearchState.FAILED, ResearchState.CANCELLED},
            ResearchState.VERIFYING: {ResearchState.SUCCEEDED, ResearchState.REPLANNING, ResearchState.FAILED, ResearchState.CANCELLED},
            ResearchState.REPLANNING: {ResearchState.PLANNING, ResearchState.FAILED, ResearchState.CANCELLED},
            ResearchState.SUCCEEDED: set(), ResearchState.FAILED: set(), ResearchState.CANCELLED: set(),
        }
        if state not in allowed[self.state]:
            raise ValueError(f"invalid_research_transition:{self.state}->{state}")
        return replace(self, state=state, reason=reason)


@dataclass(frozen=True)
class ResearchOutcome:
    session: ResearchSession
    result_code: str
    evidence: tuple[Evidence, ...] = ()
    plan: CandidatePlan | None = None
    reflection: object | None = None
    route: str = "local"
    execution_count: int = 0
