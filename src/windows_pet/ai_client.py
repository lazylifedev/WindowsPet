from __future__ import annotations

import os
import json
from collections.abc import Callable, Iterable

from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError, BadRequestError
from openai import AuthenticationError, InternalServerError, NotFoundError, PermissionDeniedError, RateLimitError
from .file_search_models import SearchRequest
from .file_search_service import FileSearchService
from .file_search_settings import SearchSettings
from .tool_dispatcher import ToolDispatcher
from .openai_credentials import get_api_key

INSTRUCTIONS = """あなたはWindows上で動作する小さくて親しみやすいデスクトップアシスタントです。
基本的に日本語で、簡潔かつ正確に回答してください。
Windowsのファイル、コマンド、アプリ、画面を操作する能力はありません。
実行していない操作を、実行したかのように報告してはいけません。
操作を求められた場合は、現在のチャットで説明できる範囲だけ可能だと説明してください。"""

class AIClientError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind

def model_name() -> str:
    return os.getenv("WINDOWS_PET_MODEL", "gpt-5-mini")

def classify_openai_error(exc: Exception) -> AIClientError:
    body = getattr(exc, "body", None); code = None
    if isinstance(body, dict):
        error = body.get("error"); code = error.get("code") if isinstance(error, dict) else body.get("code")
    status = getattr(exc, "status_code", None)
    if isinstance(exc, AuthenticationError) or status == 401: return AIClientError("auth", "OpenAI APIキーが正しくないか、無効になっています。")
    if isinstance(exc, PermissionDeniedError) or status == 403: return AIClientError("permission", "このOpenAI APIキーには必要な利用権限がありません。")
    if isinstance(exc, RateLimitError): return AIClientError("quota" if code == "insufficient_quota" else "rate_limit", "OpenAI APIの利用上限に達しています。請求設定を確認する必要があります。" if code == "insufficient_quota" else "短時間にリクエストが集中しました。少し時間をおいて再試行してください。")
    if isinstance(exc, APITimeoutError): return AIClientError("timeout", "OpenAI APIから時間内に応答がありませんでした。")
    if isinstance(exc, APIConnectionError): return AIClientError("network", "ネットワークに接続できないか、OpenAI APIへ到達できませんでした。")
    if isinstance(exc, BadRequestError) or status == 400: return AIClientError("bad_request", "OpenAI APIへのリクエストを受け付けられませんでした。")
    if isinstance(exc, NotFoundError) or status == 404: return AIClientError("model", "設定されているAIモデルを利用できません。")
    if isinstance(exc, InternalServerError) or (isinstance(exc, APIStatusError) and isinstance(status, int) and status >= 500): return AIClientError("server", "OpenAI APIで一時的な障害が発生しています。少し時間をおいて再試行してください。")
    return AIClientError("unknown", "OpenAI APIとの通信中にエラーが発生しました。")

def _error(exc: Exception) -> AIClientError:
    """Legacy adapter; production paths use classify_openai_error directly."""
    text = str(exc).lower()
    if "401" in text or "api key" in text: return AIClientError("auth", "OpenAI APIキーが正しくないか、無効になっています。")
    return classify_openai_error(exc)

