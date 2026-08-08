from __future__ import annotations

from typing import Protocol

from .models import ProactiveState


class ProactiveRepository(Protocol):
    available: bool

    def load_state(self) -> ProactiveState: ...
    def save_state(self, state: ProactiveState) -> ProactiveState: ...
