from __future__ import annotations

import ntpath
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from PySide6.QtCore import QObject, Signal

from .action_models import (ActionProposal, ActionProposalFactory, ActionTarget,
                             ConfirmationType, SideEffect, SimpleActionPreview,
                             ToolContract)
from .audit_log import AuditEvent, NullAuditSink
from .cancellation import CancellationToken
from .execution_grant import ExecutionGrantStore


class LaunchValidationCode(str, Enum):
    OK = "ok"; MISSING_PATH = "missing_path"; RELATIVE_PATH = "relative_path"
    NETWORK_PATH = "network_path"; DEVICE_PATH = "device_path"; URL_PATH = "url_path"
    UNEXPANDED_ENVIRONMENT = "unexpanded_environment"; UNSUPPORTED_EXTENSION = "unsupported_extension"
    CANDIDATE_NOT_CONFIRMED = "candidate_not_confirmed"; NOT_FOUND = "not_found"
    NOT_FILE = "not_file"; REPARSE_POINT = "reparse_point"; RESOLVE_FAILED = "resolve_failed"
    STAT_FAILED = "stat_failed"; IDENTITY_CHANGED = "identity_changed"


@dataclass(frozen=True)
class ApplicationLaunchTarget:
    display_name: str
    canonical_path: str
    file_size: int
    modified_time_ns: int


class ApplicationLaunchValidator:
    def __init__(self, resolve_path: Callable | None = None, exists: Callable | None = None,
                 is_file: Callable | None = None, stat_path: Callable | None = None,
                 is_reparse_point: Callable | None = None):
        self.resolve_path = resolve_path or (lambda p: Path(p).resolve(strict=True))
        self.exists = exists or (lambda p: Path(p).exists())
        self.is_file = is_file or (lambda p: Path(p).is_file())
        self.stat_path = stat_path or (lambda p: Path(p).stat())
        self.is_reparse_point = is_reparse_point or (lambda p: Path(p).is_symlink())

    @staticmethod
    def _safe(raw: str) -> LaunchValidationCode | None:
        if not raw: return LaunchValidationCode.MISSING_PATH
        if "://" in raw: return LaunchValidationCode.URL_PATH
        if raw.startswith("\\\\.\\") or raw.startswith("\\\\?\\"): return LaunchValidationCode.DEVICE_PATH
        if raw.startswith("\\\\"): return LaunchValidationCode.NETWORK_PATH
        if "%" in raw: return LaunchValidationCode.UNEXPANDED_ENVIRONMENT
        if not ntpath.isabs(raw): return LaunchValidationCode.RELATIVE_PATH
        if ntpath.splitext(raw)[1].casefold() != ".exe": return LaunchValidationCode.UNSUPPORTED_EXTENSION
        return None

    def validate(self, candidate) -> tuple[ApplicationLaunchTarget | None, LaunchValidationCode]:
        raw = str(getattr(candidate, "executable_path", ""))
        invalid = self._safe(raw)
        if invalid: return None, invalid
        if getattr(candidate, "executable_exists", None) is not True:
            return None, LaunchValidationCode.CANDIDATE_NOT_CONFIRMED
        try:
            path = self.resolve_path(raw)
        except FileNotFoundError: return None, LaunchValidationCode.NOT_FOUND
        except OSError: return None, LaunchValidationCode.RESOLVE_FAILED
        canonical = str(path)
        invalid = self._safe(canonical)
        if invalid: return None, invalid
        try:
            if not self.exists(path): return None, LaunchValidationCode.NOT_FOUND
            if not self.is_file(path): return None, LaunchValidationCode.NOT_FILE
            if self.is_reparse_point(path): return None, LaunchValidationCode.REPARSE_POINT
            stat = self.stat_path(path)
        except FileNotFoundError: return None, LaunchValidationCode.NOT_FOUND
        except OSError: return None, LaunchValidationCode.STAT_FAILED
        return ApplicationLaunchTarget(str(getattr(candidate, "display_name", "")), canonical,
                                       int(stat.st_size), int(stat.st_mtime_ns)), LaunchValidationCode.OK

    def validate_target(self, target: ApplicationLaunchTarget) -> bool:
        if not isinstance(target, ApplicationLaunchTarget) or not target.display_name.strip(): return False
        if target.file_size < 0 or target.modified_time_ns < 0: return False
        return self._safe(target.canonical_path) is None

    def matches(self, target: ApplicationLaunchTarget) -> bool:
        if not self.validate_target(target): return False
        class Candidate:
            executable_path = target.canonical_path
            display_name = target.display_name
            executable_exists = True
        current, code = self.validate(Candidate())
        return code is LaunchValidationCode.OK and current == target


APPLICATION_LAUNCH_CONTRACT = ToolContract("application_launcher", "1", "launch_application", SideEffect.APPLICATION_LAUNCH, ConfirmationType.SIMPLE, False, False, True, 10.0, "process creation and short post-launch verification", ("status", "result_code", "verification_result"))


