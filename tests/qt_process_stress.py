"""Run the Qt lifecycle stress in fresh Python processes.

Usage: .venv\\Scripts\\python.exe tests\\qt_process_stress.py --rounds 3
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


TARGET = "tests/test_chat_service_restart_controller.py::test_service_restart_elevation_integration_stress_normal_100_cancel_50_shutdown_50"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    if args.rounds < 1 or args.rounds > 10:
        parser.error("--rounds must be between 1 and 10")
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    for number in range(1, args.rounds + 1):
        print(f"Qt process stress round {number}/{args.rounds}", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", TARGET], cwd=root, env=environment)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
