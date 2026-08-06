from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .file_search_models import SearchResult

@dataclass
class SearchSession:
    search_id: str
    created_at: datetime
    query_summary: str
    root_ids: tuple[str, ...]
    total_found: int
    elapsed_ms: int
    truncated: bool
    results: list[SearchResult]

class SearchResultStore:
    def __init__(self, max_sessions=5): self.max_sessions=max_sessions; self._sessions=[]; self._next=1
    def add(self, query_summary, root_ids, result):
        sid=f'S{self._next:04d}'; self._next+=1
        rows=[]
        for i, row in enumerate(result.get('results', []), 1):
            if isinstance(row, dict):
                from datetime import datetime
                row = SearchResult(row.get('result_id', f'F{i:03d}'), row['name'], row['root_alias'], row.get('relative_parent',''), row.get('extension',''), datetime.fromisoformat(row['modified_at']) if isinstance(row.get('modified_at'), str) else row['modified_at'], row.get('size_bytes',0), row['full_path'])
            rows.append(row)
        rows=[SearchResult(f'{sid}-F{i:03d}', r.name,r.root_alias,r.relative_parent,r.extension,r.modified_at,r.size_bytes,r.full_path) for i,r in enumerate(rows,1)]
        session=SearchSession(sid, datetime.now().astimezone(), query_summary, tuple(root_ids), result.get('total_found',len(rows)), result.get('elapsed_ms',0), result.get('truncated',False), rows)
        self._sessions.insert(0,session); del self._sessions[self.max_sessions:]; return session
    def latest(self): return self._sessions[0] if self._sessions else None
    def sessions(self): return list(self._sessions)
    def get(self, search_id): return next((s for s in self._sessions if s.search_id==search_id), None)
