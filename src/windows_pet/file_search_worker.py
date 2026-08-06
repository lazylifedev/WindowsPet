from __future__ import annotations
from PySide6.QtCore import QObject, Signal, Slot
from .tool_dispatcher import ToolDispatcher

class FileSearchWorker(QObject):
    completed=Signal(dict); failed=Signal(str); cancelled=Signal()
    def __init__(self, arguments, dispatcher=None, cancel=None):
        super().__init__(); self.arguments=arguments; self.dispatcher=dispatcher or ToolDispatcher(); self.cancel_token=cancel
    @Slot()
    def run(self):
        try:
            result=self.dispatcher.search_files(self.arguments, self.cancel_token)
            if result.get('status') == 'cancelled': self.cancelled.emit()
            else: self.completed.emit(result)
        except ValueError: self.failed.emit('ファイル検索条件を正しく処理できませんでした。')
        except PermissionError: self.failed.emit('一部のフォルダへアクセスできませんでした。閲覧権限を確認してください。')
        except OSError: self.failed.emit('ファイルサーバーへ接続できませんでした。ネットワーク接続と共有フォルダの状態を確認してください。')
