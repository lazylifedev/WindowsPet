from pathlib import Path
import sys

def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]

def assets_root() -> Path:
    return application_root() / "assets" / "animations"
