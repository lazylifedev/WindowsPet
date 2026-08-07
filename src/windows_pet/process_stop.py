from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from threading import Event
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from .action_models import (ActionProposal, ActionProposalFactory, ActionTarget,
                            ConfirmationType, ScriptReviewActionPreview, SideEffect,
                            ToolContract)
from .audit_log import AuditEvent, NullAuditSink

STOP_PROCESS_SCRIPT = '''$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$params = $env:WINDOWSPET_PS_PARAMETERS | ConvertFrom-Json
Stop-Process -Id ([int]$params.pid) -ErrorAction Stop
'''
STOP_PROCESS_TEMPLATE_ID = "windows_pet.stop_process.v1"
STOP_PROCESS_ENVIRONMENT_KEYS = ("WINDOWSPET_PS_PARAMETERS",)
PROTECTED_PROCESS_NAMES = frozenset({"system", "idle", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass"})
STOP_PROCESS_CONTRACT = ToolContract("powershell_executor", "1", "stop_process", SideEffect.PROCESS_CONTROL,
    ConfirmationType.SCRIPT_REVIEW, False, False, True, 10.0, "target process absence or identity replacement", ("status", "result_code", "verification_result"))

def canonical_script(script: str, maximum_bytes: int = 16 * 1024) -> bytes:
    if not isinstance(script, str) or "\0" in script: raise ValueError("invalid_script")
    script = script.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(char) < 32 and char not in "\n\t" for char in script): raise ValueError("invalid_script")
    encoded = (script.rstrip("\n") + "\n").encode("utf-8")
    if len(encoded) > maximum_bytes: raise ValueError("script_too_large")
    return encoded

def script_sha256(script: str) -> str: return hashlib.sha256(canonical_script(script)).hexdigest()

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    process_name: str
    start_time_token: str

class ProcessValidationCode(str, Enum):
    OK="ok"; MISSING="missing"; NAME_MISMATCH="name_mismatch"; IDENTITY_CHANGED="identity_changed"; PROTECTED="protected"

class ProcessIdentityResolver:
    """Reads exactly one local process identity; malformed PowerShell output fails closed."""
    def __init__(self, lookup: Callable[[int], ProcessIdentity | None] | None = None, self_pid: Callable[[], int] = os.getpid, run=subprocess.run):
        self.lookup, self.self_pid, self.run = lookup, self_pid, run
    def resolve(self, pid: int) -> ProcessIdentity | None:
        if type(pid) is not int or pid < 1: return None
        if self.lookup: return self.lookup(pid)
        if os.name != "nt": return None
        command = "$ErrorActionPreference='Stop';$p=Get-Process -Id $args[0] -ErrorAction Stop;[Console]::Out.Write($p.ProcessName+'|'+$p.StartTime.ToUniversalTime().Ticks)"
        try:
            result = self.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command, str(pid)], capture_output=True, text=True, timeout=3, shell=False)
            output = result.stdout.strip()
            if result.returncode != 0 or output.count("|") != 1: return None
            name, token = output.split("|")
            if not name or not token or not token.isascii() or not token.isdecimal() or int(token) <= 0: return None
            return ProcessIdentity(pid, name, token)
        except (OSError, subprocess.SubprocessError, ValueError): return None
    def validate(self, identity: ProcessIdentity, expected_name: str | None = None) -> ProcessValidationCode:
        if not isinstance(identity, ProcessIdentity) or identity.pid in (0, 4) or identity.pid == self.self_pid() or identity.process_name.casefold() in PROTECTED_PROCESS_NAMES: return ProcessValidationCode.PROTECTED
        if expected_name and identity.process_name.casefold() != expected_name.strip().casefold(): return ProcessValidationCode.NAME_MISMATCH
        current = self.resolve(identity.pid)
        return ProcessValidationCode.OK if current == identity else (ProcessValidationCode.MISSING if current is None else ProcessValidationCode.IDENTITY_CHANGED)

