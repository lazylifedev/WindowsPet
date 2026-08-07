from __future__ import annotations

import ntpath
import os
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
            candidates += self._install_location_candidates(snapshot, request.application_name, token)
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

    @staticmethod
    def _is_program_files_location(location: str) -> bool:
        """Only inspect installed-app folders rooted in Program Files, never arbitrary paths."""
        if not location or location.startswith(("\\\\", "\\\\?\\", "\\\\.")):
            return False
        normalized = ntpath.normcase(ntpath.normpath(location))
        roots = [os.environ.get("ProgramFiles", r"C:\\Program Files"),
                 os.environ.get("ProgramW6432", r"C:\\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")]
        return any(normalized == ntpath.normcase(ntpath.normpath(root)) or
                   normalized.startswith(ntpath.normcase(ntpath.normpath(root)) + "\\") for root in roots if root)

    def _install_location_candidates(self, snapshot, query: str, token) -> list[AppCandidate]:
        """Find plausible .exe files in a small, cancellation-aware installed-app scope."""
        found: list[AppCandidate] = []
        query_key = query.casefold()
        inspected_entries = 0
        for app in snapshot.installed_apps:
            if token.is_cancelled:
                return []
            location = app.install_location
            if not self._is_program_files_location(location):
                continue
            try:
                root = Path(location)
                if root.is_symlink() or not root.is_dir():
                    continue
                for current, directories, files in os.walk(root, followlinks=False):
                    if token.is_cancelled:
                        return []
                    relative = Path(current).relative_to(root)
                    if len(relative.parts) >= 3:
                        directories[:] = []
                    directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
                    for name in files:
                        if token.is_cancelled:
                            return []
                        inspected_entries += 1
                        if inspected_entries > 200:
                            break
                        path = Path(current) / name
                        if path.is_symlink() or path.suffix.casefold() != ".exe":
                            continue
                        name_key = path.stem.casefold()
                        if query_key not in name_key and query_key not in app.display_name.casefold():
                            continue
                        found.append(AppCandidate(app.display_name, app.version, app.publisher, "install_location",
                                                  path.name, str(path), True, location))
                    if inspected_entries > 200:
                        break
                if inspected_entries > 200:
                    break
            except (OSError, ValueError):
                continue
        return found


class ApplicationCandidateResolverWorker(QObject):
    finished = Signal(object)
    def __init__(self, resolver, request, token):
        super().__init__(); self.resolver = resolver; self.request = request; self.token = token
    @Slot()
    def run(self):
        try:
            outcome = self.resolver.resolve(self.request, self.token)
        except Exception:
            outcome = CandidateResolutionOutcome(CandidateResolutionStatus.FAILED)
        self.finished.emit(outcome)
