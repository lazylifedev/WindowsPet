from __future__ import annotations

import json
from datetime import datetime
from threading import Event

from .file_search_models import SearchRequest
from .file_search_service import FileSearchService
from .file_search_settings import SearchSettings
from .powershell_read_models import WindowsInspectionArea, WindowsInspectionRequest

class ToolDispatcher:
    """Validates model arguments locally before any filesystem access."""
    def __init__(self, settings: SearchSettings | None = None):
        self.service = FileSearchService(settings or SearchSettings.load())

    def search_files(self, arguments: str | dict, cancel: Event | None = None) -> dict:
        data = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(data, dict): raise ValueError('search_files の引数が不正です。')
        allowed = {'query','root_ids','extensions','modified_after','modified_before','min_size_bytes','max_size_bytes','include_directories','max_results'}
        if set(data) != allowed: raise ValueError('search_files の引数が不正です。')
        def date(value): return datetime.fromisoformat(value) if value else None
        request = SearchRequest(str(data['query']), tuple(data['root_ids']), tuple(data['extensions']), date(data['modified_after']), date(data['modified_before']), data['min_size_bytes'], data['max_size_bytes'], bool(data['include_directories']), int(data['max_results']))
        result = self.service.search(request, cancel)
        result['results'] = [r.__dict__ | {'modified_at': r.modified_at.isoformat()} for r in result['results']]
        return result

    @staticmethod
    def parse_windows_inspection(arguments: str | dict) -> WindowsInspectionRequest:
        data = json.loads(arguments) if isinstance(arguments, str) else arguments
        if not isinstance(data, dict) or set(data) != {"area", "query", "max_results"}: raise ValueError("inspect_windows の引数が不正です。")
        try: area = WindowsInspectionArea(data["area"])
        except (TypeError, ValueError) as exc: raise ValueError("inspect_windows の引数が不正です。") from exc
        query, maximum = data["query"], data["max_results"]
        if (query is not None and (not isinstance(query, str) or len(query) > 100 or "\0" in query)) or type(maximum) is not int or not 1 <= maximum <= 100 or (area is WindowsInspectionArea.NETWORK and query is not None): raise ValueError("inspect_windows の引数が不正です。")
        return WindowsInspectionRequest(area, query, maximum)

    @staticmethod
    def safe_output(result: dict, search_id: str | None = None) -> dict:
        rows = []
        for row in result.get('results', [])[:20]:
            rows.append({k: row[k] for k in ('result_id','name','extension','root_alias','modified_at','size_bytes') if k in row})
        return {'status': result.get('status','success'), 'search_id': search_id, 'total_found': result.get('total_found',0), 'returned_to_model': len(rows), 'truncated': result.get('total_found',0) > len(rows), 'elapsed_ms': result.get('elapsed_ms',0), 'results': rows}

    @staticmethod
    def safe_inspection_output(outcome, operation: str) -> dict:
        result = outcome.result or {}
        return {"status": "ok" if outcome.status.value == "success" else outcome.status.value, "operation": operation, "count": len(result.get("items", [])), "items": result.get("items", []), "result_code": outcome.result_code}
