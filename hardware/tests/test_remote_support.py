from __future__ import annotations

import io
import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from device_credentials import RemoteSupportCredentials
from edge_store import EdgeStore
from remote_support import RemoteSupportManager, _BoundedOutputCollector
from remote_support_store import RemoteSupportStore


def _uid() -> str:
    return str(uuid.uuid4())


def _public() -> str:
    return ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


def _private() -> str:
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode("ascii")


def credentials() -> RemoteSupportCredentials:
    return RemoteSupportCredentials(
        server_host="support.example.com",
        server_port=2222,
        server_user="ecobin-tunnel",
        server_host_public_key=_public(),
        identity_private_key=_private(),
        jump_user="ecobin-jump",
        maintenance_principal="ecobin-device-ECM0-TEST",
        maintenance_ca_public_key=_public(),
    )


def store(tmp_path: Path) -> RemoteSupportStore:
    result = RemoteSupportStore(tmp_path / "remote-support.db")
    result.initialize()
    return result


def legacy_store(tmp_path: Path) -> EdgeStore:
    result = EdgeStore(str(tmp_path / "edge.db"))
    result.initialize()
    return result


class Clock:
    def __init__(self):
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.ticks = 100.0

    def utc_now(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.ticks

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.ticks += seconds


class FakeProcess:
    def __init__(self, output: bytes = b""):
        self.stderr = io.BytesIO(output)
        self.return_code = None
        self.terminated = False

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.return_code = -15

    def kill(self):
        self.return_code = -9

    def wait(self, timeout=None):
        if self.return_code is None:
            raise subprocess.TimeoutExpired("ssh", timeout)
        return self.return_code


class BrokenPollProcess(FakeProcess):
    def poll(self):
        raise RuntimeError("process supervision failed")


class CapturingPopen:
    def __init__(self, processes: list[FakeProcess] | None = None):
        self.calls = []
        self.processes = list(processes or [FakeProcess()])

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return self.processes.pop(0)


def test_v9_store_is_additively_upgraded_to_remote_support_v10(tmp_path: Path):
    path = tmp_path / "edge.db"
    original = EdgeStore(str(path))
    original.initialize()
    original._conn.execute("DELETE FROM schema_version WHERE version=10")
    original._conn.execute("DROP TABLE remote_support_session")
    original._conn.commit()
    original.close()

    upgraded = EdgeStore(str(path))
    upgraded.initialize()

    assert upgraded._conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()[0] == 10
    assert upgraded._conn.execute(
        "SELECT name FROM sqlite_master WHERE name='remote_support_session'"
    ).fetchone() is not None
    upgraded.close()


def test_open_is_persisted_outside_work_slot_and_emits_connecting(tmp_path: Path):
    edge = legacy_store(tmp_path)
    session_uid = _uid()
    command_uid = _uid()

    disposition = edge.request_remote_support_open(
        session_uid=session_uid,
        command_uid=command_uid,
        device_name="ECM0-TEST",
        remote_port=22011,
        expires_at="2099-01-01T00:00:00.000Z",
    )

    assert disposition == "ACCEPTED"
    assert edge.get_work_slot() is None
    assert edge._conn.execute(
        "SELECT work_type FROM work_slot WHERE slot_id=1"
    ).fetchone()["work_type"] == "NONE"
    row = edge.get_remote_support_session()
    assert row["state"] == "CONNECTING"
    event = edge._conn.execute(
        "SELECT payload_json FROM event_outbox WHERE event_type=?",
        ("REMOTE_SUPPORT_TUNNEL_STATUS",),
    ).fetchone()
    envelope = json.loads(event["payload_json"])
    assert envelope["payload"] == {
        "sessionUid": session_uid,
        "state": "CONNECTING",
        "remotePort": 22011,
        "failureCode": None,
    }
    edge.close()


def test_terminal_session_uid_is_idempotent_and_never_reopened(tmp_path: Path):
    edge = legacy_store(tmp_path)
    session_uid = _uid()
    request = {
        "session_uid": session_uid,
        "command_uid": _uid(),
        "device_name": "ECM0-TEST",
        "remote_port": 22011,
        "expires_at": "2099-01-01T00:00:00.000Z",
    }
    assert edge.request_remote_support_open(**request) == "ACCEPTED"
    assert edge.transition_remote_support_session(
        session_uid,
        "CLOSED",
    ) == "ACCEPTED"
    event_count = edge._conn.execute(
        "SELECT COUNT(*) FROM event_outbox WHERE event_type=?",
        ("REMOTE_SUPPORT_TUNNEL_STATUS",),
    ).fetchone()[0]

    replay = {**request, "command_uid": _uid()}
    assert edge.request_remote_support_open(**replay) == "DUPLICATE"
    assert edge.get_remote_support_session()["state"] == "CLOSED"
    assert edge._conn.execute(
        "SELECT COUNT(*) FROM event_outbox WHERE event_type=?",
        ("REMOTE_SUPPORT_TUNNEL_STATUS",),
    ).fetchone()[0] == event_count

    changed = {**replay, "remote_port": 22012}
    assert edge.request_remote_support_open(**changed) == "CONFLICT"
    assert edge.get_remote_support_session()["state"] == "CLOSED"
    edge.close()


def test_manager_uses_strict_argv_marks_open_and_closes_on_request(tmp_path: Path):
    edge = store(tmp_path)
    clock = Clock()
    process = FakeProcess(b"diagnostic" * 1000)
    popen = CapturingPopen([process])
    manager = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run",
        popen_factory=popen,
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        stabilization_seconds=2,
    )
    session_uid = _uid()
    manager.open_session(
        session_uid=session_uid,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22012,
        expires_at="2099-01-01T00:00:00.000Z",
    )

    manager.poll_once()
    argv, options = popen.calls[0]
    assert options["shell"] is False
    assert "-N" not in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "ExitOnForwardFailure=yes" in argv
    assert "127.0.0.1:22012:127.0.0.1:22" in argv
    assert argv[-2:] == [
        "ecobin-tunnel@support.example.com",
        "ecobin-lease-guard",
    ]
    identity = Path(argv[argv.index("-i") + 1])
    assert identity.read_text(encoding="ascii").startswith(
        "-----BEGIN OPENSSH PRIVATE KEY-----"
    )

    clock.advance(2)
    manager.poll_once()
    assert edge.get_remote_support_session()["state"] == "OPEN"

    manager.close_session(session_uid=session_uid, command_uid=_uid())
    manager.poll_once()
    assert process.terminated is True
    assert edge.get_remote_support_session()["state"] == "CLOSED"
    edge.close()


