from __future__ import annotations

import ctypes
import os
import time
import json
import sys
from datetime import datetime
from ctypes import wintypes
from pathlib import Path
from threading import Event

from .broker import MAX_ENVELOPE_BYTES, BrokerEntryPoint
from .envelope import canonical_json_bytes
from .models import ElevationLaunchOutcome, ElevationReason, ElevationStatus
from .protocol import ElevationLauncher
from .validation import validate_broker_identity

_RESULT_KEYS = frozenset({
    "request_id", "operation_id", "status", "result_code", "exit_code",
    "script_sha256", "started_at", "finished_at", "verification_hint",
})


def _read_result(path: Path):
    from .models import ElevationResult
    raw = path.read_bytes()
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("result_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise ValueError("invalid_result_schema")
    if canonical_json_bytes(payload) != raw:
        raise ValueError("non_canonical_result")
    return ElevationResult(
        payload["request_id"], payload["operation_id"], ElevationStatus(payload["status"]),
        payload["result_code"], payload["exit_code"], payload["script_sha256"],
        datetime.fromisoformat(payload["started_at"]), datetime.fromisoformat(payload["finished_at"]),
        payload["verification_hint"],
    )


def resolve_broker_path() -> Path:
    """Resolve only the bundled helper from the trusted app directory."""
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parents[3]
    candidate = root / "WindowsPet.ElevationBroker.exe"
    return validate_broker_identity(
        candidate, expected_path=candidate, install_root=root
    ).path


class FakeElevationLauncher:
    """Runs the same broker entry point without UAC or an admin operation."""
    def __init__(self, broker=None, *, uac_cancelled: bool = False):
        self.broker = broker or BrokerEntryPoint()
        self.uac_cancelled = uac_cancelled
        self.launch_count = 0

    def launch(self, broker_path, envelope_path, timeout_seconds, cancel_event: Event,
               *, expected_envelope_sha256: str | None = None) -> ElevationLaunchOutcome:
        if cancel_event.is_set():
            return ElevationLaunchOutcome(ElevationStatus.CANCELLED, ElevationReason.CANCELLED_BEFORE_ELEVATION.value)
        if self.uac_cancelled:
            return ElevationLaunchOutcome(ElevationStatus.CANCELLED, ElevationReason.UAC_CANCELLED.value)
        self.launch_count += 1
        started = time.monotonic()
        result = self.broker.run(Path(envelope_path), expected_envelope_sha256=expected_envelope_sha256, cancel_event=cancel_event)
        if time.monotonic() - started > timeout_seconds:
            return ElevationLaunchOutcome(ElevationStatus.TIMED_OUT, ElevationReason.BROKER_TIMEOUT.value, result)
        return ElevationLaunchOutcome(result.status, result.result_code, result)


class WindowsElevationLauncher:
    """Native ``ShellExecuteExW``/``runas`` skeleton; never used by tests here."""
    def launch(self, broker_path, envelope_path, timeout_seconds, cancel_event: Event,
               *, expected_envelope_sha256: str | None = None) -> ElevationLaunchOutcome:
        if os.name != "nt":
            return ElevationLaunchOutcome(ElevationStatus.FAILED, "native_elevation_unavailable")
        expected_broker = resolve_broker_path()
        validate_broker_identity(
            Path(broker_path), expected_path=expected_broker,
            install_root=expected_broker.parent,
        )
        if cancel_event.is_set():
            return ElevationLaunchOutcome(ElevationStatus.CANCELLED, ElevationReason.CANCELLED_BEFORE_ELEVATION.value)
        # Only fixed option names and a local envelope path cross the native
        # boundary. No script, command line, secret, or user-controlled verb is
        # passed to an elevated process.
        result_path = BrokerEntryPoint.result_path_for_envelope(Path(envelope_path))
        parameters = f'--envelope "{Path(envelope_path)}"'
        if expected_envelope_sha256:
            parameters += f" --envelope-sha256 {expected_envelope_sha256}"
        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_SHOWNORMAL = 1

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                        ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                        ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
                        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                        ("hProcess", wintypes.HANDLE)]

        info = SHELLEXECUTEINFO(ctypes.sizeof(SHELLEXECUTEINFO), SEE_MASK_NOCLOSEPROCESS,
                                None, "runas", str(Path(broker_path)), parameters,
                                str(Path(broker_path).parent), SW_SHOWNORMAL, None, None,
                                None, None, 0, None, None)
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.GetLastError()
            if error in (1223,):  # ERROR_CANCELLED: user declined UAC.
                return ElevationLaunchOutcome(ElevationStatus.CANCELLED, ElevationReason.UAC_CANCELLED.value)
            return ElevationLaunchOutcome(ElevationStatus.FAILED, ElevationReason.BROKER_FAILED.value)
        deadline = time.monotonic() + timeout_seconds
        try:
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    return ElevationLaunchOutcome(ElevationStatus.CANCELLED, "broker_cancel_requested")
                if ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 50) == 0:
                    code = wintypes.DWORD()
                    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
                    result = None
                    try:
                        result = _read_result(result_path)
                    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                        pass
                    return ElevationLaunchOutcome(ElevationStatus.SUCCEEDED if code.value == 0 else ElevationStatus.FAILED,
                                                  "ok" if code.value == 0 else ElevationReason.BROKER_FAILED.value,
                                                  result=result, exit_code=code.value)
            return ElevationLaunchOutcome(ElevationStatus.TIMED_OUT, ElevationReason.BROKER_TIMEOUT.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
