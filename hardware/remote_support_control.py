"""Authenticated local control channel for the remote-support agent."""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import struct
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


logger = logging.getLogger("remote-support-control")

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
DEFAULT_SOCKET_PATH = "/run/ecobin/remote-support/control.sock"


class RemoteSupportUnavailable(RuntimeError):
    """The local agent did not accept a control request."""


class RemoteSupportControlClient:
    def __init__(
        self,
        socket_path: str | os.PathLike[str] = DEFAULT_SOCKET_PATH,
        *,
        timeout_seconds: float = 2.0,
        attempts: int = 3,
        retry_delay_seconds: float = 0.1,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("remote support RPC timeout must be positive")
        if attempts <= 0:
            raise ValueError("remote support RPC attempts must be positive")
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._socket_factory = socket_factory

    def open_session(
        self,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        expires_at: str,
    ) -> str:
        result = self._request(
            "OPEN",
            {
                "sessionUid": session_uid,
                "commandUid": command_uid,
                "deviceName": device_name,
                "remotePort": remote_port,
                "expiresAt": expires_at,
            },
        )
        return _required_disposition(result)

    def close_session(self, *, session_uid: str, command_uid: str) -> str:
        result = self._request(
            "CLOSE",
            {"sessionUid": session_uid, "commandUid": command_uid},
        )
        return _required_disposition(result)

    def list_status_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self._request("DRAIN_STATUS", {"limit": limit})
        events = result.get("events")
        if not isinstance(events, list) or any(
            not isinstance(event, dict) for event in events
        ):
            raise RemoteSupportUnavailable(
                "remote support agent returned invalid status events"
            )
        return events

    def ack_status_event(self, event_uid: str) -> str:
        result = self._request("ACK_STATUS", {"eventUid": event_uid})
        return _required_disposition(result)

    def ping(self) -> bool:
        return self._request("PING", {}) == {"status": "READY"}

    def _request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": str(uuid.uuid4()),
            "action": action,
            "payload": payload,
        }
        encoded = _encode_message(request)
        last_error: OSError | ValueError | None = None
        for attempt in range(self.attempts):
            try:
                with self._socket_factory(
                    socket.AF_UNIX,
                    socket.SOCK_STREAM,
                ) as connection:
                    connection.settimeout(self.timeout_seconds)
                    connection.connect(self.socket_path)
                    connection.sendall(encoded)
                    response = _receive_message(connection)
                return _validate_response(response, request["requestId"])
            except (OSError, ValueError) as error:
                last_error = error
                if attempt + 1 < self.attempts:
                    time.sleep(self.retry_delay_seconds)
        raise RemoteSupportUnavailable(
            "remote support agent is unavailable"
        ) from last_error


