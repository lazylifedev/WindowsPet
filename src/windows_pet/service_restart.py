from __future__ import annotations

import ctypes, hashlib, json, os, subprocess, tempfile, time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from .action_models import (ActionProposal, ActionProposalFactory, ActionTarget,
    ConfirmationType, ScriptReviewActionPreview, SideEffect, ToolContract)
from .audit_log import AuditEvent, NullAuditSink
from .powershell_read_models import WindowsInspectionArea, WindowsInspectionRequest, PowerShellReadStatus
from .powershell_read_runner import PowerShellReadRunner

RESTART_SERVICE_SCRIPT = '''$ErrorActionPreference = "Stop"
$params = $env:WINDOWSPET_PS_PARAMETERS | ConvertFrom-Json
Restart-Service -Name ([string]$params.service_name) -ErrorAction Stop
'''
RESTART_SERVICE_TEMPLATE_ID = "windows_pet.restart_service.v1"
RESTART_SERVICE_ENVIRONMENT_KEYS = ("WINDOWSPET_PS_PARAMETERS",)
RESTART_SERVICE_CONTRACT = ToolContract("windows_service", "1", "restart_service", SideEffect.SYSTEM_CHANGE,
    ConfirmationType.SCRIPT_REVIEW, True, True, True, 30.0, "read-only service inspection; canonical service must be Running",
    ("service_name", "display_name", "script_sha256", "verification", "duration"))

@dataclass(frozen=True)
class ServiceIdentity:
    service_name: str
    display_name: str
    observed_status: str

PROTECTED_SERVICE_NAMES = frozenset({"RpcSs", "DcomLaunch", "EventLog", "PlugPlay", "Power", "SamSs", "Winmgmt", "Schedule", "services"})

