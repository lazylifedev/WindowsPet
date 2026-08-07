from threading import Event, get_ident

from PySide6.QtCore import QObject

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.chat_service_restart_controller import ChatServiceRestartController
from windows_pet.service_restart import (ServiceIdentity, ServiceResolutionCode,
                                         ServiceRestartOutcome, ServiceRestartStatus)
from windows_pet.service_restart_request import ServiceRestartRequest


SNAPSHOT = ({"name": "Spooler", "displayName": "Print Spooler", "state": "Running", "startMode": "Auto"},)


class Resolver:
    def __init__(self, code=ServiceResolutionCode.MATCHED): self.code, self.thread_id = code, None
    def resolve(self, query, snapshot=None):
        self.thread_id = get_ident(); self.last_code = self.code
        return ServiceIdentity("Spooler", "Print Spooler", "Running") if self.code is ServiceResolutionCode.MATCHED else None
    def validate(self, identity, snapshot=None): return self.code


class RejectDialog:
    def __init__(self, proposal, session, *, parent=None): self.response = ConfirmationResponse(ConfirmationDecision.CANCEL, session.session_id, proposal.proposal_id, proposal.fingerprint)
    def exec(self): return 0


class ApproveDialog:
    def __init__(self, proposal, session, *, parent=None): self.response = ConfirmationResponse(ConfirmationDecision.APPROVE, session.session_id, proposal.proposal_id, proposal.fingerprint)
    def exec(self): return 1


def test_service_controller_resolves_snapshot_off_gui_thread_and_cancelled_confirmation(qapp, qtbot):
    done, resolver = [], Resolver()
    controller = ChatServiceRestartController(done.append, resolver=resolver, dialog_factory=RejectDialog)
    assert isinstance(controller, QObject)
    assert not controller.request(ServiceRestartRequest("Spooler"))
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
    assert resolver.thread_id != get_ident() and not controller.is_busy
    controller.shutdown()


def test_service_controller_rejects_protected_before_confirmation(qapp, qtbot):
    done = []
    controller = ChatServiceRestartController(done.append, resolver=Resolver(ServiceResolutionCode.PROTECTED), dialog_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no dialog")))
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert not controller.is_busy
    controller.shutdown()


def test_service_controller_approved_flow_runs_worker_and_completes(qapp, qtbot):
    class Executor:
        def __init__(self): self.calls = 0; self.cancelled = False
        def reset_cancel(self): pass
        def cancel(self): self.cancelled = True
        def execute(self, grant_id, proposal, identity, cancel):
            self.calls += 1
            return ServiceRestartOutcome(ServiceRestartStatus.SUCCEEDED, "ok")
    done, executor = [], Executor()
    controller = ChatServiceRestartController(done.append, resolver=Resolver(), executor=executor, dialog_factory=ApproveDialog)
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    qtbot.waitUntil(lambda: controller.execution_thread is None, timeout=3000)
    assert executor.calls == 1 and not controller.is_busy
    controller.shutdown()


def test_service_controller_cancel_and_shutdown_are_cooperative(qapp, qtbot):
    class BlockingResolver(Resolver):
        def resolve(self, query, snapshot=None):
            self.thread_id = get_ident()
            cancelled.wait(2)
            self.last_code = ServiceResolutionCode.NOT_FOUND
            return None
    cancelled = Event()
    class Executor:
        def cancel(self): cancelled.set()
    controller = ChatServiceRestartController(lambda _text: None, resolver=BlockingResolver(), executor=Executor(), dialog_factory=RejectDialog)
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: controller.resolution_thread is not None and controller.resolution_thread.isRunning(), timeout=3000)
    controller.shutdown()
    qtbot.waitUntil(lambda: not controller.is_busy, timeout=3000)
