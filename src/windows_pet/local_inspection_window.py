from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout

from .cancellation import CancellationToken
from .local_inspection_service import LocalInspectionService
from .local_inspection_worker import LocalInspectionWorker


class LocalInspectionWindow(QDialog):
    """Reusable local-only inspection result window."""

    def __init__(self, parent=None, service_factory=LocalInspectionService):
        super().__init__(parent)
        self.setWindowTitle("PC調査情報")
        self.resize(620, 520)
        self.service_factory = service_factory
        self.thread = None
        self.worker = None
        self.token = None
        self.snapshot = None
        self.status = QLabel("未調査")
        self.summary = QLabel()
        self.query = QLineEdit()
        self.query.setPlaceholderText("アプリ候補を検索")
        self.results = QListWidget()
        self.refresh = QPushButton("更新")
        self.refresh.clicked.connect(self.start_inspection)
        self.query.textChanged.connect(self.search)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("この画面はPCの状態を読み取り専用で調査します。\nアプリの起動、設定変更、インストールなどは行いません。"))
        layout.addWidget(self.status)
        layout.addWidget(self.summary)
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        layout.addWidget(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        if self.snapshot is None and self.thread is None:
            self.start_inspection()

    def start_inspection(self):
        if self.thread is not None and self.thread.isRunning():
            return
        self.status.setText("調査中…")
        self.refresh.setEnabled(False)
        self.token = CancellationToken()
        self.thread = QThread(self)
        self.worker = LocalInspectionWorker(self.service_factory(), self.token)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.completed.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    def _completed(self, snapshot):
        self.snapshot = snapshot
        system = snapshot.system
        self.status.setText("調査完了")
        self.summary.setText(
            f"OS: {system.os_name} {system.version} build {system.build}\n"
            f"アーキテクチャ: {system.architecture} / Python: {system.python_architecture}\n"
            f"コンピューター名: {system.computer_name} / ユーザー名: {system.username}\n"
            f"管理者権限: {'有' if system.is_admin else '無'} / winget: {'利用可能' if snapshot.winget.available else '利用不可'}\n"
            f"PATH: {snapshot.path.item_count}件（存在 {snapshot.path.existing_count} / 不存在 {snapshot.path.missing_count}） / "
            f"App Paths: {len(snapshot.app_paths)}件 / スタートメニュー: {len(snapshot.start_menu)}件 / "
            f"インストール済みアプリ: {len(snapshot.installed_apps)}件 / 部分エラー: {len(snapshot.partial_errors)}件"
        )
        self.search()

    def _failed(self, _message):
        self.status.setText("調査失敗（本体は継続します）")

    def _thread_finished(self):
        if self.worker is not None:
            self.worker.deleteLater()
        if self.thread is not None:
            self.thread.deleteLater()
        self.worker = None
        self.thread = None
        self.refresh.setEnabled(True)

    def search(self):
        self.results.clear()
        if self.snapshot is None:
            return
        for item in LocalInspectionService.search(self.snapshot, self.query.text()):
            if item.executable_exists is True:
                state = "存在"
            elif item.executable_exists is False:
                state = "不存在"
            else:
                state = "不明"
            self.results.addItem(f"{item.display_name} | {item.version} | {item.publisher} | {item.source} | 実行ファイル候補: {state}")

    def closeEvent(self, event):
        if self.token is not None:
            self.token.cancel()
        if self.thread is not None and self.thread.isRunning():
            self.thread.quit()
            # The only blocking operation has a five-second subprocess timeout.
            # Wait longer than that so the QObject is never destroyed while running.
            self.thread.wait(6000)
        super().closeEvent(event)
