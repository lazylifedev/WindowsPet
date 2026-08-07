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


def test_controller_launch_outcomes_have_distinct_terminal_messages():
    from windows_pet.chat_application_launch_controller import ChatApplicationLaunchController
    from windows_pet.application_launch import ApplicationLaunchOutcome, ApplicationLaunchStatus
    completed = []
    controller = ChatApplicationLaunchController(completed.append)
    for status in (ApplicationLaunchStatus.STARTED, ApplicationLaunchStatus.HANDED_OFF, ApplicationLaunchStatus.CANCELLED, ApplicationLaunchStatus.REJECTED, ApplicationLaunchStatus.FAILED):
        controller._busy = True
        controller._launched(ApplicationLaunchOutcome(status, status.value))
    assert len(completed) == 5
    assert len(set(completed)) == 5

def test_controller_exposes_all_worker_factories_for_fake_integration():
    import inspect
    from windows_pet.chat_application_launch_controller import ChatApplicationLaunchController
    params = inspect.signature(ChatApplicationLaunchController).parameters
    assert {"resolver_worker_factory", "selection_dialog_factory", "confirmation_dialog_factory", "proposal_factory", "confirmation_gate", "executor", "launch_worker_factory", "thread_factory", "token_factory"} <= set(params)


def test_controller_user_messages_are_japanese():
    from windows_pet.chat_application_launch_controller import ChatApplicationLaunchController
    from windows_pet.chat_bubble import InputBubble
    import inspect
    source = inspect.getsource(ChatApplicationLaunchController)
    assert "Preparing application launch confirmation." not in source
    assert "アプリの起動確認を準備しています。" in source
    assert "アプリを起動できませんでした。" in source
    assert "Cancelling…" not in inspect.getsource(InputBubble)


def test_handoff_defers_single_ready_notification_until_thread_done(qapp):
    from windows_pet.chat_bubble import InputBubble
    from windows_pet.application_launch_request import ApplicationLaunchRequest

    class Pet:
        def play(self, _): pass

    chat = InputBubble(Pet())
    request = ApplicationLaunchRequest("Example", None)
    received, statuses = [], []
    chat._show_response_status = statuses.append
    def start_controller(item):
        received.append((item, chat._local_action_pending))
        chat.show_local_action_status("アプリの起動確認を準備しています。")
    chat.application_launch_ready.connect(start_controller)
    chat._on_application_launch_requested(request)
    assert received == []
    chat._on_local_action_handed_off()
    assert chat._local_action_pending and received == []
    chat._thread_done()
    qapp.processEvents()
    assert received == [(request, True)]
    assert statuses == ["アプリの起動確認を準備しています。"]
    chat._emit_pending_application_launch_request()
    assert received == [(request, True)]
    chat.close()


def test_handoff_failure_discards_saved_request(qapp):
    from windows_pet.chat_bubble import InputBubble
    from windows_pet.application_launch_request import ApplicationLaunchRequest

    class Pet:
        def play(self, _): pass

    chat = InputBubble(Pet())
    chat._on_application_launch_requested(ApplicationLaunchRequest("Example", None))
    chat._on_failed("network", "failed")
    chat._on_local_action_handed_off()
    chat._thread_done()
    qapp.processEvents()
    assert chat._pending_application_launch_request is None
    chat.close()
