from pathlib import Path
import sys
from PySide6.QtCore import QStandardPaths

PROJECT_ROOT = Path(__file__).resolve().parents[2]

def resource_path(relative_path: str | Path) -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if not meipass:
            raise RuntimeError("PyInstaller実行ですが sys._MEIPASS が存在しません")
        base_path = Path(meipass)
    else:
        base_path = PROJECT_ROOT
    return base_path / Path(relative_path)

def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return resource_path("")
    return PROJECT_ROOT


def runtime_data_root() -> Path:
    """Return a persistent writable root for frozen runtime diagnostics."""
    if not getattr(sys, "frozen", False):
        return application_root()
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    return Path(location) if location else application_root()

def assets_root() -> Path:
    return resource_path("assets/animations")


def character_data_root() -> Path:
    """Return the per-user character data root; never use the install directory."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if not location:
        raise RuntimeError("character data location unavailable")
    return Path(location) / "characters"


def character_working_root(data_root: Path | None = None) -> Path:
    """Return the editor working package location (inject ``data_root`` in tests)."""
    return (Path(data_root) if data_root is not None else character_data_root()) / "working"


def character_installed_root(data_root: Path | None = None) -> Path:
    return (Path(data_root) if data_root is not None else character_data_root()) / "installed"


def character_selection_path(data_root: Path | None = None) -> Path:
    return (Path(data_root) if data_root is not None else character_data_root()) / "selection.json"
