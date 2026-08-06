from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from threading import Event

from .ai_client import AIClient, AIClientError

class AIWorker(QObject):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str, str)
    search_started = Signal()
    search_completed = Signal(dict)

    def __init__(self, history: list[dict[str, str]]):
        super().__init__(); self.history = history; self.cancel_token = Event()

    def cancel(self):
        self.cancel_token.set()

    @Slot()
    def run(self):
        try:
            text = AIClient().stream_with_tools(self.history, self.delta.emit, self.search_started.emit, self.search_completed.emit, self.cancel_token)
            if not text.strip(): raise AIClientError("empty", "AIから空の応答が返されました。")
            self.finished.emit(text)
        except AIClientError as exc:
            self.failed.emit(exc.kind, str(exc))
