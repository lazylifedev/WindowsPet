# WindowsPet

Python 3.12 / PySide6 の Windows 11 向け透明デスクトップペットです。指定された PNG と `manifest.json` を事前読み込みし、idle、wave、sleep、thinking を再生します。

## 起動

`py -3.12 -m venv .venv`、`.venv\Scripts\python -m pip install -e .[test]` の後、`run.bat` を実行します。

左ドラッグで移動、左クリックで wave、右クリックで操作メニューです。位置は `data/position.json`、ログは `logs/windows_pet.log` に保存します。

## テストとビルド

`.venv\Scripts\python -m pytest` でテスト、`build.bat` で PyInstaller の `dist\WindowsPet\WindowsPet.exe` を作成します。
