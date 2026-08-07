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


class _Call:
    type = "function_call"
    name = "request_application_launch"
    call_id = "launch-1"
    arguments = r'{"application_name":"Example","exact_path":"C:\\Apps\\Example.exe"}'

class _Response:
    output = [_Call()]
    output_text = ""

class _Responses:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Response()

class _FakeClient:
    def __init__(self):
        self.responses = _Responses()

def test_fake_responses_launch_handoff_never_completes_as_ai_text():
    from windows_pet.ai_client import APPLICATION_LAUNCH_HANDOFF
    fake = _FakeClient()
    client = AIClient(client=fake, api_key="test-key")
    requests = []
    result = client.stream_with_tools(
        [{"role": "user", "content": 'open "C:\\Apps\\Example.exe"'}],
        lambda text: (_ for _ in ()).throw(AssertionError("unexpected text")),
        on_application_launch_requested=requests.append,
    )
    assert result is APPLICATION_LAUNCH_HANDOFF
    assert len(requests) == 1
    assert requests[0].exact_path == "C:\\Apps\\Example.exe"
    tools = fake.responses.kwargs["tools"]
    assert any(tool["name"] == "request_application_launch" and tool["strict"] for tool in tools)

def test_confirmation_dialog_requires_keyword_only_clock_and_parent():
    import inspect
    from windows_pet.action_confirmation_dialog import ActionConfirmationDialog
    signature = inspect.signature(ActionConfirmationDialog)
    assert signature.parameters["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["parent"].kind is inspect.Parameter.KEYWORD_ONLY
