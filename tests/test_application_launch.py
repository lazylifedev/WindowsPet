from types import SimpleNamespace
from pathlib import Path
from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.application_launch import (APPLICATION_LAUNCH_CONTRACT, ApplicationLaunchExecutor,
    ApplicationLaunchProposalFactory, ApplicationLaunchStatus, ApplicationLaunchTarget, ApplicationLaunchValidator, LaunchValidationCode)
from windows_pet.audit_log import InMemoryAuditSink
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.local_inspection_models import AppCandidate


def test_validator_rejects_relative_and_non_exe():
    validator = ApplicationLaunchValidator()
    assert validator.validate(SimpleNamespace(executable_path="app.exe", display_name="x"))[1] is LaunchValidationCode.RELATIVE_PATH
    assert validator.validate(SimpleNamespace(executable_path="C:/fake/app.bat", display_name="x"))[1] is LaunchValidationCode.UNSUPPORTED_EXTENSION


def test_executor_does_not_start_without_valid_grant():
    calls = []
    executor = ApplicationLaunchExecutor(SimpleNamespace(consume_for=lambda *args: SimpleNamespace(success=False, reason=SimpleNamespace(value="not_found"))), process_factory=lambda *a, **k: calls.append(1))
    outcome = executor.execute("g", SimpleNamespace(), SimpleNamespace(canonical_path="C:/fake/app.exe", display_name="x", file_size=1, modified_time_ns=1))
    assert outcome.status is ApplicationLaunchStatus.REJECTED and not calls


class FakeValidator:
    def validate_target(self, target): return True
    def matches(self, target): return True


class FakeProcess:
    def __init__(self, code=None): self.code = code
    def poll(self): return self.code


def approved_launch():
    candidate = AppCandidate("FAKE_APP_NAME", executable_path=r"C:\Fake\Apps\Example.exe", executable_exists=True)
    target = ApplicationLaunchTarget(candidate.display_name, candidate.executable_path, 12, 34)
    proposal = ApplicationLaunchProposalFactory().create("fake-task", candidate, target)
    audit = InMemoryAuditSink(); gate = ConfirmationGate(audit=audit, session_id_factory=lambda: "session")
    _, session = gate.prepare(APPLICATION_LAUNCH_CONTRACT, proposal)
    response = ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint)
    result = gate.decide(APPLICATION_LAUNCH_CONTRACT, proposal, response)
    return target, proposal, gate, result.grant, audit


def test_proposal_factory_uses_instance_and_full_contract():
    target, proposal, _, _, _ = approved_launch()
    assert proposal.tool_name == APPLICATION_LAUNCH_CONTRACT.name
    assert proposal.tool_version == APPLICATION_LAUNCH_CONTRACT.version
    assert proposal.target.identifier == target.canonical_path
    assert proposal.parameters["file_size"] == target.file_size
    assert proposal.parameters["modified_time_ns"] == target.modified_time_ns
    assert proposal.parameters["arguments"] == ()
    assert proposal.fingerprint


def test_approved_fake_product_flow_starts_without_real_process():
    target, proposal, gate, grant, audit = approved_launch(); calls = []
    executor = ApplicationLaunchExecutor(gate.grants, validator=FakeValidator(),
        process_factory=lambda *args, **kwargs: (calls.append((args, kwargs)) or FakeProcess()), sleeper=lambda _: None, audit=audit)
    outcome = executor.execute(grant.grant_id, proposal, target)
    assert outcome == type(outcome)(ApplicationLaunchStatus.STARTED, "process_running")
    assert calls and calls[0][0] == ([target.canonical_path],)
    assert [event.event_type for event in audit.events][-5:] == ["grant_issued", "grant_consumed", "execution_started", "verification_succeeded", "execution_succeeded"]


def test_executor_rejects_contract_mismatch_before_consuming_grant():
    target, proposal, gate, grant, _ = approved_launch(); calls = []
    bad = type(proposal)(**{**proposal.__dict__, "tool_version": "wrong"})
    outcome = ApplicationLaunchExecutor(gate.grants, validator=FakeValidator(), process_factory=lambda *a, **k: calls.append(1)).execute(grant.grant_id, bad, target)
    assert outcome.result_code == "invalid_request" and not calls


