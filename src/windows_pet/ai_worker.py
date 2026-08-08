from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from threading import Event

from .ai_client import AIClient, AIClientError, APPLICATION_LAUNCH_HANDOFF

class AIWorker(QObject):
    delta = Signal(str)
    finished = Signal(str)
    failed = Signal(str, str)
    search_started = Signal()
    search_completed = Signal(dict)
    application_launch_requested = Signal(object)
    process_stop_requested = Signal(object)
    service_restart_requested = Signal(object)
    file_rename_requested = Signal(object)
    application_launch_handed_off = Signal()
    service_restart_handed_off = Signal()
    file_rename_handed_off = Signal()
    powershell_started = Signal(str)
    powershell_completed = Signal(dict)

    def __init__(self, history: list[dict[str, str]], audit=None, current_file_context=None, personality_context=None):
        super().__init__(); self.history = history; self.cancel_token = Event(); self.audit = audit; self.current_file_context = current_file_context; self.personality_context = personality_context

    def cancel(self):
        self.cancel_token.set()

    @Slot()
    def run(self):
        try:
            from .powershell_read_runner import PowerShellReadRunner
            text = AIClient(inspection_runner=PowerShellReadRunner(audit=self.audit), current_file_context=self.current_file_context, personality_context=self.personality_context).stream_with_tools(self.history, self.delta.emit, self.search_started.emit, self.search_completed.emit, self.cancel_token, self.application_launch_requested.emit, self.powershell_started.emit, self.powershell_completed.emit, self.process_stop_requested.emit, self.service_restart_requested.emit, self.file_rename_requested.emit)
            if text is APPLICATION_LAUNCH_HANDOFF:
                self.application_launch_handed_off.emit()
                self.service_restart_handed_off.emit()
                self.file_rename_handed_off.emit()
                return
            if not text.strip(): raise AIClientError("empty", "AIから空の応答が返されました。")
            self.finished.emit(text)
        except AIClientError as exc:
            self.failed.emit(exc.kind, str(exc))
