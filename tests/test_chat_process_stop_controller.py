from threading import Event, get_ident

from PySide6.QtCore import QObject

from windows_pet.chat_process_stop_controller import ChatProcessStopController
from windows_pet.process_stop import (ProcessIdentity, ProcessValidationCode,
                                      PowerShellExecutionStatus)
from windows_pet.process_stop_request import ProcessStopRequest


class _Resolver:
    def __init__(self):
        self.thread_id = None

    def resolve(self, _pid):
        self.thread_id = get_ident()
        return ProcessIdentity(42, "notepad", "100")

    def validate(self, _identity, _expected=None):
        return ProcessValidationCode.OK


class _RejectDialog:
    Accepted = 1

    def __init__(self, _proposal, session, *, parent=None):
        self.response = type("Response", (), {"decision": None, "session_id": session.session_id})()

    def exec(self):
        return 0


def test_process_stop_controller_is_qobject_and_resolves_off_gui_thread(qapp, qtbot):
    completed, resolver = [], _Resolver()
    controller = ChatProcessStopController(completed.append, resolver=resolver,
                                           dialog_factory=_RejectDialog)
    assert isinstance(controller, QObject)
    assert controller.request(ProcessStopRequest(42, "notepad"))
    qtbot.waitUntil(lambda: bool(completed), timeout=3000)
    qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
    assert resolver.thread_id != get_ident()
    assert not controller.is_busy
    controller.shutdown()


def test_process_stop_cancel_never_performs_child_cleanup():
    class Executor:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    executor = Executor()
    controller = ChatProcessStopController(lambda _text: None, executor=executor)
    controller.cancel()
    assert executor.cancelled


def test_runner_cancel_is_signal_only():
    from windows_pet.process_stop import PowerShellExecutionRunner

    class Process:
        def terminate(self):
            raise AssertionError("GUI cancellation must not terminate the child")

    runner = PowerShellExecutionRunner(None)
    runner._active_process = Process()
    runner.cancel()
    assert runner._cancel.is_set()