class RemoteSupportControlServer:
    """One-request-per-connection local RPC server."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        controller: Any,
        store: Any,
        allowed_uids: Iterable[int] | None = (0,),
        socket_mode: int = 0o600,
        socket_gid: int | None = None,
        connection_timeout_seconds: float = 2.0,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        if connection_timeout_seconds <= 0:
            raise ValueError("control connection timeout must be positive")
        self.socket_path = Path(socket_path)
        self.controller = controller
        self.store = store
        self.allowed_uids = (
            None if allowed_uids is None else frozenset(allowed_uids)
        )
        if self.allowed_uids is not None and any(
            isinstance(uid, bool) or not isinstance(uid, int) or uid < 0
            for uid in self.allowed_uids
        ):
            raise ValueError("allowed control UIDs must be non-negative integers")
        if socket_mode not in {0o600, 0o660}:
            raise ValueError("remote support socket mode must be 0600 or 0660")
        if socket_gid is not None and (
            isinstance(socket_gid, bool)
            or not isinstance(socket_gid, int)
            or socket_gid < 0
        ):
            raise ValueError("remote support socket GID must be non-negative")
        self.socket_mode = socket_mode
        self.socket_gid = socket_gid
        self.connection_timeout_seconds = connection_timeout_seconds
        self._socket_factory = socket_factory
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._startup_error: BaseException | None = None

    def start(self, *, timeout_seconds: float = 3.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self.serve_forever,
            daemon=True,
            name="remote-support-rpc",
        )
        self._thread.start()
        if not self._ready_event.wait(timeout_seconds):
            raise RuntimeError("remote support control socket did not start")
        if self._startup_error is not None:
            raise RuntimeError(
                "remote support control socket failed to start"
            ) from self._startup_error

    def serve_forever(self) -> None:
        try:
            listener = self._prepare_listener()
            self._listener = listener
            self._ready_event.set()
            while not self._stop_event.is_set():
                try:
                    connection, _address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                with connection:
                    connection.settimeout(self.connection_timeout_seconds)
                    self._handle_connection(connection)
        except BaseException as error:
            self._startup_error = error
            self._ready_event.set()
            if not self._stop_event.is_set():
                logger.exception("remote support control server failed")
        finally:
            listener = self._listener
            self._listener = None
            if listener is not None:
                try:
                    listener.close()
                except OSError:
                    pass
            self._unlink_socket()

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        self._thread = None
        self._unlink_socket()

    def _prepare_listener(self) -> socket.socket:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            metadata = self.socket_path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode):
                raise PermissionError(
                    "remote support control path is not a socket"
                )
            self.socket_path.unlink()
        listener = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.settimeout(0.5)
        listener.bind(str(self.socket_path))
        if self.socket_gid is not None and os.name == "posix":
            os.chown(self.socket_path, -1, self.socket_gid)
        os.chmod(self.socket_path, self.socket_mode)
        listener.listen(8)
        return listener

    def _handle_connection(self, connection: socket.socket) -> None:
        request_id: str | None = None
        try:
            self._verify_peer(connection)
            request = _receive_message(connection)
            request_id, action, payload = _validate_request(request)
            result = self._dispatch(action, payload)
            response = {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request_id,
                "ok": True,
                "result": result,
            }
        except Exception as error:
            response = {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request_id,
                "ok": False,
                "errorCode": _server_error_code(error),
                "message": _safe_error_message(error),
            }
        try:
            connection.sendall(_encode_message(response))
        except OSError:
            pass

    def _verify_peer(self, connection: socket.socket) -> None:
        if self.allowed_uids is None:
            return
        if not hasattr(socket, "SO_PEERCRED"):
            raise PermissionError("peer credential validation is unavailable")
        raw = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", raw)
        if uid not in self.allowed_uids:
            raise PermissionError("remote support control peer is not allowed")

    def _dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "PING":
            _require_fields(payload, set())
            return {"status": "READY"}
        if action == "OPEN":
            _require_fields(
                payload,
                {
                    "sessionUid",
                    "commandUid",
                    "deviceName",
                    "remotePort",
                    "expiresAt",
                },
            )
            disposition = self.controller.open_session(
                session_uid=payload["sessionUid"],
                command_uid=payload["commandUid"],
                device_name=payload["deviceName"],
                remote_port=payload["remotePort"],
                expires_at=payload["expiresAt"],
            )
            return {"disposition": disposition}
        if action == "CLOSE":
            _require_fields(payload, {"sessionUid", "commandUid"})
            disposition = self.controller.close_session(
                session_uid=payload["sessionUid"],
                command_uid=payload["commandUid"],
            )
            return {"disposition": disposition}
        if action == "DRAIN_STATUS":
            _require_fields(payload, {"limit"})
            return {
                "events": self.store.list_status_events(
                    limit=payload["limit"]
                )
            }
        if action == "ACK_STATUS":
            _require_fields(payload, {"eventUid"})
            return {
                "disposition": self.store.ack_status_event(
                    payload["eventUid"]
                )
            }
        raise ValueError("remote support control action is invalid")

    def _unlink_socket(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(metadata.st_mode):
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass


class RemoteSupportStatusBridge:
    """Move agent facts into EdgeStore before acknowledging them."""

    def __init__(self, client: RemoteSupportControlClient, edge_store: Any):
        self.client = client
        self.edge_store = edge_store

    def poll_once(self, *, limit: int = 20) -> int:
        imported = 0
        for event in self.client.list_status_events(limit=limit):
            disposition = self.edge_store.import_remote_support_status_event(
                event
            )
            if disposition not in {"ACCEPTED", "DUPLICATE"}:
                raise RuntimeError(
                    "remote support status could not be imported"
                )
            acknowledged = self.client.ack_status_event(event["eventUid"])
            if acknowledged not in {"ACCEPTED", "DUPLICATE"}:
                raise RuntimeError(
                    "remote support status could not be acknowledged"
                )
            imported += 1
        return imported


def _encode_message(document: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("remote support control message is too large")
    return encoded


def _receive_message(connection: socket.socket) -> dict[str, Any]:
    buffer = bytearray()
    while len(buffer) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if b"\n" in chunk:
            break
    if not buffer or len(buffer) > MAX_MESSAGE_BYTES or not buffer.endswith(b"\n"):
        raise ValueError("remote support control frame is invalid")
    if b"\n" in buffer[:-1]:
        raise ValueError("remote support control frame contains trailing data")
    try:
        document = json.loads(buffer[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("remote support control JSON is invalid") from error
    if not isinstance(document, dict):
        raise ValueError("remote support control document must be an object")
    return document


def _validate_request(
    request: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    _require_fields(
        request,
        {"protocolVersion", "requestId", "action", "payload"},
    )
    if request["protocolVersion"] != PROTOCOL_VERSION:
        raise ValueError("remote support control protocol is unsupported")
    request_id = _require_uuid4(request["requestId"], "requestId")
    action = request["action"]
    payload = request["payload"]
    if not isinstance(action, str) or not action:
        raise ValueError("remote support control action is invalid")
    if not isinstance(payload, dict):
        raise ValueError("remote support control payload is invalid")
    return request_id, action, payload


def _validate_response(
    response: dict[str, Any],
    expected_request_id: str,
) -> dict[str, Any]:
    if response.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("remote support agent protocol differs")
    if response.get("requestId") != expected_request_id:
        raise ValueError("remote support agent response identity differs")
    if response.get("ok") is not True:
        code = response.get("errorCode")
        message = response.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            raise ValueError("remote support agent error is invalid")
        raise RuntimeError(f"{code}: {message}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError("remote support agent result is invalid")
    return result


def _required_disposition(result: dict[str, Any]) -> str:
    disposition = result.get("disposition")
    if not isinstance(disposition, str) or not disposition:
        raise RemoteSupportUnavailable(
            "remote support agent disposition is invalid"
        )
    return disposition


def _require_fields(document: dict[str, Any], expected: set[str]) -> None:
    if set(document) != expected:
        raise ValueError("remote support control fields are invalid")


def _require_uuid4(value: Any, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _server_error_code(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "PEER_NOT_AUTHORIZED"
    if isinstance(error, ValueError):
        return "REQUEST_INVALID"
    return "AGENT_INTERNAL_ERROR"


def _safe_error_message(error: Exception) -> str:
    if isinstance(error, (PermissionError, ValueError, RuntimeError)):
        message = str(error)
        if 0 < len(message) <= 256 and "\n" not in message:
            return message
    return "remote support agent request failed"
