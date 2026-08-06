from PySide6.QtCore import QObject, Signal, Slot
from .local_inspection_service import LocalInspectionService


class LocalInspectionWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service=None):
        super().__init__()
        self.service = service or LocalInspectionService()

    @Slot()
    def run(self):
        try: self.completed.emit(self.service.inspect())
        except Exception: self.failed.emit("inspection_failed")
