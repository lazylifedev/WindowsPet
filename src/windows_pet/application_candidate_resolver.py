from __future__ import annotations

import ntpath
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .application_launch import ApplicationLaunchValidator
from .cancellation import CancellationToken
from .local_inspection_models import AppCandidate
from .local_inspection_service import LocalInspectionService


class CandidateResolutionStatus(str, Enum):
    SUCCESS = "success"; NOT_FOUND = "not_found"; CANCELLED = "cancelled"; FAILED = "failed"


@dataclass(frozen=True)
class CandidateResolutionOutcome:
    status: CandidateResolutionStatus
    candidates: tuple[AppCandidate, ...] = ()
    message: str = ""


class ApplicationCandidateResolver:
    def __init__(self, inspection_service=None, validator=None, snapshot=None):
        self.inspection_service = inspection_service or LocalInspectionService()
        self.validator = validator or ApplicationLaunchValidator()
        self.snapshot = snapshot

    def resolve(self, request, token=None) -> CandidateResolutionOutcome:
        token = token or CancellationToken()
        if token.is_cancelled: return CandidateResolutionOutcome(CandidateResolutionStatus.CANCELLED)
        if request.exact_path:
            candidate = AppCandidate(request.application_name, executable_name=ntpath.basename(request.exact_path), executable_path=request.exact_path, executable_exists=True, source="user_path")
            target, _ = self.validator.validate(candidate)
            return CandidateResolutionOutcome(CandidateResolutionStatus.SUCCESS, (candidate,)) if target else CandidateResolutionOutcome(CandidateResolutionStatus.NOT_FOUND)
        try:
            snapshot = self.snapshot or self.inspection_service.inspect(token)
            candidates = self.inspection_service.search(snapshot, request.application_name, limit=25)
            valid = []
            seen = set()
            for candidate in candidates:
                if token.is_cancelled: return CandidateResolutionOutcome(CandidateResolutionStatus.CANCELLED)
                target, _ = self.validator.validate(candidate)
                if target and target.canonical_path.casefold() not in seen:
                    seen.add(target.canonical_path.casefold()); valid.append(candidate)
            return CandidateResolutionOutcome(CandidateResolutionStatus.SUCCESS, tuple(valid)) if valid else CandidateResolutionOutcome(CandidateResolutionStatus.NOT_FOUND)
        except Exception:
            return CandidateResolutionOutcome(CandidateResolutionStatus.FAILED)


class ApplicationCandidateResolverWorker(QObject):
    finished = Signal(object)
    def __init__(self, resolver, request, token):
        super().__init__(); self.resolver = resolver; self.request = request; self.token = token
    @Slot()
    def run(self):
        self.finished.emit(self.resolver.resolve(self.request, self.token))
