from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import communication_agent
from communication_agent import (
    COMMUNICATION_PROTOCOL_NAME,
    CommunicationAgent,
    CommunicationController,
)
from communication_store import CommunicationStore
from local_control import LocalControlClient


def test_controller_exposes_explicit_stage_three_disabled_boundaries(tmp_path: Path):
    store = CommunicationStore(tmp_path / "communication.db", process_id=lambda: 77)
    store.initialize()
    try:
        start = store.record_process_start("communication-3.0.0")
        controller = CommunicationController(store, "communication-3.0.0")
        controller.record_started(start)

        health = controller.health({})
        status = controller.get_status({})

        assert health == {
            "component": "COMMUNICATION_AGENT",
            "status": "READY",
            "runtimeInstanceUid": start["startUid"],
            "releaseVersion": "communication-3.0.0",
            "startedAt": start["startedAt"],
            "localProtocolName": COMMUNICATION_PROTOCOL_NAME,
            "localProtocolMajor": 1,
            "localProtocolMinor": 0,
            "onenetOwnership": "DISABLED",
            "remoteUpdateRouting": "DISABLED",
        }
        assert status["runtimeInstanceUid"] == start["startUid"]
        assert status["schemaVersion"] == 1
        assert status["processStartCount"] == 1
    finally:
        store.close()


def test_controller_does_not_claim_ready_before_start_fact(tmp_path: Path):
    store = CommunicationStore(tmp_path / "communication.db")
    store.initialize()
    try:
        controller = CommunicationController(store, "communication-3.0.0")
        with pytest.raises(RuntimeError, match="start fact is unavailable"):
            controller.health({})
    finally:
        store.close()


class FakeStore:
    def __init__(self):
        self.starts = []
        self.closed = False

    def record_process_start(self, release_version):
        self.starts.append(release_version)
        return {
            "startUid": "2a1c55c2-5422-496d-9af7-ac4880e5c50e",
            "releaseVersion": release_version,
            "processId": 12,
            "startedAt": "2030-01-01T00:00:00.000Z",
        }

    def close(self):
        self.closed = True


class FakeController:
    def __init__(self):
        self.fact = None

    def record_started(self, fact):
        self.fact = fact


class FakeServer:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.is_running = True
        self.failure = None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        self.is_running = False


def test_agent_records_start_before_serving_and_stops_independently():
    store = FakeStore()
    controller = FakeController()
    server = FakeServer()
    agent = CommunicationAgent(store, controller, server, "communication-3.0.0")

    agent.start()
    agent.start()
    agent.stop()

    assert store.starts == ["communication-3.0.0"]
    assert controller.fact["releaseVersion"] == "communication-3.0.0"
    assert server.started is True
    assert server.stopped is True
    assert store.closed is True


def test_agent_wait_fails_if_control_thread_dies():
    store = FakeStore()
    controller = FakeController()
    server = FakeServer()
    server.is_running = False
    server.failure = OSError("listener failed")
    agent = CommunicationAgent(store, controller, server, "communication-3.0.0")

    with pytest.raises(RuntimeError, match="stopped unexpectedly") as captured:
        agent.wait(check_interval_seconds=0.001)
    assert captured.value.__cause__ is server.failure


def test_allowed_user_names_are_resolved_strictly(monkeypatch):
    fake_pwd = SimpleNamespace(
        getpwnam=lambda name: SimpleNamespace(pw_uid={"business": 101, "updater": 102}[name])
    )
    monkeypatch.setitem(sys.modules, "pwd", fake_pwd)

    assert communication_agent.resolve_allowed_uids(
        [103],
        ["business", "updater"],
    ) == frozenset({0, 101, 102, 103})

    def missing(_name):
        raise KeyError("missing")

    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=missing))
    with pytest.raises(ValueError, match="does not exist"):
        communication_agent.resolve_allowed_uids(None, ["missing"])


def test_socket_group_is_resolved_strictly(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "grp",
        SimpleNamespace(getgrnam=lambda name: SimpleNamespace(gr_gid=222)),
    )
    assert communication_agent.resolve_socket_gid("ecobin-ipc") == 222

    def missing(_name):
        raise KeyError("missing")

    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=missing))
    with pytest.raises(ValueError, match="does not exist"):
        communication_agent.resolve_socket_gid("missing")


def test_parser_supports_all_runtime_identity_options():
    args = communication_agent.build_parser().parse_args(
        [
            "--state",
            "/state/communication.db",
            "--socket",
            "/run/communication.sock",
            "--release-version",
            "communication-3.0.0",
            "--allowed-uid",
            "17",
            "--allowed-user",
            "ecobin-business",
            "--allowed-user",
            "ecobin-updater",
            "--socket-group",
            "ecobin-ipc",
        ]
    )

    assert args.state == "/state/communication.db"
    assert args.socket == "/run/communication.sock"
    assert args.release_version == "communication-3.0.0"
    assert args.allowed_uid == [17]
    assert args.allowed_user == ["ecobin-business", "ecobin-updater"]
    assert args.socket_group == "ecobin-ipc"


def test_release_version_must_be_injected_by_the_image(tmp_path: Path):
    args = argparse.Namespace(
        state=str(tmp_path / "communication.db"),
        socket=str(tmp_path / "control.sock"),
        release_version=None,
        allowed_uid=[0],
        allowed_user=None,
        socket_group=None,
    )
    with pytest.raises(ValueError, match="release-version is required"):
        communication_agent.build_agent(args)
    assert not (tmp_path / "communication.db").exists()


def test_systemd_ready_and_stopping_support_abstract_socket(monkeypatch):
    sent = []

    class Notifier:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendto(self, message, address):
            sent.append((message, address))

    monkeypatch.setenv("NOTIFY_SOCKET", "@ecobin-communication-notify")
    monkeypatch.setattr(communication_agent.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(
        communication_agent.socket,
        "socket",
        lambda *_args: Notifier(),
    )

    communication_agent.notify_systemd_ready()
    communication_agent.notify_systemd_stopping()

    assert sent == [
        (b"READY=1", "\0ecobin-communication-notify"),
        (b"STOPPING=1", "\0ecobin-communication-notify"),
    ]


def test_agent_source_has_no_onenet_mqtt_or_inet_dependencies():
    source = Path(communication_agent.__file__).read_text(encoding="utf-8")
    lowered = source.casefold()
    assert "import paho" not in lowered
    assert "device_credentials" not in lowered
    assert "af_inet" not in lowered
    assert "mqtt_client" not in lowered


@pytest.mark.skipif(
    os.name != "posix"
    or not hasattr(socket, "AF_UNIX")
    or not hasattr(socket, "SO_PEERCRED"),
    reason="authenticated Unix-domain socket integration requires Linux",
)
def test_real_agent_health_and_status_over_authenticated_socket(tmp_path: Path):
    args = argparse.Namespace(
        state=str(tmp_path / "communication.db"),
        socket=str(tmp_path / "control.sock"),
        release_version="communication-3.0.0",
        allowed_uid=[os.getuid()],
        allowed_user=None,
        socket_group=None,
    )
    agent = communication_agent.build_agent(args)
    client = LocalControlClient(
        args.socket,
        protocol_name=COMMUNICATION_PROTOCOL_NAME,
    )
    try:
        agent.start()
        health = client.request("HEALTH", {})
        status = client.request("GET_STATUS", {})
        assert health["onenetOwnership"] == "DISABLED"
        assert health["remoteUpdateRouting"] == "DISABLED"
        assert status["runtimeInstanceUid"] == health["runtimeInstanceUid"]
        assert status["schemaVersion"] == 1
    finally:
        agent.stop()
