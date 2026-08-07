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
from .powershell_read_result import ResultValidationError, validate_result


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
            windows_directory = Path(self.windows_directory_resolver())
            raw_root = str(windows_directory)
            if raw_root.startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
                return None
            parts = (windows_directory, windows_directory / "System32",
                     windows_directory / "System32" / "WindowsPowerShell",
                     windows_directory / "System32" / "WindowsPowerShell" / "v1.0")
            executable = parts[-1] / "powershell.exe"
            if (not windows_directory.is_dir() or
                    any(self._is_link_or_reparse(path) for path in (*parts, executable)) or
                    not executable.is_file()):
                return None
            root = windows_directory.resolve(strict=True)
            resolved_executable = executable.resolve(strict=True)
            if os.path.commonpath((os.path.normcase(str(root)), os.path.normcase(str(resolved_executable)))) != os.path.normcase(str(root)):
                return None
            return resolved_executable
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return path.is_symlink() or os.path.islink(path) or bool(attributes & 0x400)

    @staticmethod
    def _terminate_owned_process(process, timeout_seconds: float = 2.0) -> bool:
        """Boundedly reap a PowerShell process created by this runner."""
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.communicate(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    return False
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _invalid_plan(self, request, plan) -> bool:
        return (plan.operation != request.area.value or
                hashlib.sha256(plan.script.encode("utf-8")).hexdigest() != plan.script_sha256 or
                plan.timeout_seconds != self._LIMITS.get(plan.operation) or
                plan.max_stdout_bytes != 256 * 1024 or plan.max_stderr_bytes != 32 * 1024)

    def execute(self, request: WindowsInspectionRequest, cancel=None) -> PowerShellReadOutcome:
        def emit(event, **extra):
            self.audit.write(AuditEvent(event, operation=request.area.value, result_code=extra.get("result_code", "ok"),
                                        script_sha256=extra.get("script_sha256", ""), timeout_seconds=extra.get("timeout_seconds"),
                                        exit_code=extra.get("exit_code"), verification_result=extra.get("verification_result", ""),
                                        item_count=extra.get("item_count")))
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
        argv = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", plan.script]
        emit("powershell_read_started", script_sha256=plan.script_sha256, timeout_seconds=plan.timeout_seconds)
        try:
            process = self.process_factory(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                           cwd=str(executable.parent), env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def stopped(event, status, result_code):
                if not self._terminate_owned_process(process):
                    emit("powershell_read_failed", result_code="child_cleanup_failed")
                    return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="child_cleanup_failed")
                emit(event, result_code=result_code)
                return PowerShellReadOutcome(status, result_code=result_code)
            started, payload = self.clock(), None
            while True:
                if cancel is not None and cancel.is_set():
                    return stopped("powershell_read_cancelled", PowerShellReadStatus.CANCELLED, "cancelled")
                remaining = plan.timeout_seconds - (self.clock() - started)
                if remaining <= 0:
                    return stopped("powershell_read_timeout", PowerShellReadStatus.TIMEOUT, "timeout")
                try:
                    stdout, stderr = process.communicate(payload, timeout=min(0.1, remaining)); break
                except subprocess.TimeoutExpired as exc:
                    # communicate() returns partial bytes on TimeoutExpired; terminate before buffering can grow unbounded.
                    if len(exc.output or b"") > plan.max_stdout_bytes or len(exc.stderr or b"") > plan.max_stderr_bytes:
                        return stopped("powershell_read_failed", PowerShellReadStatus.FAILED, "output_limit_exceeded")
                    payload = None
        except OSError:
            emit("powershell_read_failed", result_code="start_failed")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="start_failed")
        if len(stdout) > plan.max_stdout_bytes or len(stderr) > plan.max_stderr_bytes:
            emit("powershell_read_failed", result_code="output_limit_exceeded")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="output_limit_exceeded")
        if process.returncode != 0 or stderr:
            emit("powershell_read_failed", result_code="execution_failed", script_sha256=plan.script_sha256,
                 timeout_seconds=plan.timeout_seconds, exit_code=process.returncode, verification_result="not_run")
            return PowerShellReadOutcome(PowerShellReadStatus.FAILED, result_code="execution_failed")
        verification_result = ""
        try:
            try:
                decoded = stdout.decode("utf-8-sig")
            except UnicodeDecodeError:
                verification_result = "invalid_utf8"
                raise
            try:
                parsed = json.loads(decoded)
            except json.JSONDecodeError:
                verification_result = "invalid_json"
                raise
            result = validate_result(parsed, request.area, request.max_results)
        except ResultValidationError as exc:
            verification_result = exc.code
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        if verification_result:
            emit("powershell_read_invalid_output", result_code="invalid_output", script_sha256=plan.script_sha256,
                 timeout_seconds=plan.timeout_seconds, exit_code=process.returncode, verification_result=verification_result)
            return PowerShellReadOutcome(PowerShellReadStatus.INVALID_OUTPUT, result_code="invalid_output")
        emit("powershell_read_succeeded", script_sha256=plan.script_sha256, timeout_seconds=plan.timeout_seconds,
             exit_code=process.returncode, verification_result="passed", item_count=len(result["items"]))
        return PowerShellReadOutcome(PowerShellReadStatus.SUCCESS, result=result)
