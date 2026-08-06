from __future__ import annotations

import os
from collections.abc import Callable, Iterable

from openai import OpenAI

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

def _error(exc: Exception) -> AIClientError:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "authentication" in name or "api key" in text or "401" in text:
        return AIClientError("auth", "OpenAI APIキーが正しくないか、利用できません。")
    if "rate" in name or "quota" in text or "429" in text:
        return AIClientError("rate_limit", "OpenAI APIの利用上限またはレート制限に達しました。")
    if "timeout" in name or "timed out" in text:
        return AIClientError("timeout", "AIからの応答が時間内に返りませんでした。")
    if "connection" in name or "connect" in text:
        return AIClientError("network", "OpenAI APIへ接続できませんでした。")
    return AIClientError("server", "OpenAI APIで一時的なエラーが発生しました。")

class AIClient:
    def __init__(self, client=None, timeout: float = 60.0):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise AIClientError("missing_key", "OpenAI APIキーが設定されていません。")
        self.client = client or OpenAI(api_key=key, timeout=timeout, max_retries=0)

    def stream(self, history: list[dict[str, str]], on_delta: Callable[[str], None]) -> str:
        try:
            stream: Iterable = self.client.responses.create(
                model=model_name(), instructions=INSTRUCTIONS, input=history,
                stream=True, store=False,
            )
            parts: list[str] = []
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        parts.append(delta); on_delta(delta)
            return "".join(parts)
        except AIClientError:
            raise
        except Exception as exc:
            raise _error(exc) from exc
