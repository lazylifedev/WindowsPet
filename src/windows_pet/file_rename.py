from __future__ import annotations

import ntpath
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping

from .action_models import (
    ActionProposal,
    ActionProposalFactory,
    ActionTarget,
    BeforeAfterActionPreview,
    ConfirmationType,
    SideEffect,
    ToolContract,
)
from .audit_log import AuditEvent, NullAuditSink
from .execution_grant import ExecutionGrantStore


_INVALID_NAME = re.compile(r'[<>:"/\\|?*]')
_RESERVED_DOS = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


class FileRenameValidationCode(StrEnum):
    OK = "ok"
    MISSING_PATH = "missing_path"
    RELATIVE_PATH = "relative_path"
    URL_PATH = "url_path"
    DEVICE_PATH = "device_path"
    UNC_PATH = "unc_path"
    UNSUPPORTED_NAMESPACE = "unsupported_namespace"
    INVALID_FILENAME = "invalid_filename"
    RESERVED_NAME = "reserved_name"
    ADS = "alternate_data_stream"
    TRAVERSAL = "path_traversal"
    NOT_FOUND = "not_found"
    NOT_FILE = "not_file"
    REPARSE_POINT = "reparse_point"
    DESTINATION_EXISTS = "destination_exists"
    SAME_PATH = "same_path"
    IDENTITY_CHANGED = "identity_changed"
    STAT_FAILED = "stat_failed"


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class FileSnapshot:
    canonical_path: str
    file_type: str
    size: int
    modified_time_ns: int
    identity: FileIdentity
    source_parent: str
    target_proposed_name: str
    destination_path: str


@dataclass(frozen=True)
class FileRenameOutcome:
    success: bool
    result_code: str
    verification_result: str = ""
    rollback_metadata: tuple[tuple[str, str], ...] = ()


FILE_RENAME_CONTRACT = ToolContract(
    "file_renamer", "1", "rename_file", SideEffect.FILE_MOVE,
    ConfirmationType.BEFORE_AFTER, True, False, True, 10.0,
    "old path absent, new path exists, original file identity preserved",
    ("source_identity", "destination", "verification_result"),
)


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = os.lstat(path)
        return bool(getattr(stat, "st_file_attributes", 0) & 0x0400) or path.is_symlink()
    except OSError:
        return False


def _exact_entry_exists(path: Path) -> bool:
    try:
        return any(entry.name == path.name for entry in os.scandir(path.parent))
    except OSError:
        return False


def _reject_path_text(raw: str, *, allow_leaf: bool = False) -> FileRenameValidationCode | None:
    if not raw:
        return FileRenameValidationCode.MISSING_PATH
    if "\x00" in raw or "://" in raw:
        return FileRenameValidationCode.URL_PATH if "://" in raw else FileRenameValidationCode.INVALID_FILENAME
    if raw.startswith("\\\\?\\") or raw.startswith("\\\\.\\"):
        return FileRenameValidationCode.DEVICE_PATH
    if raw.startswith("\\\\"):
        return FileRenameValidationCode.UNC_PATH
    if not allow_leaf and not ntpath.isabs(raw):
        return FileRenameValidationCode.RELATIVE_PATH
    if allow_leaf:
        if raw in {".", ".."} or any(separator in raw for separator in ("/", "\\")):
            return FileRenameValidationCode.TRAVERSAL
        if ":" in raw:
            return FileRenameValidationCode.ADS
        if _INVALID_NAME.search(raw) or any(ord(char) < 32 for char in raw):
            return FileRenameValidationCode.INVALID_FILENAME
        if raw.endswith((" ", ".")):
            return FileRenameValidationCode.INVALID_FILENAME
        if _RESERVED_DOS.fullmatch(raw):
            return FileRenameValidationCode.RESERVED_NAME
    elif ":" in raw[2:]:
        return FileRenameValidationCode.ADS
    if any(part in {".", ".."} for part in raw.replace("/", "\\").split("\\")):
        return FileRenameValidationCode.TRAVERSAL
    return None


