from __future__ import annotations

import ctypes
import ntpath
import os
import re
import unicodedata
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


def normalize_application_name(value: str) -> str:
    """Normalize an application name without interpreting natural-language commands."""
    text = unicodedata.normalize("NFKC", value).strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = re.sub(r"\s+", " ", text).casefold()
    return text[:-4] if text.endswith(".exe") else text


# This is intentionally code-owned.  Names from a model, a file, or the registry
# cannot add targets to this catalogue.
_TRUSTED_WINDOWS_APPLICATIONS = (
    ("メモ帳", ("メモ帳", "ノートパッド", "notepad", "notepad.exe"), ("System32", "notepad.exe")),
    ("電卓", ("電卓", "calculator", "calc", "calc.exe"), ("System32", "calc.exe")),
    ("ペイント", ("ペイント", "paint", "mspaint", "mspaint.exe"), ("System32", "mspaint.exe")),
    ("エクスプローラー", ("エクスプローラー", "ファイルエクスプローラー", "explorer", "explorer.exe"), ("explorer.exe",)),
)


def _windows_directory() -> str:
    if os.name != "nt":
        raise OSError("Windows directory is unavailable")
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("GetWindowsDirectoryW failed")
    return buffer.value


class ApplicationCandidateResolver:
    def __init__(self, inspection_service=None, validator=None, snapshot=None,
                 windows_directory_resolver=None, resolve_path=None, exists=None,
                 is_file=None, is_reparse_point=None):
        self.inspection_service = inspection_service or LocalInspectionService()
        self.validator = validator or ApplicationLaunchValidator()
        self.snapshot = snapshot
        self.windows_directory_resolver = windows_directory_resolver or _windows_directory
        self.resolve_path = resolve_path or (lambda p: Path(p).resolve(strict=True))
        self.exists = exists or (lambda p: Path(p).exists())
        self.is_file = is_file or (lambda p: Path(p).is_file())
        self.is_reparse_point = is_reparse_point or ApplicationLaunchValidator._is_reparse_point

    def resolve(self, request, token=None) -> CandidateResolutionOutcome:
        token = token or CancellationToken()
        if token.is_cancelled:
            return CandidateResolutionOutcome(CandidateResolutionStatus.CANCELLED)
        if request.exact_path:
            candidate = AppCandidate(request.application_name, executable_name=ntpath.basename(request.exact_path), executable_path=request.exact_path, executable_exists=True, source="user_path")
            target, _ = self.validator.validate(candidate)
            return CandidateResolutionOutcome(CandidateResolutionStatus.SUCCESS, (candidate,)) if target else CandidateResolutionOutcome(CandidateResolutionStatus.NOT_FOUND)
        try:
            trusted = self._trusted_windows_candidate(request.application_name)
            if trusted is not None:
                return CandidateResolutionOutcome(CandidateResolutionStatus.SUCCESS, (trusted,))
            snapshot = self.snapshot or self.inspection_service.inspect(token)
            candidates = self.inspection_service.search(snapshot, request.application_name, limit=25)
            candidates += self._install_location_candidates(snapshot, request.application_name, token)
            valid, seen = [], set()
            for candidate in candidates:
                if token.is_cancelled:
                    return CandidateResolutionOutcome(CandidateResolutionStatus.CANCELLED)
                target, _ = self.validator.validate(candidate)
                if target and target.canonical_path.casefold() not in seen:
                    seen.add(target.canonical_path.casefold())
                    valid.append((candidate, target.canonical_path))
            valid.sort(key=lambda item: self._generic_sort_key(item[0], item[1], request.application_name))
            resolved = tuple(candidate for candidate, _ in valid)
            return CandidateResolutionOutcome(CandidateResolutionStatus.SUCCESS, resolved) if resolved else CandidateResolutionOutcome(CandidateResolutionStatus.NOT_FOUND)
        except Exception:
            return CandidateResolutionOutcome(CandidateResolutionStatus.FAILED)

    @staticmethod
    def _generic_sort_key(candidate: AppCandidate, canonical_path: str, query: str) -> tuple:
        source_rank = {"app_paths_hklm_64": 0, "app_paths_hklm_32": 1, "app_paths_hkcu": 2,
                       "install_location": 3, "path": 4}
        query_key = normalize_application_name(query)
        display, stem = normalize_application_name(candidate.display_name), normalize_application_name(candidate.executable_name)
        match_rank = (1 if display == query_key else 2 if stem == query_key else 3 if display.startswith(query_key) else
                      4 if stem.startswith(query_key) else 5 if query_key in display else 6)
        return (match_rank, source_rank.get(candidate.source, 99), display, canonical_path.casefold())

    @staticmethod
    def _is_local_absolute(path: str) -> bool:
        normalized = path.replace("/", "\\") if path else ""
        drive, tail = ntpath.splitdrive(normalized)
        return bool(re.fullmatch(r"[A-Za-z]:", drive) and tail.startswith("\\") and
                    not normalized.startswith(("\\\\", "\\\\?\\", "\\\\.\\")))

    def _trusted_windows_candidate(self, application_name: str) -> AppCandidate | None:
        query = normalize_application_name(application_name)
        for display_name, aliases, relative_target in _TRUSTED_WINDOWS_APPLICATIONS:
            if query not in {normalize_application_name(alias) for alias in aliases}:
                continue
            return self._resolve_trusted_target(display_name, relative_target)
        return None

    def _resolve_trusted_target(self, display_name: str, relative_target: tuple[str, ...]) -> AppCandidate | None:
        """Resolve a fixed catalogue target while rejecting every reparse boundary."""
        try:
            raw_root = str(self.windows_directory_resolver())
            if not self._is_local_absolute(raw_root):
                return None
            root = self.resolve_path(raw_root)
            if self.is_reparse_point(raw_root) or self.is_reparse_point(root) or not self.exists(root):
                return None
            raw_target = ntpath.join(raw_root, *relative_target)
            # Check raw path components too: resolve() would otherwise hide a redirect.
            current_raw = raw_root
            for part in relative_target:
                current_raw = ntpath.join(current_raw, part)
                if self.is_reparse_point(current_raw):
                    return None
            target = self.resolve_path(raw_target)
            if self.is_reparse_point(target) or not self.exists(target) or not self.is_file(target):
                return None
            canonical_root = ntpath.normcase(ntpath.normpath(str(root)))
            canonical_target = ntpath.normcase(ntpath.normpath(str(target)))
            try:
                is_below_root = ntpath.commonpath((canonical_root, canonical_target)) == canonical_root
            except ValueError:
                is_below_root = False
            if not is_below_root:
                return None
            candidate = AppCandidate(display_name, source="trusted_windows_catalogue",
                                     executable_name=ntpath.basename(raw_target), executable_path=raw_target,
                                     executable_exists=True)
            return candidate if self.validator.validate(candidate)[0] else None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _is_program_files_location(location: str) -> bool:
        if not location or location.startswith(("\\\\", "\\\\?\\", "\\\\.")):
            return False
        normalized = ntpath.normcase(ntpath.normpath(location))
        roots = [os.environ.get("ProgramFiles", r"C:\\Program Files"), os.environ.get("ProgramW6432", r"C:\\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")]
        return any(normalized == ntpath.normcase(ntpath.normpath(root)) or normalized.startswith(ntpath.normcase(ntpath.normpath(root)) + "\\") for root in roots if root)

    def _install_location_candidates(self, snapshot, query: str, token) -> list[AppCandidate]:
        found: list[AppCandidate] = []
        query_key, inspected_entries = normalize_application_name(query), 0
        for app in snapshot.installed_apps:
            if token.is_cancelled:
                return []
            location = app.install_location
            if not self._is_program_files_location(location):
                continue
            try:
                root = Path(location)
                if self.validator.is_reparse_point(root) or not root.is_dir():
                    continue
                for current, directories, files in os.walk(root, followlinks=False):
                    if token.is_cancelled:
                        return []
                    if self.validator.is_reparse_point(current):
                        directories[:] = []; continue
                    relative = Path(current).relative_to(root)
                    if len(relative.parts) >= 3:
                        directories[:] = []
                    directories[:] = [name for name in directories if not self.validator.is_reparse_point(Path(current) / name)]
                    for name in files:
                        if token.is_cancelled:
                            return []
                        inspected_entries += 1
                        if inspected_entries > 200:
                            break
                        path = Path(current) / name
                        if self.validator.is_reparse_point(path) or path.suffix.casefold() != ".exe":
                            continue
                        if query_key not in normalize_application_name(path.stem) and query_key not in normalize_application_name(app.display_name):
                            continue
                        found.append(AppCandidate(app.display_name, app.version, app.publisher, "install_location", path.name, str(path), True, location))
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
