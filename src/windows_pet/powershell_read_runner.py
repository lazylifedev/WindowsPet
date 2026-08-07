from __future__ import annotations

import json, os, subprocess, time
from pathlib import Path

from .audit_log import AuditEvent, NullAuditSink
from .powershell_read_models import PowerShellReadOutcome, PowerShellReadStatus, WindowsInspectionRequest
from .powershell_read_result import validate_result

class PowerShellReadRunner:
    def __init__(self, executable_resolver=None, process_factory=subprocess.Popen, clock=time.monotonic, sleeper=time.sleep, audit=None):
        self.executable_resolver = executable_resolver or (lambda: Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        self.process_factory, self.clock, self.sleeper, self.audit = process_factory, clock, sleeper, audit or NullAuditSink()
    def execute(self, request: WindowsInspectionRequest, plan, cancel=None) -> PowerShellReadOutcome:
        def emit(event, **extra): self.audit.write(AuditEvent(event, operation=request.area.value, result_code=extra.get("result_code", "ok")))
        if cancel is not None and cancel.is_set(): emit("powershell_read_cancelled", result_code="cancelled_before_start"); return PowerShellReadOutcome(PowerShellReadStatus.CANCELLED, result_code="cancelled_before_start")
        executable = Path(self.executable_resolver())
        if not executable.exists() or not executable.is_file() or executable.is_symlink(): emit("powershell_read_failed", result_code="not_available"); return PowerShellReadOutcome(PowerShellReadStatus.NOT_AVAILABLE, result_code="not_available")
        env = os.environ.copy(); env["WINDOWSPET_PS_PARAMETERS"] = json.dumps({"query": request.query, "maxResults": request.max_results}, ensure_ascii=False, separators=(",", ":"))
        argv = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]
        emit("powershell_read_started")
        try:
            process = self.process_factory(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, cwd=None, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started = self.clock(); payload = plan.script.encode("utf-8")
            while True:
                if cancel is not None and cancel.is_set():
                    process.terminate(); process.communicate(); emit("powershell_read_cancelled", result_code="cancelled"); return PowerShellReadOutcome(PowerShellReadStatus.CANCELLED, result_code="cancelled")
                remaining = plan.timeout_seconds - (self.clock() - started)
                if remaining <= 0:
                    process.terminate(); process.communicate(); emit("powershell_read_timeout", result_code="timeout"); return PowerShellReadOutcome(PowerShellReadStatus.TIMEOUT, result_code="timeout")
                try:
                    stdout, stderr = process.communicate(payload, timeout=min(0.1, remaining)); break
                except subprocess.TimeoutExpired:
                    payload = None
        except OSError:
            emit("powershell_read_failed", result_code="start_failed"); return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="start_failed")
        if cancel is not None and cancel.is_set(): process.terminate(); emit("powershell_read_cancelled", result_code="cancelled"); return PowerShellReadOutcome(PowerShellReadStatus.CANCELLED, result_code="cancelled")
        if len(stdout) > plan.max_stdout_bytes or len(stderr) > plan.max_stderr_bytes or process.returncode != 0 or stderr: emit("powershell_read_failed", result_code="execution_failed"); return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="execution_failed")
        try: result = validate_result(json.loads(stdout.decode("utf-8")), request.area, request.max_results)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError): emit("powershell_read_invalid_output", result_code="invalid_output"); return PowerShellReadOutcome(PowerShellReadStatus.INVALID_OUTPUT, result_code="invalid_output")
        emit("powershell_read_succeeded"); return PowerShellReadOutcome(PowerShellReadStatus.SUCCESS, result=result)
