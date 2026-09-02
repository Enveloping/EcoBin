from __future__ import annotations

import json
import os
import socket
import stat
import struct
import time
import uuid
from pathlib import Path

import pytest

import local_control
from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    MAX_MESSAGE_BYTES,
    LocalControlAction,
    LocalControlActionError,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlServer,
    LocalControlUnavailable,
    encode_message,
    receive_message,
)


PROTOCOL = "ecobin.test.control"


def _uid() -> str:
    return str(uuid.uuid4())


def _request(action: str = "HEALTH", payload=None, **overrides):
    document = {
        "protocolName": PROTOCOL,
        "protocolMajor": LOCAL_PROTOCOL_MAJOR,
        "protocolMinor": LOCAL_PROTOCOL_MINOR,
        "requestId": _uid(),
        "action": action,
        "payload": {} if payload is None else payload,
    }
    document.update(overrides)
    return document


class BufferConnection:
    def __init__(self, incoming: bytes, *, peer_uid: int = 1001):
        self.incoming = incoming
        self.peer_uid = peer_uid
        self.sent = bytearray()
        self.timeout = None

    def recv(self, amount: int) -> bytes:
        chunk = self.incoming[:amount]
        self.incoming = self.incoming[amount:]
        return chunk

    def sendall(self, value: bytes) -> None:
        self.sent.extend(value)

    def getsockopt(self, *_args):
        return struct.pack("3i", 42, self.peer_uid, 1001)

    def settimeout(self, value):
        self.timeout = value


def _server(tmp_path: Path, handler=lambda _payload: {"status": "READY"}, **kwargs):
    return LocalControlServer(
        tmp_path / "control.sock",
        protocol_name=PROTOCOL,
        actions={
            "HEALTH": LocalControlAction(
                handler,
                payload_fields=frozenset(),
                allowed_uids=frozenset({1001}),
            ),
        },
        allowed_uids={1001},
        **kwargs,
    )


def _handle(monkeypatch, server: LocalControlServer, document, *, peer_uid=1001):
    monkeypatch.setattr(local_control.socket, "SO_PEERCRED", 17, raising=False)
    connection = BufferConnection(encode_message(document), peer_uid=peer_uid)
    server._handle_connection(connection)
    return json.loads(connection.sent.decode("utf-8"))


def test_request_success_is_strict_and_correlated(monkeypatch, tmp_path: Path):
    request = _request()
    response = _handle(monkeypatch, _server(tmp_path), request)

    assert response == {
        "protocolName": PROTOCOL,
        "protocolMajor": 1,
        "protocolMinor": 0,
        "requestId": request["requestId"],
        "ok": True,
        "result": {"status": "READY"},
    }


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda value: value.update({"unexpected": True}), "REQUEST_INVALID"),
        (lambda value: value.update({"protocolMajor": 2}), "PROTOCOL_INCOMPATIBLE"),
        (
            lambda value: value.update({"protocolName": "ecobin.other.control"}),
            "PROTOCOL_INCOMPATIBLE",
        ),
        (lambda value: value.update({"protocolMinor": -1}), "REQUEST_INVALID"),
        (lambda value: value.update({"action": "UNKNOWN"}), "REQUEST_INVALID"),
        (lambda value: value.update({"payload": {"extra": True}}), "REQUEST_INVALID"),
    ],
)
def test_invalid_requests_are_rejected_with_request_identity(
    monkeypatch,
    tmp_path: Path,
    mutate,
    expected_code: str,
):
    request = _request()
    mutate(request)

    response = _handle(monkeypatch, _server(tmp_path), request)

    assert response["ok"] is False
    assert response["errorCode"] == expected_code
    assert response["requestId"] == request["requestId"]


def test_invalid_uuid_is_not_echoed(monkeypatch, tmp_path: Path):
    response = _handle(
        monkeypatch,
        _server(tmp_path),
        _request(requestId=str(uuid.uuid1())),
    )

    assert response["ok"] is False
    assert response["errorCode"] == "REQUEST_INVALID"
    assert response["requestId"] is None


def test_peer_uid_is_required_before_request_is_read(monkeypatch, tmp_path: Path):
    response = _handle(
        monkeypatch,
        _server(tmp_path),
        _request(),
        peer_uid=2002,
    )

    assert response["ok"] is False
    assert response["errorCode"] == "PEER_NOT_AUTHORIZED"
    assert response["requestId"] is None


def test_action_uid_is_checked_after_socket_peer_authentication(
    monkeypatch,
    tmp_path: Path,
):
    invoked = False

    def handler(_payload):
        nonlocal invoked
        invoked = True
        return {}

    server = LocalControlServer(
        tmp_path / "control.sock",
        protocol_name=PROTOCOL,
        actions={
            "HEALTH": LocalControlAction(
                handler,
                payload_fields=frozenset(),
                allowed_uids=frozenset({1001}),
            )
        },
        allowed_uids={1001, 2002},
    )

    response = _handle(monkeypatch, server, _request(), peer_uid=2002)

    assert response["errorCode"] == "PEER_NOT_AUTHORIZED"
    assert invoked is False


