from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable, Mapping

from .action_models import (ActionProposal, ActionProposalFactory, ActionTarget,
                            ConfirmationType, ScriptReviewActionPreview,
                            SideEffect, ToolContract)
from .audit_log import AuditEvent, NullAuditSink
from .powershell_read_models import (PowerShellReadStatus, WindowsInspectionArea,
                                     WindowsInspectionRequest)
from .powershell_read_runner import PowerShellReadRunner

RESTART_SERVICE_SCRIPT = '''$ErrorActionPreference = "Stop"
$params = $env:WINDOWSPET_PS_PARAMETERS | ConvertFrom-Json
Restart-Service -Name ([string]$params.service_name) -ErrorAction Stop
'''
RESTART_SERVICE_TEMPLATE_ID = "windows_pet.restart_service.v1"
RESTART_SERVICE_STANDARD_ENVIRONMENT_KEYS = (
    "SystemRoot", "WINDIR", "SystemDrive", "ComSpec",
    "TEMP", "TMP", "PSModulePath", "PATHEXT",
)
RESTART_SERVICE_ENVIRONMENT_KEYS = (
    "WINDOWSPET_PS_PARAMETERS", *RESTART_SERVICE_STANDARD_ENVIRONMENT_KEYS
)
SERVICE_VERIFICATION_TIMEOUT_SECONDS = 5.0
SERVICE_VERIFICATION_POLL_INTERVAL_SECONDS = 0.25
SERVICE_TRANSITIONAL_STATES = frozenset({
    "startpending", "stoppending", "pausepending", "continuepending",
})
RESTART_SERVICE_CONTRACT = ToolContract(
    "windows_service", "1", "restart_service", SideEffect.SYSTEM_CHANGE,
    ConfirmationType.SCRIPT_REVIEW, True, True, True, 30.0,
    "read-only service inspection; canonical service must be Running",
    ("service_name", "display_name", "script_sha256", "verification", "duration"),
)


@dataclass(frozen=True)
class ServiceIdentity:
    service_name: str
    display_name: str
    observed_status: str


PROTECTED_SERVICE_NAMES = frozenset({
    "RpcSs", "DcomLaunch", "EventLog", "PlugPlay", "Power", "SamSs",
    "Winmgmt", "Schedule", "services",
})


