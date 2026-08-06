# WindowsPet

## Action safety foundation

副作用操作は型付きActionProposalとして表現し、Policy Gateで検証します。ユーザー承認後だけ対象・操作・引数に固定された一回限りのExecutionGrantを発行します。再利用、期限切れ、改変されたProposalやGrantは拒否します。現時点では実際のPC操作は実装していません。監査ログには秘密情報や対象の実値を保存しません。

## PC調査情報

キャラクターまたは通知領域アイコンを右クリックして「PC調査情報」を開くと、OS、PATH、App Paths、スタートメニュー、インストール済みアプリ、wingetの状態を読み取り専用で確認できます。アプリの起動、設定変更、インストールは行いません。調査結果はOpenAI APIへ自動送信せず、永続保存もしません。

WindowsPet は、Windows 11 のデスクトップ上で動作する透明なデスクトップペットです。白と水色のキャラクターが常に手前に表示され、チャット、ローカルファイル検索、会話履歴などの操作を右クリックメニューから利用できます。
Windows通知領域のWindowsPetアイコンから、表示、チャット、設定、履歴、使い方、終了操作も利用できます。

## 主な機能

- `idle`、`wave`、`thinking`、`sleep` アニメーションの再生
- キャラクターの左ドラッグによる移動と位置の保存
- 左クリックでチャット入力欄を開閉、ダブルクリックでチャットを開く操作
- Enter で送信、Shift+Enter で改行
- OpenAI Responses API を使った会話とストリーミング表示
- Windows Credential Manager または `OPENAI_API_KEY` による API キー取得
- 実行中の会話履歴を確認できる会話履歴ウィンドウ
- 許可したフォルダー内の読み取り専用ファイル検索
- ファイル名、拡張子、更新日時、サイズなどのメタデータ検索
- 検索結果の表示、保存場所を Explorer で開く、パスのコピー、検索キャンセル
- PyInstaller による Windows 実行ファイルの作成

## 必要な環境

- Windows 11
- Python 3.12 または 3.13
- PySide6
- OpenAI API キー（会話を利用する場合）

## セットアップと起動

PowerShell またはコマンドプロンプトで次を実行します。

```bat
cd WindowsPet
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[test,build]"
run.bat
```

Python モジュールから起動する場合は、次を実行します。

```bat
.venv\Scripts\python -m windows_pet.main
```

## 基本操作

キャラクターを左ドラッグすると位置を移動できます。左クリックでチャット入力欄を開閉し、右クリックで操作メニューを表示します。チャットの送信は Enter、改行は Shift+Enter です。処理中は送信ボタンが `■` に変わり、キャンセル要求に使用できます。回答吹き出しの右クリックでコピー・固定・再試行などを利用でき、「使い方」画面で操作一覧を確認できます。
キャラクターを見失った場合は、Windows通知領域のWindowsPetアイコンから「WindowsPetを表示」を選択できます。

右クリックメニューには次の項目があります。

1. OpenAI API 設定
2. ファイル検索設定
3. 最近の検索結果
4. 処理をキャンセル
5. チャットを開く
6. チャットを閉じる
7. 会話履歴
8. 使い方
9. 位置をリセット
10. 終了

## OpenAI API の設定

キャラクターを右クリックし、「OpenAI API 設定」を開いて API キーを入力・接続確認・保存します。キーは通常の JSON 設定ファイルには保存せず、Windows Credential Manager に保存します。環境変数 `OPENAI_API_KEY` も利用できます。

API キーや実際のユーザー環境、ファイルパス、サーバー情報などの秘密情報は README やログに記録しないでください。

## ファイル検索

「ファイル検索設定」で検索対象のフォルダーと拡張子を登録します。検索は登録済みのフォルダー内で、ファイル内容を読み込まずメタデータだけを対象に行います。検索中はキャンセルでき、検索結果から Explorer で保存場所を開いたり、パスをコピーしたりできます。UNC パスやユーザー固有の実パスは README に記載しないでください。

## データとログ

- キャラクターの位置: `data/position.json`
- 会話履歴: 実行中のメモリ（終了時には保存されません）
- ファイル検索設定: `%LOCALAPPDATA%\WindowsPet\settings.json`
- ログ: `logs/windows_pet.log`
- API キー: Windows Credential Manager

実際の保存ファイル名やユーザー固有の保存場所は、実装と環境により変わるため README では一般的な保存先だけを説明しています。

## テスト

```bat
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m compileall src tests
```

## ビルド

```bat
build.bat
```

ビルド成果物は `dist\WindowsPet.exe` に出力されます。PyInstaller の設定は `WindowsPet.spec`、アニメーション素材は `assets\animations\` にあります。

## プロジェクト構成

- `src/windows_pet/main.py`: ペットウィンドウと右クリックメニュー
- `src/windows_pet/chat_bubble.py`: チャット入力、応答、会話履歴
- `src/windows_pet/ai_client.py`、`ai_worker.py`: OpenAI API 通信
- `src/windows_pet/openai_settings_window.py`: API 設定画面
- `src/windows_pet/file_search_*.py`: ファイル検索設定・サービス・ワーカー
- `src/windows_pet/search_results_window.py`: 検索結果画面
- `tests/`: 自動テスト
- `assets/animations/`: アニメーション素材

## セキュリティ上の注意

API キーをリポジトリ、ログ、画面キャプチャに残さないでください。ファイル検索は許可されたフォルダーだけを対象にし、AI へはファイルのフルパスや UNC パスを送信しない設計です。ファイルの作成、変更、コピー、移動、削除は行いません。