def test_action_uid_policy_must_be_nonempty_and_within_server_allowlist(
    tmp_path: Path,
):
    with pytest.raises(TypeError, match="non-empty frozenset"):
        LocalControlAction(
            lambda _: {},
            payload_fields=frozenset(),
            allowed_uids=frozenset(),
        )

    with pytest.raises(ValueError, match="included in the server allowlist"):
        LocalControlServer(
            tmp_path / "control.sock",
            protocol_name=PROTOCOL,
            actions={
                "HEALTH": LocalControlAction(
                    lambda _: {},
                    payload_fields=frozenset(),
                    allowed_uids=frozenset({2002}),
                )
            },
            allowed_uids={1001},
        )


def test_peer_validation_cannot_be_disabled_or_empty(tmp_path: Path):
    with pytest.raises(ValueError, match="must not be disabled"):
        LocalControlServer(
            tmp_path / "none.sock",
            protocol_name=PROTOCOL,
            actions={
                "HEALTH": LocalControlAction(
                    lambda _: {},
                    frozenset(),
                    frozenset({0}),
                )
            },
            allowed_uids=None,
        )
    with pytest.raises(ValueError, match="non-negative integers"):
        LocalControlServer(
            tmp_path / "empty.sock",
            protocol_name=PROTOCOL,
            actions={
                "HEALTH": LocalControlAction(
                    lambda _: {},
                    frozenset(),
                    frozenset({0}),
                )
            },
            allowed_uids=set(),
        )


@pytest.mark.parametrize("invalid_timeout", [float("nan"), float("inf")])
def test_non_finite_timeouts_cannot_disable_local_deadlines(
    tmp_path: Path,
    invalid_timeout: float,
):
    with pytest.raises(ValueError, match="finite"):
        _server(
            tmp_path,
            processing_timeout_seconds=invalid_timeout,
        )

    with pytest.raises(ValueError, match="finite"):
        LocalControlClient(
            tmp_path / "control.sock",
            protocol_name=PROTOCOL,
            connect_timeout_seconds=invalid_timeout,
        )

    with pytest.raises(ValueError, match="finite"):
        LocalControlClient(
            tmp_path / "control.sock",
            protocol_name=PROTOCOL,
            response_timeout_seconds=invalid_timeout,
        )


def test_handler_can_return_stable_error(monkeypatch, tmp_path: Path):
    def disabled(_payload):
        raise LocalControlActionError("FEATURE_DISABLED", "feature is not active")

    response = _handle(monkeypatch, _server(tmp_path, disabled), _request())

    assert response["errorCode"] == "FEATURE_DISABLED"
    assert response["message"] == "feature is not active"


def test_processing_deadline_returns_without_waiting_for_handler(
    monkeypatch,
    tmp_path: Path,
):
    def slow(_payload):
        time.sleep(0.2)
        return {}

    server = _server(tmp_path, slow, processing_timeout_seconds=0.02)
    started = time.monotonic()
    response = _handle(monkeypatch, server, _request())

    assert time.monotonic() - started < 0.15
    assert response["errorCode"] == "RESULT_UNKNOWN"
    assert "same request identity" in response["message"]


class ReadConnection:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)

    def recv(self, _amount: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def test_message_reader_rejects_duplicate_fields_and_trailing_frames():
    with pytest.raises(ValueError, match="JSON is invalid"):
        receive_message(ReadConnection([b'{"a":1,"a":2}\n']))
    with pytest.raises(ValueError, match="trailing data"):
        receive_message(ReadConnection([b"{}\n{}\n"]))


def test_message_encoder_enforces_256_kib_and_finite_json():
    with pytest.raises(ValueError, match="256 KiB"):
        encode_message({"value": "x" * MAX_MESSAGE_BYTES})
    with pytest.raises(ValueError, match="valid JSON"):
        encode_message({"value": float("nan")})


class ClientSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = b""
        self.timeouts = []
        self.connected_to = None
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, path):
        self.connected_to = path

    def sendall(self, value):
        self.sent = value

    def recv(self, amount):
        value = self.response[:amount]
        self.response = self.response[amount:]
        return value

    def close(self):
        self.closed = True


