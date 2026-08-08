from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationLaunchRequest:
    application_name: str
    exact_path: str | None
    source: str = "chat"
    user_text: str = ""


def _normal(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().strip('"').strip("'").casefold()


def _user_paths(history) -> set[str]:
    paths = set()
    for message in history:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        for value in re.findall(r"(?:[A-Za-z]:\\[^\r\n\"']+?\.exe|\"[A-Za-z]:\\[^\"]+?\.exe\")", content, re.I):
            paths.add(_normal(value))
    return paths


def parse_application_launch_request(arguments, history) -> ApplicationLaunchRequest:
    try:
        data = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_arguments") from exc
    if not isinstance(data, dict) or set(data) != {"application_name", "exact_path"}:
        raise ValueError("invalid_arguments")
    name, exact_path = data["application_name"], data["exact_path"]
    if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(char) < 32 for char in name):
        raise ValueError("invalid_application_name")
    if exact_path is not None:
        if not isinstance(exact_path, str) or len(exact_path) > 1024 or "\0" in exact_path or any(ord(char) < 32 for char in exact_path):
            raise ValueError("invalid_exact_path")
        if _normal(exact_path) not in _user_paths(history):
            exact_path = None
    user_text = next((str(message.get("content", "")).strip() for message in reversed(history)
                      if message.get("role") == "user" and str(message.get("content", "")).strip()), "")
    return ApplicationLaunchRequest(name.strip(), exact_path, "chat", user_text)
