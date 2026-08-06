from __future__ import annotations

from dataclasses import dataclass

MAX_TURNS = 20

@dataclass(frozen=True)
class Message:
    role: str
    content: str

class Conversation:
    def __init__(self, max_turns: int = MAX_TURNS):
        self.max_turns = max_turns
        self._messages: list[Message] = []

    def add_user(self, content: str) -> None:
        self._messages.append(Message("user", content))
        self._trim()

    def add_assistant(self, content: str) -> None:
        self._messages.append(Message("assistant", content))
        self._trim()

    def clear(self) -> None:
        self._messages.clear()

    def messages(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self._messages]

    def _trim(self) -> None:
        self._messages = self._messages[-self.max_turns * 2:]
