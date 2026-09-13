"""Explicit recovery-only CLI; no live UART, network or hardware is opened."""
import threading
import os
import sqlite3
import pytest

import main


def test_default_main_runs_only_legacy_gateway():
    calls = []

    class Legacy:
        def run(self):
            calls.append("legacy")

    def forbidden_candidate(argv):
        pytest.fail("default entry activated native recovery")

    assert main.run_gateway([], legacy_factory=Legacy, candidate_runner=forbidden_candidate) is None
    assert calls == ["legacy"]


def test_explicit_candidate_main_never_constructs_legacy_gateway():
    def legacy():
        pytest.fail("candidate constructed the legacy gateway")

    def candidate(argv):
        assert argv == ["--store-path", "existing.db"]
        return {"admissionAllowed": False}

    assert main.run_gateway(["--native-recovery-candidate", "--store-path", "existing.db"],
        legacy_factory=legacy, candidate_runner=candidate) == {"admissionAllowed": False}


def test_candidate_refuses_missing_database_before_opening_serial(tmp_path):
    from native_recovery_entry import main as run_candidate

    missing = tmp_path / "missing.db"

    def forbidden_port(**kwargs):
        pytest.fail("missing database allowed serial activity")

    with pytest.raises(ValueError, match="existing"):
        run_candidate(["--store-path", str(missing), "--serial-device", "/dev/not-opened",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")],
            port_factory=forbidden_port, stop=lambda: True)
    assert not missing.exists()


def test_candidate_foreground_loop_is_bounded_and_closes_resources(tmp_path):
    from edge_store import EdgeStore
    from native_recovery_entry import main as run_candidate

    db = tmp_path / "edge.db"
    store = EdgeStore(str(db))
    store.initialize()
    store.close()
    owner = threading.get_ident()
    ports, stores, waits, states = [], [], [], []

    class Port:
        timeout = 0
        write_timeout = 1
        is_open = True
        in_waiting = 0

        def close(self):
            assert threading.get_ident() == owner
            self.is_open = False

    def port_factory(**kwargs):
        assert kwargs == dict(port="/dev/fake", baudrate=115200, timeout=0,
            write_timeout=1, exclusive=True)
        port = Port()
        ports.append(port)
        return port

    class Driver:
        def __init__(self, store, safety, transport, *, device_name, clock):
            assert threading.get_ident() == owner
            assert device_name == "device-1"
            assert safety.enabled
            stores.append(store)

        def poll(self):
            assert threading.get_ident() == owner
            state = {"status": "WAIT_FOR_BOOT", "admissionAllowed": False}
            states.append(state)
            return state

    result = run_candidate(["--store-path", str(db), "--serial-device", "/dev/fake",
        "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")],
        port_factory=port_factory, driver_factory=Driver, stop=lambda: len(states) == 2,
        wait=waits.append, clock=lambda: 0)
    assert result == states[-1]
    assert waits == [0.05, 0.05]
    assert not ports[0].is_open
    assert stores[0]._conn is None


@pytest.mark.parametrize("args", [["--unknown"], ["--store-path", "existing.db"],
    ["--native-recovery"], ["--other", "--native-recovery-candidate"]])
def test_unknown_arguments_do_not_construct_either_runtime(args):
    def forbidden(*args):
        pytest.fail("invalid entry constructed a runtime")
    with pytest.raises(SystemExit) as error:
        main.run_gateway(args, legacy_factory=forbidden, candidate_runner=forbidden)
    assert error.value.code == 2


def test_candidate_failure_does_not_fall_back_to_legacy():
    def forbidden():
        pytest.fail("candidate failure fell back to legacy")
    def failed(argv):
        raise OSError("candidate unavailable")
    with pytest.raises(OSError, match="candidate unavailable"):
        main.run_gateway(["--native-recovery-candidate"],
            legacy_factory=forbidden, candidate_runner=failed)


