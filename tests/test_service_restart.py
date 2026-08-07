import json

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.service_restart import (RESTART_SERVICE_CONTRACT, ServiceIdentity,
                                         ServiceIdentityResolver, ServiceRestartProposalFactory,
                                         ServiceRestartRunner, ServiceRestartStatus)


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
