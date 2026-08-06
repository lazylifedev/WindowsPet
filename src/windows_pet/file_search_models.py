from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass(frozen=True)
class SearchRoot:
    id: str
    alias: str
    path: str
    enabled: bool = True

@dataclass(frozen=True)
class SearchRequest:
    query: str
    root_ids: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    modified_after: datetime | None = None
    modified_before: datetime | None = None
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    include_directories: bool = False
    max_results: int = 50

@dataclass(frozen=True)
class SearchResult:
    result_id: str
    name: str
    root_alias: str
    relative_parent: str
    extension: str
    modified_at: datetime
    size_bytes: int
    full_path: str

