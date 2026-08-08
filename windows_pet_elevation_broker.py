"""PyInstaller entry point for the short-lived elevation broker."""
from __future__ import annotations

import argparse
from pathlib import Path

from windows_pet.elevation import BrokerEntryPoint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--envelope-sha256", default=None)
    parser.add_argument("--result", required=False)
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        return 2
    result = BrokerEntryPoint().run(
        Path(args.envelope), expected_envelope_sha256=args.envelope_sha256
    )
    if args.result:
        BrokerEntryPoint.write_result(result, Path(args.result))
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
