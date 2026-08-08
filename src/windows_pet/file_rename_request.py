from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FileRenameRequest:
    source_path: str | None
    new_name: str
    user_text: str = ""


def _user_absolute_paths(history) -> set[str]:
    paths: set[str] = set()
    for message in history:
        if message.get("role") != "user":
            continue
        content = str(message.get("content", ""))
        paths.update(value.strip('"\'') for value in re.findall(r"[A-Za-z]:\\[^\r\n\"']+", content))
    return paths


def parse_file_rename_request(arguments, history, current_file_context: str | None = None) -> FileRenameRequest:
    try:
        data = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_arguments") from exc
    if not isinstance(data, dict) or set(data) != {"source_path", "new_name"}:
        raise ValueError("invalid_arguments")
    source, new_name = data["source_path"], data["new_name"]
    if source is not None and (not isinstance(source, str) or not source.strip() or len(source) > 1024):
        raise ValueError("invalid_source_path")
    if not isinstance(new_name, str) or not new_name.strip() or len(new_name) > 255 or any(ord(char) < 32 for char in new_name):
        raise ValueError("invalid_new_name")
    if source is not None:
        allowed = {str(message.get("content", "")).casefold() for message in history if message.get("role") == "user"}
        if not any(source.casefold() in content for content in allowed):
            raise ValueError("source_not_in_user_context")
    elif current_file_context is None:
        raise ValueError("source_context_ambiguous")
    elif not isinstance(current_file_context, str) or not current_file_context.strip():
        raise ValueError("source_context_ambiguous")
    user_text = next((str(item.get("content", "")).strip() for item in reversed(history) if item.get("role") == "user"), "")
    resolved_source = source.strip().strip('"\'') if source is not None else current_file_context.strip()
    return FileRenameRequest(resolved_source, new_name.strip(), user_text)