def test_client_uses_distinct_default_timeouts_and_validates_response_identity(monkeypatch):
    monkeypatch.setattr(local_control.socket, "AF_UNIX", 1, raising=False)
    request_id = _uid()
    response = encode_message(
        {
            "protocolName": PROTOCOL,
            "protocolMajor": 1,
            "protocolMinor": 7,
            "requestId": request_id,
            "ok": True,
            "result": {"status": "READY"},
        }
    )
    connection = ClientSocket(response)
    client = LocalControlClient(
        "/run/test.sock",
        protocol_name=PROTOCOL,
        socket_factory=lambda *_args: connection,
    )

    assert client.request("HEALTH", {}, request_id=request_id) == {"status": "READY"}
    assert connection.timeouts == [1.0, 5.0]
    assert connection.connected_to == "/run/test.sock"
    assert connection.closed is True
    sent = json.loads(connection.sent.decode("utf-8"))
    assert set(sent) == {
        "protocolName",
        "protocolMajor",
        "protocolMinor",
        "requestId",
        "action",
        "payload",
    }


def test_client_rejects_explicit_empty_request_id():
    client = LocalControlClient("unused.sock", protocol_name=PROTOCOL)
    with pytest.raises(ValueError, match="UUIDv4"):
        client.request("HEALTH", {}, request_id="")


def test_client_surfaces_correlated_remote_error(monkeypatch):
    monkeypatch.setattr(local_control.socket, "AF_UNIX", 1, raising=False)
    request_id = _uid()
    response = encode_message(
        {
            "protocolName": PROTOCOL,
            "protocolMajor": 1,
            "protocolMinor": 0,
            "requestId": request_id,
            "ok": False,
            "errorCode": "FEATURE_DISABLED",
            "message": "disabled",
        }
    )
    client = LocalControlClient(
        "unused.sock",
        protocol_name=PROTOCOL,
        socket_factory=lambda *_args: ClientSocket(response),
    )

    with pytest.raises(LocalControlRemoteError) as captured:
        client.request("HEALTH", {}, request_id=request_id)
    assert captured.value.code == "FEATURE_DISABLED"
    assert captured.value.request_id == request_id


def test_client_rejects_mismatched_response_identity(monkeypatch):
    monkeypatch.setattr(local_control.socket, "AF_UNIX", 1, raising=False)
    request_id = _uid()
    response = encode_message(
        {
            "protocolName": PROTOCOL,
            "protocolMajor": 1,
            "protocolMinor": 0,
            "requestId": _uid(),
            "ok": True,
            "result": {},
        }
    )
    client = LocalControlClient(
        "unused.sock",
        protocol_name=PROTOCOL,
        socket_factory=lambda *_args: ClientSocket(response),
    )

    with pytest.raises(LocalControlUnavailable, match="requestId differs"):
        client.request("HEALTH", {}, request_id=request_id)


def test_socket_parent_must_exist(tmp_path: Path):
    server = _server(tmp_path / "missing")
    with pytest.raises(PermissionError, match="parent does not exist"):
        server._prepare_listener()


def test_socket_path_refuses_regular_file_and_symlink(tmp_path: Path):
    regular = tmp_path / "control.sock"
    regular.write_text("preserve", encoding="utf-8")
    server = _server(tmp_path)
    with pytest.raises(PermissionError, match="not a socket"):
        server._prepare_listener()
    assert regular.read_text(encoding="utf-8") == "preserve"

    regular.unlink()
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    try:
        regular.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(PermissionError, match="not a socket"):
        server._prepare_listener()
    assert target.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(socket, "AF_UNIX"),
    reason="Unix permission bits require a POSIX Unix-domain socket",
)
def test_socket_parent_must_not_be_group_writable(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o770)
    runtime_dir.chmod(0o770)
    try:
        with pytest.raises(PermissionError, match="group/world writable"):
            _server(runtime_dir)._prepare_listener()
    finally:
        runtime_dir.chmod(0o700)


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(socket, "AF_UNIX"),
    reason="Unix permission bits require a POSIX Unix-domain socket",
)
def test_socket_is_0660_and_cleanup_only_removes_owned_inode(tmp_path: Path):
    server = _server(tmp_path)
    listener = server._prepare_listener()
    try:
        assert stat.S_IMODE((tmp_path / "control.sock").lstat().st_mode) == 0o660
        (tmp_path / "control.sock").unlink()
        (tmp_path / "control.sock").write_text("replacement", encoding="utf-8")
        server._unlink_owned_socket()
        assert (tmp_path / "control.sock").read_text(encoding="utf-8") == "replacement"
    finally:
        listener.close()


def test_machine_identifiers_are_ascii_and_canonical(tmp_path: Path):
    with pytest.raises(ValueError, match="protocol name"):
        LocalControlClient("unused", protocol_name="Écobin.control")
    with pytest.raises(ValueError, match="action name"):
        LocalControlServer(
            tmp_path / "control.sock",
            protocol_name=PROTOCOL,
            actions={
                "健康": LocalControlAction(
                    lambda _: {},
                    frozenset(),
                    frozenset({0}),
                )
            },
        )
    with pytest.raises(ValueError, match="error code"):
        LocalControlActionError("ÉRROR", "invalid")
