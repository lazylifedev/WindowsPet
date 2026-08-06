from __future__ import annotations
from .action_models import ActionProposal, ConfirmationDecision, PolicyDecision, ToolContract
from .audit_log import AuditEvent, AuditSink
from .execution_grant import ExecutionGrant, ExecutionGrantStore
from .policy_gate import PolicyGate


class ConfirmationGate:
    """Connects policy validation, user decisions, grants, and safe audit events."""
    def __init__(self, policy=None, grants=None, audit=None):
        self.policy = policy or PolicyGate()
        self.grants = grants or ExecutionGrantStore()
        self.audit = audit

    def prepare(self, contract: ToolContract, proposal: ActionProposal):
        result = self.policy.evaluate(contract, proposal)
        self._audit("policy_allowed_read_only" if result.decision is PolicyDecision.ALLOW_READ_ONLY else "policy_confirmation_required" if result.decision is PolicyDecision.REQUIRE_CONFIRMATION else "policy_denied", proposal, result.reason)
        return result

    def decide(self, contract: ToolContract, proposal: ActionProposal, decision: ConfirmationDecision) -> ExecutionGrant | None:
        if decision is not ConfirmationDecision.APPROVE:
            self._audit("confirmation_" + decision.value, proposal, decision.value)
            return None
        result = self.policy.evaluate(contract, proposal)
        if result.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            self._audit("policy_denied", proposal, result.reason)
            return None
        grant = self.grants.issue(proposal)
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
