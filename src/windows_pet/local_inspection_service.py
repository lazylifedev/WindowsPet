from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Callable
from .cancellation import CancellationToken

from .local_inspection_models import (AppCandidate, InspectionErrorCode, InspectionSnapshot,
    PartialError, PathInspection, SystemInfo, WingetStatus)


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip().strip('"'))


def _application_key(value: str) -> str:
    value = _norm(value).strip("'").casefold()
    value = " ".join(value.split())
    return value[:-4] if value.endswith(".exe") else value


class LocalInspectionService:
    def __init__(self, env: dict[str, str] | None = None, exists: Callable[[str], bool] | None = None,
                 which: Callable[[str], str | None] | None = None, run_command=None):
        self.env = env if env is not None else dict(os.environ)
        self.exists = exists or os.path.isdir
        self.which = which or shutil.which
        self.run_command = run_command or subprocess.run

    def inspect(self, token: CancellationToken | None = None) -> InspectionSnapshot:
        token = token or CancellationToken()
        errors: list[PartialError] = []
        system = self._system_info()
        path = self._path_info(token)
        app_paths = self._registry_apps("app_paths", errors, token)
        installed = self._registry_apps("installed_apps", errors, token)
        start = self._start_menu(errors, token)
        path_candidates = self._path_candidates(token)
        winget = self._winget(errors, token)
        return InspectionSnapshot(system, path, winget, app_paths, start, installed,
                                   partial_errors=errors, path_candidates=path_candidates)

    def _system_info(self) -> SystemInfo:
        is_admin = False
        if sys.platform == "win32":
            try: is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except OSError: pass
        return SystemInfo(platform.system(), platform.release(), platform.version(), platform.machine(),
                          self.env.get("COMPUTERNAME", ""), self.env.get("USERNAME", ""), is_admin, platform.architecture()[0])

    def _path_info(self, token=None) -> PathInspection:
        unique: list[str] = []; seen: set[str] = set()
        for raw in self.env.get("PATH", "").split(os.pathsep):
            if token and token.is_cancelled: break
            item = _norm(raw)
            key = os.path.normcase(os.path.normpath(item)) if item else ""
            if item and key not in seen: seen.add(key); unique.append(item)
        existing = sum(self.exists(item) for item in unique)
        return PathInspection(len(unique), existing, len(unique) - existing, tuple(unique))

    def _registry_apps(self, area: str, errors: list[PartialError], token=None) -> list[AppCandidate]:
        if sys.platform != "win32": return []
        try:
            import winreg
            roots = [(winreg.HKEY_CURRENT_USER, "hkcu", 0), (winreg.HKEY_LOCAL_MACHINE, "hklm_64", winreg.KEY_WOW64_64KEY), (winreg.HKEY_LOCAL_MACHINE, "hklm_32", winreg.KEY_WOW64_32KEY)]
            if area == "installed_apps": sub = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
            else: sub = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
            results: list[AppCandidate] = []
            for root, view, flag in roots:
                if token and token.is_cancelled: break
                source = f"{area}_{view}"
                try:
                    with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | flag) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            if token and token.is_cancelled: break
                            name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, name) as child:
                                display = winreg.QueryValueEx(child, "DisplayName")[0] if area == "installed_apps" else name
                                if not isinstance(display, str) or not display.strip(): continue
                                def val(n: str) -> str:
                                    try: return str(winreg.QueryValueEx(child, n)[0])
                                    except (OSError, TypeError): return ""
                                exe = val("") if area == "app_paths" else ""
                                install = val("InstallLocation") if area == "installed_apps" else ""
                                results.append(AppCandidate(display.strip(), val("DisplayVersion"), val("Publisher"), source, name, exe, bool(exe) and Path(exe).exists(), install))
                except FileNotFoundError: continue
                except PermissionError: errors.append(PartialError(source, InspectionErrorCode.ACCESS_DENIED))
            return results
        except (ImportError, OSError):
            errors.append(PartialError(area, InspectionErrorCode.UNAVAILABLE)); return []

    def _start_menu(self, errors: list[PartialError], token=None) -> list[AppCandidate]:
        folders = [Path(value) / "Microsoft/Windows/Start Menu" for value in (self.env.get("APPDATA"), self.env.get("PROGRAMDATA")) if value and Path(value).is_dir()]
        found: list[AppCandidate] = []
        for folder in folders:
            if token and token.is_cancelled: break
            try:
                for path in Path(folder).rglob("*"):
                    if token and token.is_cancelled: break
                    if path.is_symlink() or path.suffix.lower() not in (".lnk", ".url"): continue
                    found.append(AppCandidate(path.stem, source="start_menu", executable_name=path.name))
            except (OSError, PermissionError): errors.append(PartialError("start_menu", InspectionErrorCode.ACCESS_DENIED))
        return found

    def _winget(self, errors: list[PartialError], token=None) -> WingetStatus:
        executable = self.which("winget")
        if not executable: return WingetStatus(False, error=InspectionErrorCode.NOT_FOUND)
        if token and token.is_cancelled: return WingetStatus(False, error=InspectionErrorCode.CANCELLED)
        try:
            result = self.run_command([executable, "--version"], capture_output=True, text=True, timeout=5, shell=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            if result.returncode == 0: return WingetStatus(True, result.stdout.strip())
            return WingetStatus(False, error=InspectionErrorCode.UNAVAILABLE)
        except subprocess.TimeoutExpired: return WingetStatus(False, error=InspectionErrorCode.TIMEOUT)
        except OSError: return WingetStatus(False, error=InspectionErrorCode.UNAVAILABLE)

    def _path_candidates(self, token=None) -> list[AppCandidate]:
        """Return safe executable-name candidates without executing anything."""
        if token and token.is_cancelled:
            return []
        suffixes = self.env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        return [AppCandidate("", source="path", executable_name=suffix.lower()) for suffix in suffixes
                if suffix.strip().lower() in (".com", ".exe", ".bat", ".cmd")]

    def search(self, snapshot: InspectionSnapshot, query: str, limit: int = 25) -> list[AppCandidate]:
        q = _application_key(query)
        if not q or limit <= 0: return []
        path_match = self.which(q)
        existing_names = {_application_key(item.display_name) for item in snapshot.app_paths + snapshot.start_menu + snapshot.installed_apps}
        if path_match and q not in existing_names:
            path_candidate = AppCandidate(q, source="path", executable_name=q,
                                          executable_path=path_match, executable_exists=True)
        else:
            path_candidate = None
        unique: dict[tuple[str, str, str], AppCandidate] = {}
        candidates = snapshot.app_paths + snapshot.start_menu + snapshot.installed_apps + snapshot.path_candidates
        if path_candidate is not None:
            candidates = candidates + [path_candidate]
        for candidate in candidates:
            if not candidate.display_name:
                continue
            key = (_norm(candidate.display_name).casefold(), candidate.source, candidate.executable_name.casefold())
            unique.setdefault(key, candidate)
        source_rank = {"app_paths_hklm_64": 0, "app_paths_hklm_32": 1, "app_paths_hkcu": 2,
                       "install_location": 3, "path": 4, "installed_apps_hkcu": 5,
                       "installed_apps_hklm_64": 6, "installed_apps_hklm_32": 7, "start_menu": 8}
        ranked = []
        for candidate in unique.values():
            name, stem = _application_key(candidate.display_name), _application_key(candidate.executable_name)
            rank = (1 if name == q else 2 if stem == q else 3 if name.startswith(q) else
                    4 if stem.startswith(q) else 5 if q in name else 6 if q in stem else 99)
            if rank < 99:
                path = str(candidate.executable_path)
                ranked.append((rank, source_rank.get(candidate.source, 99), name, path.casefold(), candidate))
        ranked.sort(key=lambda item: item[:-1])
        return [item[-1] for item in ranked[:limit]]
