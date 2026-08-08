import json

import pytest

from windows_pet.file_rename_request import parse_file_rename_request


def test_explicit_user_path_is_required_and_new_name_is_structured():
    history = [{"role": "user", "content": r"C:\Users\tester\old.txt の名前を report.txt にして"}]
    request = parse_file_rename_request(json.dumps({"source_path": r"C:\Users\tester\old.txt", "new_name": "report.txt"}), history)
    assert request.source_path.endswith("old.txt") and request.new_name == "report.txt"


def test_current_file_context_can_resolve_this_file_but_ambiguous_context_cannot():
    history = [{"role": "user", "content": "このファイルをreport.txtにして"}]
    request = parse_file_rename_request({"source_path": None, "new_name": "report.txt"}, history, r"C:\Users\tester\old.txt")
    assert request.source_path.endswith("old.txt")
    with pytest.raises(ValueError, match="source_context_ambiguous"):
        parse_file_rename_request({"source_path": None, "new_name": "report.txt"}, history)


def test_model_cannot_invent_source_path():
    with pytest.raises(ValueError, match="source_not_in_user_context"):
        parse_file_rename_request({"source_path": r"C:\private\old.txt", "new_name": "report.txt"}, [{"role": "user", "content": "rename the selected file"}])


def test_ai_tool_handoff_is_not_execution():
    from windows_pet.ai_client import AIClient, APPLICATION_LAUNCH_HANDOFF

    class Call:
        type = "function_call"; call_id = "rename-1"; name = "request_file_rename"
        arguments = json.dumps({"source_path": r"C:\Users\tester\old.txt", "new_name": "report.txt"})

    class Response:
        output = [Call()]; output_text = ""

    class Responses:
        def create(self, **kwargs):
            return Response()

    class Client:
        responses = Responses()

    handed = []
    result = AIClient(client=Client(), api_key="test-key").stream_with_tools(
        [{"role": "user", "content": r"C:\Users\tester\old.txt を report.txt にして"}], lambda _: None,
        on_file_rename_requested=handed.append)
    assert result is APPLICATION_LAUNCH_HANDOFF and handed[0].new_name == "report.txt"
