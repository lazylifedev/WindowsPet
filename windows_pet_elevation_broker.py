"""PyInstaller entry point for the short-lived elevation broker."""
from __future__ import annotations

import argparse
from pathlib import Path

from windows_pet.elevation import BrokerEntryPoint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--envelope-sha256", default=None)
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        return 2
    broker = BrokerEntryPoint.production()
    envelope_path = Path(args.envelope)
    result = broker.run(
        envelope_path, expected_envelope_sha256=args.envelope_sha256
    )
    BrokerEntryPoint.write_result(
        result, broker.result_path_for_envelope(envelope_path), root=broker.envelope_root
    )
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
