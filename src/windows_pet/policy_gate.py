from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from .action_models import ActionProposal, ConfirmationType, PolicyDecision, SideEffect, ToolContract, proposal_fingerprint


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str = ""


class PolicyGate:
    """Validates the contract and immutable proposal before confirmation."""
    def evaluate(self, contract: ToolContract, proposal: ActionProposal, now: datetime | None = None) -> PolicyResult:
        if not isinstance(proposal.side_effect, SideEffect): return PolicyResult(PolicyDecision.DENY, "unknown_side_effect")
        if proposal.fingerprint != proposal_fingerprint(proposal): return PolicyResult(PolicyDecision.DENY, "invalid_fingerprint")
        if proposal.tool_name != contract.name or proposal.tool_version != contract.version: return PolicyResult(PolicyDecision.DENY, "contract_mismatch")
        if proposal.operation != contract.operation or proposal.side_effect != contract.side_effect: return PolicyResult(PolicyDecision.DENY, "operation_mismatch")
        if proposal.confirmation_type != contract.confirmation: return PolicyResult(PolicyDecision.DENY, "confirmation_mismatch")
        current = now or datetime.now(timezone.utc)
        if current >= proposal.expires_at: return PolicyResult(PolicyDecision.DENY, "expired_proposal")
        if not proposal.target.kind or not proposal.target.identifier: return PolicyResult(PolicyDecision.DENY, "missing_target")
        if contract.side_effect == SideEffect.READ_ONLY:
            if contract.confirmation is not ConfirmationType.NONE: return PolicyResult(PolicyDecision.DENY, "invalid_confirmation_type")
            return PolicyResult(PolicyDecision.ALLOW_READ_ONLY)
        if contract.confirmation is ConfirmationType.NONE: return PolicyResult(PolicyDecision.DENY, "confirmation_required")
        return PolicyResult(PolicyDecision.REQUIRE_CONFIRMATION)
