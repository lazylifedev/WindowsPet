from __future__ import annotations

import ntpath
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessInspectionSnapshot:
    processes: dict[int, str]
    truncated: bool = False


from .application_candidate_resolver import _TRUSTED_WINDOWS_APPLICATIONS, normalize_application_name

_TRUSTED_ALIASES = {
    normalize_application_name(alias): ntpath.splitext(relative_target[-1])[0].casefold()
    for _display, aliases, relative_target in _TRUSTED_WINDOWS_APPLICATIONS
    for alias in aliases
}


def normalize_process_query(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = _TRUSTED_ALIASES.get(normalized, normalized)
    return normalized


def resolve_process_candidate(query: str | None, items: list[dict], *, truncated: bool = False):
    snapshot = ProcessInspectionSnapshot(
        {item["pid"]: item["name"] for item in items
         if type(item.get("pid")) is int and item["pid"] > 0 and isinstance(item.get("name"), str)},
        truncated,
    )
    normalized = normalize_process_query(query)
    if not normalized:
        return snapshot, None, "inspection_process_not_found"
    matches = [(pid, name) for pid, name in snapshot.processes.items()
               if unicodedata.normalize("NFKC", name).casefold() == normalized]
    if len(matches) == 1:
        return snapshot, matches[0], "inspection_candidate_found"
    if len(matches) > 1:
        return snapshot, None, "inspection_candidate_ambiguous"
    if truncated:
        return snapshot, None, "inspection_result_truncated"
    return snapshot, None, "inspection_process_not_found"
