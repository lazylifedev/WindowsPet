from __future__ import annotations

import json
import subprocess
from pathlib import Path
from threading import Event

import pytest

from windows_pet.powershell_read_builder import build_read_plan
from windows_pet.powershell_read_models import WindowsInspectionArea, WindowsInspectionRequest, PowerShellReadStatus
from windows_pet.powershell_read_result import ResultValidationError, validate_result
from windows_pet.powershell_read_runner import PowerShellReadRunner
from windows_pet.tool_dispatcher import ToolDispatcher
from windows_pet.ai_client import AIClient
from windows_pet.audit_log import InMemoryAuditSink


def request(area="processes", query=None, maximum=10):
    return WindowsInspectionRequest(WindowsInspectionArea(area), query, maximum)


def test_tool_request_schema_and_parser_rejects_unsafe_values():
    parsed = ToolDispatcher.parse_windows_inspection({"area":"processes", "query":"note", "max_results":5})
    assert parsed.area is WindowsInspectionArea.PROCESSES
    for invalid in ({"area":"network","query":"x","max_results":1}, {"area":"other","query":None,"max_results":1}, {"area":"services","query":"x\0","max_results":1}, {"area":"services","query":"x\r\ny","max_results":1}, {"area":"services","query":"x\ty","max_results":1}, {"area":"services","query":"x\u007fy","max_results":1}, {"area":"services","query":None,"max_results":101}):
        with pytest.raises(ValueError): ToolDispatcher.parse_windows_inspection(invalid)


def test_inspection_tool_schema_is_strict_and_exposed():
    tool = next(tool for tool in AIClient.__new__(AIClient)._tools() if tool["name"] == "inspect_windows")
    assert tool["strict"] is True and tool["parameters"]["additionalProperties"] is False
    assert tool["parameters"]["required"] == ["area", "query", "max_results"]


def test_fake_responses_send_process_stop_tool_and_preserve_single_definition():
    class Responses:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return type("Response", (), {"output": [], "output_text": "ok"})()
    fake = type("Client", (), {"responses": Responses()})()
    AIClient(client=fake, api_key="test-key").stream_with_tools([], lambda _: None)
    tools = fake.responses.kwargs["tools"]
    stops = [tool for tool in tools if tool["name"] == "request_process_stop"]
    assert len(stops) == 1
    assert stops[0]["strict"] is True
    assert stops[0]["parameters"]["required"] == ["process_id", "expected_process_name"]


def test_builder_is_fixed_hashable_and_contains_no_mutating_constructs():
    plan = build_read_plan(request("services", "update", 50))
    assert plan == build_read_plan(request("services", "other", 1))
    assert len(plan.script_sha256) == 64 and "WINDOWSPET_PS_PARAMETERS" in plan.script
    forbidden = ("Set-", "Start-", "Stop-", "Restart-", "Invoke-Expression", "Add-Type", "Start-Process", "-ExecutionPolicy", "-EncodedCommand")
    assert not any(token in plan.script for token in forbidden)


class FakeProcess:
    def __init__(self, stdout, stderr=b"", code=0): self.stdout, self.stderr, self.returncode, self.terminated = stdout, stderr, code, False
    def communicate(self, stdin=None, timeout=None): self.stdin, self.timeout = stdin, timeout; return self.stdout, self.stderr
    def poll(self): return self.returncode
    def terminate(self): self.terminated = True


def powershell_root(tmp_path):
    executable = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    executable.parent.mkdir(parents=True); executable.write_bytes(b"x")
    return tmp_path, executable


def test_runner_uses_safe_argv_environment_and_returns_valid_output(tmp_path):
    root, executable = powershell_root(tmp_path); calls=[]
    output = json.dumps({"schemaVersion":1,"operation":"processes","items":[{"name":"notepad","pid":1,"cpuSeconds":None,"workingSetMb":2.5}]}).encode()
    def factory(*args, **kwargs): calls.append((args, kwargs)); return FakeProcess(output)
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=factory).execute(request())
    assert outcome.status is PowerShellReadStatus.SUCCESS and outcome.result["items"][0]["name"] == "notepad"
    args, kwargs = calls[0]; assert args[0][1:5] == ["-NoLogo","-NoProfile","-NonInteractive","-Command"] and args[0][5] == build_read_plan(request()).script and kwargs["shell"] is False
    assert json.loads(kwargs["env"]["WINDOWSPET_PS_PARAMETERS"]) == {"query":None,"maxResults":10} and kwargs["cwd"] == str(executable.parent)


