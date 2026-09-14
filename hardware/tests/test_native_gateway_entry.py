"""Actual gateway/command routing, with no serial, cloud or physical IO."""
import threading
from types import SimpleNamespace
import uuid

import pytest

import main
from command_processor import CommandProcessor
from hardware.tests.test_command_processor import make_store, valid_service_command


def test_native_runtime_error_trace_is_bounded_and_repeats_are_summarized(
    monkeypatch,
):
    now = [0.0]
    traces = []
    summaries = []
    monkeypatch.setattr(main.logger, "exception", lambda *args: traces.append(args))
    monkeypatch.setattr(main.logger, "error", lambda *args: summaries.append(args))
    reporter = main._NativeRuntimeErrorReporter(clock=lambda: now[0])

    error = RuntimeError("persistent failure")
    reporter.report(error)
    for _ in range(20):
        reporter.report(error)

    assert len(traces) == 1
    assert summaries == []
    now[0] = 60.0
    reporter.report(error)
    assert len(traces) == 1
    assert len(summaries) == 1
    assert summaries[0][1] == 21

    reporter.report(ValueError("a different failure class"))
    assert len(traces) == 2


def test_native_gateway_owns_uart_and_commands_on_one_foreground_thread(monkeypatch):
    edge = main.EcoBinEdge.__new__(main.EcoBinEdge)
    edge._native_mode = True
    edge._exit_flag = threading.Event()
    edge._runtime_snapshot_requested = threading.Event()
    edge._report_factory_progress = lambda **values: None
    owner, trace, threads = threading.get_ident(), [], []

    def action(name):
        def call(*args, **kwargs):
            assert threading.get_ident() == owner
            trace.append(name)
            if name == "commands":
                edge._exit_flag.set()
            return {"uartState": "READY"}
        return call

    class Thread:
        def __init__(self, *, target, daemon, name):
            assert daemon
            threads.append(name)

        def start(self):
            pass  # The cloud boundary is a stub; no real connection is opened.

    monkeypatch.setattr(main.threading, "Thread", Thread)
    monkeypatch.setattr(main, "boot_sequence", lambda **kw: pytest.fail("legacy boot entered"))
    monkeypatch.setattr(main, "notify_systemd_ready", action("service-ready"))
    edge.business_control = SimpleNamespace(start=action("control-start"), mark_ready=action("control-ready"))
    edge.uart = edge.work = SimpleNamespace(open=action("serial-open"), poll=action("poll"))
    edge.cloud_transport = SimpleNamespace(connect=action("cloud-connect"), run_forever=lambda: None, connected=True)
    edge.commands = SimpleNamespace(process_next=action("commands"))
    edge._poll_remote_support_status = action("support")
    edge._publish_runtime_snapshot_now = action("snapshot")
    edge._shutdown = action("shutdown")
    edge.run()
    assert trace == ["control-start", "serial-open", "control-ready", "service-ready",
        "poll", "commands", "shutdown"]
    assert threads == ["native-cloud", "clock-health", "runtime-snap", "factory-progress", "native-support"]
    assert "uart-evt" not in threads and "cmd-consumer" not in threads


@pytest.mark.parametrize("state,progress", [("READY", "READY"), ("FAULT", "FAILED"), ("STARTING", "STARTING")])
def test_native_factory_progress_uses_supported_state_without_a_legacy_probe(state, progress):
    edge = main.EcoBinEdge.__new__(main.EcoBinEdge)
    edge._native_mode = True
    edge.uart = SimpleNamespace(uart_state=state)
    assert edge._current_uart_progress_state() == progress


def test_native_factory_progress_reads_one_snapshot_generation():
    edge = main.EcoBinEdge.__new__(main.EcoBinEdge)
    edge._native_mode = True

    class Uart:
        reads = 0

        @property
        def uart_state(self):
            self.reads += 1
            if self.reads > 1:
                raise AssertionError("factory progress mixed UART snapshots")
            return "FAULT"

    edge.uart = Uart()
    assert edge._current_uart_progress_state() == "FAILED"
    assert edge.uart.reads == 1


