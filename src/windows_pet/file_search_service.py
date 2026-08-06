from __future__ import annotations

import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from threading import Event

from .file_search_models import SearchRequest, SearchResult
from .file_search_settings import SearchSettings

_SKIP = {'desktop.ini', 'thumbs.db'}
_SYSTEM = {'$recycle.bin', 'system volume information'}

def _tokens(value: str) -> list[str]:
    value = unicodedata.normalize('NFKC', value).casefold()
    return [x for x in re.split(r'\s+', value.strip()) if x]

class FileSearchService:
    def __init__(self, settings: SearchSettings): self.settings = settings

    def search(self, request: SearchRequest, cancel: Event | None = None) -> dict:
        if not 1 <= request.max_results <= 100: raise ValueError('max_results は1から100の範囲で指定してください。')
        query = _tokens(request.query); roots = self.settings.enabled_roots(request.root_ids)
        if not roots: raise ValueError('検索対象フォルダーが設定されていません。')
        extensions = {x.lower() if x.startswith('.') else '.' + x.lower() for x in request.extensions}
        started = time.monotonic(); found = []; directories = 0; permission_errors = 0; other_errors = 0
        for root in roots:
            root_path = Path(root.path)
            if not root_path.exists() or not root_path.is_dir(): other_errors += 1; continue
            stack = [root_path]
            while stack and len(found) < request.max_results:
                if cancel and cancel.is_set(): return {'status':'cancelled', 'results': found, 'total_found': len(found)}
                current = stack.pop()
                try: entries = os.scandir(current)
                except (PermissionError, FileNotFoundError): permission_errors += 1; continue
                with entries:
                    for entry in entries:
                        if entry.name.casefold() in _SKIP or entry.name.casefold() in _SYSTEM or entry.name.startswith('~$'): continue
                        try:
                            if entry.is_symlink(): continue
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if is_dir:
                                directories += 1
                                if not request.include_directories:
                                    stack.append(Path(entry.path)); continue
                                if query and not all(q in token for q in query for token in _tokens(entry.name)):
                                    stack.append(Path(entry.path)); continue
                                stat = entry.stat(follow_symlinks=False); modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
                                found.append(SearchResult(f'F{len(found)+1:03d}', entry.name, root.alias, str(Path(entry.path).parent.relative_to(root_path)), '', modified, 0, str(Path(entry.path))))
                                stack.append(Path(entry.path)); continue
                            stat = entry.stat(follow_symlinks=False); modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
                            if extensions and Path(entry.name).suffix.lower() not in extensions: continue
                            if request.modified_after and modified < request.modified_after: continue
                            if request.modified_before and modified > request.modified_before: continue
                            if request.min_size_bytes is not None and stat.st_size < request.min_size_bytes: continue
                            if request.max_size_bytes is not None and stat.st_size > request.max_size_bytes: continue
                            haystack = _tokens(entry.name) + _tokens(str(Path(entry.path).parent.relative_to(root_path)))
                            if query and not all(any(q in token for token in haystack) for q in query): continue
                            found.append(SearchResult(f'F{len(found)+1:03d}', entry.name, root.alias, str(Path(entry.path).parent.relative_to(root_path)), Path(entry.name).suffix.lower(), modified, stat.st_size, str(Path(entry.path))))
                            if len(found) >= request.max_results: break
                        except (PermissionError, FileNotFoundError): permission_errors += 1
                        except OSError: other_errors += 1
        return {'status':'success', 'total_found': len(found), 'results': found, 'directories_scanned': directories, 'permission_errors': permission_errors, 'other_errors': other_errors, 'elapsed_ms': round((time.monotonic()-started)*1000)}
