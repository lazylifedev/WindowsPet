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
