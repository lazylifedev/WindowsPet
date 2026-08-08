from pathlib import Path
from windows_pet.local_inspection_models import ConfirmationType, SideEffect, AppCandidate, InspectionSnapshot, PathInspection, SystemInfo, WingetStatus
from windows_pet.local_inspection_service import LocalInspectionService
from windows_pet.local_inspection_worker import LocalInspectionWorker
from windows_pet.cancellation import CancellationToken
from windows_pet.local_inspection_models import InspectionStatus


def snapshot():
    return InspectionSnapshot(SystemInfo("FakeOS", "1", "2", "x64", "host", "user", False, "64bit"), PathInspection(0, 0, 0), WingetStatus(False), installed_apps=[AppCandidate("Notepad", "1", "Pub", "installed")], start_menu=[AppCandidate("Notepad++", source="start_menu")])


def test_contract_is_read_only():
    contract = snapshot().contract
    assert contract.side_effect is SideEffect.READ_ONLY
    assert contract.requires_admin is False
    assert contract.confirmation is ConfirmationType.NONE
    assert not hasattr(contract, "execute")


def test_start_menu_resolves_only_literal_lnk_targets(tmp_path):
    from windows_pet.local_inspection_service import LocalInspectionService

    appdata = tmp_path / "AppData"; menu = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    menu.mkdir(parents=True)
    target = tmp_path / "Apps" / "Editor.exe"; target.parent.mkdir(); target.write_bytes(b"MZ")
    valid = menu / "Editor.lnk"; valid.write_bytes(b"shortcut")
    with_args = menu / "Editor with args.lnk"; with_args.write_bytes(b"shortcut")
    url = menu / "Editor.url"; url.write_text("[InternetShortcut]", encoding="utf-8")

    def read_shortcut(path):
        return str(target) if path.endswith("Editor.lnk") and "with args" not in path else None

    service = LocalInspectionService(env={"APPDATA": str(appdata), "PROGRAMDATA": ""}, shortcut_reader=read_shortcut)
    found = service._start_menu([], None)
    assert len(found) == 1
    assert found[0].display_name == "Editor"
    assert found[0].source == "start_menu"
    assert found[0].executable_path == str(target)
    assert found[0].executable_exists is True
    assert len(found[0].provenance_hash) == 64


def test_display_icon_accepts_only_exe_and_numeric_suffix():
    from windows_pet.local_inspection_service import LocalInspectionService

    assert LocalInspectionService._display_icon_path(r'"C:\Apps\Editor.exe",0') == r"C:\Apps\Editor.exe"
    assert LocalInspectionService._display_icon_path(r"C:\Apps\Editor.exe, -1") == r"C:\Apps\Editor.exe"
    assert LocalInspectionService._display_icon_path(r'"C:\Apps\Editor.exe",shell') == ""
    assert LocalInspectionService._display_icon_path(r"C:\Apps\uninstall.exe /quiet") == ""
    assert LocalInspectionService._display_icon_path(r"%TEMP%\Editor.exe,0") == r"%TEMP%\Editor.exe"


def test_search_preserves_safe_start_menu_and_installed_sources():
    from types import SimpleNamespace
    from windows_pet.local_inspection_service import LocalInspectionService

    snapshot = SimpleNamespace(
        app_paths=[],
        start_menu=[SimpleNamespace(display_name="Editor", source="start_menu", executable_name="Editor.exe", executable_path=r"C:\Apps\Editor.exe")],
        installed_apps=[SimpleNamespace(display_name="Editor Pro", source="installed_apps_hklm_64", executable_name="Editor.exe", executable_path=r"C:\Apps\Pro\Editor.exe")],
        path_candidates=[],
    )
    results = LocalInspectionService(which=lambda _name: None).search(snapshot, "editor")
    assert [item.source for item in results] == ["start_menu", "installed_apps_hklm_64"]


def test_path_normalization_and_deduplication():
    service = LocalInspectionService(env={"PATH": ' "A";a;;B '}, exists=lambda value: value.casefold() == "a")
    result = service._path_info()
    assert result.item_count == 2 and result.existing_count == 1 and result.missing_count == 1


def test_search_ranking_and_empty_query():
    service = LocalInspectionService(which=lambda _: None)
    result = service.search(snapshot(), "notepad")
    assert [item.display_name for item in result] == ["Notepad", "Notepad++"]
    assert service.search(snapshot(), "") == []


class FakeService:
    def __init__(self, result=None):
        self.result = result

    def inspect(self, token):
        if self.result == "cancel":
            token.cancel()
        if self.result == "fail":
            raise OSError
        return snapshot()


def run_worker(service):
    received = []
    worker = LocalInspectionWorker(service, CancellationToken())
    worker.finished.connect(received.append)
    worker.run()
    return received


def test_worker_emits_one_success_terminal_outcome():
    outcomes = run_worker(FakeService())
    assert len(outcomes) == 1 and outcomes[0].status is InspectionStatus.SUCCESS


def test_worker_emits_one_cancelled_terminal_outcome():
    outcomes = run_worker(FakeService("cancel"))
    assert len(outcomes) == 1 and outcomes[0].status is InspectionStatus.CANCELLED


def test_worker_emits_one_failed_terminal_outcome():
    outcomes = run_worker(FakeService("fail"))
    assert len(outcomes) == 1 and outcomes[0].status is InspectionStatus.FAILED
