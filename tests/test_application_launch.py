from types import SimpleNamespace
from pathlib import Path
from windows_pet.application_launch import ApplicationLaunchValidator, LaunchValidationCode, ApplicationLaunchExecutor, ApplicationLaunchStatus


def test_validator_rejects_relative_and_non_exe():
    validator = ApplicationLaunchValidator()
    assert validator.validate(SimpleNamespace(executable_path="app.exe", display_name="x"))[1] is LaunchValidationCode.RELATIVE_PATH
    assert validator.validate(SimpleNamespace(executable_path="C:/fake/app.bat", display_name="x"))[1] is LaunchValidationCode.UNSUPPORTED_EXTENSION


def test_executor_does_not_start_without_valid_grant():
    calls = []
    executor = ApplicationLaunchExecutor(SimpleNamespace(consume_for=lambda *args: SimpleNamespace(success=False, reason=SimpleNamespace(value="not_found"))), process_factory=lambda *a, **k: calls.append(1))
    outcome = executor.execute("g", SimpleNamespace(), SimpleNamespace(canonical_path="C:/fake/app.exe", display_name="x", file_size=1, modified_time_ns=1))
    assert outcome.status is ApplicationLaunchStatus.REJECTED and not calls