class AIClient:
    @staticmethod
    def _raise_if_cancelled(cancel):
        if cancel is not None and cancel.is_set():
            raise AIClientError('cancelled', '処理をキャンセルしました。')
    def __init__(self, client=None, timeout: float = 60.0, api_key: str | None = None):
        key = api_key or get_api_key()
        if not key:
            raise AIClientError("missing_key", "OpenAI APIキーが設定されていません。")
        self.client = client or OpenAI(api_key=key, timeout=timeout, max_retries=0)

    def stream(self, history: list[dict[str, str]], on_delta: Callable[[str], None], cancel=None) -> str:
        try:
            tools = [{"type": "function", "name": "search_files", "description": "許可済み検索ルートのファイル名・フォルダー名・メタデータだけを読み取り専用で検索します。ファイル内容は検索しません。", "parameters": {"type":"object", "properties": {"query":{"type":"string"}, "root_ids":{"type":"array","items":{"type":"string"}}, "extensions":{"type":"array","items":{"type":"string"}}, "modified_after":{"type":["string","null"]}, "modified_before":{"type":["string","null"]}, "min_size_bytes":{"type":["integer","null"]}, "max_size_bytes":{"type":["integer","null"]}, "include_directories":{"type":"boolean"}, "max_results":{"type":"integer"}}, "required":["query","root_ids","extensions","modified_after","modified_before","min_size_bytes","max_size_bytes","include_directories","max_results"], "additionalProperties":False}, "strict": True}]
            self._raise_if_cancelled(cancel)
            stream: Iterable = self.client.responses.create(
                model=model_name(), instructions=INSTRUCTIONS, input=history,
                tools=tools, stream=True, store=False,
            )
            self._raise_if_cancelled(cancel)
            parts: list[str] = []
            # Streaming text remains compatible with the existing UI. Tool calls
            # are handled through a bounded non-streaming continuation below.
            for event in stream:
                self._raise_if_cancelled(cancel)
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        self._raise_if_cancelled(cancel)
                        parts.append(delta); on_delta(delta)
            self._raise_if_cancelled(cancel)
            return "".join(parts)
        except AIClientError:
            raise
        except Exception as exc:
            raise classify_openai_error(exc) from exc

    def stream_with_tools(self, history, on_delta, on_search_started=None, on_search_completed=None, cancel=None) -> str:
        """Run the public Responses API tool loop; full paths never enter API input."""
        seen=set(); inputs=list(history); dispatcher=ToolDispatcher(); calls=0
        try:
            while calls < 3:
                self._raise_if_cancelled(cancel)
                response=self.client.responses.create(model=model_name(), instructions=INSTRUCTIONS, input=inputs, tools=self._tools(), store=False)
                self._raise_if_cancelled(cancel)
                calls_found=[x for x in getattr(response, 'output', []) if getattr(x, 'type', '') == 'function_call']
                if not calls_found:
                    text = getattr(response, 'output_text', '') or ''
                    if text:
                        self._raise_if_cancelled(cancel)
                        on_delta(text)
                    return text
                self._raise_if_cancelled(cancel)
                call=calls_found[0]; call_id=getattr(call,'call_id',None); name=getattr(call,'name',None)
                if not call_id or call_id in seen or name != 'search_files': raise AIClientError('tool', 'この操作にはまだ対応していません。')
                seen.add(call_id); calls += 1
                self._raise_if_cancelled(cancel)
                if on_search_started: on_search_started()
                result=dispatcher.search_files(getattr(call,'arguments',''), cancel)
                self._raise_if_cancelled(cancel)
                request_data = json.loads(getattr(call, 'arguments', '{}')) if isinstance(getattr(call, 'arguments', '{}'), str) else getattr(call, 'arguments', {})
                result['query'] = request_data.get('query', '')
                result['root_ids'] = request_data.get('root_ids', [])
                if result.get('status') == 'cancelled': raise AIClientError('cancelled','ファイル検索をキャンセルしました。')
                safe=dispatcher.safe_output(result)
                self._raise_if_cancelled(cancel)
                if on_search_completed: on_search_completed(result)
                inputs.extend(getattr(response,'output',[])); inputs.append({'type':'function_call_output','call_id':call_id,'output':json.dumps(safe,ensure_ascii=False)})
                self._raise_if_cancelled(cancel)
            raise AIClientError('tool_limit','ファイル検索の呼び出し回数が上限に達しました。')
        except AIClientError: raise
        except Exception as exc: raise classify_openai_error(exc) from exc

    def _tools(self):
        return [{"type":"function","name":"search_files","description":"許可済みフォルダーを読み取り専用で検索します。ファイル本文は検索しません。","parameters":{"type":"object","properties":{"query":{"type":"string"},"root_ids":{"type":"array","items":{"type":"string"}},"extensions":{"type":"array","items":{"type":"string"}},"modified_after":{"type":["string","null"]},"modified_before":{"type":["string","null"]},"min_size_bytes":{"type":["integer","null"]},"max_size_bytes":{"type":["integer","null"]},"include_directories":{"type":"boolean"},"max_results":{"type":"integer"}},"required":["query","root_ids","extensions","modified_after","modified_before","min_size_bytes","max_size_bytes","include_directories","max_results"],"additionalProperties":False},"strict":True}]