class ApplicationLaunchProposalFactory:
    def create(self, task_id: str, candidate, target: ApplicationLaunchTarget) -> ActionProposal:
        return ActionProposalFactory.create(APPLICATION_LAUNCH_CONTRACT, task_id,
            ActionTarget("local_application", target.canonical_path, target.display_name),
            {"file_size": target.file_size, "modified_time_ns": target.modified_time_ns, "arguments": []},
            SimpleActionPreview("アプリ起動", "選択したアプリを起動します", "起動"))


class ApplicationLaunchStatus(str, Enum):
    STARTED = "started"; HANDED_OFF = "handed_off"; REJECTED = "rejected"; FAILED = "failed"; CANCELLED = "cancelled"


@dataclass(frozen=True)
class ApplicationLaunchOutcome:
    status: ApplicationLaunchStatus
    result_code: str


class ApplicationLaunchExecutor:
    def __init__(self, grants: ExecutionGrantStore, validator=None, process_factory=None,
                 wait_seconds: float = .35, sleeper: Callable = time.sleep, audit=None):
        self.grants = grants; self.validator = validator or ApplicationLaunchValidator()
        self.process_factory = process_factory or subprocess.Popen; self.wait_seconds = wait_seconds
        self.sleeper = sleeper; self.audit = audit or NullAuditSink()

    def _valid_proposal(self, proposal, target) -> bool:
        if not isinstance(proposal, ActionProposal) or not isinstance(target, ApplicationLaunchTarget): return False
        if proposal.tool_name != APPLICATION_LAUNCH_CONTRACT.tool_name or proposal.operation != APPLICATION_LAUNCH_CONTRACT.operation: return False
        if proposal.target.identifier != target.canonical_path or proposal.target.display_name != target.display_name: return False
        params = proposal.parameters
        return params.get("file_size") == target.file_size and params.get("modified_time_ns") == target.modified_time_ns and params.get("arguments", ()) in ((), [])

    def execute(self, grant_id: str, proposal: ActionProposal, target: ApplicationLaunchTarget, token: CancellationToken | None = None) -> ApplicationLaunchOutcome:
        if not self._valid_proposal(proposal, target) or not self.validator.validate_target(target): return ApplicationLaunchOutcome(ApplicationLaunchStatus.REJECTED, "invalid_request")
        if token and token.is_cancelled: return ApplicationLaunchOutcome(ApplicationLaunchStatus.CANCELLED, "cancelled_before_consume")
        result = self.grants.consume_for(grant_id, APPLICATION_LAUNCH_CONTRACT, proposal)
        if not result.success: return ApplicationLaunchOutcome(ApplicationLaunchStatus.REJECTED, result.reason.value)
        if token and token.is_cancelled: return ApplicationLaunchOutcome(ApplicationLaunchStatus.CANCELLED, "cancelled_after_consume")
        if not self.validator.matches(target): return ApplicationLaunchOutcome(ApplicationLaunchStatus.REJECTED, LaunchValidationCode.IDENTITY_CHANGED.value)
        self.audit.write(AuditEvent("execution_started", task_id=proposal.task_id, proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint, grant_id=grant_id, tool_name=proposal.tool_name, tool_version=proposal.tool_version, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value))
        try:
            process = self.process_factory([target.canonical_path], shell=False, cwd=ntpath.dirname(target.canonical_path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.sleeper(self.wait_seconds)
            code = process.poll()
            if code is None: status, reason = ApplicationLaunchStatus.STARTED, "process_running"
            elif code == 0: status, reason = ApplicationLaunchStatus.HANDED_OFF, "process_handed_off"
            else: status, reason = ApplicationLaunchStatus.FAILED, "process_exited_nonzero"
        except OSError: status, reason = ApplicationLaunchStatus.FAILED, "process_create_failed"
        event = "execution_succeeded" if status in (ApplicationLaunchStatus.STARTED, ApplicationLaunchStatus.HANDED_OFF) else "execution_failed"
        self.audit.write(AuditEvent(event, result_code=reason, task_id=proposal.task_id, proposal_id=proposal.proposal_id, proposal_fingerprint=proposal.fingerprint, grant_id=grant_id, tool_name=proposal.tool_name, tool_version=proposal.tool_version, operation=proposal.operation, side_effect=proposal.side_effect.value, confirmation_type=proposal.confirmation_type.value))
        return ApplicationLaunchOutcome(status, reason)


class ApplicationLaunchWorker(QObject):
    finished = Signal(object)
    def __init__(self, executor, grant_id, proposal, target, token):
        super().__init__(); self.executor = executor; self.grant_id = grant_id; self.proposal = proposal; self.target = target; self.token = token
    def run(self):
        try: outcome = self.executor.execute(self.grant_id, self.proposal, self.target, self.token)
        except Exception: outcome = ApplicationLaunchOutcome(ApplicationLaunchStatus.FAILED, "worker_failed")
        self.finished.emit(outcome)
