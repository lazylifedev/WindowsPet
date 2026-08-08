from threading import Event, get_ident

from PySide6.QtCore import QObject, QThread

from windows_pet.action_models import ConfirmationDecision, ConfirmationResponse
from windows_pet.chat_service_restart_controller import (ChatServiceRestartController,
                                                          ServiceRestartExecutionThread,
                                                          ServiceRestartResolutionThread)
from windows_pet.elevation import (BrokerEntryPoint, ElevationBrokerClient,
                                   ElevatedOperationDispatcher,
                                   FakeElevatedExecutor, FakeElevationLauncher,
                                   OneShotClaimStore)
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


def test_service_controller_validates_against_the_inspection_snapshot():
    class SnapshotOnlyResolver(Resolver):
        def __init__(self):
            super().__init__()
            self.live_inspection_used = False

        def validate(self, identity, snapshot=None):
            if snapshot is None:
                self.live_inspection_used = True
                return ServiceResolutionCode.NOT_FOUND
            return super().validate(identity, snapshot)

    resolver = SnapshotOnlyResolver()
    results = []
    thread = ServiceRestartResolutionThread(resolver, ServiceRestartRequest("Spooler", SNAPSHOT), Event())
    thread.result_ready.connect(lambda identity, code: results.append((identity, code)))
    thread.run()
    assert resolver.live_inspection_used is False
    assert results[0][1] is ServiceResolutionCode.MATCHED


def test_service_restart_uses_qthread_subclasses_without_qobject_workers(qapp, qtbot):
    done, resolver = [], Resolver()
    controller = ChatServiceRestartController(done.append, resolver=resolver, dialog_factory=RejectDialog)
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
    assert controller.resolution_thread is None
    assert controller.execution_thread is None
    controller.shutdown()


def test_service_restart_execution_thread_emits_result_from_run():
    class Executor:
        def execute(self, *_args):
            return ServiceRestartOutcome(ServiceRestartStatus.CANCELLED, "cancelled")

    results = []
    thread = ServiceRestartExecutionThread(
        Executor(), "grant", object(), object(), Event()
    )
    assert isinstance(thread, QThread)
    thread.result_ready.connect(results.append)
    thread.run()
    assert results[0].status is ServiceRestartStatus.CANCELLED


def test_service_controller_maps_execution_and_verification_reasons_to_safe_messages():
    execution = ChatServiceRestartController._message_for_outcome(
        ServiceRestartOutcome(ServiceRestartStatus.FAILED, "nonzero_exit", 1)
    )
    verification = ChatServiceRestartController._message_for_outcome(
        ServiceRestartOutcome(ServiceRestartStatus.VERIFICATION_FAILED, "service_not_running", 0, "service_not_running")
    )
    verification_timeout = ChatServiceRestartController._message_for_outcome(
        ServiceRestartOutcome(ServiceRestartStatus.VERIFICATION_FAILED, "verification_timeout", 0, "verification_timeout")
    )
    assert execution == "サービスの再起動処理を実行できませんでした。"
    assert verification == "再起動処理は実行しましたが、サービスが実行中であることを確認できませんでした。"
    assert verification_timeout == "サービスの再起動後の確認がタイムアウトしました。"


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


def test_service_restart_controller_stress_100_requests_same_qapplication(qapp, qtbot):
    for _ in range(100):
        done = []
        controller = ChatServiceRestartController(
            done.append, resolver=Resolver(), dialog_factory=RejectDialog
        )
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        qtbot.waitUntil(lambda: bool(done), timeout=3000)
        qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
        controller.shutdown()


def test_service_restart_controller_create_destroy_stress_100(qapp, qtbot):
    for _ in range(100):
        done = []
        controller = ChatServiceRestartController(
            done.append, resolver=Resolver(), dialog_factory=RejectDialog
        )
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        controller.shutdown()
        qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
        assert not controller.is_busy


def test_service_restart_controller_cancel_stress_50(qapp, qtbot):
    for _ in range(50):
        started, release, done = Event(), Event(), []

        class BlockingResolver(Resolver):
            def resolve(self, query, snapshot=None):
                started.set()
                release.wait(30)
                return super().resolve(query, snapshot)

        controller = ChatServiceRestartController(
            done.append, resolver=BlockingResolver(), dialog_factory=RejectDialog
        )
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        qtbot.waitUntil(started.is_set, timeout=3000)
        controller.cancel()
        release.set()
        qtbot.waitUntil(lambda: bool(done), timeout=3000)
        qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
        controller.shutdown()


def test_service_restart_controller_shutdown_stress_50(qapp, qtbot):
    for _ in range(50):
        done = []
        class CooperativeResolver(Resolver):
            def __init__(self):
                super().__init__()
                self.cancel_event = None

            def resolve(self, query, snapshot=None):
                self.cancel_event.wait(30)
                return super().resolve(query, snapshot)

        resolver = CooperativeResolver()
        controller = ChatServiceRestartController(
            done.append, resolver=resolver, dialog_factory=RejectDialog
        )
        resolver.cancel_event = controller._cancel
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        controller.shutdown()
        qtbot.waitUntil(lambda: controller.resolution_thread is None, timeout=3000)
        assert not controller.is_busy


