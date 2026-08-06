from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from .action_models import ActionProposal, ConfirmationType, PolicyDecision, SideEffect, ToolContract, proposal_fingerprint, validate_contract, validate_preview


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str = ""


class PolicyGate:
    """Validates the contract and immutable proposal before confirmation."""
    def evaluate(self, contract: ToolContract, proposal: ActionProposal, now: datetime | None = None) -> PolicyResult:
        try:
            validate_contract(contract)
        except ValueError as error:
            return PolicyResult(PolicyDecision.DENY, str(error))
        if not isinstance(proposal.side_effect, SideEffect): return PolicyResult(PolicyDecision.DENY, "unknown_side_effect")
        if proposal.fingerprint != proposal_fingerprint(proposal): return PolicyResult(PolicyDecision.DENY, "invalid_fingerprint")
        if proposal.tool_name != contract.name: return PolicyResult(PolicyDecision.DENY, "tool_name_mismatch")
        if proposal.tool_version != contract.version: return PolicyResult(PolicyDecision.DENY, "tool_version_mismatch")
        if proposal.operation != contract.operation: return PolicyResult(PolicyDecision.DENY, "operation_mismatch")
        if proposal.side_effect != contract.side_effect: return PolicyResult(PolicyDecision.DENY, "side_effect_mismatch")
        if proposal.confirmation_type != contract.confirmation: return PolicyResult(PolicyDecision.DENY, "confirmation_type_mismatch")
        if proposal.reversible != contract.reversible: return PolicyResult(PolicyDecision.DENY, "reversible_mismatch")
        if proposal.requires_admin != contract.requires_admin: return PolicyResult(PolicyDecision.DENY, "admin_requirement_mismatch")
        if proposal.cancellation_support != contract.cancellation_support: return PolicyResult(PolicyDecision.DENY, "cancellation_mismatch")
        if proposal.timeout_seconds != contract.timeout_seconds: return PolicyResult(PolicyDecision.DENY, "timeout_mismatch")
        if proposal.verification_method != contract.verification_method: return PolicyResult(PolicyDecision.DENY, "verification_mismatch")
        if proposal.audit_fields != contract.audit_fields: return PolicyResult(PolicyDecision.DENY, "audit_fields_mismatch")
        try: validate_preview(proposal.preview, contract.confirmation)
        except ValueError: return PolicyResult(PolicyDecision.DENY, "preview_mismatch")
        current = now or datetime.now(timezone.utc)
        if current >= proposal.expires_at: return PolicyResult(PolicyDecision.DENY, "expired_proposal")
        if not proposal.target.kind or not proposal.target.identifier: return PolicyResult(PolicyDecision.DENY, "missing_target")
        if contract.side_effect == SideEffect.READ_ONLY:
            if contract.confirmation is not ConfirmationType.NONE: return PolicyResult(PolicyDecision.DENY, "invalid_confirmation_type")
            return PolicyResult(PolicyDecision.ALLOW_READ_ONLY)
        if contract.confirmation is ConfirmationType.NONE: return PolicyResult(PolicyDecision.DENY, "confirmation_required")
        return PolicyResult(PolicyDecision.REQUIRE_CONFIRMATION)
