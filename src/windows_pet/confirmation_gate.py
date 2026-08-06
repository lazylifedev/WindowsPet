from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from .action_models import ActionProposal, ConfirmationDecision, ConfirmationResponse, PolicyDecision, ToolContract
from .audit_log import AuditEvent
from .execution_grant import ExecutionGrant, ExecutionGrantStore
from .policy_gate import PolicyGate


class SessionState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    REVISE_REQUESTED = "revise_requested"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ConfirmationSession:
    session_id: str
    proposal_id: str
    proposal_fingerprint: str
    created_at: datetime
    expires_at: datetime
    state: SessionState = SessionState.PENDING


class ConfirmationResultCode(str, Enum):
    APPROVED = "approved"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    REVISE_REQUESTED = "revise_requested"
    EXPIRED = "expired"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_NOT_PENDING = "session_not_pending"
    PROPOSAL_MISMATCH = "proposal_mismatch"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    POLICY_DENIED = "policy_denied"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class ConfirmationResult:
    success: bool
    reason: ConfirmationResultCode
    grant: ExecutionGrant | None = None


class ConfirmationGate:
    """Owns the only grant-recording closure and serializes session decisions."""
    def __init__(self, policy=None, grants=None, audit=None, now=None, session_id_factory=None):
        self.policy = policy or PolicyGate()
        self.audit = audit
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.session_id_factory = session_id_factory or (lambda: secrets.token_urlsafe(18))
        self._sessions: dict[str, ConfirmationSession] = {}
        self._lock = threading.RLock()
        self.grants = grants or ExecutionGrantStore(policy=self.policy, session_lookup=self._sessions.get)

    def prepare(self, contract, proposal):
        result = self.policy.evaluate(contract, proposal)
        if result.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            self._audit("policy_allowed_read_only" if result.decision is PolicyDecision.ALLOW_READ_ONLY else "policy_denied", proposal, result.reason)
            return result, None
        now = self.now()
        session = ConfirmationSession(self.session_id_factory(), proposal.proposal_id, proposal.fingerprint, now, min(proposal.expires_at, now + timedelta(seconds=90)))
        with self._lock:
            self._sessions[session.session_id] = session
        self._audit("policy_confirmation_required", proposal, "confirmation_required")
        self._audit("confirmation_shown", proposal, "ok")
        return result, session

    @staticmethod
    def _state(session, state):
        return ConfirmationSession(session.session_id, session.proposal_id, session.proposal_fingerprint, session.created_at, session.expires_at, state)

    def decide(self, contract: ToolContract, proposal: ActionProposal, response: ConfirmationResponse) -> ConfirmationResult:
        if not isinstance(response, ConfirmationResponse): return ConfirmationResult(False, ConfirmationResultCode.INVALID_RESPONSE)
        with self._lock:
            session = self._sessions.get(response.session_id)
            if session is None: return ConfirmationResult(False, ConfirmationResultCode.SESSION_NOT_FOUND)
            if session.state is not SessionState.PENDING: return ConfirmationResult(False, ConfirmationResultCode.SESSION_NOT_PENDING)
            if session.proposal_id != proposal.proposal_id or response.proposal_id != proposal.proposal_id: return ConfirmationResult(False, ConfirmationResultCode.PROPOSAL_MISMATCH)
            if session.proposal_fingerprint != proposal.fingerprint or response.displayed_fingerprint != proposal.fingerprint: return ConfirmationResult(False, ConfirmationResultCode.FINGERPRINT_MISMATCH)
            if self.now() >= min(session.expires_at, proposal.expires_at):
                self._sessions[session.session_id] = self._state(session, SessionState.EXPIRED)
                self._audit("proposal_expired", proposal, "expired")
                return ConfirmationResult(False, ConfirmationResultCode.EXPIRED)
            if response.decision is not ConfirmationDecision.APPROVE:
                states = {ConfirmationDecision.CANCEL: (SessionState.CANCELLED, ConfirmationResultCode.CANCELLED), ConfirmationDecision.CLOSED: (SessionState.CLOSED, ConfirmationResultCode.CLOSED), ConfirmationDecision.REVISE: (SessionState.REVISE_REQUESTED, ConfirmationResultCode.REVISE_REQUESTED), ConfirmationDecision.EXPIRED: (SessionState.EXPIRED, ConfirmationResultCode.EXPIRED)}
                state, code = states.get(response.decision, (SessionState.CANCELLED, ConfirmationResultCode.INVALID_RESPONSE))
                self._sessions[session.session_id] = self._state(session, state)
                event_name = {ConfirmationDecision.CANCEL: "confirmation_cancelled", ConfirmationDecision.CLOSED: "confirmation_closed", ConfirmationDecision.REVISE: "confirmation_revise_requested", ConfirmationDecision.EXPIRED: "proposal_expired"}[response.decision]
                self._audit(event_name, proposal, code.value)
                return ConfirmationResult(False, code)
            if self.policy.evaluate(contract, proposal).decision is not PolicyDecision.REQUIRE_CONFIRMATION:
                return ConfirmationResult(False, ConfirmationResultCode.POLICY_DENIED)
            try:
                grant = self.grants._record_grant(proposal, session.session_id)
            except Exception:
                return ConfirmationResult(False, ConfirmationResultCode.POLICY_DENIED)
            self._sessions[session.session_id] = self._state(session, SessionState.APPROVED)
            self._audit("confirmation_approved", proposal, "approved", grant)
            self._audit("grant_issued", proposal, "issued", grant)
            return ConfirmationResult(True, ConfirmationResultCode.APPROVED, grant)

    def _audit(self, event_type, proposal, code, grant=None):
        if self.audit:
            self.audit.write(AuditEvent(event_type, code, task_id=proposal.task_id, proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint, grant_id=grant.grant_id if grant else "", tool_name=proposal.tool_name, tool_version=proposal.tool_version, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value, requires_admin=proposal.requires_admin, reversible=proposal.reversible))