def test_standard_user_admin_required_uses_one_shot_elevation_and_read_only_verification(qapp, qtbot, tmp_path):
    class StandardResolver(Resolver):
        def __init__(self): super().__init__(ServiceResolutionCode.ADMIN_REQUIRED)
        def resolve(self, query, snapshot=None):
            self.last_code = ServiceResolutionCode.ADMIN_REQUIRED
            return ServiceIdentity("Spooler", "Print Spooler", "Running")
        def validate(self, identity, snapshot=None):
            return ServiceResolutionCode.ADMIN_REQUIRED
        def read_only_identity(self, service_name):
            return ServiceIdentity("Spooler", "Print Spooler", "Running")

    payloads = tmp_path / "payloads"
    elevated = FakeElevatedExecutor()
    broker = BrokerEntryPoint(
        dispatcher=ElevatedOperationDispatcher({"restart_service": elevated}),
        claims=OneShotClaimStore(tmp_path / "claims"), envelope_root=payloads,
    )
    launcher = FakeElevationLauncher(broker)
    client = ElevationBrokerClient(launcher, envelope_directory=payloads)
    direct_calls = []
    class DirectExecutor:
        def execute(self, *args):
            direct_calls.append(args)
            return ServiceRestartOutcome(ServiceRestartStatus.SUCCEEDED, "ok")
    done = []
    controller = ChatServiceRestartController(
        done.append, resolver=StandardResolver(), executor=DirectExecutor(),
        dialog_factory=ApproveDialog, elevation_client=client,
        broker_path_resolver=lambda: tmp_path / "WindowsPet.ElevationBroker.exe",
    )
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    qtbot.waitUntil(lambda: not controller.is_busy, timeout=3000)
    assert done == ["サービスを再起動しました。"]
    assert launcher.launch_count == 1 and elevated.execution_count == 1
    assert direct_calls == []
    controller.shutdown()


def test_standard_user_cancel_never_launches_elevation(qapp, qtbot, tmp_path):
    launcher = FakeElevationLauncher()
    client = ElevationBrokerClient(launcher, envelope_directory=tmp_path / "payloads")
    class StandardResolver(Resolver):
        def __init__(self): super().__init__(ServiceResolutionCode.ADMIN_REQUIRED)
        def resolve(self, query, snapshot=None):
            self.last_code = ServiceResolutionCode.ADMIN_REQUIRED
            return ServiceIdentity("Spooler", "Print Spooler", "Running")
        def validate(self, identity, snapshot=None):
            return ServiceResolutionCode.ADMIN_REQUIRED
    done = []
    controller = ChatServiceRestartController(
        done.append, resolver=StandardResolver(), dialog_factory=RejectDialog,
        elevation_client=client, broker_path_resolver=lambda: tmp_path / "broker.exe",
    )
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert launcher.launch_count == 0
    controller.shutdown()


def test_standard_user_missing_broker_fails_closed_before_elevation(qapp, qtbot, tmp_path):
    launcher = FakeElevationLauncher()
    client = ElevationBrokerClient(launcher, envelope_directory=tmp_path / "payloads")
    class StandardResolver(Resolver):
        def __init__(self): super().__init__(ServiceResolutionCode.ADMIN_REQUIRED)
        def resolve(self, query, snapshot=None):
            self.last_code = ServiceResolutionCode.ADMIN_REQUIRED
            return ServiceIdentity("Spooler", "Print Spooler", "Running")
        def validate(self, identity, snapshot=None):
            return ServiceResolutionCode.ADMIN_REQUIRED
    done = []
    controller = ChatServiceRestartController(
        done.append, resolver=StandardResolver(), dialog_factory=ApproveDialog,
        elevation_client=client, broker_path_resolver=lambda: (_ for _ in ()).throw(ValueError("missing")),
    )
    assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
    qtbot.waitUntil(lambda: bool(done), timeout=3000)
    assert launcher.launch_count == 0
    controller.shutdown()


def test_service_restart_elevation_integration_stress_normal_100_cancel_50_shutdown_50(qapp, qtbot, tmp_path):
    class StandardResolver(Resolver):
        def __init__(self): super().__init__(ServiceResolutionCode.ADMIN_REQUIRED)
        def resolve(self, query, snapshot=None):
            self.last_code = ServiceResolutionCode.ADMIN_REQUIRED
            return ServiceIdentity("Spooler", "Print Spooler", "Running")
        def validate(self, identity, snapshot=None):
            return ServiceResolutionCode.ADMIN_REQUIRED
        def read_only_identity(self, service_name):
            return ServiceIdentity("Spooler", "Print Spooler", "Running")

    def make_controller(base, *, uac_cancelled=False):
        payloads = base / "payloads"
        broker = BrokerEntryPoint(
            dispatcher=ElevatedOperationDispatcher({"restart_service": FakeElevatedExecutor()}),
            claims=OneShotClaimStore(base / "claims"), envelope_root=payloads,
        )
        launcher = FakeElevationLauncher(broker, uac_cancelled=uac_cancelled)
        client = ElevationBrokerClient(launcher, envelope_directory=payloads)
        controller = ChatServiceRestartController(
            lambda _text: None, resolver=StandardResolver(), dialog_factory=ApproveDialog,
            elevation_client=client, broker_path_resolver=lambda: base / "WindowsPet.ElevationBroker.exe",
        )
        return controller, launcher

    for index in range(100):
        controller, launcher = make_controller(tmp_path / f"normal-{index}")
        done = []
        controller.complete = done.append
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        qtbot.waitUntil(lambda: bool(done), timeout=3000)
        assert launcher.launch_count == 1 and done == ["サービスを再起動しました。"]
        controller.shutdown()
    for index in range(50):
        controller, launcher = make_controller(tmp_path / f"cancel-{index}", uac_cancelled=True)
        done = []
        controller.complete = done.append
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        qtbot.waitUntil(lambda: bool(done), timeout=3000)
        assert launcher.launch_count == 0
        controller.shutdown()
    for index in range(50):
        controller, _launcher = make_controller(tmp_path / f"shutdown-{index}", uac_cancelled=True)
        assert controller.request(ServiceRestartRequest("Spooler", SNAPSHOT))
        controller.shutdown()
        qtbot.waitUntil(lambda: controller.elevation_controller.thread is None, timeout=3000)
        assert not controller.is_busy
