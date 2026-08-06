from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from .local_inspection_models import (AppCandidate, InspectionErrorCode, InspectionSnapshot,
    PartialError, PathInspection, SystemInfo, WingetStatus)


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip().strip('"'))


class LocalInspectionService:
    def __init__(self, env: dict[str, str] | None = None, exists: Callable[[str], bool] | None = None,
                 now: Callable[[], object] | None = None):
        self.env = env if env is not None else dict(os.environ)
        self.exists = exists or os.path.isdir

    def inspect(self) -> InspectionSnapshot:
        errors: list[PartialError] = []
        system = self._system_info()
        path = self._path_info()
        app_paths = self._registry_apps("app_paths", errors)
        installed = self._registry_apps("installed_apps", errors)
        start = self._start_menu(errors)
        winget = self._winget(errors)
        return InspectionSnapshot(system, path, winget, app_paths, start, installed, partial_errors=errors)

    def _system_info(self) -> SystemInfo:
        is_admin = False
        if sys.platform == "win32":
            try: is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except OSError: pass
        return SystemInfo(platform.system(), platform.release(), platform.version(), platform.machine(),
                          self.env.get("COMPUTERNAME", ""), self.env.get("USERNAME", ""), is_admin, platform.architecture()[0])

    def _path_info(self) -> PathInspection:
        unique: list[str] = []; seen: set[str] = set()
        for raw in self.env.get("PATH", "").split(os.pathsep):
            item = _norm(raw)
            key = os.path.normcase(os.path.normpath(item)) if item else ""
            if item and key not in seen: seen.add(key); unique.append(item)
        existing = sum(self.exists(item) for item in unique)
        return PathInspection(len(unique), existing, len(unique) - existing, tuple(unique))

    def _registry_apps(self, area: str, errors: list[PartialError]) -> list[AppCandidate]:
        if sys.platform != "win32": return []
        try:
            import winreg
            roots = [(winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")]
            if area == "installed_apps": sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
            else: sub = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
            results: list[AppCandidate] = []
            for root, source in roots:
                try:
                    with winreg.OpenKey(root, sub) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, name) as child:
                                display = winreg.QueryValueEx(child, "DisplayName")[0] if area == "installed_apps" else name
                                if not isinstance(display, str) or not display.strip(): continue
                                def val(n: str) -> str:
                                    try: return str(winreg.QueryValueEx(child, n)[0])
                                    except (OSError, TypeError): return ""
                                exe = val("") if area == "app_paths" else ""
                                results.append(AppCandidate(display.strip(), val("DisplayVersion"), val("Publisher"), source, name, exe, bool(exe) and Path(exe).exists()))
                except FileNotFoundError: continue
                except PermissionError: errors.append(PartialError(area, InspectionErrorCode.ACCESS_DENIED))
            return results
        except (ImportError, OSError):
            errors.append(PartialError(area, InspectionErrorCode.UNAVAILABLE)); return []

    def _start_menu(self, errors: list[PartialError]) -> list[AppCandidate]:
        folders = [self.env.get("APPDATA", "") + r"\Microsoft\Windows\Start Menu", self.env.get("PROGRAMDATA", "") + r"\Microsoft\Windows\Start Menu"]
        found: list[AppCandidate] = []
        for folder in folders:
            if not folder: continue
            try:
                for path in Path(folder).rglob("*"):
                    if path.is_symlink() or path.suffix.lower() not in (".lnk", ".url"): continue
                    found.append(AppCandidate(path.stem, source="start_menu", executable_name=path.name))
            except (OSError, PermissionError): errors.append(PartialError("start_menu", InspectionErrorCode.ACCESS_DENIED))
        return found

    def _winget(self, errors: list[PartialError]) -> WingetStatus:
        executable = shutil.which("winget")
        if not executable: return WingetStatus(False, error=InspectionErrorCode.NOT_FOUND)
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, shell=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            if result.returncode == 0: return WingetStatus(True, result.stdout.strip())
            return WingetStatus(False, error=InspectionErrorCode.UNAVAILABLE)
        except subprocess.TimeoutExpired: return WingetStatus(False, error=InspectionErrorCode.TIMEOUT)
        except OSError: return WingetStatus(False, error=InspectionErrorCode.UNAVAILABLE)

    @staticmethod
    def search(snapshot: InspectionSnapshot, query: str, limit: int = 25) -> list[AppCandidate]:
        q = _norm(query).casefold()
        if not q or limit <= 0: return []
        unique: dict[tuple[str, str, str], AppCandidate] = {}
        for candidate in snapshot.app_paths + snapshot.start_menu + snapshot.installed_apps:
            key = (_norm(candidate.display_name).casefold(), candidate.source, candidate.executable_name.casefold())
            unique.setdefault(key, candidate)
        ranked = []
        for candidate in unique.values():
            name = _norm(candidate.display_name).casefold()
            rank = 0 if name == q else 1 if name.startswith(q) else 2 if q in name else 99
            if rank < 99: ranked.append((rank, name, candidate))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[:limit]]
