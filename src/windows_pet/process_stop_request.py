from __future__ import annotations
import json
from dataclasses import dataclass

@dataclass(frozen=True)
class ProcessStopRequest:
    process_id: int
    expected_process_name: str

def parse_process_stop_request(arguments: str | dict) -> ProcessStopRequest:
    data = json.loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(data, dict) or set(data) != {"process_id", "expected_process_name"}: raise ValueError("invalid_stop_request")
    pid, name = data["process_id"], data["expected_process_name"]
    if type(pid) is not int or not 1 <= pid <= 2147483647 or not isinstance(name, str) or not name.strip() or len(name) > 260 or any(ord(c) < 32 for c in name): raise ValueError("invalid_stop_request")
    return ProcessStopRequest(pid, name.strip())
