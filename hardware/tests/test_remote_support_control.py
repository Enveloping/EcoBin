from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path

import pytest

from edge_store import EdgeStore
from remote_support_control import (
    RemoteSupportControlClient,
    RemoteSupportControlServer,
    RemoteSupportStatusBridge,
)
from remote_support_store import RemoteSupportStore


requires_unix_socket = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"),
    reason="Unix domain sockets are unavailable",
)


def _uid() -> str:
    return str(uuid.uuid4())


class Controller:
    def __init__(self, store: RemoteSupportStore):
        self.store = store
        self.wake_count = 0

    def open_session(self, **request):
        result = self.store.request_remote_support_open(**request)
        self.wake_count += 1
        return result

    def close_session(self, *, session_uid: str, command_uid: str):
        result = self.store.request_remote_support_close(
            session_uid,
            command_uid,
        )
        self.wake_count += 1
        return result


def _start_server(tmp_path: Path):
    store = RemoteSupportStore(tmp_path / "remote.db")
    store.initialize()
    controller = Controller(store)
    socket_path = tmp_path / "control.sock"
    server = RemoteSupportControlServer(
        socket_path,
        controller=controller,
        store=store,
        allowed_uids={os.getuid()} if hasattr(os, "getuid") else None,
    )
    server.start()
    return store, controller, server, RemoteSupportControlClient(socket_path)


def test_control_server_rejects_invalid_peer_uid_configuration(tmp_path: Path):
    with pytest.raises(ValueError, match="non-negative integers"):
        RemoteSupportControlServer(
            tmp_path / "control.sock",
            controller=object(),
            store=object(),
            allowed_uids={-1},
        )
    with pytest.raises(ValueError, match="socket mode"):
        RemoteSupportControlServer(
            tmp_path / "control.sock",
            controller=object(),
            store=object(),
            socket_mode=0o666,
        )


@requires_unix_socket
def test_local_control_open_close_and_status_ack(tmp_path: Path):
    store, controller, server, client = _start_server(tmp_path)
    session_uid = _uid()
    try:
        assert client.open_session(
            session_uid=session_uid,
            command_uid=_uid(),
            device_name="ECM0-TEST",
            remote_port=22011,
            expires_at="2099-01-01T00:00:00.000Z",
        ) == "ACCEPTED"
        events = client.list_status_events(limit=10)
        assert len(events) == 1
        assert events[0]["state"] == "CONNECTING"
        assert client.ack_status_event(events[0]["eventUid"]) == "ACCEPTED"
        assert client.list_status_events() == []

        assert client.close_session(
            session_uid=session_uid,
            command_uid=_uid(),
        ) == "ACCEPTED"
        assert store.get_remote_support_session()["state"] == "CLOSING"
        assert controller.wake_count == 2
    finally:
        server.stop()
        store.close()


def test_status_bridge_imports_once_then_acknowledges_agent(tmp_path: Path):
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()
    event = {
        "eventUid": _uid(),
        "sessionUid": _uid(),
        "commandUid": _uid(),
        "deviceName": "ECM0-TEST",
        "remotePort": 22012,
        "state": "CONNECTING",
        "failureCode": None,
        "occurredAt": "2030-01-01T00:00:00.000Z",
    }

    class Client:
        def __init__(self):
            self.events = [event]
            self.acked = []

        def list_status_events(self, *, limit=100):
            return list(self.events[:limit])

        def ack_status_event(self, event_uid):
            self.acked.append(event_uid)
            self.events = [
                item for item in self.events
                if item["eventUid"] != event_uid
            ]
            return "ACCEPTED"

    client = Client()
    try:
        bridge = RemoteSupportStatusBridge(client, edge)

        assert bridge.poll_once() == 1
        assert bridge.poll_once() == 0
        row = edge._conn.execute(
            "SELECT payload_json FROM event_outbox WHERE event_type=?",
            ("REMOTE_SUPPORT_TUNNEL_STATUS",),
        ).fetchone()
        assert row is not None
        assert client.acked == [event["eventUid"]]
    finally:
        edge.close()


def test_edge_import_is_idempotent_before_agent_ack(tmp_path: Path):
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()
    event = {
        "eventUid": _uid(),
        "sessionUid": _uid(),
        "commandUid": _uid(),
        "deviceName": "ECM0-TEST",
        "remotePort": 22013,
        "state": "OPEN",
        "failureCode": None,
        "occurredAt": "2030-01-01T00:00:00.000Z",
    }
    try:
        assert edge.import_remote_support_status_event(event) == "ACCEPTED"
        assert edge.import_remote_support_status_event(event) == "DUPLICATE"
        assert edge._conn.execute(
            "SELECT COUNT(*) FROM event_outbox WHERE event_uid=?",
            (event["eventUid"],),
        ).fetchone()[0] == 1
    finally:
        edge.close()
