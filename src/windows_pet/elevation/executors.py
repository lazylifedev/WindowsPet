from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Callable, Mapping

from ..service_restart import (
    RESTART_SERVICE_ENVIRONMENT_KEYS,
    RESTART_SERVICE_SCRIPT,
    RESTART_SERVICE_TEMPLATE_ID,
    canonical_script,
    script_sha256,
)
from .broker import DispatchOutcome
from .models import ElevationStatus
from .validation import validate_restart_service_parameters


class ElevatedRestartServiceExecutor:
    """Production-only fixed-template executor used by the elevated Broker."""

    def __init__(self, *, process_factory=subprocess.Popen, powershell_exe=None,
                 working_directory=None, clock=time.monotonic,
                 sleeper=time.sleep):
        self.process_factory = process_factory
        self.powershell_exe = powershell_exe
        self.working_directory = working_directory
        self.clock = clock
        self.sleeper = sleeper
        self._active_process = None

    @staticmethod
    def _environment(service_name: str) -> dict[str, str]:
        environment = {
            "WINDOWSPET_PS_PARAMETERS": json.dumps(
                {"service_name": service_name}, separators=(",", ":")
            )
        }
        for key in RESTART_SERVICE_ENVIRONMENT_KEYS[1:]:
            value = os.environ.get(key)
            if value:
                environment[key] = value
        return environment

    @staticmethod
    def _cleanup(process) -> None:
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass

    def execute(self, operation_id: str, parameters: Mapping,
                cancel_event: Event | None = None) -> DispatchOutcome:
        cancel_event = cancel_event or Event()
        if operation_id != "restart_service":
            return DispatchOutcome(ElevationStatus.REJECTED, "wrong_operation", None)
        try:
            service_name = validate_restart_service_parameters(dict(parameters))
            if parameters["template_version"] != RESTART_SERVICE_TEMPLATE_ID:
                return DispatchOutcome(ElevationStatus.REJECTED, "template_mismatch", None)
            expected_hash = script_sha256(RESTART_SERVICE_SCRIPT)
            if parameters["script_sha256"] != expected_hash:
                return DispatchOutcome(ElevationStatus.REJECTED, "script_hash_mismatch", None)
            executable = Path(self.powershell_exe) if self.powershell_exe else Path(
                os.environ.get("SystemRoot", r"C:\Windows")
            ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if not executable.is_absolute() or not executable.is_file():
                return DispatchOutcome(ElevationStatus.REJECTED, "backend_unavailable", None)
            workdir = Path(self.working_directory) if self.working_directory else executable.parent
            if not workdir.is_dir():
                return DispatchOutcome(ElevationStatus.REJECTED, "working_directory_unavailable", None)
            if cancel_event.is_set():
                return DispatchOutcome(ElevationStatus.CANCELLED, "broker_execution_cancelled", None)
        except Exception:
            return DispatchOutcome(ElevationStatus.REJECTED, "invalid_parameters", None)

        script_path = None
        process = None
        try:
            fd, name = tempfile.mkstemp(prefix="WindowsPet.Elevated.", suffix=".ps1")
            script_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical_script(RESTART_SERVICE_SCRIPT))
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(script_path.read_bytes()).hexdigest() != expected_hash:
                return DispatchOutcome(ElevationStatus.REJECTED, "script_hash_mismatch", None)
            if cancel_event.is_set():
                return DispatchOutcome(ElevationStatus.CANCELLED, "broker_execution_cancelled", None)
            if hashlib.sha256(script_path.read_bytes()).hexdigest() != expected_hash:
                return DispatchOutcome(ElevationStatus.REJECTED, "script_hash_mismatch", None)
            process = self._active_process = self.process_factory(
                [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-File", str(script_path)],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=self._environment(service_name),
                cwd=str(workdir), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            started = self.clock()
            while True:
                if cancel_event.is_set():
                    self._cleanup(process)
                    return DispatchOutcome(ElevationStatus.CANCELLED, "broker_execution_cancelled", None)
                remaining = 30.0 - (self.clock() - started)
                if remaining <= 0:
                    self._cleanup(process)
                    return DispatchOutcome(ElevationStatus.TIMED_OUT, "broker_execution_timeout", None)
                try:
                    process.communicate(timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                return DispatchOutcome(ElevationStatus.FAILED, "nonzero_exit", process.returncode)
            return DispatchOutcome(ElevationStatus.SUCCEEDED, "ok", 0, "execution_completed")
        except (OSError, subprocess.SubprocessError):
            return DispatchOutcome(ElevationStatus.FAILED, "spawn_failed", None)
        finally:
            self._active_process = None
            if script_path is not None:
                try:
                    script_path.unlink(missing_ok=True)
                except OSError:
                    pass