class PowerShellExecutionProposalFactory:
    def __init__(self, proposal_factory=None): self.proposal_factory = proposal_factory or ActionProposalFactory()
    def create(self, task_id: str, identity: ProcessIdentity) -> ActionProposal:
        digest = script_sha256(STOP_PROCESS_SCRIPT)
        preview = ScriptReviewActionPreview(operation="stop_process", impact="The selected process will be terminated.", button_label="Stop process", purpose="Stop a process", target=f"{identity.process_name} (PID {identity.pid})", script_text=STOP_PROCESS_SCRIPT, script_sha256_short=digest[:16], backend="Windows PowerShell", working_directory_display="PowerShell system directory", environment_summary="WINDOWSPET_PS_PARAMETERS contains only the PID", expected_changes="The process is stopped.", requires_admin_display="No", timeout_display="10 seconds", verification_plan="Confirm the original PID/start-time identity is absent or replaced.", rollback_plan="None")
        return self.proposal_factory.create(STOP_PROCESS_CONTRACT, task_id, ActionTarget("local_process", str(identity.pid), identity.process_name), {"pid":identity.pid, "process_name":identity.process_name, "start_identity":identity.start_time_token, "script_sha256":digest, "backend":"windows_powershell", "environment_keys":list(STOP_PROCESS_ENVIRONMENT_KEYS)}, preview)

class PowerShellExecutionStatus(str, Enum):
    SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"; REJECTED="rejected"; VERIFICATION_FAILED="verification_failed"
@dataclass(frozen=True)
class PowerShellExecutionOutcome:
    status: PowerShellExecutionStatus; result_code: str; exit_code: int | None = None; verification_result: str = ""; safe_output: str = ""

