from pathlib import Path
import sys

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

def assets_root() -> Path:
    return resource_path("assets/animations")
