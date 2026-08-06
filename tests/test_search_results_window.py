from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from windows_pet.search_results_window import SearchResultsWindow


def result(result_id, path, name=None):
    path = Path(path)
    return SimpleNamespace(
        result_id=result_id, name=name or path.name, root_alias="root",
        relative_parent="", extension=path.suffix, modified_at=datetime.now(),
        size_bytes=1, full_path=str(path),
    )


def window(qapp, *results):
    return SearchResultsWindow(SimpleNamespace(results=list(results)))


def test_populate_selects_first_and_empty_disables(qapp, tmp_path):
    one = window(qapp, result("a", tmp_path / "a.txt"))
    assert one.table.currentRow() == 0 and one.table.selectionModel().selectedRows()
    assert one.open_btn.isEnabled() and one.copy_btn.isEnabled()
    zero = window(qapp)
    assert not zero.open_btn.isEnabled() and not zero.copy_btn.isEnabled()


def test_filter_selects_first_visible_or_disables(qapp, tmp_path):
    w = window(qapp, result("a", tmp_path / "alpha.txt"), result("b", tmp_path / "beta.txt"))
    w.filter.setText("beta")
    assert w.table.currentRow() == 1 and w._selected().result_id == "b"
    w.filter.setText("missing")
    assert w._selected() is None and not w.open_btn.isEnabled() and not w.copy_btn.isEnabled()


def test_selected_resolves_by_result_id_after_reordering(qapp, tmp_path):
    a, b = result("a", tmp_path / "a.txt"), result("b", tmp_path / "b.txt")
    w = window(qapp, a, b)
    w.session.results[:] = [b, a]
    w.table.selectRow(0)
    assert w._selected().result_id == "a"


def test_unselected_messages_and_copy_does_not_change_clipboard(qapp, monkeypatch, tmp_path):
    w = window(qapp, result("a", tmp_path / "a.txt"))
    messages = []
    monkeypatch.setattr("windows_pet.search_results_window.QMessageBox.warning", lambda *a: messages.append(a[2]))
    clipboard = qapp.clipboard()
    clipboard.setText("old")
    w.table.clearSelection()
    w.copy_path()
    w.open_location()
    assert clipboard.text() == "old"
    assert messages == ["検索結果を選択してください。", "検索結果を選択してください。"]


def test_copy_and_explorer_file_args(qapp, monkeypatch, tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("x")
    w = window(qapp, result("a", path))
    calls = []
    monkeypatch.setattr("windows_pet.search_results_window.subprocess.Popen", lambda args, shell: calls.append((args, shell)))
    w.copy_path()
    w.open_location()
    assert qapp.clipboard().text() == str(path)
    assert calls == [(["explorer.exe", f"/select,{path}"], False)]


def test_missing_file_parent_prompt_and_missing_parent(qapp, monkeypatch, tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    w = window(qapp, result("a", parent / "gone.txt"))
    calls = []
    monkeypatch.setattr("windows_pet.search_results_window.QMessageBox.question", lambda *a: QMessageBox.Yes)
    monkeypatch.setattr("windows_pet.search_results_window.subprocess.Popen", lambda args, shell: calls.append(args))
    w.open_location()
    assert calls == [["explorer.exe", str(parent)]]
    messages = []
    monkeypatch.setattr("windows_pet.search_results_window.QMessageBox.warning", lambda *a: messages.append(a[2]))
    w2 = window(qapp, result("b", tmp_path / "gone" / "file.txt"))
    w2.open_location()
    assert messages[-1] == "保存場所が見つかりません。"
