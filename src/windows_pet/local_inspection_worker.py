from PySide6.QtCore import QObject, Signal, Slot
from .local_inspection_service import LocalInspectionService
from .cancellation import CancellationToken
from .local_inspection_models import InspectionErrorCode, InspectionOutcome, InspectionStatus


class LocalInspectionWorker(QObject):
    finished = Signal(object)

    def __init__(self, service=None, token=None):
        super().__init__()
        self.service = service or LocalInspectionService()
        self.token = token or CancellationToken()

    @Slot()
    def run(self):
        try:
            result = self.service.inspect(self.token)
            if self.token.is_cancelled:
                outcome = InspectionOutcome(InspectionStatus.CANCELLED, error_code=InspectionErrorCode.CANCELLED)
            else:
                outcome = InspectionOutcome(InspectionStatus.SUCCESS, snapshot=result)
        except Exception:
            status = InspectionStatus.CANCELLED if self.token.is_cancelled else InspectionStatus.FAILED
            code = InspectionErrorCode.CANCELLED if self.token.is_cancelled else InspectionErrorCode.UNEXPECTED_ERROR
            outcome = InspectionOutcome(status, error_code=code)
        self.finished.emit(outcome)
