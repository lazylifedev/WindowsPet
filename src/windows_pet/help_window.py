from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

HELP_TEXT = """WindowsPet の使い方

■ キャラクターの操作

左クリック
チャット入力欄を開閉します。

ダブルクリック
チャット入力欄を開きます。

左ドラッグ
キャラクターを移動します。終了時に位置が保存されます。

右クリック
設定、会話履歴、検索結果などのメニューを表示します。

■ チャット

Enter
入力内容を送信します。

Shift + Enter
入力欄で改行します。

送信ボタン
待機中は「➤」です。

停止ボタン
処理中は「■」へ変わります。押すと処理のキャンセルを要求します。
OpenAI APIの応答待ち中は、安全に停止できる地点まで少し時間がかかる場合があります。

APIキーが未設定の場合
入力内容を残したままOpenAI API設定画面を開きます。設定後にもう一度送信してください。

■ 回答吹き出し

回答を右クリックすると、回答をコピー、回答を固定／固定を解除、再試行、閉じるなどの操作が表示されます。
回答は通常12秒後に自動で隠れます。「回答を固定」を選ぶと自動で隠れません。

■ 会話履歴

キャラクターを右クリックし、「会話履歴」を選ぶと現在の会話を確認できます。
会話をコピー、履歴を消去、ウィンドウを閉じる操作ができます。
会話履歴はアプリ実行中のメモリだけに保持されます。

■ ファイル検索

「ファイル検索設定」で検索を許可するフォルダーや拡張子を設定します。
検索は読み取り専用で、ファイル本文ではなくファイル名、拡張子、更新日時、サイズなどのメタデータを検索します。
検索結果では保存場所をExplorerで開く、パスをコピー、最近の検索結果を再表示できます。
処理中は送信ボタンの「■」または右クリックメニューの「処理をキャンセル」で停止を要求できます。

■ OpenAI API設定

OpenAI APIキーは環境変数 OPENAI_API_KEY または Windows Credential Manager から取得します。
環境変数が設定されている場合は、保存済みキーより優先されます。
APIキーは通常の設定JSONへ保存しません。

■ 右クリックメニュー

OpenAI API 設定
ファイル検索設定
最近の検索結果
処理をキャンセル
チャットを開く
チャットを閉じる
会話履歴
使い方
位置をリセット
終了
"""


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WindowsPet の使い方")
        self.resize(620, 600); self.setMinimumSize(460, 380)
        self.setStyleSheet("QDialog{background:#20242b;} QPlainTextEdit{color:#f2f5f8;background:#20242b;border:1px solid #4d5663;padding:12px;}")
        self.browser = QPlainTextEdit(); self.browser.setReadOnly(True); self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.browser.setPlainText(HELP_TEXT)
        self.browser.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        close = QDialogButtonBox(QDialogButtonBox.Close); close.rejected.connect(self.close)
        layout = QVBoxLayout(self); layout.addWidget(self.browser); layout.addWidget(close)
