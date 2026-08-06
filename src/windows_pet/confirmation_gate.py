from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .action_models import ActionProposal, ConfirmationDecision, PolicyDecision, ToolContract
from enum import Enum
from .audit_log import AuditEvent, AuditSink
from .execution_grant import ExecutionGrant, ExecutionGrantStore
from .policy_gate import PolicyGate


@dataclass(frozen=True)
class ConfirmationSession:
    session_id: str
    proposal_id: str
    proposal_fingerprint: str
    created_at: datetime
    expires_at: datetime


class SessionState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    REVISE_REQUESTED = "revise_requested"
    EXPIRED = "expired"


class ConfirmationGate:
    """Connects policy validation, user decisions, grants, and safe audit events."""
    def __init__(self, policy=None, grants=None, audit=None):
        self.policy = policy or PolicyGate()
        self.grants = grants or ExecutionGrantStore()
        self.audit = audit
        self._sessions = {}
        self._lock = threading.Lock()

    def prepare(self, contract: ToolContract, proposal: ActionProposal):
        result = self.policy.evaluate(contract, proposal)
        if result.decision is PolicyDecision.REQUIRE_CONFIRMATION:
            now = datetime.now(timezone.utc)
            session = ConfirmationSession(secrets.token_urlsafe(18), proposal.proposal_id, proposal.fingerprint, now, min(proposal.expires_at, now + timedelta(seconds=90)))
            with self._lock:
                self._sessions[session.session_id] = (session, SessionState.PENDING)
            self._audit("confirmation_shown", proposal, "ok")
            return result, session
        self._audit("policy_allowed_read_only" if result.decision is PolicyDecision.ALLOW_READ_ONLY else "policy_confirmation_required" if result.decision is PolicyDecision.REQUIRE_CONFIRMATION else "policy_denied", proposal, result.reason)
        return result, None

    def decide(self, contract: ToolContract, proposal: ActionProposal, decision: ConfirmationDecision, session_id: str | None = None) -> ExecutionGrant | None:
        if decision is ConfirmationDecision.APPROVE:
            with self._lock:
                session_state = self._sessions.get(session_id or "")
                if session_state is None or session_state[1] is not SessionState.PENDING or session_state[0].proposal_id != proposal.proposal_id or session_state[0].proposal_fingerprint != proposal.fingerprint:
                    return None
        if decision is not ConfirmationDecision.APPROVE:
            event_name = {ConfirmationDecision.CANCEL: "confirmation_cancelled", ConfirmationDecision.CLOSED: "confirmation_closed", ConfirmationDecision.REVISE: "confirmation_revise_requested", ConfirmationDecision.EXPIRED: "proposal_expired"}.get(decision, "policy_denied")
            if session_id:
                with self._lock:
                    current = self._sessions.get(session_id)
                    if current is not None and current[1] is SessionState.PENDING:
                        state = {ConfirmationDecision.CANCEL: SessionState.CANCELLED, ConfirmationDecision.CLOSED: SessionState.CLOSED, ConfirmationDecision.REVISE: SessionState.REVISE_REQUESTED, ConfirmationDecision.EXPIRED: SessionState.EXPIRED}.get(decision, SessionState.EXPIRED)
                        self._sessions[session_id] = (current[0], state)
            self._audit(event_name, proposal, decision.value)
            return None
        result = self.policy.evaluate(contract, proposal)
        if result.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            self._audit("policy_denied", proposal, result.reason)
            return None
        grant = self.grants._issue_for_session(proposal, session_id or "")
        with self._lock:
            self._sessions[session_id] = (session_state[0], SessionState.APPROVED)
        self._audit("confirmation_approved", proposal, "ok", grant)
        self._audit("grant_issued", proposal, "ok", grant)
        return grant

    def _audit(self, event_type, proposal, code, grant=None):
        if self.audit is not None:
            self.audit.write(AuditEvent(event_type, code, task_id=proposal.task_id, proposal_id=proposal.proposal_id,
                                        proposal_fingerprint=proposal.fingerprint, grant_id=grant.grant_id if grant else "",
                                        tool_name=proposal.tool_name, tool_version=proposal.tool_version,
                                        operation=proposal.operation, side_effect=proposal.side_effect.value,
                                        confirmation_type=proposal.confirmation_type.value,
                                        requires_admin=proposal.requires_admin, reversible=proposal.reversible))
