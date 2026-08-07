from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WindowsInspectionArea(str, Enum):
    PROCESSES = "processes"
    SERVICES = "services"
    NETWORK = "network"


@dataclass(frozen=True)
class WindowsInspectionRequest:
    area: WindowsInspectionArea
    query: str | None
    max_results: int


class PowerShellReadStatus(str, Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    NOT_AVAILABLE = "not_available"
    INVALID_OUTPUT = "invalid_output"
    FAILED = "failed"


@dataclass(frozen=True)
class PowerShellReadOutcome:
    status: PowerShellReadStatus
    result: dict | None = None
    result_code: str = ""


@dataclass(frozen=True)
class PowerShellReadPlan:
    operation: str
    script: str
    script_sha256: str
    timeout_seconds: float
    max_stdout_bytes: int = 256 * 1024
    max_stderr_bytes: int = 32 * 1024
