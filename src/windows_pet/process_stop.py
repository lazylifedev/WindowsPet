from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
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
PROTECTED_PROCESS_NAMES = frozenset({"system", "idle", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass"})
STOP_PROCESS_CONTRACT = ToolContract("powershell_executor", "1", "stop_process", SideEffect.PROCESS_CONTROL,
    ConfirmationType.SCRIPT_REVIEW, False, False, True, 10.0, "target process absence", ("status", "result_code", "verification_result"))


def canonical_script(script: str, maximum_bytes: int = 16 * 1024) -> bytes:
    if not isinstance(script, str) or "\0" in script:
        raise ValueError("invalid_script")
    script = script.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(char) < 32 and char not in "\n\t" for char in script):
        raise ValueError("invalid_script")
    script = script.rstrip("\n") + "\n"
    encoded = script.encode("utf-8")
    if len(encoded) > maximum_bytes: raise ValueError("script_too_large")
    return encoded


def script_sha256(script: str) -> str:
    return hashlib.sha256(canonical_script(script)).hexdigest()


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    process_name: str
    start_time_token: str


class ProcessValidationCode(str, Enum):
    OK = "ok"; MISSING = "missing"; NAME_MISMATCH = "name_mismatch"; IDENTITY_CHANGED = "identity_changed"; PROTECTED = "protected"


class ProcessIdentityResolver:
    """Local-only resolver. It exposes no executable paths or user data."""
    def __init__(self, lookup: Callable[[int], ProcessIdentity | None] | None = None, self_pid: Callable[[], int] = os.getpid):
        self.lookup, self.self_pid = lookup, self_pid

    def resolve(self, pid: int) -> ProcessIdentity | None:
        if type(pid) is not int or pid < 0: return None
        if self.lookup: return self.lookup(pid)
        if os.name != "nt": return None
        command = "(Get-Process -Id $args[0] -ErrorAction Stop); $p=Get-Process -Id $args[0] -ErrorAction Stop; [Console]::Out.Write(($p.ProcessName+'|'+$p.StartTime.ToUniversalTime().Ticks))"
        try:
            result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command, str(pid)], capture_output=True, text=True, timeout=3, shell=False)
            name, token = result.stdout.strip().split("|", 1)
            return ProcessIdentity(pid, name, token) if result.returncode == 0 and name and token else None
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
        preview = ScriptReviewActionPreview(operation="stop_process", impact="The selected process will be terminated.", button_label="Stop process", purpose="Stop a process", target=f"{identity.process_name} (PID {identity.pid})", script_text=STOP_PROCESS_SCRIPT, script_sha256_short=digest[:16], backend="PowerShell", working_directory_display="PowerShell system directory", environment_summary="WINDOWSPET_PS_PARAMETERS contains only the PID", expected_changes="The process is stopped.", requires_admin_display="No", timeout_display="10 seconds", verification_plan="Confirm the same PID and start-time identity is absent.", rollback_plan="None")
        return self.proposal_factory.create(STOP_PROCESS_CONTRACT, task_id, ActionTarget("local_process", str(identity.pid), identity.process_name),
            {"pid": identity.pid, "process_name": identity.process_name, "start_identity": identity.start_time_token, "script_sha256": digest, "backend": "powershell", "environment_keys": ["WINDOWSPET_PS_PARAMETERS"]}, preview)


class PowerShellExecutionStatus(str, Enum):
    SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"; REJECTED="rejected"; VERIFICATION_FAILED="verification_failed"
@dataclass(frozen=True)
class PowerShellExecutionOutcome:
    status: PowerShellExecutionStatus; result_code: str; exit_code: int | None = None; verification_result: str = ""; safe_output: str = ""


class PowerShellExecutionRunner:
    def __init__(self, grants, resolver=None, process_factory=subprocess.Popen, clock=time.monotonic, audit=None, powershell_exe="powershell.exe"):
        self.grants, self.resolver, self.process_factory, self.clock, self.audit, self.powershell_exe = grants, resolver or ProcessIdentityResolver(), process_factory, clock, audit or NullAuditSink(), powershell_exe
    def execute(self, grant_id, proposal: ActionProposal, identity: ProcessIdentity, cancel=None):
        params = proposal.parameters if isinstance(proposal, ActionProposal) else {}
        if proposal.operation != "stop_process" or proposal.target.identifier != str(identity.pid) or not isinstance(params, Mapping) or params.get("script_sha256") != script_sha256(STOP_PROCESS_SCRIPT): return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "invalid_request")
        if self.resolver.validate(identity) is not ProcessValidationCode.OK: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "identity_changed")
        if cancel is not None and cancel.is_set(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_before_consume")
        consumed = self.grants.consume_for(grant_id, STOP_PROCESS_CONTRACT, proposal)
        if not consumed.success: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, consumed.reason.value)
        if cancel is not None and cancel.is_set(): return PowerShellExecutionOutcome(PowerShellExecutionStatus.CANCELLED, "cancelled_after_consume")
        if self.resolver.validate(identity) is not ProcessValidationCode.OK: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "identity_changed")
        fd, name = tempfile.mkstemp(prefix="windows_pet_", suffix=".ps1"); path = Path(name)
        try:
            with os.fdopen(fd, "wb") as file: file.write(canonical_script(STOP_PROCESS_SCRIPT)); file.flush(); os.fsync(file.fileno())
            if hashlib.sha256(path.read_bytes()).hexdigest() != params["script_sha256"]: return PowerShellExecutionOutcome(PowerShellExecutionStatus.REJECTED, "script_hash_mismatch")
            env = {"WINDOWSPET_PS_PARAMETERS": json.dumps({"pid": identity.pid}, separators=(",", ":")), "SystemRoot": os.environ.get("SystemRoot", r"C:\\Windows"), "WINDIR": os.environ.get("WINDIR", r"C:\\Windows")}
            self.audit.write(AuditEvent("execution_started", proposal_id=proposal.proposal_id, grant_id=grant_id, operation="stop_process", script_sha256=params["script_sha256"], timeout_seconds=10))
            process = self.process_factory([self.powershell_exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)], shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try: _, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate(); process.communicate(timeout=2); return PowerShellExecutionOutcome(PowerShellExecutionStatus.TIMED_OUT, "timeout")
            if process.returncode != 0: return PowerShellExecutionOutcome(PowerShellExecutionStatus.FAILED, "powershell_failed", process.returncode)
            verification = self.resolver.resolve(identity.pid) is None
            status = PowerShellExecutionStatus.SUCCEEDED if verification else PowerShellExecutionStatus.VERIFICATION_FAILED
            return PowerShellExecutionOutcome(status, "ok" if verification else "target_remains", process.returncode, "absent" if verification else "present")
        except (OSError, subprocess.SubprocessError): return PowerShellExecutionOutcome(PowerShellExecutionStatus.FAILED, "execution_failed")
        finally:
            try: path.unlink(missing_ok=True)
            except OSError: self.audit.write(AuditEvent("execution_failed", result_code="temp_cleanup_failed", operation="stop_process"))
