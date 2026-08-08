from __future__ import annotations

import re
from dataclasses import dataclass


MAX_KEY_LENGTH = 120
MAX_VALUE_LENGTH = 2000

_SECRET_MARKERS = (
    "api key", "api_key", "apikey", "access token", "access_token", "bearer ",
    "password", "passwd", "credential", "cookie", "private key", "private_key",
    "secret", "token=", "authorization:",
)
_CONTENT_MARKERS = (
    "raw conversation", "conversation全文", "stdout", "stderr", "screenshot",
    "スクリーンショット", "画像", "full document", "全文", "machine log", "machine log",
)
_TOKEN_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)", re.I)


@dataclass(frozen=True)
class PrivacyDecision:
    accepted: bool
    reason: str = ""


def validate_memory_input(key: str, value: str) -> PrivacyDecision:
    key_text, value_text = str(key).strip(), str(value).strip()
    if not key_text:
        return PrivacyDecision(False, "memory key is required")
    if not value_text:
        return PrivacyDecision(False, "memory value is required")
    if len(key_text) > MAX_KEY_LENGTH:
        return PrivacyDecision(False, "memory key is too large")
    if len(value_text) > MAX_VALUE_LENGTH:
        return PrivacyDecision(False, "memory value is too large")
    combined = f"{key_text} {value_text}".casefold()
    if any(marker in combined for marker in _SECRET_MARKERS) or _TOKEN_PATTERN.search(value_text):
        return PrivacyDecision(False, "secret-like values are not stored")
    if any(marker in combined for marker in _CONTENT_MARKERS):
        return PrivacyDecision(False, "raw content and logs are not stored")
    if value_text.count("\n") > 40:
        return PrivacyDecision(False, "large multi-line content is not stored")
    return PrivacyDecision(True)