@pytest.mark.parametrize("name", ["empty.db", "text.db", "directory"])
def test_existing_non_database_is_not_initialized_or_allowed_to_open_serial(tmp_path, name):
    from native_recovery_entry import main as run_candidate
    path = tmp_path / name
    if name == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"" if name == "empty.db" else b"not a sqlite database")
    before = None if path.is_dir() else path.read_bytes()
    def forbidden(**kwargs):
        pytest.fail("invalid database opened UART")
    with pytest.raises((ValueError, RuntimeError, sqlite3.DatabaseError)):
        run_candidate(["--store-path", str(path), "--serial-device", "/dev/fake",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")],
            port_factory=forbidden, stop=lambda: True)
    if before is not None:
        assert path.read_bytes() == before


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX live serial rejection")
def test_live_entry_refuses_non_posix_before_importing_runtime_or_opening_uart(tmp_path):
    from native_recovery_entry import main as run_candidate
    path = tmp_path / "existing.db"
    path.write_bytes(b"")
    with pytest.raises(RuntimeError, match="POSIX"):
        run_candidate(["--store-path", str(path), "--serial-device", "COM999",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")])
    assert path.read_bytes() == b""


def test_actual_main_candidate_help_routes_to_recovery_parser_without_legacy(capsys):
    def forbidden():
        pytest.fail("candidate help built legacy runtime")
    with pytest.raises(SystemExit) as error:
        main.run_gateway(["--native-recovery-candidate", "--help"], legacy_factory=forbidden)
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--store-path" in output and "--updater-socket" in output


@pytest.mark.parametrize("phase", ["port-open", "driver-build", "driver-poll", "unsafe-admission", "port-close"])
def test_entry_error_closes_resources_without_retry_or_fallback(tmp_path, phase):
    from edge_store import EdgeStore
    from native_recovery_entry import main as run_candidate
    path = tmp_path / "edge.db"
    original = EdgeStore(str(path))
    original.initialize()
    original.close()
    ports, stores = [], []
    opens = []

    class Port:
        timeout = 0
        write_timeout = 1
        is_open = True
        def close(self):
            self.is_open = False
            if phase == "port-close":
                raise OSError("close failed")

    def open_port(**kwargs):
        opens.append(kwargs)
        if phase == "port-open":
            raise OSError("open failed")
        port = Port()
        ports.append(port)
        return port

    class Driver:
        def __init__(self, store, safety, transport, **kwargs):
            stores.append(store)
            if phase == "driver-build":
                raise OSError("driver build failed")
        def poll(self):
            if phase == "unsafe-admission":
                return {"admissionAllowed": True}
            raise OSError("poll failed")

    with pytest.raises((OSError, RuntimeError)):
        run_candidate(["--store-path", str(path), "--serial-device", "/dev/fake",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")],
            port_factory=open_port, driver_factory=Driver, stop=lambda: phase == "port-close")
    assert len(opens) == 1
    assert all(not port.is_open for port in ports)
    assert all(store._conn is None for store in stores)
    with sqlite3.connect(str(path), timeout=0) as check:
        check.execute("BEGIN EXCLUSIVE")
        check.rollback()


@pytest.mark.parametrize("extra", [["--baudrate", "0"], ["--write-timeout", "0"],
    ["--write-timeout", "1.1"], ["--write-timeout", "nan"], ["--write-timeout", "inf"],
    ["--device-name", " "], ["--serial-device", " "], ["--updater-socket", "relative.sock"]])
def test_bad_candidate_arguments_fail_before_serial(tmp_path, extra):
    from native_recovery_entry import main as run_candidate
    path = tmp_path / "edge.db"
    path.write_bytes(b"")
    def forbidden(**kwargs):
        pytest.fail("invalid args opened serial")
    with pytest.raises(ValueError):
        run_candidate(["--store-path", str(path), "--serial-device", "/dev/fake",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")] + extra,
            port_factory=forbidden, stop=lambda: True)


def test_old_schema_is_rejected_unchanged_before_serial(tmp_path):
    from native_recovery_entry import main as run_candidate
    path = tmp_path / "old.db"
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES(25)")
    before = path.read_bytes()
    def forbidden(**kwargs):
        pytest.fail("old schema opened native serial")
    with pytest.raises(ValueError, match="native recovery schema"):
        run_candidate(["--store-path", str(path), "--serial-device", "/dev/fake",
            "--device-name", "device-1", "--updater-socket", str(tmp_path / "updater.sock")],
            port_factory=forbidden, stop=lambda: True)
    assert path.read_bytes() == before


def test_main_runs_actual_candidate_driver_without_recovering_cloud_sends(tmp_path):
    from functools import partial
    from edge_store import EdgeStore
    from native_recovery_entry import main as run_candidate
    import uart2_protocol as uart
    path = tmp_path / "edge.db"
    store = EdgeStore(str(path))
    store.initialize()
    store.receive_mcu_event("kept-event", "E1", {})
    store.mark_event_sending("kept-event", 42)
    store.close()
    frames, waits, ports = [], [], []

    class Port:
        timeout = 0
        write_timeout = 1
        in_waiting = 0
        is_open = True
        def write(self, data):
            frames.append(data)
            return len(data)
        def close(self):
            self.is_open = False

    def factory(**kwargs):
        port = Port()
        ports.append(port)
        return port

    def forbidden_legacy():
        pytest.fail("actual candidate constructed old gateway")

    state = main.run_gateway(["--native-recovery-candidate", "--store-path", str(path),
        "--serial-device", "/dev/fake", "--device-name", "device-1",
        "--updater-socket", str(tmp_path / "absent.sock")], legacy_factory=forbidden_legacy,
        candidate_runner=partial(run_candidate, port_factory=factory,
            stop=lambda: len(waits) == 2, wait=waits.append, clock=lambda: 0))
    assert state["admissionAllowed"] is False and state["state"] == "WAITING_BOOT"
    assert len(frames) == 1
    assert uart.decode_frame(frames[0], sender_role="EDGE")["messageName"] == "BOOT_PROBE"
    assert not ports[0].is_open
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute("SELECT state,mqtt_msg_id FROM event_outbox WHERE event_uid='kept-event'").fetchone() == ("SENDING", 42)
