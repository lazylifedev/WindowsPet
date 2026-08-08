from __future__ import annotations

import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.elevation import (
    BrokerEntryPoint,
    ElevationBrokerClient,
    ElevationEnvelopeFactory,
    ElevationReason,
    ElevationRequest,
    ElevationStatus,
    FakeElevatedExecutor,
    FakeElevationLauncher,
    OneShotClaimStore,
    canonical_json_bytes,
    write_envelope_file,
)
from windows_pet.service_restart import (
    RESTART_SERVICE_CONTRACT,
    RESTART_SERVICE_TEMPLATE_ID,
    ServiceIdentity,
    ServiceRestartProposalFactory,
)


def _approved_request():
    identity = ServiceIdentity("Spooler", "Print Spooler", "Running")
    proposal = ServiceRestartProposalFactory().create("task", identity)
    gate = ConfirmationGate()
    _, session = gate.prepare(RESTART_SERVICE_CONTRACT, proposal)
    response = ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id,
                                    proposal.proposal_id, proposal.fingerprint)
    grant = gate.decide(RESTART_SERVICE_CONTRACT, proposal, response).grant
    return gate, ElevationRequest.from_proposal(proposal, grant, request_id="request-1", nonce="nonce-1")


def test_envelope_is_canonical_and_contains_no_script_body(tmp_path):
    _gate, request = _approved_request()
    envelope = ElevationEnvelopeFactory.create(request)
    payload = canonical_json_bytes({key: getattr(envelope, key) for key in (
        "schema_version", "request_id", "proposal_id", "proposal_fingerprint", "grant_id",
        "operation_id", "template_id", "template_version", "script_sha256", "effect_class",
        "requires_admin", "timeout_seconds", "created_at", "expires_at", "nonce",
        "parameter_digest", "verification_plan_id", "parameters")})
    assert b"Restart-Service" not in payload
    file = write_envelope_file(envelope, tmp_path)
    assert file.path.name.startswith("envelope-") and file.path.suffix == ".json"
    assert file.path.stat().st_size == len(payload)
    file.cleanup()


def test_fake_integration_validates_result_then_main_verifies(tmp_path):
    _gate, request = _approved_request()
    executor = FakeElevatedExecutor()
    broker = BrokerEntryPoint(dispatcher=__import__("windows_pet.elevation", fromlist=["ElevatedOperationDispatcher"]).ElevatedOperationDispatcher({"restart_service": executor}), claims=OneShotClaimStore(tmp_path / "claims"), envelope_root=tmp_path / "payloads")
    outcome = ElevationBrokerClient(FakeElevationLauncher(broker), envelope_directory=tmp_path / "payloads").execute(
        request, None, verifier=lambda result: "running")
    assert outcome.status is ElevationStatus.SUCCEEDED
    assert outcome.verification == "running"
    assert executor.execution_count == 1


def test_validation_rejection_does_not_execute(tmp_path):
    _gate, request = _approved_request()
    envelope = ElevationEnvelopeFactory.create(request)
    bad = type(envelope)(**{**envelope.__dict__, "script_sha256": "0" * 64})
    payload = write_envelope_file(bad, tmp_path / "payloads")
    executor = FakeElevatedExecutor()
    from windows_pet.elevation import ElevatedOperationDispatcher
    result = BrokerEntryPoint(dispatcher=ElevatedOperationDispatcher({"restart_service": executor}), claims=OneShotClaimStore(tmp_path / "claims"), envelope_root=tmp_path / "payloads").run(payload.path)
    assert result.status is ElevationStatus.REJECTED
    assert result.result_code == ElevationReason.SCRIPT_HASH_MISMATCH.value
    assert executor.execution_count == 0


def test_same_envelope_is_rejected_by_a_second_broker_process(tmp_path):
    _gate, request = _approved_request()
    envelope = ElevationEnvelopeFactory.create(request)
    source = canonical_json_bytes({key: getattr(envelope, key) for key in (
        "schema_version", "request_id", "proposal_id", "proposal_fingerprint", "grant_id",
        "operation_id", "template_id", "template_version", "script_sha256", "effect_class",
        "requires_admin", "timeout_seconds", "created_at", "expires_at", "nonce",
        "parameter_digest", "verification_plan_id", "parameters")})
    payloads = tmp_path / "payloads"; payloads.mkdir()
    first, second = payloads / "one.json", payloads / "two.json"
    first.write_bytes(source); second.write_bytes(source)
    claims = OneShotClaimStore(tmp_path / "claims")
    from windows_pet.elevation import ElevatedOperationDispatcher
    first_executor, second_executor = FakeElevatedExecutor(), FakeElevatedExecutor()
    first_result = BrokerEntryPoint(dispatcher=ElevatedOperationDispatcher({"restart_service": first_executor}), claims=claims, envelope_root=payloads).run(first)
    second_result = BrokerEntryPoint(dispatcher=ElevatedOperationDispatcher({"restart_service": second_executor}), claims=OneShotClaimStore(tmp_path / "claims"), envelope_root=payloads).run(second)
    assert first_result.status is ElevationStatus.SUCCEEDED
    assert second_result.status is ElevationStatus.REJECTED
    assert second_result.result_code in {"grant_reused", "replayed_nonce"}
    assert first_executor.execution_count == 1 and second_executor.execution_count == 0


def _claim_worker(directory: str, queue):
    queue.put(OneShotClaimStore(Path(directory)).claim("same-grant", "same-nonce"))


def _broker_worker(payload_path: str, claims_path: str, envelope_root: str, queue):
    from windows_pet.elevation import BrokerEntryPoint, ElevatedOperationDispatcher, FakeElevatedExecutor, OneShotClaimStore
    executor = FakeElevatedExecutor()
    result = BrokerEntryPoint(
        dispatcher=ElevatedOperationDispatcher({"restart_service": executor}),
        claims=OneShotClaimStore(Path(claims_path)), envelope_root=Path(envelope_root),
    ).run(Path(payload_path))
    queue.put((result.status.value, result.result_code, executor.execution_count))


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL/process semantics are acceptance target")
def test_two_broker_processes_have_one_atomic_claim(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_claim_worker, args=(str(tmp_path / "claims"), queue)) for _ in range(2)]
    for process in processes: process.start()
    for process in processes: process.join(10)
    results = [queue.get(timeout=2) for _ in processes]
    assert sorted(results) == ["claimed", "grant_reused"]


@pytest.mark.skipif(os.name != "nt", reason="Windows process semantics are acceptance target")
def test_two_broker_processes_execute_at_most_once(tmp_path):
    _gate, request = _approved_request()
    envelope = ElevationEnvelopeFactory.create(request)
    from windows_pet.elevation.envelope import serialize_envelope
    payloads = tmp_path / "payloads"; payloads.mkdir()
    source = serialize_envelope(envelope)
    first, second = payloads / "one.json", payloads / "two.json"
    first.write_bytes(source); second.write_bytes(source)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_broker_worker, args=(str(path), str(tmp_path / "claims"), str(payloads), queue)) for path in (first, second)]
    for process in processes: process.start()
    for process in processes: process.join(10)
    results = [queue.get(timeout=2) for _ in processes]
    assert sorted(results) == [("rejected", "grant_reused", 0), ("succeeded", "ok", 1)]
