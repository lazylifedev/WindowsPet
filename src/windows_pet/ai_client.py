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
from .application_launch_request import parse_application_launch_request
from .process_stop_request import parse_process_stop_request
from .service_restart_request import parse_service_restart_request
from .file_rename_request import parse_file_rename_request
from .powershell_read_runner import PowerShellReadRunner
from .process_inspection import normalize_process_query, resolve_process_candidate
from .research.orchestrator import ResearchOrchestrator

APPLICATION_LAUNCH_HANDOFF = object()

INSTRUCTIONS = """Respond in Japanese, politely and concisely. WindowsPet can use only the approved local tools exposed in this request.

Use inspect_windows for read-only process, service, network, event-log, fixed-catalog registry, or winget name-search inspection. Winget inspection only searches package metadata; it never installs, upgrades, uninstalls, or changes sources. A user request such as 「メモ帳を終了して」「メモ帳を閉じて」「Notepadを終了して」or「このプロセスを終了して」is a request to stop an application/process, not a request for manual instructions. For such a request, inspect_windows(processes) when needed, then continue the same request with request_process_stop only when the inspection result contains exactly one matching process and its returned PID and name can be used. Never guess a PID or choose among ambiguous candidates; ask the user to clarify instead. 「閉じる」may mean process termination in this context, but treat file/window-only wording as ambiguous and ask for clarification.

request_process_stop only hands the request to WindowsPet's local safety flow. The Tool call itself never stops a process. WindowsPet locally re-resolves the process, validates identity, rejects protected processes, shows SCRIPT_REVIEW confirmation, issues a one-shot ExecutionGrant only after approval, runs only the approved fixed PowerShell operation, and performs read-only verification afterward. Do not claim that the process was stopped before that flow completes.

For a Windows service restart request, first use inspect_windows(services). Call request_service_restart only with a service name or display name returned by that inspection. This hands off only to the local confirmation flow; it never restarts a service itself. WindowsPet binds the inspection snapshot, re-resolves the canonical service identity, rejects protected services and missing administrator rights, requires SCRIPT_REVIEW approval, then performs the fixed operation and verifies the service is Running.

If none of the approved tools directly matches an otherwise safe user goal, call research_unknown with a concise summary of the original goal. Do not claim that WindowsPet cannot help merely because a dedicated tool is absent. research_unknown only starts bounded local research and may return a confirmation-waiting plan; it never executes provider-generated commands.

Do not generate or execute PowerShell, shell commands, generic computer-control actions, or arbitrary scripts. Do not tell the user that WindowsPet is inherently unable to stop processes or immediately fall back to Task Manager/manual instructions when request_process_stop is available. Use a safety/policy explanation only when the tool is unavailable, the target is ambiguous, or local validation/policy rejects it.

For an application launch request, use request_application_launch. Do not use shell commands, PowerShell, generic computer-control tools, or any other tool for launching. The tool call never starts an application: WindowsPet asks for confirmation and launches only after local validation and approval. exact_path may contain only a .exe path explicitly supplied by the user in a user message. Never infer it, use a default path, or use a path from assistant or tool output. For file renaming, use request_file_rename only when the source is explicitly in the user's current request/context or the local current-file context is unique. The tool call never renames a file; WindowsPet shows before/after confirmation and verifies identity after approval. Keep search_files behavior unchanged."""

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
    def __init__(self, client=None, timeout: float = 60.0, api_key: str | None = None, inspection_runner=None, research_orchestrator=None, current_file_context: str | None = None, personality_context: dict | None = None):
        key = api_key or get_api_key()
        if not key:
            raise AIClientError("missing_key", "OpenAI APIキーが設定されていません。")
        self.client = client or OpenAI(api_key=key, timeout=timeout, max_retries=0)
        self.inspection_runner = inspection_runner or PowerShellReadRunner()
        self.research_orchestrator = research_orchestrator or ResearchOrchestrator()
        self.current_file_context = current_file_context
        self.personality_context = personality_context or {}

    def _instructions(self) -> str:
        if not self.personality_context:
            return INSTRUCTIONS
        import json
        bounded = json.dumps(self.personality_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:2000]
        return INSTRUCTIONS + "\nUse this bounded local relationship context only to adapt formality and message length; do not reveal or persist it:\n" + bounded

    def stream(self, history: list[dict[str, str]], on_delta: Callable[[str], None], cancel=None) -> str:
        try:
            tools = [{"type": "function", "name": "search_files", "description": "許可済み検索ルートのファイル名・フォルダー名・メタデータだけを読み取り専用で検索します。ファイル内容は検索しません。", "parameters": {"type":"object", "properties": {"query":{"type":"string"}, "root_ids":{"type":"array","items":{"type":"string"}}, "extensions":{"type":"array","items":{"type":"string"}}, "modified_after":{"type":["string","null"]}, "modified_before":{"type":["string","null"]}, "min_size_bytes":{"type":["integer","null"]}, "max_size_bytes":{"type":["integer","null"]}, "include_directories":{"type":"boolean"}, "max_results":{"type":"integer"}}, "required":["query","root_ids","extensions","modified_after","modified_before","min_size_bytes","max_size_bytes","include_directories","max_results"], "additionalProperties":False}, "strict": True}]
            self._raise_if_cancelled(cancel)
            stream: Iterable = self.client.responses.create(
                model=model_name(), instructions=self._instructions(), input=history,
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

    def stream_with_tools(self, history, on_delta, on_search_started=None, on_search_completed=None, cancel=None, on_application_launch_requested=None, on_powershell_started=None, on_powershell_completed=None, on_process_stop_requested=None, on_service_restart_requested=None, on_file_rename_requested=None) -> str:
        """Run the public Responses API tool loop; full paths never enter API input."""
        seen=set(); failed_inspections=set(); inspected_processes={}; service_snapshot=(); inputs=list(history); dispatcher=ToolDispatcher(); calls=0
        try:
            while calls < 3:
                self._raise_if_cancelled(cancel)
                response=self.client.responses.create(model=model_name(), instructions=self._instructions(), input=inputs, tools=self._tools() + [self._launch_tool()], store=False)
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
                if name == 'request_application_launch':
                    if not call_id or call_id in seen or len(calls_found) != 1: raise AIClientError('tool', 'unsupported_tool')
                    try: request = parse_application_launch_request(getattr(call, 'arguments', ''), history)
                    except ValueError as exc: raise AIClientError('tool', 'invalid_launch_request') from exc
                    if on_application_launch_requested: on_application_launch_requested(request)
                    return APPLICATION_LAUNCH_HANDOFF
                if name == "research_unknown":
                    if not call_id or call_id in seen or len(calls_found) != 1:
                        raise AIClientError("tool", "unsupported_tool")
                    try:
                        outcome = dispatcher.research_unknown(getattr(call, "arguments", ""), self.research_orchestrator)
                    except (TypeError, ValueError) as exc:
                        raise AIClientError("tool", "invalid_research_request") from exc
                    seen.add(call_id); calls += 1
                    safe = {
                        "status": outcome.session.state.value,
                        "result_code": outcome.result_code,
                        "route": outcome.route,
                        "evidence_count": len(outcome.evidence),
                        "plan_available": outcome.plan is not None,
                        "confirmation_required": bool(outcome.plan and outcome.plan.requires_confirmation),
                    }
                    inputs.extend(getattr(response, "output", [])); inputs.append({"type":"function_call_output","call_id":call_id,"output":json.dumps(safe, ensure_ascii=False)})
                    continue
                if name == "request_file_rename":
                    if not call_id or call_id in seen or len(calls_found) != 1:
                        raise AIClientError("tool", "unsupported_tool")
                    try:
                        request = parse_file_rename_request(getattr(call, "arguments", ""), history, self.current_file_context)
                    except ValueError as exc:
                        raise AIClientError("tool", "invalid_file_rename_request") from exc
                    if on_file_rename_requested:
                        on_file_rename_requested(request)
                    return APPLICATION_LAUNCH_HANDOFF
                if name == "request_process_stop":
                    if not call_id or call_id in seen or len(calls_found) != 1: raise AIClientError("tool", "unsupported_tool")
                    try: request = parse_process_stop_request(getattr(call, "arguments", ""))
                    except ValueError as exc: raise AIClientError("tool", "invalid_stop_request") from exc
                    # The model may only hand off an identity returned by the
                    # immediately preceding local process inspection.  In
                    # particular, a UI/display name must never become the
                    # resolver's expected ProcessName.
                    if inspected_processes.get(request.process_id) != request.expected_process_name:
                        raise AIClientError("tool", "process_identity_not_from_inspection")
                    if on_process_stop_requested: on_process_stop_requested(request)
                    return APPLICATION_LAUNCH_HANDOFF
                if name == "request_service_restart":
                    if not call_id or call_id in seen or len(calls_found) != 1: raise AIClientError("tool", "unsupported_tool")
                    try: request = parse_service_restart_request(getattr(call, "arguments", ""))
                    except ValueError as exc: raise AIClientError("tool", "invalid_service_restart_request") from exc
                    # Bind the handoff to the exact read-only service snapshot
                    # returned in this chat turn; the controller revalidates it.
                    if not service_snapshot:
                        raise AIClientError("tool", "service_snapshot_not_available")
                    request = request.__class__(request.service_query, service_snapshot)
                    if on_service_restart_requested: on_service_restart_requested(request)
                    return APPLICATION_LAUNCH_HANDOFF
                if name == "inspect_windows":
                    if not call_id or call_id in seen or len(calls_found) != 1: raise AIClientError("tool", "unsupported_tool")
                    try: request = dispatcher.parse_windows_inspection(getattr(call, "arguments", ""))
                    except ValueError as exc: raise AIClientError("tool", "invalid_inspection_request") from exc
                    signature = (request.area.value, request.query, request.max_results)
                    if signature in failed_inspections: raise AIClientError("tool", "inspection_retry_blocked")
                    seen.add(call_id); calls += 1
                    if on_powershell_started: on_powershell_started(request.area.value)
                    effective_request = request
                    if request.area.value == "processes":
                        effective_request = request.__class__(request.area, normalize_process_query(request.query), request.max_results)
                    outcome = self.inspection_runner.execute(effective_request, cancel)
                    if outcome.status.value == "cancelled": raise AIClientError("cancelled", "Windows調査をキャンセルしました。")
                    safe = dispatcher.safe_inspection_output(outcome, request.area.value)
                    if request.area.value == "processes" and outcome.status.value == "success":
                        items = safe.get("items", [])
                        snapshot, candidate, reason = resolve_process_candidate(request.query, items, truncated=len(items) >= request.max_results)
                        inspection_snapshots = snapshot.processes
                        # Only the locally resolved, unique candidate may be handed
                        # to the stop tool; arbitrary rows remain display data.
                        inspected_processes = {candidate[0]: candidate[1]} if candidate is not None else {}
                        safe["inspection_reason"] = reason
                        if candidate is not None:
                            safe["candidate"] = {"process_id": candidate[0], "canonical_process_name": candidate[1]}
                    if request.area.value == "services" and outcome.status.value == "success":
                        service_snapshot = tuple(dict(item) for item in safe.get("items", ()))
                    if outcome.result_code in {"invalid_output", "not_available", "execution_failed", "timeout", "output_limit_exceeded", "child_cleanup_failed"}: failed_inspections.add(signature)
                    if on_powershell_completed: on_powershell_completed(safe)
                    inputs.extend(getattr(response, "output", [])); inputs.append({"type":"function_call_output","call_id":call_id,"output":json.dumps(safe, ensure_ascii=False)})
                    continue
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

    def _launch_tool(self):
        return {"type":"function","name":"request_application_launch","description":"アプリ起動の確認を依頼します。このTool callだけでは起動しません。","parameters":{"type":"object","properties":{"application_name":{"type":"string","minLength":1,"maxLength":200},"exact_path":{"type":["string","null"],"maxLength":1024}},"required":["application_name","exact_path"],"additionalProperties":False},"strict":True}

    def _stop_process_tool(self):
        return {"type":"function","name":"request_process_stop","description":"inspect_windows(processes) で確認済みの 1 件のプロセスについて、終了確認を依頼します。このTool callだけでは終了しません。","parameters":{"type":"object","properties":{"process_id":{"type":"integer","minimum":1},"expected_process_name":{"type":"string","minLength":1,"maxLength":260}},"required":["process_id","expected_process_name"],"additionalProperties":False},"strict":True}

    def _tools(self):
        return self._base_tools() + [self._stop_process_tool(), self._service_restart_tool(), self._research_tool(), self._file_rename_tool()]

    def _file_rename_tool(self):
        return {"type":"function","name":"request_file_rename","description":"ユーザーの現在のファイル文脈にあるファイルについて、変更前後を示すファイル名変更確認を依頼します。このTool callだけでは変更しません。","parameters":{"type":"object","properties":{"source_path":{"type":["string","null"],"maxLength":1024},"new_name":{"type":"string","minLength":1,"maxLength":255}},"required":["source_path","new_name"],"additionalProperties":False},"strict":True}

    def _research_tool(self):
        return {"type":"function","name":"research_unknown","description":"専用Toolがない安全な目的を、boundedなread-only Research Orchestratorへ渡します。副作用は実行せず、必要なら確認待ち計画を返します。","parameters":{"type":"object","properties":{"request":{"type":"string","minLength":1,"maxLength":240}},"required":["request"],"additionalProperties":False},"strict":True}

    def _service_restart_tool(self):
        return {"type":"function","name":"request_service_restart","description":"inspect_windows(services)で確認済みのWindowsサービス再起動について確認を依頼します。このTool callだけでは再起動しません。","parameters":{"type":"object","properties":{"service_query":{"type":"string","minLength":1,"maxLength":260}},"required":["service_query"],"additionalProperties":False},"strict":True}

    def _base_tools(self):
        return [{"type":"function","name":"search_files","description":"許可済みフォルダーを読み取り専用で検索します。ファイル本文は検索しません。","parameters":{"type":"object","properties":{"query":{"type":"string"},"root_ids":{"type":"array","items":{"type":"string"}},"extensions":{"type":"array","items":{"type":"string"}},"modified_after":{"type":["string","null"]},"modified_before":{"type":["string","null"]},"min_size_bytes":{"type":["integer","null"]},"max_size_bytes":{"type":["integer","null"]},"include_directories":{"type":"boolean"},"max_results":{"type":"integer"}},"required":["query","root_ids","extensions","modified_after","modified_before","min_size_bytes","max_size_bytes","include_directories","max_results"],"additionalProperties":False},"strict":True}, {"type":"function","name":"inspect_windows","description":"PowerShellを使用して現在のWindows状態を読み取り専用で調査します。構成変更は行いません。wingetはパッケージ名のmetadata検索だけを行います。","parameters":{"type":"object","properties":{"area":{"type":"string","enum":["processes","services","network","event_logs","registry","winget"]},"query":{"type":["string","null"],"maxLength":100},"max_results":{"type":"integer","minimum":1,"maximum":100}},"required":["area","query","max_results"],"additionalProperties":False},"strict":True}]