def test_runner_rejects_bad_json_nonzero_and_pre_cancel(tmp_path):
    root, _ = powershell_root(tmp_path)
    runner = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: FakeProcess(b"not json"))
    assert runner.execute(request()).status is PowerShellReadStatus.INVALID_OUTPUT
    failed = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: FakeProcess(b"{}", code=1))
    assert failed.execute(request()).status is PowerShellReadStatus.FAILED
    cancelled = Event(); cancelled.set()
    assert runner.execute(request(), cancelled).status is PowerShellReadStatus.CANCELLED


def test_runner_builds_and_validates_its_own_plan_before_starting_a_process(tmp_path):
    root, _ = powershell_root(tmp_path); calls = []
    invalid = build_read_plan(request())
    invalid = invalid.__class__("services", invalid.script, invalid.script_sha256, invalid.timeout_seconds)
    outcome = PowerShellReadRunner(plan_factory=lambda _: invalid, windows_directory_resolver=lambda: root,
                                   process_factory=lambda *a, **k: calls.append((a, k))).execute(request())
    assert outcome.status is PowerShellReadStatus.INVALID_OUTPUT
    assert outcome.result_code == "invalid_plan" and calls == []


def test_result_validator_rejects_sensitive_or_unknown_shape():
    with pytest.raises(ValueError): validate_result({"schemaVersion":1,"operation":"network","items":[{"interfaceAlias":"x","status":"Up","ipv4Addresses":[],"defaultGateway":None,"guid":"secret"}]}, WindowsInspectionArea.NETWORK, 1)


def test_runner_accepts_utf8_with_or_without_bom_and_rejects_other_encodings(tmp_path):
    root, _ = powershell_root(tmp_path); valid = json.dumps({"schemaVersion":1,"operation":"processes","items":[]}).encode()
    for payload in (valid, b"\xef\xbb\xbf" + valid):
        assert PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: FakeProcess(payload)).execute(request()).status is PowerShellReadStatus.SUCCESS
    for payload in (b"\xff\xfe" + valid, "あ".encode("cp932"), b"prefix" + valid, valid + b"suffix"):
        assert PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: FakeProcess(payload)).execute(request()).status is PowerShellReadStatus.INVALID_OUTPUT


@pytest.mark.parametrize(("item", "code"), [({"name":"x","pid":"1","cpuSeconds":None,"workingSetMb":1}, "invalid_process_pid"), ({"name":"x","pid":1,"cpuSeconds":float("nan"),"workingSetMb":1}, "invalid_process_cpu"), ({"name":"x","pid":1,"cpuSeconds":float("inf"),"workingSetMb":1}, "invalid_process_cpu"), ({"name":"x","pid":1,"cpuSeconds":None,"workingSetMb":float("nan")}, "invalid_process_working_set"), ({"name":"x","pid":1,"cpuSeconds":None,"workingSetMb":float("inf")}, "invalid_process_working_set")])
def test_process_validation_codes(item, code):
    with pytest.raises(ResultValidationError, match=code): validate_result({"schemaVersion":1,"operation":"processes","items":[item]}, WindowsInspectionArea.PROCESSES, 1)


def test_invalid_output_audit_contains_only_fixed_code(tmp_path):
    root, _ = powershell_root(tmp_path); audit = InMemoryAuditSink()
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: FakeProcess(b"not-json"), audit=audit).execute(request())
    event = audit.events[-1]
    assert outcome.status is PowerShellReadStatus.INVALID_OUTPUT
    assert event.verification_result == "invalid_json" and "stdout" not in event.__dict__ and "stderr" not in event.__dict__


def test_ai_does_not_repeat_a_failed_inspection(monkeypatch):
    class Call:
        type = "function_call"; name = "inspect_windows"; arguments = '{"area":"processes","query":null,"max_results":5}'
        def __init__(self, call_id): self.call_id = call_id
    class Response:
        def __init__(self, call): self.output = [call]; self.output_text = ""
    class Responses:
        def __init__(self): self.calls = 0
        def create(self, **kwargs): self.calls += 1; return Response(Call(f"call-{self.calls}"))
    class Client:
        def __init__(self): self.responses = Responses()
    class Runner:
        def __init__(self): self.calls = 0
        def execute(self, *_):
            self.calls += 1
            from windows_pet.powershell_read_models import PowerShellReadOutcome
            return PowerShellReadOutcome(PowerShellReadStatus.INVALID_OUTPUT, result_code="invalid_output")
    runner = Runner(); client = Client()
    with pytest.raises(Exception, match="inspection_retry_blocked"):
        AIClient(client=client, api_key="test-key", inspection_runner=runner).stream_with_tools([], lambda _: None)
    assert runner.calls == 1 and client.responses.calls == 2


