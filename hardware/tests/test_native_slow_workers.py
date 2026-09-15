import threading
import time
import logging

from bounded_worker import SingleSlotWorker
from command_processor import CommandProcessor


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("background worker did not finish")


def test_single_slot_worker_never_blocks_foreground_polling():
    entered = threading.Event()
    release = threading.Event()
    worker_thread = []

    def slow_external():
        worker_thread.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return "DONE"

    worker = SingleSlotWorker("test-slow-external")
    try:
        owner = threading.get_ident()
        assert worker.submit("one", slow_external)
        assert entered.wait(1)
        polls = 0
        for _ in range(100):
            polls += 1
        assert polls == 100
        assert worker.take_completion() is None
        assert worker_thread == [worker._thread.ident]
        assert worker_thread[0] != owner
        release.set()
        wait_until(lambda: not worker.busy or worker._completion is not None)
        completion = worker.take_completion()
        assert completion.key == "one"
        assert completion.value == "DONE"
        assert completion.error is None
    finally:
        release.set()
        worker.close(timeout_s=1)


def test_native_acceptance_camera_work_returns_to_foreground_for_persistence():
    owner = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()

    class Store:
        def claim_next_command(self):
            return None

        def fail_command(self, *_args, **_kwargs):
            raise AssertionError("successful worker must not fail command")

    class Uart:
        native_protocol = 2

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1

    class Acceptance:
        def __init__(self):
            self.completed_on = None

        def report_progress(self, *_args):
            pass

        def prepare(self, command):
            return (command["commandUid"], threading.get_ident())

        def execute_external(self, snapshot):
            assert threading.get_ident() != snapshot[1]
            entered.set()
            release.wait(2)
            return {"camera": "complete"}

        def complete(self, snapshot, result):
            assert result == {"camera": "complete"}
            self.completed_on = threading.get_ident()

    uart = Uart()
    acceptance = Acceptance()
    processor = CommandProcessor(
        Store(),
        uart,
        acceptance_runner=acceptance,
    )
    processor._persist_and_dispatch_device_entry_url = lambda _command: {
        "native_pending": False,
    }
    command = {
        "commandUid": "11111111-1111-4111-8111-111111111111",
        "cosGrant": {"temporary": True},
    }
    try:
        processor._request_device_acceptance(command)
        assert entered.wait(1)
        for _ in range(100):
            uart.poll()
        assert uart.polls == 100
        assert acceptance.completed_on is None
        release.set()
        wait_until(lambda: processor._acceptance_worker._completion is not None)
        assert processor.process_next()
        assert acceptance.completed_on == owner
    finally:
        release.set()
        processor.close()


def test_native_remote_socket_work_never_runs_on_uart_owner():
    owner = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class Store:
        def __init__(self):
            self.completed = None

        def claim_next_command(self):
            return None

        def complete_command(self, uid, result):
            self.completed = (uid, result, threading.get_ident())

        def fail_command(self, *_args, **_kwargs):
            raise AssertionError("successful worker must not fail command")

    class Uart:
        native_protocol = 2

    class Remote:
        def open_session(self, **parameters):
            calls.append((parameters, threading.get_ident()))
            entered.set()
            release.wait(2)
            return "OPENED"

    store = Store()
    processor = CommandProcessor(
        store,
        Uart(),
        remote_support_controller=Remote(),
    )
    command = {
        "commandUid": "11111111-1111-4111-8111-111111111111",
        "targetDeviceName": "device-1",
        "payload": {
            "sessionUid": "22222222-2222-4222-8222-222222222222",
            "remotePort": 22022,
            "expiresAt": "2030-01-01T00:00:00Z",
        },
    }
    try:
        processor._open_remote_support_tunnel(command)
        assert entered.wait(1)
        assert calls[0][1] != owner
        assert store.completed is None
        release.set()
        wait_until(lambda: processor._remote_worker._completion is not None)
        assert processor.process_next()
        assert store.completed == (
            command["commandUid"],
            {
                "sessionUid": command["payload"]["sessionUid"],
                "disposition": "OPENED",
            },
            owner,
        )
    finally:
        release.set()
        processor.close()


def test_close_discards_a_late_worker_completion_without_database_callback():
    entered = threading.Event()
    release = threading.Event()
    worker = SingleSlotWorker("test-late-close")

    def blocked():
        entered.set()
        release.wait(2)
        return "TOO_LATE"

    assert worker.submit("late", blocked)
    assert entered.wait(1)
    assert worker.close(timeout_s=0.01) is False
    release.set()
    worker._thread.join(1)
    assert worker.take_completion() is None


def test_uart_owner_stall_monitor_is_rate_limited_local_diagnostics_only(
    monkeypatch,
    caplog,
):
    import native_business_runtime as native

    owner = object.__new__(native.NativeBusinessRuntime)
    owner.clock = lambda: 10_000
    owner._owner_stall_threshold_ms = 250.0
    owner._owner_stall_log_interval_ms = 60_000
    owner._last_owner_stall_log_ms = -60_000
    owner._last_owner_timings = {}
    monkeypatch.setattr(native, "monotonic_ns", lambda: 400_000_000)

    with caplog.at_level(logging.WARNING, logger="native-uart-owner"):
        owner._record_owner_timings(
            started_ns=0,
            serial_ns=25_000_000,
            completion_ns=50_000_000,
        )
        owner._record_owner_timings(
            started_ns=0,
            serial_ns=25_000_000,
            completion_ns=50_000_000,
        )

    assert owner.owner_timing_snapshot() == {
        "serialPollMs": 25.0,
        "databaseShortTransactionsMs": 325.0,
        "completionQueueMs": 50.0,
        "totalMs": 400.0,
    }
    messages = [
        record.message
        for record in caplog.records
        if "UART_OWNER_STALLED" in record.message
    ]
    assert len(messages) == 1
