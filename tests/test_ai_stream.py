from threading import Event

import pytest

from windows_pet.ai_client import AIClient, AIClientError


class EventStreamClient:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    class Responses:
        def __init__(self, owner): self.owner = owner
        def create(self, **kwargs):
            self.owner.calls += 1
            return iter(self.owner.events)

    @property
    def responses(self): return self.Responses(self)


def make_client(monkeypatch, events):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    fake = EventStreamClient(events)
    return AIClient(fake), fake


def event(text):
    return type('Event', (), {'type': 'response.output_text.delta', 'delta': text})()


def test_stream_keeps_two_argument_compatibility(monkeypatch):
    client, fake = make_client(monkeypatch, [event('こんにちは')])
    deltas = []
    assert client.stream([], deltas.append) == 'こんにちは'
    assert deltas == ['こんにちは'] and fake.calls == 1


def test_stream_cancel_before_request(monkeypatch):
    client, fake = make_client(monkeypatch, [event('unused')])
    cancel = Event(); cancel.set(); deltas = []
    with pytest.raises(AIClientError) as exc:
        client.stream([], deltas.append, cancel=cancel)
    assert exc.value.kind == 'cancelled' and deltas == [] and fake.calls == 0


def test_stream_cancel_between_events(monkeypatch):
    client, _ = make_client(monkeypatch, [event('first'), event('second')])
    cancel = Event(); deltas = []
    def receive(text):
        deltas.append(text)
        cancel.set()
    with pytest.raises(AIClientError) as exc:
        client.stream([], receive, cancel=cancel)
    assert exc.value.kind == 'cancelled' and deltas == ['first']
