from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryCommand:
    action: str
    category: str | None = None
    key: str | None = None
    value: str | None = None
    protected: bool = False


_REMEMBER = re.compile(r"^(?P<prefix>覚えておいて|忘れないで|記憶しておいて|remember)\s*(?:(?P<category>[a-z_]+)\s+)?(?P<key>[^=＝:：\s]+)\s*[=＝:：]\s*(?P<value>.+)$", re.I)
_FORGET = re.compile(r"^(?:忘れて|この記憶を消して|forget)\s*(?:[:：]\s*)?(?P<key>[^\s]+)$", re.I)


def parse_memory_command(text: str) -> MemoryCommand | None:
    value = str(text or "").strip()
    match = _REMEMBER.match(value)
    if match:
        return MemoryCommand("remember", match.group("category") or "fact", match.group("key"), match.group("value"), match.group("prefix") in {"覚えておいて", "忘れないで"})
    match = _FORGET.match(value)
    return MemoryCommand("forget", key=match.group("key")) if match else None
