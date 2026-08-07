from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .audit_log import AuditEvent, NullAuditSink
from .powershell_read_builder import build_read_plan
from .powershell_read_models import PowerShellReadOutcome, PowerShellReadStatus, WindowsInspectionRequest
from .powershell_read_result import validate_result


class PowerShellReadRunner:
    """Runs only the locally-built, hash-verified inspection scripts."""
    _LIMITS = {"processes": 10.0, "services": 15.0, "network": 15.0}

    def __init__(self, plan_factory=build_read_plan, windows_directory_resolver=None,
                 process_factory=subprocess.Popen, clock=time.monotonic, sleeper=time.sleep, audit=None):
        self.plan_factory = plan_factory
        self.windows_directory_resolver = windows_directory_resolver or self._windows_directory
        self.process_factory, self.clock, self.sleeper, self.audit = process_factory, clock, sleeper, audit or NullAuditSink()

    @staticmethod
    def _windows_directory() -> Path:
        if os.name != "nt":
            return Path(os.environ.get("SystemRoot", r"C:\\Windows"))
        size = ctypes.windll.kernel32.GetWindowsDirectoryW(None, 0)
        if not size:
            raise OSError("GetWindowsDirectoryW failed")
        buffer = ctypes.create_unicode_buffer(size + 1)
        if not ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer)):
            raise OSError("GetWindowsDirectoryW failed")
        return Path(buffer.value)

    def _executable(self) -> Path | None:
        try:
            root = Path(self.windows_directory_resolver()).resolve(strict=True)
            executable = (root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe").resolve(strict=True)
            # resolve() must remain within the OS directory, and no link/reparse point is trusted.
            if executable.parent != (root / "System32" / "WindowsPowerShell" / "v1.0").resolve(strict=True):
                return None
            if not executable.is_file() or executable.is_symlink() or os.path.islink(executable):
                return None
            if os.name == "nt" and os.stat(executable).st_file_attributes & 0x400:
                return None
            return executable
        except (OSError, RuntimeError):
            return None

    def _invalid_plan(self, request, plan) -> bool:
        return (plan.operation != request.area.value or
                hashlib.sha256(plan.script.encode("utf-8")).hexdigest() != plan.script_sha256 or
                plan.timeout_seconds != self._LIMITS.get(plan.operation) or
                plan.max_stdout_bytes != 256 * 1024 or plan.max_stderr_bytes != 32 * 1024)

    def execute(self, request: WindowsInspectionRequest, cancel=None) -> PowerShellReadOutcome:
        def emit(event, **extra):
            self.audit.write(AuditEvent(event, operation=request.area.value, result_code=extra.get("result_code", "ok")))
        if cancel is not None and cancel.is_set():
            emit("powershell_read_cancelled", result_code="cancelled_before_start")
            return PowerShellReadOutcome(PowerShellReadStatus.CANCELLED, result_code="cancelled_before_start")
        plan = self.plan_factory(request)
        if self._invalid_plan(request, plan):
            emit("powershell_read_failed", result_code="invalid_plan")
            return PowerShellReadOutcome(PowerShellReadStatus.INVALID_OUTPUT, result_code="invalid_plan")
        executable = self._executable()
        if executable is None:
            emit("powershell_read_failed", result_code="not_available")
            return PowerShellReadOutcome(PowerShellReadStatus.NOT_AVAILABLE, result_code="not_available")
        env = os.environ.copy()
        env["WINDOWSPET_PS_PARAMETERS"] = json.dumps({"query": request.query, "maxResults": request.max_results}, ensure_ascii=False, separators=(",", ":"))
        argv = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]
        emit("powershell_read_started")
        try:
            process = self.process_factory(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                           cwd=str(executable.parent), env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started, payload = self.clock(), plan.script.encode("utf-8")
            while True:
                if cancel is not None and cancel.is_set():
                    process.terminate(); process.communicate(); emit("powershell_read_cancelled", result_code="cancelled")
                    return PowerShellReadOutcome(PowerShellReadStatus.CANCELLED, result_code="cancelled")
                remaining = plan.timeout_seconds - (self.clock() - started)
                if remaining <= 0:
                    process.terminate(); process.communicate(); emit("powershell_read_timeout", result_code="timeout")
                    return PowerShellReadOutcome(PowerShellReadStatus.TIMEOUT, result_code="timeout")
                try:
                    stdout, stderr = process.communicate(payload, timeout=min(0.1, remaining)); break
                except subprocess.TimeoutExpired as exc:
                    # communicate() returns partial bytes on TimeoutExpired; terminate before buffering can grow unbounded.
                    if len(exc.output or b"") > plan.max_stdout_bytes or len(exc.stderr or b"") > plan.max_stderr_bytes:
                        process.terminate(); process.communicate(); emit("powershell_read_failed", result_code="output_limit_exceeded")
                        return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="output_limit_exceeded")
                    payload = None
        except OSError:
            emit("powershell_read_failed", result_code="start_failed")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="start_failed")
        if len(stdout) > plan.max_stdout_bytes or len(stderr) > plan.max_stderr_bytes:
            emit("powershell_read_failed", result_code="output_limit_exceeded")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="output_limit_exceeded")
        if process.returncode != 0 or stderr:
            emit("powershell_read_failed", result_code="execution_failed")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="execution_failed")
        try:
            result = validate_result(json.loads(stdout.decode("utf-8")), request.area, request.max_results)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            emit("powershell_read_invalid_output", result_code="invalid_output")
            return PowerShellReadOutcome(PowerShellReadStatus.INVALID_OUTPUT, result_code="invalid_output")
        emit("powershell_read_succeeded")
        return PowerShellReadOutcome(PowerShellReadStatus.SUCCESS, result=result)