class FileRenameValidator:
    """Fail-closed validation and identity binding for local regular files."""

    def __init__(self, resolve: Callable | None = None, stat: Callable | None = None,
                 exists: Callable | None = None, is_file: Callable | None = None,
                 is_reparse: Callable | None = None):
        self.resolve = resolve or (lambda value: Path(value).resolve(strict=True))
        self.stat = stat or (lambda value: Path(value).stat())
        self.exists = exists or (lambda value: Path(value).exists())
        self.is_file = is_file or (lambda value: Path(value).is_file())
        self.is_reparse = is_reparse or _is_reparse_point

    def snapshot(self, source_path: str, new_name: str) -> tuple[FileSnapshot | None, FileRenameValidationCode]:
        invalid = _reject_path_text(str(source_path))
        if invalid:
            return None, invalid
        invalid = _reject_path_text(str(new_name), allow_leaf=True)
        if invalid:
            return None, invalid
        try:
            # Check the user-supplied spelling before canonicalization; resolving a
            # symlink first would erase the evidence that the source was reparse-backed.
            if self.is_reparse(Path(source_path)):
                return None, FileRenameValidationCode.REPARSE_POINT
            source = self.resolve(source_path)
        except FileNotFoundError:
            return None, FileRenameValidationCode.NOT_FOUND
        except OSError:
            return None, FileRenameValidationCode.STAT_FAILED
        invalid = _reject_path_text(str(source))
        if invalid:
            return None, invalid
        try:
            if not self.exists(source):
                return None, FileRenameValidationCode.NOT_FOUND
            if not self.is_file(source):
                return None, FileRenameValidationCode.NOT_FILE
            if self.is_reparse(source):
                return None, FileRenameValidationCode.REPARSE_POINT
            stat = self.stat(source)
            destination = source.parent / new_name
            if str(destination) == str(source):
                return None, FileRenameValidationCode.SAME_PATH
            same_case_insensitive = str(destination).casefold() == str(source).casefold() and str(destination) != str(source)
            if self.exists(destination) and not same_case_insensitive:
                return None, FileRenameValidationCode.DESTINATION_EXISTS
        except FileNotFoundError:
            return None, FileRenameValidationCode.NOT_FOUND
        except OSError:
            return None, FileRenameValidationCode.STAT_FAILED
        return FileSnapshot(
            str(source), "file", int(stat.st_size), int(stat.st_mtime_ns),
            FileIdentity(int(getattr(stat, "st_dev", 0)), int(getattr(stat, "st_ino", 0))),
            str(source.parent), new_name, str(destination),
        ), FileRenameValidationCode.OK

    def matches(self, snapshot: FileSnapshot) -> bool:
        if not isinstance(snapshot, FileSnapshot):
            return False
        try:
            source = Path(snapshot.canonical_path)
            if not self.exists(source) or not self.is_file(source) or self.is_reparse(source):
                return False
            current = self.stat(source)
            identity = FileIdentity(int(getattr(current, "st_dev", 0)), int(getattr(current, "st_ino", 0)))
            if (current.st_size, current.st_mtime_ns, identity) != (snapshot.size, snapshot.modified_time_ns, snapshot.identity):
                return False
            destination = Path(snapshot.destination_path)
            same_case_insensitive = str(destination).casefold() == str(source).casefold() and str(destination) != str(source)
            return not self.exists(destination) or same_case_insensitive
        except OSError:
            return False


class FileRenameProposalFactory:
    def __init__(self, proposal_factory=None):
        self.proposal_factory = proposal_factory or ActionProposalFactory()

    def create(self, task_id: str, snapshot: FileSnapshot) -> ActionProposal:
        if not isinstance(snapshot, FileSnapshot):
            raise ValueError("invalid_snapshot")
        preview = BeforeAfterActionPreview(
            operation="ファイル名変更", impact="同じ親フォルダー内でファイル名だけを変更します。",
            button_label="ファイル名を変更", before=Path(snapshot.canonical_path).name,
            after=snapshot.target_proposed_name, change_summary="上書きしない", backup_available=False,
        )
        return self.proposal_factory.create(
            FILE_RENAME_CONTRACT, task_id,
            ActionTarget("local_file", snapshot.canonical_path, Path(snapshot.canonical_path).name),
            {
                "source": snapshot.canonical_path, "destination": snapshot.destination_path,
                "source_parent": snapshot.source_parent, "target_name": snapshot.target_proposed_name,
                "file_type": snapshot.file_type, "size": snapshot.size,
                "modified_time_ns": snapshot.modified_time_ns,
                "identity_device": snapshot.identity.device, "identity_inode": snapshot.identity.inode,
                "overwrite": False,
            }, preview,
        )


