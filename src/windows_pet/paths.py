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


def local_skill_db_path() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "skills.sqlite3"
    return Path.home() / "WindowsPet" / "skills.sqlite3"


def personal_memory_db_path() -> Path:
    """Return the local-only Personal Memory database path."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "personal_memory.sqlite3"
    return Path.home() / "WindowsPet" / "personal_memory.sqlite3"


def habit_memory_db_path() -> Path:
    """Return the separate local-only Habit Memory database path."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "habit_memory.sqlite3"
    return Path.home() / "WindowsPet" / "habit_memory.sqlite3"


def proactive_state_db_path() -> Path:
    """Return local proactive decision state; speech bodies are not stored."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "proactive_state.sqlite3"
    return Path.home() / "WindowsPet" / "proactive_state.sqlite3"


def personality_db_path() -> Path:
    """Return the local relationship and speech preference database path."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "personality.sqlite3"
    return Path.home() / "WindowsPet" / "personality.sqlite3"


def shared_knowledge_cache_path() -> Path:
    """Return the local shared-knowledge cache path; no transport is implied."""
    location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if location:
        return Path(location) / "shared_knowledge.sqlite3"
    return Path.home() / "WindowsPet" / "shared_knowledge.sqlite3"

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