def _norm(value):
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def canonical_script(script):
    return (script.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")


def script_sha256(script):
    return hashlib.sha256(canonical_script(script)).hexdigest()


class ServiceResolutionCode(str, Enum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    PROTECTED = "protected"
    ADMIN_REQUIRED = "admin_required"
    CHANGED = "changed"


class ServiceRestartStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class ServiceRestartOutcome:
    status: ServiceRestartStatus
    result_code: str
    exit_code: int | None = None
    verification_result: str = ""


class ServiceIdentityResolver:
    def __init__(self, inspection: Callable[[], list[dict]] | None = None,
                 protected=PROTECTED_SERVICE_NAMES, is_admin=None):
        self.inspection = inspection or self._inspect_services
        self._inspection_is_default = inspection is None
        self.protected = frozenset(_norm(x) for x in protected)
        self.is_admin = is_admin or self._is_admin
        self.last_code = ServiceResolutionCode.NOT_FOUND

    @staticmethod
    def _is_admin():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else False
        except (AttributeError, OSError):
            return False

    @staticmethod
    def _inspect_services(query=None):
        outcome = PowerShellReadRunner().execute(
            WindowsInspectionRequest(WindowsInspectionArea.SERVICES, query, 100)
        )
        return outcome.result.get("items", []) if outcome.status is PowerShellReadStatus.SUCCESS and outcome.result else []

    def resolve(self, query, snapshot: list[dict] | None = None):
        if snapshot is not None:
            rows = snapshot
        elif self._inspection_is_default:
            # A bounded unfiltered service list can omit a valid target (for
            # example Spooler on machines with more than 100 services). Live
            # validation must use the canonical name as the read-only query.
            rows = self.inspection(query) if self.inspection else []
        else:
            rows = self.inspection() if self.inspection else []
        q = _norm(query)
        matches = []
        for row in rows:
            name = row.get("name", row.get("service_name", ""))
            display = row.get("displayName", row.get("display_name", ""))
            if _norm(name) == q:
                matches.append((row, 2))
            elif _norm(display) == q:
                matches.append((row, 1))
            elif q and any(_norm(part) == q for part in (str(name).split(), str(display).split())):
                matches.append((row, 0))
        if not matches:
            self.last_code = ServiceResolutionCode.NOT_FOUND
            return None
        best = max(x[1] for x in matches)
        matches = [x for x in matches if x[1] == best]
        if len(matches) != 1:
            self.last_code = ServiceResolutionCode.AMBIGUOUS
            return None
        row = matches[0][0]
        identity = ServiceIdentity(
            str(row.get("name", row.get("service_name", ""))),
            str(row.get("displayName", row.get("display_name", ""))),
            str(row.get("state", row.get("status", ""))),
        )
        self.last_code = (
            ServiceResolutionCode.PROTECTED
            if _norm(identity.service_name) in self.protected
            else (ServiceResolutionCode.MATCHED if self.is_admin() else ServiceResolutionCode.ADMIN_REQUIRED)
        )
        return identity

    def validate(self, identity, snapshot=None):
        current = self.resolve(identity.service_name, snapshot)
        if current is None:
            return self.last_code
        if self.last_code is not ServiceResolutionCode.MATCHED:
            return self.last_code
        return ServiceResolutionCode.MATCHED if current == identity else ServiceResolutionCode.CHANGED

    def read_only_identity(self, service_name):
        """Read the canonical service identity without requiring admin rights."""
        rows = (self.inspection(service_name) if self._inspection_is_default
                else self.inspection()) if self.inspection else []
        matches = [row for row in rows
                   if _norm(row.get("name", row.get("service_name", ""))) == _norm(service_name)]
        if len(matches) != 1:
            return None
        row = matches[0]
        return ServiceIdentity(
            str(row.get("name", row.get("service_name", ""))),
            str(row.get("displayName", row.get("display_name", ""))),
            str(row.get("state", row.get("status", ""))),
        )


class ServiceRestartVerifier:
    """Bounded read-only verification usable before/after elevation."""

    def __init__(self, resolver, *, clock=time.monotonic, sleeper=time.sleep):
        self.resolver = resolver
        self.clock = clock
        self.sleeper = sleeper

    def verify_running(self, identity, cancel=None):
        cancel = cancel or (lambda: False)
        deadline = self.clock() + SERVICE_VERIFICATION_TIMEOUT_SECONDS
        while True:
            if cancel():
                return "cancelled", "cancelled"
            try:
                reader = getattr(self.resolver, "read_only_identity", None)
                current = (reader(identity.service_name) if reader else
                           self.resolver.resolve(identity.service_name))
            except Exception:
                return "verification_failed", "verification_provider_error"
            if current is None:
                return "verification_failed", "service_not_found_after_execution"
            if (current.service_name != identity.service_name
                    or current.display_name != identity.display_name):
                return "verification_failed", "identity_changed"
            state = _norm(current.observed_status).replace(" ", "")
            if state == "running":
                return "succeeded", "ok"
            if state not in SERVICE_TRANSITIONAL_STATES:
                return "verification_failed", "service_not_running"
            remaining = deadline - self.clock()
            if remaining <= 0:
                return "verification_failed", "verification_timeout"
            self.sleeper(min(SERVICE_VERIFICATION_POLL_INTERVAL_SECONDS, remaining))


class ServiceRestartProposalFactory:
    def __init__(self, factory=None):
        self.factory = factory or ActionProposalFactory()

    def create(self, task_id, identity):
        digest = script_sha256(RESTART_SERVICE_SCRIPT)
        preview = ScriptReviewActionPreview(
            "サービス再起動",
            "選択した Windows サービスを停止して再起動します。",
            "サービスを再起動",
            purpose="Windows サービスを再起動する",
            target=f"{identity.display_name} ({identity.service_name})",
            script_text=RESTART_SERVICE_SCRIPT,
            script_sha256_short=digest[:16],
            backend="Windows PowerShell 5.1",
            working_directory_display="PowerShell システムディレクトリ",
            environment_summary="サービス名を含む JSON と許可リストの Windows 標準環境変数のみ",
            expected_changes="サービスを停止して再起動します。",
            requires_admin_display="必要",
            timeout_display="30 秒",
            verification_plan="正規サービス名で再取得し、Running になるまで確認します。",
            rollback_plan="なし",
        )
        params = {
            "service_name": identity.service_name,
            "display_name": identity.display_name,
            "observed_status": identity.observed_status,
            "script_sha256": digest,
            "template_version": RESTART_SERVICE_TEMPLATE_ID,
            "backend": "windows_powershell",
            "environment_keys": list(RESTART_SERVICE_ENVIRONMENT_KEYS),
        }
        return self.factory.create(
            RESTART_SERVICE_CONTRACT, task_id,
            ActionTarget("windows_service", identity.service_name, identity.display_name),
            params, preview,
        )


class ServiceRestartRunner:
    def __init__(self, grants, resolver, process_factory=subprocess.Popen,
                 powershell_exe=None, working_directory=None, audit=None,
                 clock=time.monotonic, sleeper=time.sleep):
        self.grants = grants
        self.resolver = resolver
        self.process_factory = process_factory
        self.powershell_exe = powershell_exe
        self.working_directory = working_directory
        self.audit = audit or NullAuditSink()
        self.clock = clock
        self.sleeper = sleeper
        self._cancel = Event()
        self._active_process = None

    def cancel(self):
        self._cancel.set()

    def reset_cancel(self):
        self._cancel.clear()

    @staticmethod
    def _valid_request(proposal, identity):
        if not isinstance(proposal, ActionProposal) or not isinstance(identity, ServiceIdentity):
            return False
        params = proposal.parameters
        return (
            proposal.tool_name == RESTART_SERVICE_CONTRACT.name
            and proposal.tool_version == RESTART_SERVICE_CONTRACT.version
            and proposal.operation == "restart_service"
            and proposal.side_effect is SideEffect.SYSTEM_CHANGE
            and proposal.confirmation_type is ConfirmationType.SCRIPT_REVIEW
            and proposal.reversible
            and proposal.requires_admin
            and proposal.cancellation_support
            and proposal.timeout_seconds == RESTART_SERVICE_CONTRACT.timeout_seconds
            and proposal.verification_method == RESTART_SERVICE_CONTRACT.verification_method
            and proposal.target == ActionTarget("windows_service", identity.service_name, identity.display_name)
            and isinstance(params, Mapping)
            and set(params) == {"service_name", "display_name", "observed_status", "script_sha256", "template_version", "backend", "environment_keys"}
            and params["service_name"] == identity.service_name
            and params["display_name"] == identity.display_name
            and params["observed_status"] == identity.observed_status
            and params["script_sha256"] == script_sha256(RESTART_SERVICE_SCRIPT)
            and params["template_version"] == RESTART_SERVICE_TEMPLATE_ID
            and params["backend"] == "windows_powershell"
            and tuple(params["environment_keys"]) == RESTART_SERVICE_ENVIRONMENT_KEYS
        )

    @staticmethod
    def _cleanup(process):
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _backend(powershell_exe, working_directory):
        exe = Path(powershell_exe) if powershell_exe else Path(
            os.environ.get("SystemRoot", r"C:\Windows")
        ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if not exe.is_absolute() or not exe.is_file():
            return None
        return exe, Path(working_directory) if working_directory else exe.parent

    @staticmethod
    def _restricted_environment(service_name):
        environment = {
            "WINDOWSPET_PS_PARAMETERS": json.dumps(
                {"service_name": service_name}, separators=(",", ":")
            )
        }
        for key in RESTART_SERVICE_STANDARD_ENVIRONMENT_KEYS:
            value = os.environ.get(key)
            if value:
                environment[key] = value
        return environment

    def _emit(self, event, proposal, grant_id, *, result_code="ok",
              exit_code=None, verification_result=""):
        self.audit.write(AuditEvent(
            event,
            result_code=result_code,
            proposal_id=proposal.proposal_id,
            grant_id=grant_id,
            operation=RESTART_SERVICE_CONTRACT.operation,
            script_sha256=script_sha256(RESTART_SERVICE_SCRIPT),
            timeout_seconds=RESTART_SERVICE_CONTRACT.timeout_seconds,
            exit_code=exit_code,
            verification_result=verification_result,
        ))

    def _outcome(self, status, code, proposal, grant_id, *, exit_code=None,
                 verification_result="", event="powershell_execution_failed"):
        self._emit(
            event, proposal, grant_id, result_code=code, exit_code=exit_code,
            verification_result=verification_result,
        )
        return ServiceRestartOutcome(status, code, exit_code, verification_result)

    def _verify_running(self, identity, cancel):
        deadline = self.clock() + SERVICE_VERIFICATION_TIMEOUT_SECONDS
        while True:
            if cancel():
                return "cancelled", "cancelled"
            try:
                current = self.resolver.resolve(identity.service_name)
            except Exception:
                return "verification_failed", "verification_provider_error"
            if current is None:
                return "verification_failed", "service_not_found_after_execution"
            if (current.service_name != identity.service_name
                    or current.display_name != identity.display_name):
                return "verification_failed", "identity_changed"
            state = _norm(current.observed_status).replace(" ", "")
            if state == "running":
                return "succeeded", "ok"
            if state not in SERVICE_TRANSITIONAL_STATES:
                return "verification_failed", "service_not_running"
            remaining = deadline - self.clock()
            if remaining <= 0:
                return "verification_failed", "verification_timeout"
            self.sleeper(min(SERVICE_VERIFICATION_POLL_INTERVAL_SECONDS, remaining))

    def execute(self, grant_id, proposal, identity, cancel=None):
        cancelled = lambda: self._cancel.is_set() or (cancel is not None and cancel.is_set())
        if not self._valid_request(proposal, identity):
            return ServiceRestartOutcome(ServiceRestartStatus.REJECTED, "invalid_request")
        if self.resolver.validate(identity) is not ServiceResolutionCode.MATCHED:
            return self._outcome(ServiceRestartStatus.REJECTED, "identity_changed", proposal, grant_id)
        if cancelled():
            return self._outcome(ServiceRestartStatus.CANCELLED, "cancelled", proposal, grant_id, event="powershell_execution_cancelled")
        consumed = self.grants.consume_for(grant_id, RESTART_SERVICE_CONTRACT, proposal)
        if not consumed.success:
            return self._outcome(ServiceRestartStatus.REJECTED, consumed.reason.value, proposal, grant_id)
        backend = self._backend(self.powershell_exe, self.working_directory)
        if backend is None:
            return self._outcome(ServiceRestartStatus.REJECTED, "backend_unavailable", proposal, grant_id)
        executable, working_directory = backend
        path = None
        try:
            fd, name = tempfile.mkstemp(prefix="windows_pet_", suffix=".ps1")
            path = Path(name)
            with os.fdopen(fd, "wb") as file:
                file.write(canonical_script(RESTART_SERVICE_SCRIPT))
                file.flush()
                os.fsync(file.fileno())
            expected_hash = proposal.parameters["script_sha256"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                return self._outcome(ServiceRestartStatus.REJECTED, "script_hash_mismatch", proposal, grant_id)
            if self.resolver.validate(identity) is not ServiceResolutionCode.MATCHED:
                return self._outcome(ServiceRestartStatus.REJECTED, "identity_changed", proposal, grant_id)
            if cancelled():
                return self._outcome(ServiceRestartStatus.CANCELLED, "cancelled", proposal, grant_id, event="powershell_execution_cancelled")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                return self._outcome(ServiceRestartStatus.REJECTED, "script_hash_mismatch", proposal, grant_id)
            self._emit("powershell_execution_started", proposal, grant_id, result_code="preflight_validated")
            try:
                self._active_process = self.process_factory(
                    [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)],
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._restricted_environment(identity.service_name),
                    cwd=str(working_directory),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                return self._outcome(ServiceRestartStatus.FAILED, "spawn_failed", proposal, grant_id)
            except subprocess.SubprocessError:
                return self._outcome(ServiceRestartStatus.FAILED, "execution_failed", proposal, grant_id)
            started = self.clock()
            while True:
                if cancelled():
                    self._cleanup(self._active_process)
                    return self._outcome(ServiceRestartStatus.CANCELLED, "cancelled", proposal, grant_id, event="powershell_execution_cancelled")
                remaining = RESTART_SERVICE_CONTRACT.timeout_seconds - (self.clock() - started)
                if remaining <= 0:
                    self._cleanup(self._active_process)
                    return self._outcome(ServiceRestartStatus.TIMED_OUT, "timeout", proposal, grant_id, event="powershell_execution_timed_out")
                try:
                    self._active_process.communicate(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if self._active_process.returncode != 0:
                return self._outcome(ServiceRestartStatus.FAILED, "nonzero_exit", proposal, grant_id, exit_code=self._active_process.returncode)
            self._emit("powershell_execution_succeeded", proposal, grant_id, result_code="exit_0", exit_code=0)
            verification_status, verification_code = self._verify_running(identity, cancelled)
            if verification_status == "succeeded":
                self._emit("powershell_verification_succeeded", proposal, grant_id, result_code="ok", exit_code=0, verification_result="running")
                return ServiceRestartOutcome(ServiceRestartStatus.SUCCEEDED, "ok", 0, "running")
            if verification_status == "cancelled":
                self._emit("powershell_execution_cancelled", proposal, grant_id, result_code="cancelled", exit_code=0, verification_result="not_completed")
                return ServiceRestartOutcome(ServiceRestartStatus.CANCELLED, "cancelled", 0, "not_completed")
            self._emit("powershell_verification_failed", proposal, grant_id, result_code=verification_code, exit_code=0, verification_result=verification_code)
            return ServiceRestartOutcome(ServiceRestartStatus.VERIFICATION_FAILED, verification_code, 0, verification_code)
        except OSError:
            return self._outcome(ServiceRestartStatus.FAILED, "execution_failed", proposal, grant_id)
        except subprocess.SubprocessError:
            return self._outcome(ServiceRestartStatus.FAILED, "execution_failed", proposal, grant_id)
        except Exception:
            return self._outcome(ServiceRestartStatus.FAILED, "unexpected_error", proposal, grant_id)
        finally:
            self._active_process = None
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