class FileRenameExecutor:
    def __init__(self, grants: ExecutionGrantStore, validator: FileRenameValidator | None = None,
                 rename: Callable | None = None, audit=None):
        self.grants = grants
        self.validator = validator or FileRenameValidator()
        self.rename = rename or os.rename
        self.audit = audit or NullAuditSink()

    @staticmethod
    def _valid_request(proposal: ActionProposal, snapshot: FileSnapshot) -> bool:
        params = proposal.parameters
        return (
            isinstance(proposal, ActionProposal) and isinstance(snapshot, FileSnapshot)
            and proposal.tool_name == FILE_RENAME_CONTRACT.name
            and proposal.tool_version == FILE_RENAME_CONTRACT.version
            and proposal.operation == FILE_RENAME_CONTRACT.operation
            and proposal.side_effect is FILE_RENAME_CONTRACT.side_effect
            and proposal.confirmation_type is FILE_RENAME_CONTRACT.confirmation
            and proposal.target == ActionTarget("local_file", snapshot.canonical_path, Path(snapshot.canonical_path).name)
            and isinstance(params, Mapping) and params.get("source") == snapshot.canonical_path
            and params.get("destination") == snapshot.destination_path
            and params.get("target_name") == snapshot.target_proposed_name
            and params.get("size") == snapshot.size
            and params.get("modified_time_ns") == snapshot.modified_time_ns
            and params.get("identity_device") == snapshot.identity.device
            and params.get("identity_inode") == snapshot.identity.inode
            and params.get("overwrite") is False
        )

    def _audit(self, event: str, proposal: ActionProposal, grant_id: str = "", result_code: str = "ok", verification: str = "") -> None:
        self.audit.write(AuditEvent(event, result_code=result_code, task_id=proposal.task_id,
                                    proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint,
                                    grant_id=grant_id, tool_name=proposal.tool_name, tool_version=proposal.tool_version,
                                    operation=proposal.operation, side_effect=proposal.side_effect.value,
                                    confirmation_type=proposal.confirmation_type.value,
                                    requires_admin=proposal.requires_admin, reversible=proposal.reversible,
                                    verification_result=verification))

    def execute(self, grant_id: str, proposal: ActionProposal, snapshot: FileSnapshot) -> FileRenameOutcome:
        rollback = (("old_path", snapshot.canonical_path), ("new_path", snapshot.destination_path),
                    ("identity", f"{snapshot.identity.device}:{snapshot.identity.inode}"))
        if not self._valid_request(proposal, snapshot):
            return FileRenameOutcome(False, "invalid_request", rollback_metadata=rollback)
        consumed = self.grants.consume_for(grant_id, FILE_RENAME_CONTRACT, proposal)
        if not consumed.success:
            return FileRenameOutcome(False, consumed.reason.value, rollback_metadata=rollback)
        if not self.validator.matches(snapshot):
            self._audit("file_rename_failed", proposal, grant_id, FileRenameValidationCode.IDENTITY_CHANGED.value)
            return FileRenameOutcome(False, FileRenameValidationCode.IDENTITY_CHANGED.value, rollback_metadata=rollback)
        self._audit("file_rename_started", proposal, grant_id)
        try:
            self.rename(snapshot.canonical_path, snapshot.destination_path)
        except (OSError, ValueError):
            self._audit("file_rename_failed", proposal, grant_id, "rename_failed")
            return FileRenameOutcome(False, "rename_failed", rollback_metadata=rollback)
        try:
            old_exists = _exact_entry_exists(Path(snapshot.canonical_path))
            new_path = Path(snapshot.destination_path)
            new_exists = _exact_entry_exists(new_path) and new_path.is_file() and not self.validator.is_reparse(new_path)
            stat = self.validator.stat(new_path) if new_exists else None
            identity_preserved = bool(stat) and FileIdentity(int(getattr(stat, "st_dev", 0)), int(getattr(stat, "st_ino", 0))) == snapshot.identity
            if not old_exists and new_exists and identity_preserved and int(stat.st_size) == snapshot.size:
                self._audit("file_rename_verified", proposal, grant_id, verification="identity_preserved")
                return FileRenameOutcome(True, "verified", "old_absent_new_identity_preserved", rollback)
        except OSError:
            pass
        self._audit("file_rename_failed", proposal, grant_id, "verification_failed", "verification_failed")
        return FileRenameOutcome(False, "verification_failed", "old_or_new_identity_invalid", rollback)
