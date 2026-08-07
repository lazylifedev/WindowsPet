from __future__ import annotations

from windows_pet.ai_client import AIClient
from windows_pet.application_launch_request import parse_application_launch_request


def test_launch_tool_schema_is_strict_and_nullable():
    tool = AIClient.__new__(AIClient)._launch_tool()
    assert tool["strict"] is True and tool["parameters"]["additionalProperties"] is False
    assert tool["parameters"]["required"] == ["application_name", "exact_path"]
    assert tool["parameters"]["properties"]["exact_path"]["type"] == ["string", "null"]


def test_user_supplied_exact_path_is_accepted():
    request = parse_application_launch_request({"application_name": "Example", "exact_path": 'C:\\Apps\\Example.exe'}, [{"role": "user", "content": 'open "C:\\Apps\\Example.exe"'}])
    assert request.exact_path == 'C:\\Apps\\Example.exe'


def test_assistant_only_path_is_not_accepted():
    request = parse_application_launch_request({"application_name": "Example", "exact_path": 'C:\\Apps\\Example.exe'}, [{"role": "assistant", "content": 'C:\\Apps\\Example.exe'}])
    assert request.exact_path is None


def test_launch_request_rejects_extra_keys_and_controls():
    history = [{"role": "user", "content": 'C:\\Apps\\Example.exe'}]
    for data in ({"application_name": "Example", "exact_path": None, "extra": 1}, {"application_name": "bad\nname", "exact_path": None}, {"application_name": "Example", "exact_path": "C:\\bad\0.exe"}):
        try: parse_application_launch_request(data, history)
        except ValueError: pass
        else: assert False
