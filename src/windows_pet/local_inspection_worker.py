from PySide6.QtCore import QObject, Signal, Slot
from .local_inspection_service import LocalInspectionService
from .cancellation import CancellationToken


class LocalInspectionWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service=None, token=None):
        super().__init__()
        self.service = service or LocalInspectionService()
        self.token = token or CancellationToken()

    @Slot()
    def run(self):
        try:
            result = self.service.inspect(self.token)
            if not self.token.is_cancelled:
                self.completed.emit(result)
        except Exception:
            if not self.token.is_cancelled:
                self.failed.emit("inspection_failed")
