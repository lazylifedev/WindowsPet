from datetime import datetime, timezone
import pytest

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.process_stop import (ProcessIdentity, ProcessIdentityResolver,
    ProcessValidationCode, PowerShellExecutionProposalFactory, STOP_PROCESS_CONTRACT,
    canonical_script, script_sha256)
from windows_pet.process_stop_request import parse_process_stop_request

def test_script_canonicalization_is_stable_and_rejects_controls():
    assert script_sha256("a\r\n") == script_sha256("a\n\n")
    with pytest.raises(ValueError): canonical_script("a\0")
    with pytest.raises(ValueError): canonical_script("a\x01")

def test_stop_request_is_strict():
    request = parse_process_stop_request({"process_id": 123, "expected_process_name": "notepad"})
    assert request.process_id == 123
    with pytest.raises(ValueError): parse_process_stop_request({"process_id": 123, "expected_process_name": "x", "extra": True})

def test_protected_and_pid_reuse_are_rejected():
    current = ProcessIdentity(11, "notepad", "one")
    resolver = ProcessIdentityResolver(lambda _: current, self_pid=lambda: 99)
    assert resolver.validate(current, "notepad") is ProcessValidationCode.OK
    assert resolver.validate(current, "calc") is ProcessValidationCode.NAME_MISMATCH
    assert resolver.validate(ProcessIdentity(4, "anything", "x")) is ProcessValidationCode.PROTECTED
    assert resolver.validate(ProcessIdentity(11, "notepad", "old")) is ProcessValidationCode.IDENTITY_CHANGED

def test_stop_proposal_requires_script_review_and_hash_bound():
    identity = ProcessIdentity(123, "notepad", "ticks")
    proposal = PowerShellExecutionProposalFactory().create("task", identity)
    assert proposal.confirmation_type.value == "script_review"
    assert proposal.parameters["script_sha256"] == script_sha256(proposal.preview.script_text)
    gate = ConfirmationGate(); _, session = gate.prepare(STOP_PROCESS_CONTRACT, proposal)
    grant = gate.decide(STOP_PROCESS_CONTRACT, proposal, ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint)).grant
    assert grant is not None
