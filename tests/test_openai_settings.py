from types import SimpleNamespace
import json
import pytest

from windows_pet.ai_client import AIClient, AIClientError
from windows_pet.openai_credentials import delete_api_key, get_api_key, has_environment_key, save_api_key

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
