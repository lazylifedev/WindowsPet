from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .ai_client import AIClient, AIClientError

class AIWorker(QObject):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self, history: list[dict[str, str]]):
        super().__init__(); self.history = history

    @Slot()
    def run(self):
        try:
            text = AIClient().stream(self.history, self.delta.emit)
            if not text.strip(): raise AIClientError("empty", "AIから空の応答が返されました。")
            self.finished.emit(text)
        except AIClientError as exc:
            self.failed.emit(exc.kind, str(exc))
