from __future__ import annotations

import json
import os
from pathlib import Path

from .file_search_models import SearchRoot

def settings_path() -> Path:
    return Path(os.getenv('LOCALAPPDATA', Path.home())) / 'WindowsPet' / 'settings.json'

class SearchSettings:
    def __init__(self, roots: list[SearchRoot] | None = None):
        self.roots = roots or []

    @classmethod
    def load(cls, path: Path | None = None) -> 'SearchSettings':
        path = path or settings_path()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            roots = [SearchRoot(str(x['id']), str(x['alias']), str(x['path']), bool(x.get('enabled', True))) for x in data.get('search_roots', [])]
            return cls(roots)
        except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, TypeError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path(); path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'search_roots': [r.__dict__ for r in self.roots]}, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)

    def enabled_roots(self, ids: tuple[str, ...] = ()) -> list[SearchRoot]:
        allowed = {r.id for r in self.roots if r.enabled}
        if ids and not set(ids) <= allowed:
            raise ValueError('検索対象に未登録または無効なルートが含まれています。')
        return [r for r in self.roots if r.enabled and (not ids or r.id in ids)]