def test_executor_maps_poll_failure_to_verification_failed():
    target, proposal, gate, grant, audit = approved_launch()
    class PollFailure:
        def poll(self): raise RuntimeError("FAKE_EXCEPTION_DETAIL")
    outcome = ApplicationLaunchExecutor(gate.grants, validator=FakeValidator(), process_factory=lambda *a, **k: PollFailure(), sleeper=lambda _: None, audit=audit).execute(grant.grant_id, proposal, target)
    assert outcome.status is ApplicationLaunchStatus.FAILED and outcome.result_code == "verification_failed"
    assert audit.events[-1].event_type == "verification_failed"


def test_grant_rejection_and_cancellation_are_audited():
    target, proposal, gate, grant, audit = approved_launch()
    assert gate.grants.cancel(grant.grant_id).value == "cancelled"
    result = gate.grants.consume_for(grant.grant_id, APPLICATION_LAUNCH_CONTRACT, proposal)
    assert result.reason.value == "cancelled"
    assert [event.event_type for event in audit.events][-2:] == ["grant_cancelled", "grant_rejected"]


def test_expired_grant_writes_expired_then_rejected():
    from dataclasses import replace
    from datetime import timedelta
    target, proposal, gate, grant, audit = approved_launch()
    gate.grants._records[grant.grant_id].grant = replace(
        gate.grants._records[grant.grant_id].grant,
        expires_at=grant.issued_at - timedelta(seconds=1),
    )
    result = gate.grants.consume_for(grant.grant_id, APPLICATION_LAUNCH_CONTRACT, proposal)
    assert result.reason.value == "expired"
    events = audit.events[-2:]
    assert [event.event_type for event in events] == ["grant_expired", "grant_rejected"]
    assert [event.result_code for event in events] == ["expired", "expired"]


def test_install_location_reparse_root_is_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from windows_pet.application_candidate_resolver import ApplicationCandidateResolver
    from windows_pet.cancellation import CancellationToken

    root = tmp_path / "Program Files" / "Example"; root.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    validator = SimpleNamespace(is_reparse_point=lambda path: Path(path) == root)
    resolver = ApplicationCandidateResolver(validator=validator)
    snapshot = SimpleNamespace(installed_apps=[SimpleNamespace(install_location=str(root), display_name="Example", version="", publisher="")])
    assert resolver._install_location_candidates(snapshot, "Example", CancellationToken()) == []


def test_install_location_reparse_child_is_not_walked(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from windows_pet.application_candidate_resolver import ApplicationCandidateResolver
    from windows_pet.cancellation import CancellationToken

    root = tmp_path / "Program Files" / "Example"; child = root / "redirect"; child.mkdir(parents=True)
    (child / "Example.exe").write_text("fake")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    validator = SimpleNamespace(is_reparse_point=lambda path: Path(path) == child)
    resolver = ApplicationCandidateResolver(validator=validator)
    snapshot = SimpleNamespace(installed_apps=[SimpleNamespace(install_location=str(root), display_name="Example", version="", publisher="")])
    assert resolver._install_location_candidates(snapshot, "Example", CancellationToken()) == []


def test_install_location_reparse_executable_is_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from windows_pet.application_candidate_resolver import ApplicationCandidateResolver
    from windows_pet.cancellation import CancellationToken

    root = tmp_path / "Program Files" / "Example"; root.mkdir(parents=True); executable = root / "Example.exe"; executable.write_text("fake")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    validator = SimpleNamespace(is_reparse_point=lambda path: Path(path) == executable)
    resolver = ApplicationCandidateResolver(validator=validator)
    snapshot = SimpleNamespace(installed_apps=[SimpleNamespace(install_location=str(root), display_name="Example", version="", publisher="")])
    assert resolver._install_location_candidates(snapshot, "Example", CancellationToken()) == []