def test_open_state_survives_process_restart_and_reconnects(tmp_path: Path):
    edge = store(tmp_path)
    clock = Clock()
    first = FakeProcess()
    first_factory = CapturingPopen([first])
    manager = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run-1",
        popen_factory=first_factory,
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        stabilization_seconds=0,
    )
    session_uid = _uid()
    manager.open_session(
        session_uid=session_uid,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22013,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    manager.poll_once()
    manager.poll_once()
    assert edge.get_remote_support_session()["state"] == "OPEN"
    manager.stop()
    assert edge.get_remote_support_session()["state"] == "OPEN"

    replacement = FakeProcess()
    replacement_factory = CapturingPopen([replacement])
    recovered = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run-2",
        popen_factory=replacement_factory,
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        stabilization_seconds=0,
    )
    recovered.poll_once()
    assert replacement_factory.calls
    assert edge.get_remote_support_session()["state"] == "CONNECTING"
    recovered.poll_once()
    assert edge.get_remote_support_session()["state"] == "OPEN"
    recovered.stop()
    edge.close()


def test_expiry_and_bounded_start_failures_are_terminal(tmp_path: Path):
    edge = store(tmp_path)
    clock = Clock()
    manager = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run-expiry",
        popen_factory=CapturingPopen(),
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )
    expired_session = _uid()
    edge.request_remote_support_open(
        session_uid=expired_session,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22011,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    clock.now = datetime(2100, 1, 1, tzinfo=timezone.utc)
    manager.poll_once()
    assert edge.get_remote_support_session()["state"] == "EXPIRED"

    clock.now = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("ssh")

    failed = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run-failed",
        popen_factory=unavailable,
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
        retry_base_seconds=1,
        retry_max_seconds=2,
        max_consecutive_attempts=2,
    )
    failed_session = _uid()
    failed.open_session(
        session_uid=failed_session,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22014,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    failed.poll_once()
    clock.advance(1)
    failed.poll_once()
    row = edge.get_remote_support_session()
    assert row["state"] == "FAILED"
    assert row["failure_code"] == "SSH_NOT_AVAILABLE"
    edge.close()


def test_child_output_collector_keeps_only_bounded_tail():
    collector = _BoundedOutputCollector(io.BytesIO(b"x" * 10_000), limit=128)
    if collector._thread is not None:
        collector._thread.join(timeout=1)

    assert collector.snapshot() == b"x" * 128
    collector.close()


def test_unexpected_process_supervision_failure_is_terminal(tmp_path: Path):
    edge = store(tmp_path)
    clock = Clock()
    process = BrokenPollProcess()
    manager = RemoteSupportManager(
        edge,
        credentials(),
        runtime_dir=tmp_path / "run-broken-process",
        popen_factory=CapturingPopen([process]),
        utc_now=clock.utc_now,
        monotonic=clock.monotonic,
    )
    session_uid = _uid()
    manager.open_session(
        session_uid=session_uid,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22011,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    manager.poll_once()

    assert manager._poll_safely() is False
    row = edge.get_remote_support_session()
    assert row["state"] == "FAILED"
    assert row["failure_code"] == "PROCESS_SUPERVISION_FAILED"
    assert process.terminated is True
    edge.close()