def _norm(value): return unicodedata.normalize("NFKC", str(value)).casefold().strip()
def canonical_script(script): return (script.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")
def script_sha256(script): return hashlib.sha256(canonical_script(script)).hexdigest()

class ServiceResolutionCode(str, Enum):
    MATCHED="matched"; NOT_FOUND="not_found"; AMBIGUOUS="ambiguous"; PROTECTED="protected"; ADMIN_REQUIRED="admin_required"; CHANGED="changed"

class ServiceRestartStatus(str, Enum):
    SUCCEEDED="succeeded"; FAILED="failed"; CANCELLED="cancelled"; TIMED_OUT="timed_out"; REJECTED="rejected"; VERIFICATION_FAILED="verification_failed"

@dataclass(frozen=True)
class ServiceRestartOutcome:
    status: ServiceRestartStatus
    result_code: str

class ServiceIdentityResolver:
    def __init__(self, inspection: Callable[[], list[dict]] | None = None, protected=PROTECTED_SERVICE_NAMES, is_admin=None):
        self.inspection, self.protected = inspection or self._inspect_services, frozenset(_norm(x) for x in protected)
        self.is_admin = is_admin or self._is_admin
        self.last_code = ServiceResolutionCode.NOT_FOUND
    @staticmethod
    def _is_admin():
        try: return bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else False
        except (AttributeError, OSError): return False
    @staticmethod
    def _inspect_services():
        outcome = PowerShellReadRunner().execute(WindowsInspectionRequest(WindowsInspectionArea.SERVICES, None, 100))
        return outcome.result.get("items", []) if outcome.status is PowerShellReadStatus.SUCCESS and outcome.result else []
    def resolve(self, query, snapshot: list[dict] | None = None):
        rows = snapshot if snapshot is not None else (self.inspection() if self.inspection else [])
        q = _norm(query); matches=[]
        for row in rows:
            name, display = row.get("name", row.get("service_name", "")), row.get("displayName", row.get("display_name", ""))
            if _norm(name) == q: matches.append((row, 2))
            elif _norm(display) == q: matches.append((row, 1))
            elif q and any(_norm(part) == q for part in (str(name).split(), str(display).split())): matches.append((row, 0))
        if not matches: self.last_code=ServiceResolutionCode.NOT_FOUND; return None
        best=max(x[1] for x in matches); matches=[x for x in matches if x[1] == best]
        if len(matches) != 1: self.last_code=ServiceResolutionCode.AMBIGUOUS; return None
        row=matches[0][0]; identity=ServiceIdentity(str(row.get("name", row.get("service_name", ""))), str(row.get("displayName", row.get("display_name", ""))), str(row.get("state", row.get("status", ""))))
        self.last_code = (ServiceResolutionCode.PROTECTED if _norm(identity.service_name) in self.protected else (ServiceResolutionCode.MATCHED if self.is_admin() else ServiceResolutionCode.ADMIN_REQUIRED))
        return identity
    def validate(self, identity, snapshot=None):
        current=self.resolve(identity.service_name, snapshot)
        if current is None: return self.last_code
        if self.last_code is not ServiceResolutionCode.MATCHED: return self.last_code
        return ServiceResolutionCode.MATCHED if current == identity else ServiceResolutionCode.CHANGED

class ServiceRestartProposalFactory:
    def __init__(self, factory=None): self.factory=factory or ActionProposalFactory()
    def create(self, task_id, identity):
        digest=script_sha256(RESTART_SERVICE_SCRIPT)
        preview=ScriptReviewActionPreview("restart_service", "The selected Windows service will be stopped and started.", "Restart service", purpose="Restart a Windows service", target=f"{identity.display_name} ({identity.service_name})", script_text=RESTART_SERVICE_SCRIPT, script_sha256_short=digest[:16], backend="Windows PowerShell 5.1", working_directory_display="PowerShell system directory", environment_summary="WINDOWSPET_PS_PARAMETERS contains only the canonical service name", expected_changes="The service is restarted.", requires_admin_display="Required", timeout_display="30 seconds", verification_plan="Read the service again and require status Running.", rollback_plan="None")
        params={"service_name":identity.service_name,"display_name":identity.display_name,"observed_status":identity.observed_status,"script_sha256":digest,"template_version":RESTART_SERVICE_TEMPLATE_ID,"backend":"windows_powershell","environment_keys":list(RESTART_SERVICE_ENVIRONMENT_KEYS)}
        return self.factory.create(RESTART_SERVICE_CONTRACT, task_id, ActionTarget("windows_service", identity.service_name, identity.display_name), params, preview)

class ServiceRestartRunner:
    def __init__(self, grants, resolver, process_factory=subprocess.Popen, powershell_exe=None, working_directory=None, audit=None, clock=time.monotonic):
        self.grants,self.resolver,self.process_factory=grants,resolver,process_factory; self.powershell_exe=powershell_exe; self.working_directory=working_directory; self.audit=audit or NullAuditSink()
        self.clock, self._cancel, self._active_process = clock, __import__("threading").Event(), None
    def cancel(self): self._cancel.set()
    def reset_cancel(self): self._cancel.clear()
    @staticmethod
    def _valid_request(proposal, identity):
        if not isinstance(proposal, ActionProposal) or not isinstance(identity, ServiceIdentity): return False
        params = proposal.parameters
        return (proposal.tool_name == RESTART_SERVICE_CONTRACT.name and proposal.tool_version == RESTART_SERVICE_CONTRACT.version and proposal.operation == "restart_service" and proposal.side_effect is SideEffect.SYSTEM_CHANGE and proposal.confirmation_type is ConfirmationType.SCRIPT_REVIEW and proposal.reversible and proposal.requires_admin and proposal.cancellation_support and proposal.timeout_seconds == RESTART_SERVICE_CONTRACT.timeout_seconds and proposal.verification_method == RESTART_SERVICE_CONTRACT.verification_method and proposal.target == ActionTarget("windows_service", identity.service_name, identity.display_name) and isinstance(params, Mapping) and set(params) == {"service_name", "display_name", "observed_status", "script_sha256", "template_version", "backend", "environment_keys"} and params["service_name"] == identity.service_name and params["display_name"] == identity.display_name and params["observed_status"] == identity.observed_status and params["script_sha256"] == script_sha256(RESTART_SERVICE_SCRIPT) and params["template_version"] == RESTART_SERVICE_TEMPLATE_ID and params["backend"] == "windows_powershell" and tuple(params["environment_keys"]) == RESTART_SERVICE_ENVIRONMENT_KEYS)
    @staticmethod
    def _cleanup(process):
        try:
            if process.poll() is None: process.terminate()
            try: process.communicate(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.communicate(timeout=2)
        except (OSError, subprocess.SubprocessError): pass
    def execute(self, grant_id, proposal, identity, cancel=None):
        cancelled=lambda: self._cancel.is_set() or (cancel is not None and cancel.is_set())
        if not self._valid_request(proposal, identity): return ServiceRestartOutcome(ServiceRestartStatus.REJECTED,"invalid_request")
        if self.resolver.validate(identity) is not ServiceResolutionCode.MATCHED: return ServiceRestartOutcome(ServiceRestartStatus.REJECTED,"identity_changed")
        if cancelled(): return ServiceRestartOutcome(ServiceRestartStatus.CANCELLED,"cancelled")
        consumed=self.grants.consume_for(grant_id, RESTART_SERVICE_CONTRACT, proposal)
        if not consumed.success: return ServiceRestartOutcome(ServiceRestartStatus.REJECTED,consumed.reason.value)
        exe=Path(self.powershell_exe) if self.powershell_exe else Path(os.environ.get("SystemRoot",r"C:\\Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
        if not exe.is_absolute() or not exe.is_file(): return ServiceRestartOutcome(ServiceRestartStatus.REJECTED,"backend_unavailable")
        fd,name=tempfile.mkstemp(prefix="windows_pet_",suffix=".ps1"); path=Path(name)
        try:
            with os.fdopen(fd,"wb") as f: f.write(canonical_script(RESTART_SERVICE_SCRIPT))
            if hashlib.sha256(path.read_bytes()).hexdigest()!=proposal.parameters["script_sha256"] or self.resolver.validate(identity) is not ServiceResolutionCode.MATCHED: return ServiceRestartOutcome(ServiceRestartStatus.REJECTED,"identity_changed")
            if cancelled(): return ServiceRestartOutcome(ServiceRestartStatus.CANCELLED,"cancelled_before_start")
            self._active_process=p=self.process_factory([str(exe),"-NoLogo","-NoProfile","-NonInteractive","-File",str(path)], shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"WINDOWSPET_PS_PARAMETERS":json.dumps({"service_name":identity.service_name}, separators=(",",":")),"SystemRoot":os.environ.get("SystemRoot",r"C:\\Windows"),"WINDIR":os.environ.get("WINDIR",r"C:\\Windows")}, cwd=str(self.working_directory or exe.parent), creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            started=self.clock()
            while True:
                if cancelled(): self._cleanup(p); return ServiceRestartOutcome(ServiceRestartStatus.CANCELLED,"cancelled")
                remaining=RESTART_SERVICE_CONTRACT.timeout_seconds-(self.clock()-started)
                if remaining<=0: self._cleanup(p); return ServiceRestartOutcome(ServiceRestartStatus.TIMED_OUT,"timeout")
                try: p.communicate(timeout=min(.1,remaining)); break
                except subprocess.TimeoutExpired: continue
            if p.returncode != 0: return ServiceRestartOutcome(ServiceRestartStatus.FAILED,"powershell_failed")
            verified=self.resolver.resolve(identity.service_name)
            status=ServiceRestartStatus.SUCCEEDED if verified and verified.observed_status.casefold()=="running" else ServiceRestartStatus.VERIFICATION_FAILED
            return ServiceRestartOutcome(status,"ok" if status is ServiceRestartStatus.SUCCEEDED else "verification_failed")
        except (OSError, subprocess.SubprocessError): return ServiceRestartOutcome(ServiceRestartStatus.FAILED,"execution_failed")
        finally:
            self._active_process=None
            try: path.unlink(missing_ok=True)
            except OSError: pass
