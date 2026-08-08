from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .models import ElevationClientOutcome, ElevationStatus


class ElevationExecutionThread(QThread):
    """QThread subclass with cooperative cancellation and explicit ownership."""
    result_ready = Signal(object)

    def __init__(self, client, request, broker_path, verifier, cancel_event=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.request = request
        self.broker_path = broker_path
        self.verifier = verifier
        self.cancel_event = cancel_event or Event()

    def run(self):
        try:
            result = self.client.execute(
                self.request, self.broker_path, verifier=self.verifier,
                cancel_event=self.cancel_event,
            )
        except Exception:
            result = ElevationClientOutcome(ElevationStatus.FAILED, "broker_failed")
        self.result_ready.emit(result)

    def request_cancel(self):
        self.cancel_event.set()


class ElevationQtController(QObject):
    """Keeps result delivery and QThread destruction as separate events."""
    completed = Signal(object)

    def __init__(self, client, parent=None, thread_factory=ElevationExecutionThread):
        super().__init__(parent)
        self.client = client
        self.thread_factory = thread_factory
        self.thread = None
        self._result = None

    def start(self, request, broker_path, verifier):
        # A finished QThread can still have queued ``finished``/deleteLater
        # delivery pending. Keep the strong reference until _finished clears
        # it; replacing it early can leave Qt callbacks targeting a released
        # wrapper during repeated controller creation.
        if self.thread is not None:
            return False
        thread = self.thread = self.thread_factory(
            self.client, request, broker_path, verifier, parent=self
        )
        thread.result_ready.connect(self._received)
        thread.finished.connect(self._finished)
        thread.start()
        return True

    @Slot(object)
    def _received(self, result):
        self._result = result
        self.completed.emit(result)

    @Slot()
    def _finished(self):
        thread = self.sender()
        if thread is self.thread:
            self.thread = None

    def cancel(self):
        if self.thread is not None:
            self.thread.request_cancel()

    def shutdown(self, timeout_ms=6000):
        self.cancel()
        thread = self.thread
        if thread is not None and thread.isRunning():
            thread.wait(timeout_ms)
        # Do not release a completed thread before its queued finished signal
        # has run. _finished is the single reference-release point.
