import json
import os
import subprocess
from threading import Event

from PySide6.QtWidgets import QLabel

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.audit_log import InMemoryAuditSink
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.powershell_read_models import PowerShellReadOutcome, PowerShellReadStatus
from windows_pet.service_restart import (RESTART_SERVICE_CONTRACT, ServiceIdentity,
                                         RESTART_SERVICE_ENVIRONMENT_KEYS,
                                         RESTART_SERVICE_SCRIPT, ServiceIdentityResolver,
                                         ServiceRestartOutcome, ServiceRestartProposalFactory,
                                         ServiceRestartRunner, ServiceRestartStatus,
                                         ServiceResolutionCode, canonical_script)


ROWS = [{"name": "Spooler", "displayName": "Print Spooler", "state": "Running", "startMode": "Auto"}]


def approved(identity):
    proposal = ServiceRestartProposalFactory().create("task", identity)
    gate = ConfirmationGate(); _, session = gate.prepare(RESTART_SERVICE_CONTRACT, proposal)
    grant = gate.decide(RESTART_SERVICE_CONTRACT, proposal, ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint)).grant
    return gate, proposal, grant


def test_service_identity_snapshot_is_normalized_protected_and_admin_gated():
    resolver = ServiceIdentityResolver(lambda: ROWS, is_admin=lambda: True)
    identity = resolver.resolve("print spooler", ROWS)
    assert identity.service_name == "Spooler" and resolver.validate(identity) is not None
    assert ServiceIdentityResolver(lambda: [{"name":"RpcSs","displayName":"RPC","state":"Running","startMode":"Auto"}], is_admin=lambda: True).resolve("RPC").service_name == "RpcSs"
    denied = ServiceIdentityResolver(lambda: ROWS, is_admin=lambda: False)
    assert denied.resolve("Spooler").service_name == "Spooler" and denied.last_code.value == "admin_required"


def test_default_live_service_inspection_queries_canonical_name(monkeypatch):
    requests = []

    class ReadRunner:
        def execute(self, request):
            requests.append(request)
            return PowerShellReadOutcome(PowerShellReadStatus.SUCCESS, {"items": ROWS})

    monkeypatch.setattr("windows_pet.service_restart.PowerShellReadRunner", ReadRunner)
    resolver = ServiceIdentityResolver(is_admin=lambda: True)
    identity = resolver.resolve("Spooler")
    assert identity == ServiceIdentity("Spooler", "Print Spooler", "Running")
    assert requests[0].query == "Spooler"
    assert resolver.validate(identity) is ServiceResolutionCode.MATCHED
    assert all(request.query == "Spooler" for request in requests)


def test_service_runner_uses_fixed_argv_environment_and_read_only_verification(tmp_path):
    backend = tmp_path / "powershell.exe"; backend.write_bytes(b"fake")
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    gate, proposal, grant = approved(identity); calls = []
    class Process:
        returncode = 0
        def poll(self): return 0
        def communicate(self, *args, **kwargs): return b"", b""
    resolver = ServiceIdentityResolver(lambda: ROWS, is_admin=lambda: True)
    def factory(argv, **kwargs): calls.append((argv, kwargs)); return Process()
    outcome = ServiceRestartRunner(gate.grants, resolver, process_factory=factory, powershell_exe=backend).execute(grant.grant_id, proposal, identity)
    assert outcome.status is ServiceRestartStatus.SUCCEEDED
    assert calls[0][0][:4] == [str(backend), "-NoLogo", "-NoProfile", "-NonInteractive"] and calls[0][1]["shell"] is False
    assert json.loads(calls[0][1]["env"]["WINDOWSPET_PS_PARAMETERS"]) == {"service_name": "Spooler"}


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _Process:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def communicate(self, *args, **kwargs):
        return b"safe stdout that is never logged", b"safe stderr that is never logged"


class _VerificationResolver:
    def __init__(self, states=(), validation=ServiceResolutionCode.MATCHED, error=None):
        self.states = iter(states)
        self.last_state = "Running"
        self.validation = validation
        self.error = error
        self.resolve_calls = []

    def validate(self, identity, snapshot=None):
        return self.validation

    def resolve(self, query, snapshot=None):
        self.resolve_calls.append(query)
        if self.error:
            raise self.error
        state = next(self.states, self.last_state)
        self.last_state = state
        if state is None:
            return None
        return ServiceIdentity("Spooler", "Print Spooler", state)


