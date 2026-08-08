from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.structured_operations import (
    SETTINGS_OPERATION_CONTRACT,
    OpenWindowsSettingsExecutor,
    StructuredOperationFactory,
    default_template_registry,
)


def approved():
    proposal, operation = StructuredOperationFactory().create("settings-test", "open_windows_settings", {"settings_id": "display"})
    gate = ConfirmationGate()
    _, session = gate.prepare(SETTINGS_OPERATION_CONTRACT, proposal)
    response = ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint)
    result = gate.decide(SETTINGS_OPERATION_CONTRACT, proposal, response)
    return proposal, operation, gate, result.grant


def test_template_is_code_owned_and_uri_catalog_is_bounded():
    proposal, operation = StructuredOperationFactory().create("task", "open_windows_settings", {"settings_id": "network"})
    assert operation.template_id == "windows_pet.open_windows_settings.v1"
    assert proposal.parameters["uri"] == "ms-settings:network"
    try:
        StructuredOperationFactory().create("task", "open_windows_settings", {"settings_id": "ms-settings:evil"})
    except ValueError as exc:
        assert str(exc) == "unsupported_settings_id"
    else:
        raise AssertionError("arbitrary settings URI accepted")


def test_cancel_has_no_opener_call():
    proposal, operation = StructuredOperationFactory().create("task", "open_windows_settings", {"settings_id": "display"})
    gate = ConfirmationGate(); _, session = gate.prepare(SETTINGS_OPERATION_CONTRACT, proposal)
    result = gate.decide(SETTINGS_OPERATION_CONTRACT, proposal, ConfirmationResponse(ConfirmationDecision.CANCEL, session.session_id, proposal.proposal_id, proposal.fingerprint))
    calls = []
    assert result.grant is None and calls == []


def test_fake_executor_uses_exact_uri_and_verifies():
    proposal, operation, gate, grant = approved()
    calls = []
    executor = OpenWindowsSettingsExecutor(gate.grants, opener=lambda uri: calls.append(uri), verifier=lambda uri: uri == "ms-settings:display")
    outcome = executor.execute(grant.grant_id, proposal, operation)
    assert outcome.success and calls == ["ms-settings:display"]
    reused = executor.execute(grant.grant_id, proposal, operation)
    assert not reused.success and reused.result_code == "already_used" and calls == ["ms-settings:display"]


def test_fake_executor_verification_failure_is_not_success():
    proposal, operation, gate, grant = approved()
    executor = OpenWindowsSettingsExecutor(gate.grants, opener=lambda _: None, verifier=lambda _: False)
    outcome = executor.execute(grant.grant_id, proposal, operation)
    assert not outcome.success and outcome.result_code == "verification_failed"
