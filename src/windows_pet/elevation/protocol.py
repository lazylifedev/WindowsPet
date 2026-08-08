from __future__ import annotations

from threading import Event
from typing import Protocol

from .models import ElevationLaunchOutcome, ElevationResult


class ElevationLauncher(Protocol):
    def launch(self, broker_path, envelope_path, timeout_seconds, cancel_event: Event,
               *, expected_envelope_sha256: str | None = None) -> ElevationLaunchOutcome: ...


class ElevatedExecutor(Protocol):
    def execute(self, operation_id: str, parameters: dict, cancel_event: Event | None = None): ...


class ResultVerifier(Protocol):
    def verify(self, result: ElevationResult) -> str: ...