class CleanupProcess(FakeProcess):
    def __init__(self, *, cleanup_timeouts=0):
        super().__init__(b"{}")
        self.returncode, self.calls, self.killed = None, [], False
        self.cleanup_timeouts = cleanup_timeouts

    def communicate(self, stdin=None, timeout=None):
        self.calls.append((stdin, timeout))
        if stdin is not None:
            raise subprocess.TimeoutExpired("powershell", timeout)
        if self.cleanup_timeouts:
            self.cleanup_timeouts -= 1
            raise subprocess.TimeoutExpired("powershell", timeout)
        self.returncode = 0
        return self.stdout, self.stderr

    def terminate(self): self.terminated = True
    def kill(self): self.killed = True


def test_cancel_cleanup_communicate_is_bounded_and_does_not_touch_other_process(tmp_path):
    root, _ = powershell_root(tmp_path); cancel = Event(); process = CleanupProcess()
    class OtherProcess:
        def terminate(self): raise AssertionError("inspection target must not be terminated")
        def kill(self): raise AssertionError("inspection target must not be killed")
    other = OtherProcess()
    def factory(*args, **kwargs):
        cancel.set()
        return process
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=factory).execute(request(), cancel)
    assert outcome.result_code == "cancelled" and process.terminated and not process.killed
    assert process.calls == [(None, 2.0)]
    assert other is not None


def test_timeout_cleanup_communicate_is_bounded(tmp_path):
    root, _ = powershell_root(tmp_path); process = CleanupProcess(); ticks = iter((0.0, 11.0))
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: process,
                                   clock=lambda: next(ticks)).execute(request())
    assert outcome.result_code == "timeout" and process.terminated and process.calls == [(None, 2.0)]


def test_cleanup_kills_only_when_terminate_does_not_finish_and_is_bounded(tmp_path):
    root, _ = powershell_root(tmp_path); process = CleanupProcess(cleanup_timeouts=1); cancel = Event()
    def factory(*args, **kwargs): cancel.set(); return process
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=factory).execute(request(), cancel)
    assert outcome.result_code == "cancelled" and process.terminated and process.killed
    assert process.calls == [(None, 2.0), (None, 2.0)]


def test_cleanup_failure_returns_result_and_does_not_terminate_exited_process(tmp_path):
    root, _ = powershell_root(tmp_path); process = CleanupProcess(cleanup_timeouts=2); cancel = Event()
    def factory(*args, **kwargs): cancel.set(); return process
    outcome = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=factory).execute(request(), cancel)
    assert outcome.status is PowerShellReadStatus.FAILED and outcome.result_code == "child_cleanup_failed"
    exited = FakeProcess(b"{}", code=0)
    assert PowerShellReadRunner._terminate_owned_process(exited) and not exited.terminated and exited.timeout == 2.0


@pytest.mark.parametrize("component", ["System32", "WindowsPowerShell", "v1.0"])
def test_reparse_parent_prevents_popen(tmp_path, monkeypatch, component):
    root, _ = powershell_root(tmp_path); calls = []
    runner = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: calls.append(a))
    original = runner._is_link_or_reparse
    monkeypatch.setattr(runner, "_is_link_or_reparse", lambda path: path.name == component or original(path))
    assert runner.execute(request()).status is PowerShellReadStatus.NOT_AVAILABLE and calls == []


def test_resolved_executable_outside_windows_directory_prevents_popen(tmp_path, monkeypatch):
    root, executable = powershell_root(tmp_path); outside = tmp_path.parent / (tmp_path.name + "-outside") / "powershell.exe"; outside.parent.mkdir(); outside.write_bytes(b"x")
    executable.unlink(); executable.symlink_to(outside)
    calls = []
    runner = PowerShellReadRunner(windows_directory_resolver=lambda: root, process_factory=lambda *a, **k: calls.append(a))
    monkeypatch.setattr(runner, "_is_link_or_reparse", lambda path: False)
    assert runner.execute(request()).status is PowerShellReadStatus.NOT_AVAILABLE and calls == []