class PowerShellExecutionRunner:
    def __init__(self, grants, resolver=None, process_factory=subprocess.Popen, clock=time.monotonic, audit=None, powershell_exe=None, working_directory=None):
        self.grants, self.resolver, self.process_factory, self.clock, self.audit = grants, resolver or ProcessIdentityResolver(), process_factory, clock, audit or NullAuditSink()
        self.powershell_exe, self.working_directory = powershell_exe, working_directory
        self._active_process = None
        self._cancel = Event()
    def _backend(self):
        """Bind execution to the installed Windows PowerShell, never PATH lookup."""
        if self.powershell_exe is not None:
            executable = Path(self.powershell_exe)
            if not executable.is_absolute(): return None
            return executable, Path(self.working_directory) if self.working_directory is not None else executable.parent
        if os.name != "nt": return None
        executable = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        try:
            resolved = executable.resolve(strict=True)
            return (resolved, resolved.parent) if resolved.is_file() else None
        except OSError: return None
    def _emit(self, event, proposal, grant_id, **values):
        self.audit.write(AuditEvent(event, proposal_id=proposal.proposal_id, grant_id=grant_id, operation="stop_process", script_sha256=script_sha256(STOP_PROCESS_SCRIPT), timeout_seconds=STOP_PROCESS_CONTRACT.timeout_seconds, **values))
    def _valid_request(self, proposal, identity) -> bool:
        if not isinstance(proposal, ActionProposal) or not isinstance(identity, ProcessIdentity): return False
        p = proposal.parameters
        return (proposal.tool_name == STOP_PROCESS_CONTRACT.name and proposal.tool_version == STOP_PROCESS_CONTRACT.version and proposal.operation == "stop_process" and proposal.side_effect is SideEffect.PROCESS_CONTROL and proposal.confirmation_type is ConfirmationType.SCRIPT_REVIEW and not proposal.reversible and not proposal.requires_admin and proposal.cancellation_support and proposal.timeout_seconds == STOP_PROCESS_CONTRACT.timeout_seconds and proposal.verification_method == STOP_PROCESS_CONTRACT.verification_method and proposal.target == ActionTarget("local_process", str(identity.pid), identity.process_name) and isinstance(p, Mapping) and set(p) == {"pid","process_name","start_identity","script_sha256","backend","environment_keys"} and p["pid"] == identity.pid and p["process_name"] == identity.process_name and p["start_identity"] == identity.start_time_token and p["script_sha256"] == script_sha256(STOP_PROCESS_SCRIPT) and p["backend"] == "windows_powershell" and tuple(p["environment_keys"]) == STOP_PROCESS_ENVIRONMENT_KEYS)
    @staticmethod
    def _cleanup(process) -> bool:
        try:
            if process.poll() is None: process.terminate()
            try: process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(timeout=2)
            return True
        except (OSError, subprocess.SubprocessError): return False
    def cancel(self):
        """Signal cancellation without touching a child from the GUI thread."""
        self._cancel.set()
    def reset_cancel(self):
        self._cancel.clear()
    def execute(self, grant_id, proposal: ActionProposal, identity: ProcessIdentity, cancel=None):
        cancelled = lambda: self._cancel.is_set() or (cancel is not None and cancel.is_set())
        if not self._valid_request(proposal, identity): return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "invalid_request")
        if self.resolver.validate(identity) is not ProcessValidationCode.OK: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "identity_changed")
        if cancelled(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_before_consume")
        consumed = self.grants.consume_for(grant_id, STOP_PROCESS_CONTRACT, proposal)
        if not consumed.success: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, consumed.reason.value)
        if cancelled(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_after_consume")
        backend = self._backend()
        if backend is None: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "backend_unavailable")
        fd, name = tempfile.mkstemp(prefix="windows_pet_", suffix=".ps1"); path = Path(name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(canonical_script(STOP_PROCESS_SCRIPT)); file.flush(); os.fsync(file.fileno())
            if hashlib.sha256(path.read_bytes()).hexdigest() != proposal.parameters["script_sha256"]: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "script_hash_mismatch")
            if self.resolver.validate(identity) is not ProcessValidationCode.OK: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "identity_changed")
            if cancelled(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_before_start")
            # Re-read immediately before Popen: no mutable script or stale identity window.
            if hashlib.sha256(path.read_bytes()).hexdigest() != proposal.parameters["script_sha256"]: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "script_hash_mismatch")
            if cancelled(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_before_start")
            env = {"WINDOWSPET_PS_PARAMETERS":json.dumps({"pid":identity.pid}, separators=(",",":")), "SystemRoot":os.environ.get("SystemRoot",r"C:\\Windows"), "WINDIR":os.environ.get("WINDIR",r"C:\\Windows")}
            executable, cwd = backend
            kwargs = dict(shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd), creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            self._emit("powershell_execution_started", proposal, grant_id)
            self._active_process = self.process_factory([str(executable),"-NoLogo","-NoProfile","-NonInteractive","-File",str(path)], **kwargs)
            started = self.clock()
            while True:
                if cancelled():
                    self._cleanup(self._active_process); self._emit("powershell_execution_cancelled", proposal, grant_id, result_code="cancelled"); return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED,"cancelled")
                remaining = STOP_PROCESS_CONTRACT.timeout_seconds - (self.clock() - started)
                if remaining <= 0:
                    self._cleanup(self._active_process); self._emit("powershell_execution_timed_out", proposal, grant_id, result_code="timeout"); return PowerShellExecutionOutcome(PowerShellExecutionStatus.TIMED_OUT,"timeout")
                try:
                    stdout, _ = self._active_process.communicate(timeout=min(.1, remaining)); break
                except subprocess.TimeoutExpired: continue
            if self._active_process.returncode != 0:
                self._emit("powershell_execution_failed", proposal, grant_id, result_code="powershell_failed", exit_code=self._active_process.returncode); return PowerShellExecutionOutcome(PowerShellExecutionStatus.FAILED,"powershell_failed",self._active_process.returncode)
            self._emit("powershell_execution_succeeded", proposal, grant_id, exit_code=0)
            current = self.resolver.resolve(identity.pid)
            verified = current is None or current.start_time_token != identity.start_time_token
            status = PowerShellExecutionStatus.SUCCEEDED if verified else PowerShellExecutionStatus.VERIFICATION_FAILED
            self._emit("powershell_verification_succeeded" if verified else "powershell_verification_failed", proposal, grant_id, result_code="ok" if verified else "target_remains", exit_code=0, verification_result="absent_or_replaced" if verified else "same_identity")
            return PowerShellExecutionOutcome(status, "ok" if verified else "target_remains", 0, "absent_or_replaced" if verified else "same_identity", (stdout or b"")[:1024].decode("utf-8", "replace"))
        except (OSError, subprocess.SubprocessError):
            self._emit("powershell_execution_failed", proposal, grant_id, result_code="execution_failed"); return PowerShellExecutionOutcome(PowerShellExecutionStatus.FAILED,"execution_failed")
        finally:
            self._active_process = None
            try: path.unlink(missing_ok=True)
            except OSError: self._emit("powershell_temp_cleanup_failed", proposal, grant_id, result_code="temp_cleanup_failed")
