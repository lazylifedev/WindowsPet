from types import SimpleNamespace
import json
import pytest
import httpx
from openai import APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, InternalServerError, NotFoundError, PermissionDeniedError, RateLimitError

from windows_pet.ai_client import AIClient, AIClientError, classify_openai_error
from windows_pet.openai_credentials import delete_api_key, get_api_key, has_environment_key, has_stored_key, save_api_key

class FakeKeyring:
    def __init__(self): self.values = {}
    def get_password(self, service, user): return self.values.get((service, user))
    def set_password(self, service, user, value): self.values[(service, user)] = value
    def delete_password(self, service, user): self.values.pop((service, user), None)

def test_environment_key_precedes_credential_manager(monkeypatch):
    kr = FakeKeyring(); kr.set_password("WindowsPet", "openai_api_key", "credential")
    monkeypatch.setattr("windows_pet.openai_credentials._keyring", lambda: kr); monkeypatch.setenv("OPENAI_API_KEY", "environment")
    assert get_api_key() == "environment"

def test_credential_manager_read_save_delete(monkeypatch):
    kr = FakeKeyring(); monkeypatch.setattr("windows_pet.openai_credentials._keyring", lambda: kr); monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    save_api_key("dummy"); assert get_api_key() == "dummy"; delete_api_key(); assert get_api_key() is None

def test_has_stored_key_does_not_use_environment(monkeypatch):
    kr = FakeKeyring(); monkeypatch.setattr("windows_pet.openai_credentials._keyring", lambda: kr); monkeypatch.setenv("OPENAI_API_KEY", "environment")
    assert not has_stored_key(); kr.set_password("WindowsPet", "openai_api_key", "dummy"); assert has_stored_key()

def test_unconfigured_key_is_none(monkeypatch):
    monkeypatch.setattr("windows_pet.openai_credentials._keyring", lambda: None); monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_api_key() is None and not has_environment_key()

def test_keyring_exception_is_safe(monkeypatch):
    class Broken:
        def get_password(self, *_): raise RuntimeError("secret dummy")
    monkeypatch.setattr("windows_pet.openai_credentials._keyring", lambda: Broken()); monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_api_key() is None

def test_ai_client_accepts_explicit_key_without_provider(monkeypatch):
    fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: []))
    monkeypatch.setattr("windows_pet.ai_client.get_api_key", lambda: (_ for _ in ()).throw(AssertionError()))
    assert AIClient(fake, api_key="dummy").client is fake

def test_ai_client_uses_provider_and_missing_key(monkeypatch):
    fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: []))
    monkeypatch.setattr("windows_pet.ai_client.get_api_key", lambda: "dummy")
    assert AIClient(fake).client is fake
    monkeypatch.setattr("windows_pet.ai_client.get_api_key", lambda: None)
    with pytest.raises(AIClientError, match="APIキー") as exc: AIClient(fake)
    assert exc.value.kind == "missing_key"

def test_settings_json_never_contains_key(tmp_path):
    settings = tmp_path / "settings.json"; settings.write_text(json.dumps({"position": {"x": 1}}), encoding="utf-8")
    assert "dummy" not in settings.read_text(encoding="utf-8")

def test_ai_request_is_store_false_and_no_tools_for_connection(monkeypatch):
    calls = []
    class Responses:
        def create(self, **kwargs): calls.append(kwargs); return SimpleNamespace(output_text="OK", output=[])
    monkeypatch.setattr("windows_pet.openai_settings_window.OpenAI", lambda **kwargs: SimpleNamespace(responses=Responses()))
    from windows_pet.openai_settings_window import _CheckWorker
    worker = _CheckWorker("dummy"); worker.run()
    assert calls and calls[0]["store"] is False and "tools" not in calls[0]

def test_ai_client_request_does_not_include_api_key(monkeypatch):
    calls = []
    class Responses:
        def create(self, **kwargs): calls.append(kwargs); return []
    client = AIClient(SimpleNamespace(responses=Responses()), api_key="dummy")
    client.stream([], lambda _: None)
    payload = repr(calls[0]); assert "dummy" not in payload

def _sdk_error(error_type, status, body=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    if error_type in (APITimeoutError,): return error_type(httpx.Request("POST", "https://api.openai.com"))
    if error_type is APIConnectionError: return error_type(request=httpx.Request("POST", "https://api.openai.com"))
    return error_type("test", response=response, body=body)

@pytest.mark.parametrize("error_type,status,body,kind,text", [
    (AuthenticationError, 401, None, "auth", "APIキー"),
    (PermissionDeniedError, 403, None, "permission", "権限"),
    (RateLimitError, 429, {"code": "insufficient_quota"}, "quota", "利用上限"),
    (RateLimitError, 429, {"error": {"code": "rate_limit_exceeded"}}, "rate_limit", "集中"),
    (APITimeoutError, None, None, "timeout", "時間内"),
    (APIConnectionError, None, None, "network", "ネットワーク"),
    (BadRequestError, 400, None, "bad_request", "リクエスト"),
    (NotFoundError, 404, None, "model", "モデル"),
    (InternalServerError, 500, None, "server", "障害"),
])
def test_openai_sdk_error_classification(error_type, status, body, kind, text):
    error = classify_openai_error(_sdk_error(error_type, status, body))
    assert error.kind == kind and text in str(error)

def test_unknown_error_classification_is_safe():
    error = classify_openai_error(RuntimeError("private response details"))
    assert error.kind == "unknown" and "private response" not in str(error)
