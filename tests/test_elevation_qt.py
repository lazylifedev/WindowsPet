from __future__ import annotations

from windows_pet.elevation import (
    ElevationBrokerClient,
    ElevationEnvelopeFactory,
    ElevationQtController,
    ElevationRequest,
    ElevationStatus,
    FakeElevationLauncher,
    BrokerEntryPoint,
    OneShotClaimStore,
)
from windows_pet.service_restart import (
    RESTART_SERVICE_CONTRACT,
    ServiceIdentity,
    ServiceRestartProposalFactory,
)
from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.confirmation_gate import ConfirmationGate


def _request():
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    proposal = ServiceRestartProposalFactory().create("qt-task", identity)
    gate = ConfirmationGate(); _, session = gate.prepare(RESTART_SERVICE_CONTRACT, proposal)
    grant = gate.decide(RESTART_SERVICE_CONTRACT, proposal, ConfirmationResponse(
        ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id,
        proposal.fingerprint)).grant
    return ElevationRequest.from_proposal(proposal, grant)


def test_elevation_qthread_normal_completion_and_shutdown(qapp, tmp_path):
    request = _request()
    broker = BrokerEntryPoint(envelope_root=tmp_path / "payloads", claims=OneShotClaimStore(tmp_path / "claims"))
    client = ElevationBrokerClient(FakeElevationLauncher(broker), envelope_directory=tmp_path / "payloads")
    controller = ElevationQtController(client)
    results = []
    controller.completed.connect(results.append)
    assert controller.start(request, None, lambda _result: True)
    assert controller.thread is not None and controller.thread.wait(5000)
    qapp.processEvents()
    assert results[0].status is ElevationStatus.SUCCEEDED
    controller.shutdown()
    assert controller.thread is None


def test_elevation_qthread_cancel_and_repeated_shutdown_are_cooperative(qapp, tmp_path):
    for _ in range(10):
        request = _request()
        broker = BrokerEntryPoint(envelope_root=tmp_path / f"payloads-{_}", claims=OneShotClaimStore(tmp_path / f"claims-{_}"))
        client = ElevationBrokerClient(FakeElevationLauncher(broker, uac_cancelled=True), envelope_directory=tmp_path / f"payloads-{_}")
        controller = ElevationQtController(client)
        assert controller.start(request, None, lambda _result: True)
        controller.cancel()
        controller.shutdown()
        controller.shutdown()
        qapp.processEvents()
        assert controller.thread is None


def test_elevation_qthread_stress_normal_100_cancel_50_shutdown_50(qapp, tmp_path):
    normal = 0
    cancelled = 0
    shutdown = 0
    for index in range(100):
        base = tmp_path / f"normal-{index}"
        request = _request()
        broker = BrokerEntryPoint(envelope_root=base / "payloads", claims=OneShotClaimStore(base / "claims"))
        client = ElevationBrokerClient(FakeElevationLauncher(broker), envelope_directory=base / "payloads")
        controller = ElevationQtController(client)
        received = []
        controller.completed.connect(received.append)
        assert controller.start(request, None, lambda _result: True)
        assert controller.thread.wait(5000)
        qapp.processEvents()
        assert received and received[-1].status is ElevationStatus.SUCCEEDED
        normal += 1
        controller.shutdown()
    for index in range(50):
        base = tmp_path / f"cancel-{index}"
        request = _request()
        broker = BrokerEntryPoint(envelope_root=base / "payloads", claims=OneShotClaimStore(base / "claims"))
        client = ElevationBrokerClient(FakeElevationLauncher(broker, uac_cancelled=True), envelope_directory=base / "payloads")
        controller = ElevationQtController(client)
        assert controller.start(request, None, lambda _result: True)
        controller.cancel()
        controller.shutdown()
        qapp.processEvents()
        assert controller.thread is None
        cancelled += 1
    for index in range(50):
        base = tmp_path / f"shutdown-{index}"
        request = _request()
        broker = BrokerEntryPoint(envelope_root=base / "payloads", claims=OneShotClaimStore(base / "claims"))
        client = ElevationBrokerClient(FakeElevationLauncher(broker, uac_cancelled=True), envelope_directory=base / "payloads")
        controller = ElevationQtController(client)
        assert controller.start(request, None, lambda _result: True)
        controller.shutdown()
        controller.shutdown()
        qapp.processEvents()
        assert controller.thread is None
        shutdown += 1
    assert (normal, cancelled, shutdown) == (100, 50, 50)
