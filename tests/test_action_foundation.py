from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from windows_pet.action_models import *
from windows_pet.policy_gate import PolicyGate
from windows_pet.execution_grant import ExecutionGrantStore
from windows_pet.audit_log import AuditEvent, InMemoryAuditSink, JsonlAuditSink
from windows_pet.confirmation_gate import ConfirmationGate


def make(side=SideEffect.APPLICATION_LAUNCH, confirmation=ConfirmationType.SIMPLE, now=None):
    contract = ToolContract("fake", "1", "inspect", side, confirmation, True, False, True, 2, "verify")
    target = ActionTarget("application", "FAKE_TARGET", "Fake app")
    preview = SimpleActionPreview("inspect", "Fake impact", "Allow")
    proposal = ActionProposalFactory(now=now or (lambda: datetime.now(timezone.utc))).create(contract, "task", target, {"path": "FAKE_PATH"}, preview)
    return contract, proposal


def test_proposal_is_immutable_and_canonical():
    source = {"b": 2, "a": [1]}
    contract, proposal = make()
    with pytest.raises(TypeError): proposal.parameters["x"] = 1
    equivalent = ActionProposalFactory(now=lambda: proposal.created_at).create(contract, "task", proposal.target, {"path": "FAKE_PATH"}, proposal.preview)
    assert proposal.fingerprint == equivalent.fingerprint
    source["a"].append(3)
    assert proposal.parameters["path"] == "FAKE_PATH"


def test_sensitive_and_invalid_parameters_rejected():
    contract, target = make()[0], ActionTarget("file", "x", "x")
    preview = SimpleActionPreview("x", "x", "Allow")
    with pytest.raises(ValueError, match="sensitive_parameter"):
        ActionProposalFactory().create(contract, "t", target, {"api_key": "FAKE_SECRET_VALUE"}, preview)
    with pytest.raises(ValueError):
        ActionProposalFactory().create(contract, "t", target, {1: "bad"}, preview)


def test_policy_read_only_and_confirmation():
    readonly, proposal = make(SideEffect.READ_ONLY, ConfirmationType.NONE)
    assert PolicyGate().evaluate(readonly, proposal).decision is PolicyDecision.ALLOW_READ_ONLY
    contract, proposal = make()
    assert PolicyGate().evaluate(contract, proposal).decision is PolicyDecision.REQUIRE_CONFIRMATION


def test_grant_is_single_use_and_fingerprint_bound():
    contract, proposal = make()
    gate = ConfirmationGate(); _, session = gate.prepare(contract, proposal)
    grant = gate.decide(contract, proposal, ConfirmationDecision.APPROVE, session.session_id)
    store = gate.grants
    assert store.consume_for(grant.grant_id, contract, proposal).success
    assert not store.consume_for(grant.grant_id, contract, proposal).success


def test_concurrent_grant_consume_has_one_success():
    contract, proposal = make()
    gate = ConfirmationGate(); _, session = gate.prepare(contract, proposal)
    grant = gate.decide(contract, proposal, ConfirmationDecision.APPROVE, session.session_id)
    store = gate.grants
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.consume_for(grant.grant_id, contract, proposal).success, range(8)))
    assert sum(results) == 1


def test_gate_only_approves_and_audit_excludes_targets(tmp_path):
    contract, proposal = make(); sink = InMemoryAuditSink(); gate = ConfirmationGate(audit=sink)
    _, session = gate.prepare(contract, proposal)
    grant = gate.decide(contract, proposal, ConfirmationDecision.CANCEL, session.session_id)
    assert grant is None
    _, session = gate.prepare(contract, proposal)
    grant = gate.decide(contract, proposal, ConfirmationDecision.APPROVE, session.session_id)
    assert grant is not None
    text = json.dumps([event.__dict__ for event in sink.events])
    assert "FAKE_TARGET" not in text and "FAKE_PATH" not in text


def test_jsonl_audit_is_one_event_per_line(tmp_path):
    path = tmp_path / "audit.jsonl"; sink = JsonlAuditSink(path)
    sink.write(AuditEvent("proposal_created", task_id="task"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_direct_grant_issue_is_rejected():
    contract, proposal = make()
    with pytest.raises(PermissionError):
        ExecutionGrantStore().issue(proposal)


def test_session_is_required_and_readonly_has_no_session():
    contract, proposal = make()
    gate = ConfirmationGate()
    result, session = gate.prepare(contract, proposal)
    assert session is not None
    readonly, read_proposal = make(SideEffect.READ_ONLY, ConfirmationType.NONE)
    read_result, read_session = gate.prepare(readonly, read_proposal)
    assert read_session is None and read_result.decision is PolicyDecision.ALLOW_READ_ONLY


def test_factory_rejects_empty_task_and_invalid_timeout():
    contract, proposal = make()
    with pytest.raises(ValueError):
        ActionProposalFactory().create(contract, "", proposal.target, {}, proposal.preview)
    bad = ToolContract("x", "1", "x", SideEffect.APPLICATION_LAUNCH, ConfirmationType.SIMPLE, True, False, True, 0, "verify")
    with pytest.raises(ValueError):
        ActionProposalFactory().create(bad, "task", proposal.target, {}, proposal.preview)