def _runner(tmp_path, resolver, process, *, clock=None, sleeper=None, process_factory=None):
    backend = tmp_path / "powershell.exe"
    backend.write_bytes(b"fake")
    gate, proposal, grant = approved(ServiceIdentity("Spooler", "Print Spooler", "Running"))
    audit = InMemoryAuditSink()
    captured = {}

    def factory(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        captured["script"] = open(argv[-1], "rb").read()
        return process

    runner = ServiceRestartRunner(
        gate.grants, resolver,
        process_factory=process_factory or factory,
        powershell_exe=backend,
        audit=audit,
        clock=clock or _Clock(),
        sleeper=sleeper or (lambda _seconds: None),
    )
    return runner, proposal, grant, audit, captured


def test_runner_success_records_execution_and_immediate_running_verification(tmp_path):
    resolver = _VerificationResolver(["Running"])
    runner, proposal, grant, audit, captured = _runner(tmp_path, resolver, _Process())
    outcome = runner.execute(grant.grant_id, proposal, ServiceIdentity("Spooler", "Print Spooler", "Running"))
    assert outcome == ServiceRestartOutcome(ServiceRestartStatus.SUCCEEDED, "ok", 0, "running")
    assert captured["script"] == canonical_script(RESTART_SERVICE_SCRIPT)
    assert captured["argv"][:4] == [str(tmp_path / "powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive"]
    assert set(captured["kwargs"]["env"]).issubset(set(RESTART_SERVICE_ENVIRONMENT_KEYS))
    assert json.loads(captured["kwargs"]["env"]["WINDOWSPET_PS_PARAMETERS"]) == {"service_name": "Spooler"}
    assert [event.event_type for event in audit.events] == [
        "powershell_execution_started", "powershell_execution_succeeded",
        "powershell_verification_succeeded",
    ]
    assert all("stdout" not in event.__dict__ and "stderr" not in event.__dict__ for event in audit.events)


def test_runner_polls_transitional_state_until_running(tmp_path):
    resolver = _VerificationResolver(["StartPending", "Running"])
    clock = _Clock()
    sleeps = []
    runner, proposal, grant, _audit, _captured = _runner(
        tmp_path, resolver, _Process(), clock=clock,
        sleeper=lambda seconds: (sleeps.append(seconds), clock.advance(seconds)),
    )
    outcome = runner.execute(grant.grant_id, proposal, ServiceIdentity("Spooler", "Print Spooler", "Running"))
    assert outcome.status is ServiceRestartStatus.SUCCEEDED
    assert outcome.result_code == "ok" and sleeps == [0.25]


def test_runner_reports_verification_timeout_for_persistent_transitional_state(tmp_path):
    resolver = _VerificationResolver(["StartPending"])
    clock = _Clock()
    runner, proposal, grant, audit, _captured = _runner(
        tmp_path, resolver, _Process(), clock=clock,
        sleeper=lambda seconds: clock.advance(seconds),
    )
    outcome = runner.execute(grant.grant_id, proposal, ServiceIdentity("Spooler", "Print Spooler", "Running"))
    assert outcome.status is ServiceRestartStatus.VERIFICATION_FAILED
    assert outcome.result_code == "verification_timeout"
    assert audit.events[-1].event_type == "powershell_verification_failed"
    assert audit.events[-1].verification_result == "verification_timeout"


def test_runner_distinguishes_nonzero_exit_and_spawn_failure(tmp_path):
    resolver = _VerificationResolver(["Running"])
    runner, proposal, grant, audit, _captured = _runner(tmp_path, resolver, _Process(5))
    outcome = runner.execute(grant.grant_id, proposal, ServiceIdentity("Spooler", "Print Spooler", "Running"))
    assert outcome.status is ServiceRestartStatus.FAILED
    assert outcome.result_code == "nonzero_exit" and outcome.exit_code == 5
    assert resolver.resolve_calls == []
    assert audit.events[-1].result_code == "nonzero_exit"

    gate, proposal, grant = approved(ServiceIdentity("Spooler", "Print Spooler", "Running"))
    backend = tmp_path / "powershell-spawn.exe"
    backend.write_bytes(b"fake")
    spawn_runner = ServiceRestartRunner(
        gate.grants, resolver, process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn")),
        powershell_exe=backend,
    )
    spawn_outcome = spawn_runner.execute(grant.grant_id, proposal, ServiceIdentity("Spooler", "Print Spooler", "Running"))
    assert spawn_outcome.status is ServiceRestartStatus.FAILED
    assert spawn_outcome.result_code == "spawn_failed"


def test_runner_distinguishes_cancel_timeout_and_verification_provider_error(tmp_path):
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    resolver = _VerificationResolver(["Running"])
    runner, proposal, grant, _audit, _captured = _runner(tmp_path, resolver, _Process())
    runner.cancel()
    cancelled = runner.execute(grant.grant_id, proposal, identity)
    assert cancelled.status is ServiceRestartStatus.CANCELLED and cancelled.result_code == "cancelled"

    class NeverEnding(_Process):
        def __init__(self, clock):
            super().__init__(None)
            self.clock = clock

        def poll(self):
            return None

        def communicate(self, *args, **kwargs):
            self.clock.advance(31)
            raise subprocess.TimeoutExpired("powershell", kwargs.get("timeout", 0))

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    timeout_clock = _Clock()
    timeout_process = NeverEnding(timeout_clock)
    timeout_runner, proposal, grant, _audit, _captured = _runner(
        tmp_path, resolver, timeout_process, clock=timeout_clock,
    )
    timed_out = timeout_runner.execute(grant.grant_id, proposal, identity)
    assert timed_out.status is ServiceRestartStatus.TIMED_OUT and timed_out.result_code == "timeout"

    error_runner, proposal, grant, _audit, _captured = _runner(
        tmp_path, _VerificationResolver(error=RuntimeError("provider")), _Process()
    )
    verification_error = error_runner.execute(grant.grant_id, proposal, identity)
    assert verification_error.status is ServiceRestartStatus.VERIFICATION_FAILED
    assert verification_error.result_code == "verification_provider_error"


def test_runner_reports_service_disappeared_and_not_running(tmp_path):
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    runner, proposal, grant, _audit, _captured = _runner(tmp_path, _VerificationResolver([None]), _Process())
    disappeared = runner.execute(grant.grant_id, proposal, identity)
    assert disappeared.result_code == "service_not_found_after_execution"

    runner, proposal, grant, _audit, _captured = _runner(tmp_path, _VerificationResolver(["Stopped"]), _Process())
    stopped = runner.execute(grant.grant_id, proposal, identity)
    assert stopped.status is ServiceRestartStatus.VERIFICATION_FAILED
    assert stopped.result_code == "service_not_running"


def test_runner_rejects_identity_change_before_spawn(tmp_path):
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    runner, proposal, grant, _audit, _captured = _runner(
        tmp_path, _VerificationResolver(validation=ServiceResolutionCode.CHANGED), _Process()
    )
    outcome = runner.execute(grant.grant_id, proposal, identity)
    assert outcome.status is ServiceRestartStatus.REJECTED
    assert outcome.result_code == "identity_changed"


def test_service_restart_confirmation_uses_japanese_labels_and_safe_default(qapp):
    from windows_pet.action_confirmation_dialog import ActionConfirmationDialog

    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    proposal = ServiceRestartProposalFactory().create("task", identity)
    _, session = ConfirmationGate().prepare(RESTART_SERVICE_CONTRACT, proposal)
    dialog = ActionConfirmationDialog(proposal, session)
    labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert all(label in labels for label in (
        "目的:", "対象:", "実行環境:", "作業ディレクトリ:", "環境変数:",
        "想定される変更:", "管理者権限:", "タイムアウト:", "実行後の確認:", "元に戻す方法:",
    ))
    assert dialog.approve_button.text() == "サービスを再起動"
    assert dialog.cancel_button.text() == "キャンセル"
    assert dialog.cancel_button.isDefault()
    dialog.close()
