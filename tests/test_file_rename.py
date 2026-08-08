from __future__ import annotations

import os
import pytest

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.audit_log import InMemoryAuditSink
from windows_pet.confirmation_gate import ConfirmationGate
from windows_pet.file_rename import (
    FILE_RENAME_CONTRACT,
    FileRenameExecutor,
    FileRenameProposalFactory,
    FileRenameValidator,
    FileRenameValidationCode,
)


def prepared(tmp_path, name="example_old.txt", new_name="example_new.txt", *, validator=None, rename=None):
    source = tmp_path / name
    source.write_text("stable content", encoding="utf-8")
    validator = validator or FileRenameValidator()
    snapshot, code = validator.snapshot(str(source), new_name)
    assert code is FileRenameValidationCode.OK
    proposal = FileRenameProposalFactory().create("rename-test", snapshot)
    audit = InMemoryAuditSink()
    gate = ConfirmationGate(audit=audit)
    _, session = gate.prepare(FILE_RENAME_CONTRACT, proposal)
    assert session is not None
    approved = gate.decide(FILE_RENAME_CONTRACT, proposal, ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint))
    assert approved.grant is not None
    executor = FileRenameExecutor(gate.grants, validator=validator, rename=rename, audit=audit)
    return source, snapshot, proposal, gate, approved.grant, executor, audit


def test_valid_rename_is_exactly_once_and_identity_is_preserved(tmp_path):
    source, snapshot, proposal, gate, grant, executor, audit = prepared(tmp_path)
    calls = []
    executor.rename = lambda old, new: calls.append((old, new)) or os.rename(old, new)
    outcome = executor.execute(grant.grant_id, proposal, snapshot)
    assert outcome.success and outcome.verification_result == "old_absent_new_identity_preserved"
    assert len(calls) == 1 and not source.exists() and (tmp_path / "example_new.txt").read_text(encoding="utf-8") == "stable content"
    assert outcome.rollback_metadata[0] == ("old_path", str(source))


def test_cancel_has_zero_mutations(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("content", encoding="utf-8")
    validator = FileRenameValidator()
    snapshot, _ = validator.snapshot(str(source), "new.txt")
    proposal = FileRenameProposalFactory().create("cancel", snapshot)
    gate = ConfirmationGate()
    _, session = gate.prepare(FILE_RENAME_CONTRACT, proposal)
    result = gate.decide(FILE_RENAME_CONTRACT, proposal, ConfirmationResponse(ConfirmationDecision.CANCEL, session.session_id, proposal.proposal_id, proposal.fingerprint))
    assert result.grant is None and source.exists() and not (tmp_path / "new.txt").exists()


def test_grant_reuse_has_zero_mutations(tmp_path):
    source, snapshot, proposal, gate, grant, executor, _ = prepared(tmp_path)
    calls = []
    executor.rename = lambda old, new: calls.append(1) or os.rename(old, new)
    first = executor.execute(grant.grant_id, proposal, snapshot)
    second = executor.execute(grant.grant_id, proposal, snapshot)
    assert first.success and not second.success and second.result_code == "already_used" and len(calls) == 1


@pytest.mark.parametrize("new_name", ["", "bad/name.txt", "bad\\name.txt", "CON.txt", "name:stream", "name. ", ".."])
def test_invalid_filename_has_zero_mutations(tmp_path, new_name):
    source = tmp_path / "old.txt"
    source.write_text("content", encoding="utf-8")
    snapshot, code = FileRenameValidator().snapshot(str(source), new_name)
    assert snapshot is None and code is not FileRenameValidationCode.OK and source.exists()


@pytest.mark.parametrize("source_text,code", [("relative.txt", FileRenameValidationCode.RELATIVE_PATH), ("https://example.test/a", FileRenameValidationCode.URL_PATH), (r"\\server\share\a.txt", FileRenameValidationCode.UNC_PATH), (r"\\.\C:\a.txt", FileRenameValidationCode.DEVICE_PATH)])
def test_unsafe_source_paths_are_rejected(tmp_path, source_text, code):
    snapshot, actual = FileRenameValidator().snapshot(source_text, "new.txt")
    assert snapshot is None and actual is code


def test_source_changed_or_replaced_has_zero_rename_calls(tmp_path):
    source, snapshot, proposal, gate, grant, executor, _ = prepared(tmp_path)
    source.write_text("changed content with a new size", encoding="utf-8")
    calls = []
    executor.rename = lambda old, new: calls.append(1) or os.rename(old, new)
    outcome = executor.execute(grant.grant_id, proposal, snapshot)
    assert not outcome.success and outcome.result_code == "identity_changed" and calls == []

    source2 = tmp_path / "replace_old.txt"
    source2.write_text("original", encoding="utf-8")
    validator = FileRenameValidator()
    snapshot2, _ = validator.snapshot(str(source2), "replace_new.txt")
    proposal2 = FileRenameProposalFactory().create("replace", snapshot2)
    gate2 = ConfirmationGate()
    _, session2 = gate2.prepare(FILE_RENAME_CONTRACT, proposal2)
    grant2 = gate2.decide(FILE_RENAME_CONTRACT, proposal2, ConfirmationResponse(ConfirmationDecision.APPROVE, session2.session_id, proposal2.proposal_id, proposal2.fingerprint)).grant
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("replacement", encoding="utf-8")
    os.replace(replacement, source2)
    calls2 = []
    executor2 = FileRenameExecutor(gate2.grants, rename=lambda old, new: calls2.append(1) or os.rename(old, new))
    outcome2 = executor2.execute(grant2.grant_id, proposal2, snapshot2)
    assert not outcome2.success and outcome2.result_code == "identity_changed" and calls2 == []


def test_destination_appeared_after_confirmation_has_zero_rename_calls(tmp_path):
    source, snapshot, proposal, gate, grant, executor, _ = prepared(tmp_path)
    Path = type(source)
    Path(snapshot.destination_path).write_text("conflict", encoding="utf-8")
    calls = []
    executor.rename = lambda old, new: calls.append(1) or os.rename(old, new)
    outcome = executor.execute(grant.grant_id, proposal, snapshot)
    assert not outcome.success and outcome.result_code == "identity_changed" and calls == []


def test_reparse_point_is_fail_closed(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("content", encoding="utf-8")
    snapshot, code = FileRenameValidator(is_reparse=lambda _: True).snapshot(str(source), "new.txt")
    assert snapshot is None
    assert code is FileRenameValidationCode.REPARSE_POINT


def test_verification_failure_is_not_reported_as_success(tmp_path):
    source, snapshot, proposal, gate, grant, executor, _ = prepared(tmp_path, rename=lambda old, new: None)
    outcome = executor.execute(grant.grant_id, proposal, snapshot)
    assert not outcome.success and outcome.result_code == "verification_failed" and source.exists()


def test_case_only_rename_is_supported(tmp_path):
    source, snapshot, proposal, gate, grant, executor, _ = prepared(tmp_path, name="Report.TXT", new_name="report.txt")
    outcome = executor.execute(grant.grant_id, proposal, snapshot)
    names = {entry.name for entry in os.scandir(tmp_path)}
    assert outcome.success and "report.txt" in names and "Report.TXT" not in names
