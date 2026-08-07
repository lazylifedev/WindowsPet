from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import pytest

from windows_pet.powershell_read_builder import build_read_plan
from windows_pet.powershell_read_models import WindowsInspectionArea, WindowsInspectionRequest, PowerShellReadStatus
from windows_pet.powershell_read_result import validate_result
from windows_pet.powershell_read_runner import PowerShellReadRunner
from windows_pet.tool_dispatcher import ToolDispatcher
from windows_pet.ai_client import AIClient


def request(area="processes", query=None, maximum=10):
    return WindowsInspectionRequest(WindowsInspectionArea(area), query, maximum)


def test_tool_request_schema_and_parser_rejects_unsafe_values():
    parsed = ToolDispatcher.parse_windows_inspection({"area":"processes", "query":"note", "max_results":5})
    assert parsed.area is WindowsInspectionArea.PROCESSES
    for invalid in ({"area":"network","query":"x","max_results":1}, {"area":"other","query":None,"max_results":1}, {"area":"services","query":"x\0","max_results":1}, {"area":"services","query":None,"max_results":101}):
        with pytest.raises(ValueError): ToolDispatcher.parse_windows_inspection(invalid)


def test_inspection_tool_schema_is_strict_and_exposed():
    tool = next(tool for tool in AIClient.__new__(AIClient)._tools() if tool["name"] == "inspect_windows")
    assert tool["strict"] is True and tool["parameters"]["additionalProperties"] is False
    assert tool["parameters"]["required"] == ["area", "query", "max_results"]


def test_builder_is_fixed_hashable_and_contains_no_mutating_constructs():
    plan = build_read_plan(request("services", "update", 50))
    assert plan == build_read_plan(request("services", "other", 1))
    assert len(plan.script_sha256) == 64 and "WINDOWSPET_PS_PARAMETERS" in plan.script
    forbidden = ("Set-", "Start-", "Stop-", "Restart-", "Invoke-Expression", "Add-Type", "Start-Process", "-ExecutionPolicy", "-EncodedCommand")
    assert not any(token in plan.script for token in forbidden)


class FakeProcess:
    def __init__(self, stdout, stderr=b"", code=0): self.stdout, self.stderr, self.returncode, self.terminated = stdout, stderr, code, False
    def communicate(self, stdin=None, timeout=None): self.stdin, self.timeout = stdin, timeout; return self.stdout, self.stderr
    def terminate(self): self.terminated = True


def test_runner_uses_safe_argv_environment_and_returns_valid_output(tmp_path):
    executable = tmp_path / "powershell.exe"; executable.write_bytes(b"x"); calls=[]
    output = json.dumps({"schemaVersion":1,"operation":"processes","items":[{"name":"notepad","pid":1,"cpuSeconds":None,"workingSetMb":2.5}]}).encode()
    def factory(*args, **kwargs): calls.append((args, kwargs)); return FakeProcess(output)
    outcome = PowerShellReadRunner(executable_resolver=lambda: executable, process_factory=factory).execute(request(), build_read_plan(request()))
    assert outcome.status is PowerShellReadStatus.SUCCESS and outcome.result["items"][0]["name"] == "notepad"
    args, kwargs = calls[0]; assert args[0][1:] == ["-NoLogo","-NoProfile","-NonInteractive","-Command","-"] and kwargs["shell"] is False
    assert json.loads(kwargs["env"]["WINDOWSPET_PS_PARAMETERS"]) == {"query":None,"maxResults":10}


def test_runner_rejects_bad_json_nonzero_and_pre_cancel(tmp_path):
    executable = tmp_path / "powershell.exe"; executable.write_bytes(b"x")
    runner = PowerShellReadRunner(executable_resolver=lambda: executable, process_factory=lambda *a, **k: FakeProcess(b"not json"))
    assert runner.execute(request(), build_read_plan(request())).status is PowerShellReadStatus.INVALID_OUTPUT
    failed = PowerShellReadRunner(executable_resolver=lambda: executable, process_factory=lambda *a, **k: FakeProcess(b"{}", code=1))
    assert failed.execute(request(), build_read_plan(request())).status is PowerShellReadStatus.FAILED
    cancelled = Event(); cancelled.set()
    assert runner.execute(request(), build_read_plan(request()), cancelled).status is PowerShellReadStatus.CANCELLED


def test_result_validator_rejects_sensitive_or_unknown_shape():
    with pytest.raises(ValueError): validate_result({"schemaVersion":1,"operation":"network","items":[{"interfaceAlias":"x","status":"Up","ipv4Addresses":[],"defaultGateway":None,"guid":"secret"}]}, WindowsInspectionArea.NETWORK, 1)
