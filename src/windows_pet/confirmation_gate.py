from __future__ import annotations
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .action_models import ActionProposal, ConfirmationDecision, PolicyDecision, ToolContract
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
                self._sessions[session.session_id] = (session, "pending")
            self._audit("confirmation_shown", proposal, "ok")
            return result, session
        self._audit("policy_allowed_read_only" if result.decision is PolicyDecision.ALLOW_READ_ONLY else "policy_confirmation_required" if result.decision is PolicyDecision.REQUIRE_CONFIRMATION else "policy_denied", proposal, result.reason)
        return result, None

    def decide(self, contract: ToolContract, proposal: ActionProposal, decision: ConfirmationDecision, session_id: str | None = None) -> ExecutionGrant | None:
        if decision is ConfirmationDecision.APPROVE:
            with self._lock:
                session_state = self._sessions.get(session_id or "")
                if session_state is None or session_state[1] != "pending" or session_state[0].proposal_id != proposal.proposal_id or session_state[0].proposal_fingerprint != proposal.fingerprint:
                    return None
                self._sessions[session_id] = (session_state[0], "approved")
        if decision is not ConfirmationDecision.APPROVE:
            self._audit("confirmation_" + decision.value, proposal, decision.value)
            return None
        result = self.policy.evaluate(contract, proposal)
        if result.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            self._audit("policy_denied", proposal, result.reason)
            return None
        grant = self.grants._issue(proposal)
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