def test_native_cloud_connect_wait_does_not_block_foreground_uart(monkeypatch):
    edge = main.EcoBinEdge.__new__(main.EcoBinEdge)
    edge._native_mode = True
    edge._exit_flag = threading.Event()
    edge._runtime_snapshot_requested = threading.Event()
    edge._report_factory_progress = lambda **values: None
    edge.business_control = None
    started, release = threading.Event(), threading.Event()
    owner, polls = threading.get_ident(), []
    real_thread, workers = threading.Thread, []

    def blocked_connect():
        assert threading.get_ident() != owner
        started.set()
        assert release.wait(3)

    def poll():
        assert threading.get_ident() == owner
        assert started.wait(1)
        assert not release.is_set()
        polls.append(True)
        edge._exit_flag.set()
        return {"uartState": "READY"}

    class Thread:
        def __init__(self, *, target, daemon, name):
            self.worker = real_thread(target=target, daemon=daemon) if name == "native-cloud" else None
        def start(self):
            if self.worker:
                workers.append(self.worker)
                self.worker.start()

    monkeypatch.setattr(main.threading, "Thread", Thread)
    monkeypatch.setattr(main, "notify_systemd_ready", lambda *args: None)
    edge.uart = edge.work = SimpleNamespace(open=lambda **kwargs: None, poll=poll)
    edge.cloud_transport = SimpleNamespace(connect=blocked_connect, run_forever=lambda: None)
    edge.commands = SimpleNamespace(process_next=lambda: None)
    edge._shutdown = release.set
    try:
        edge.run()
        assert polls == [True]
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()


@pytest.mark.parametrize("clean", [False, True])
def test_native_pending_is_not_an_acceptance_ack(tmp_path, clean):
    store = make_store(tmp_path)
    try:
        command = valid_service_command("start-clean-operation.service-wire.json" if clean
            else "start-delivery-session.service-wire.json")
        assert store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
        native_uid = str(uuid.uuid4())
        class Work:
            native_protocol = 2

            def start_delivery_command(self, value):
                assert value["commandUid"] == command["commandUid"]
                return {"native_pending": True, "mcu_command_uid": native_uid}

            start_clean_command = start_delivery_command

        processor = CommandProcessor(store, object(), Work())
        assert processor.process_next()
        saved = store.get_command(command["commandUid"])
        assert saved["state"] == "WAITING_MCU_RESULT"
        assert saved["mcu_command_uid"] == native_uid
        assert saved["result"] == {"native_pending": True, "mcu_command_uid": native_uid}
        assert "acked" not in saved["result"]
        assert not store.list_native_commands()  # Routing does not fabricate a wire decision.
    finally:
        store.close()


def test_native_protocol_never_silently_constructs_v1_link(monkeypatch):
    monkeypatch.setattr(main, "MCU_PROTOCOL_MODE", "uart-v2")
    with pytest.raises(ValueError, match="foreground business owner"):
        main._make_uart_link("never-open", 1, 115200, 1, None)


def test_native_direct_cloud_entry_constructs_acceptance_runner(monkeypatch):
    edge = main.EcoBinEdge.__new__(main.EcoBinEdge)
    edge._native_mode = True
    edge.store = object()
    edge.uart = object()
    edge.photo = object()
    edge.cos_uploader = object()
    captured = {}

    class Runner:
        def __init__(self, store, uart, photo, uploader, **options):
            captured.update(
                store=store,
                uart=uart,
                photo=photo,
                uploader=uploader,
                options=options,
            )

    monkeypatch.setattr("device_acceptance.DeviceAcceptanceRunner", Runner)
    runner = edge._make_device_acceptance_runner(False)
    assert isinstance(runner, Runner)
    assert captured["store"] is edge.store
    assert captured["uart"] is edge.uart
    assert captured["options"]["device_name"] == main.DEVICE_NAME
    assert edge._make_device_acceptance_runner(True) is None
