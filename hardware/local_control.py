"""Strict authenticated JSON RPC over a local Unix-domain socket.

The protocol intentionally carries only small JSON control messages.  It does
not expose transport credentials, arbitrary paths, commands, or binary data.
Each connection contains exactly one newline-terminated request and one
newline-terminated response.
"""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import queue
import re
import socket
import stat
import struct
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger("local-control")

LOCAL_PROTOCOL_MAJOR = 1
LOCAL_PROTOCOL_MINOR = 0
MAX_MESSAGE_BYTES = 256 * 1024
DEFAULT_CONNECT_TIMEOUT_SECONDS = 1.0
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 5.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_PROCESSING_TIMEOUT_SECONDS = 5.0

_PROTOCOL_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_ACTION_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_ERROR_CODE_PATTERN = _ACTION_PATTERN
_PAYLOAD_FIELD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]{0,63}\Z")


class LocalControlUnavailable(RuntimeError):
    """The local service could not be reached or did not return a response."""


class LocalControlRemoteError(RuntimeError):
    """A valid peer returned a stable application or protocol error."""

    def __init__(self, code: str, message: str, request_id: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


class LocalControlActionError(RuntimeError):
    """Stable error deliberately returned by a registered action handler."""

    def __init__(self, code: str, message: str) -> None:
        _validate_error_code(code)
        _validate_safe_message(message)
        super().__init__(message)
        self.code = code
        self.message = message


class _ProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LocalControlAction:
    """A handler, exact payload fields, and exact caller identities."""

    handler: Callable[[dict[str, Any]], dict[str, Any]]
    payload_fields: frozenset[str]
    allowed_uids: frozenset[int]

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise TypeError("local control action handler must be callable")
        fields = self.payload_fields
        if not isinstance(fields, frozenset) or any(
            not isinstance(field, str)
            or _PAYLOAD_FIELD_PATTERN.fullmatch(field) is None
            for field in fields
        ):
            raise TypeError("local control payload fields must be a frozenset of names")
        if (
            not isinstance(self.allowed_uids, frozenset)
            or not self.allowed_uids
            or any(
                isinstance(uid, bool) or not isinstance(uid, int) or uid < 0
                for uid in self.allowed_uids
            )
        ):
            raise TypeError(
                "local control action allowed UIDs must be a non-empty frozenset"
            )


class LocalControlClient:
    """One-request-per-connection client with distinct connection/response limits."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        protocol_name: str,
        protocol_major: int = LOCAL_PROTOCOL_MAJOR,
        protocol_minor: int = LOCAL_PROTOCOL_MINOR,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        response_timeout_seconds: float = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.socket_path = str(socket_path)
        self.protocol_name = _validate_protocol_name(protocol_name)
        self.protocol_major = _validate_version(protocol_major, "protocol major")
        self.protocol_minor = _validate_version(protocol_minor, "protocol minor")
        self.connect_timeout_seconds = _validate_timeout(
            connect_timeout_seconds,
            "connection timeout",
        )
        self.response_timeout_seconds = _validate_timeout(
            response_timeout_seconds,
            "response timeout",
        )
        self._socket_factory = socket_factory

    def request(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or _ACTION_PATTERN.fullmatch(action) is None:
            raise ValueError("local control action is invalid")
        if not isinstance(payload, dict):
            raise ValueError("local control payload must be an object")
        correlation_id = str(uuid.uuid4()) if request_id is None else request_id
        _require_uuid4(correlation_id, "requestId")
        document = {
            "protocolName": self.protocol_name,
            "protocolMajor": self.protocol_major,
            "protocolMinor": self.protocol_minor,
            "requestId": correlation_id,
            "action": action,
            "payload": payload,
        }
        encoded = encode_message(document)

        try:
            connection = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as error:
            raise LocalControlUnavailable(
                "local control socket could not be created"
            ) from error
        try:
            connection.settimeout(self.connect_timeout_seconds)
            try:
                connection.connect(self.socket_path)
            except OSError as error:
                raise LocalControlUnavailable(
                    "local control service could not be reached"
                ) from error
            connection.settimeout(self.response_timeout_seconds)
            try:
                connection.sendall(encoded)
                response = receive_message(connection)
            except (OSError, ValueError) as error:
                raise LocalControlUnavailable(
                    "local control response could not be confirmed"
                ) from error
        finally:
            connection.close()
        return _validate_response(
            response,
            protocol_name=self.protocol_name,
            protocol_major=self.protocol_major,
            request_id=correlation_id,
        )


class LocalControlServer:
    """Authenticated, strict, one-request-per-connection local RPC server."""

    def __init__(
        self,
        socket_path: str | os.PathLike[str],
        *,
        protocol_name: str,
        actions: Mapping[str, LocalControlAction],
        protocol_major: int = LOCAL_PROTOCOL_MAJOR,
        protocol_minor: int = LOCAL_PROTOCOL_MINOR,
        allowed_uids: Iterable[int] = (0,),
        socket_mode: int = 0o660,
        socket_gid: int | None = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        processing_timeout_seconds: float = DEFAULT_PROCESSING_TIMEOUT_SECONDS,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.protocol_name = _validate_protocol_name(protocol_name)
        self.protocol_major = _validate_version(protocol_major, "protocol major")
        self.protocol_minor = _validate_version(protocol_minor, "protocol minor")
        self.allowed_uids = _validate_allowed_uids(allowed_uids)
        self.actions = _validate_actions(actions)
        if any(
            not specification.allowed_uids.issubset(self.allowed_uids)
            for specification in self.actions.values()
        ):
            raise ValueError(
                "local control action UIDs must be included in the server allowlist"
            )
        if (
            isinstance(socket_mode, bool)
            or not isinstance(socket_mode, int)
            or socket_mode < 0
            or socket_mode > 0o777
        ):
            raise ValueError("local control socket mode must contain permission bits only")
        if (
            socket_gid is not None
            and (
                isinstance(socket_gid, bool)
                or not isinstance(socket_gid, int)
                or socket_gid < 0
            )
        ):
            raise ValueError("local control socket GID must be non-negative")
        self.socket_mode = socket_mode
        self.socket_gid = socket_gid
        self.request_timeout_seconds = _validate_timeout(
            request_timeout_seconds,
            "request timeout",
        )
        self.processing_timeout_seconds = _validate_timeout(
            processing_timeout_seconds,
            "processing timeout",
        )
        self._socket_factory = socket_factory
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._active_connection: socket.socket | None = None
        self._connection_lock = threading.Lock()
        self._startup_error: BaseException | None = None
        self._failure: BaseException | None = None
        self._bound_identity: tuple[int, int] | None = None

    def start(self, *, timeout_seconds: float = 3.0) -> None:
        _validate_timeout(timeout_seconds, "startup timeout")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self._startup_error = None
        self._failure = None
        self._thread = threading.Thread(
            target=self.serve_forever,
            daemon=True,
            name=f"local-control-{self.protocol_name}",
        )
        self._thread.start()
        if not self._ready_event.wait(timeout_seconds):
            self.stop()
            raise RuntimeError("local control socket did not start in time")
        if self._startup_error is not None:
            self.stop()
            raise RuntimeError("local control socket failed to start") from self._startup_error

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    def wait_stopped(self, timeout_seconds: float | None = None) -> bool:
        """Wait for the server thread; return true once it has terminated."""

        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("wait timeout must be non-negative")
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout_seconds)
        return not thread.is_alive()

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
                with self._connection_lock:
                    self._active_connection = connection
                try:
                    with connection:
                        connection.settimeout(self.request_timeout_seconds)
                        self._handle_connection(connection)
                finally:
                    with self._connection_lock:
                        if self._active_connection is connection:
                            self._active_connection = None
        except BaseException as error:
            self._startup_error = error
            self._failure = error
            self._ready_event.set()
            if not self._stop_event.is_set():
                logger.exception("local control server failed")
        finally:
            listener = self._listener
            self._listener = None
            if listener is not None:
                try:
                    listener.close()
                except OSError:
                    pass
            self._unlink_owned_socket()

    def stop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._connection_lock:
            connection = self._active_connection
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(3.0, self.request_timeout_seconds + 0.5))
            if thread.is_alive():
                raise RuntimeError("local control server did not stop in time")
        self._thread = None
        self._unlink_owned_socket()

    def _prepare_listener(self) -> socket.socket:
        try:
            parent_metadata = self.socket_path.parent.lstat()
        except FileNotFoundError as error:
            raise PermissionError(
                "local control socket parent does not exist"
            ) from error
        if self.socket_path.parent.is_symlink() or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise PermissionError("local control socket parent is not a real directory")
        if os.name == "posix":
            if parent_metadata.st_uid != os.getuid():
                raise PermissionError(
                    "local control socket parent has an unexpected owner"
                )
            if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
                raise PermissionError(
                    "local control socket parent must not be group/world writable"
                )
        self._remove_stale_socket()

        listener = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.settimeout(0.2)
            listener.bind(str(self.socket_path))
            metadata = self.socket_path.lstat()
            if not stat.S_ISSOCK(metadata.st_mode):
                raise PermissionError("local control path did not become a socket")
            self._bound_identity = (metadata.st_dev, metadata.st_ino)
            if self.socket_gid is not None:
                os.chown(self.socket_path, -1, self.socket_gid)
            os.chmod(self.socket_path, self.socket_mode)
            listener.listen(16)
            return listener
        except Exception:
            try:
                listener.close()
            finally:
                self._unlink_owned_socket()
            raise

    def _remove_stale_socket(self) -> None:
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode):
            raise PermissionError("local control path is not a socket")

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            try:
                probe.connect(str(self.socket_path))
            except OSError as error:
                if error.errno not in {
                    errno.ECONNREFUSED,
                    errno.ENOENT,
                    errno.ECONNRESET,
                }:
                    raise PermissionError(
                        "local control socket ownership could not be verified"
                    ) from error
            else:
                raise RuntimeError("local control socket is already active")
        finally:
            probe.close()

        try:
            current = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISSOCK(current.st_mode)
            or current.st_dev != metadata.st_dev
            or current.st_ino != metadata.st_ino
        ):
            raise PermissionError("local control socket changed during stale cleanup")
        self.socket_path.unlink()

    def _handle_connection(self, connection: socket.socket) -> None:
        request_id: str | None = None
        try:
            peer_uid = self._verify_peer(connection)
            request = receive_message(connection)
            request_id = _extract_request_id(request)
            action, payload = _validate_request(
                request,
                protocol_name=self.protocol_name,
                protocol_major=self.protocol_major,
            )
            specification = self.actions.get(action)
            if specification is None:
                raise _ProtocolError(
                    "REQUEST_INVALID",
                    "local control action is not supported",
                )
            if peer_uid not in specification.allowed_uids:
                raise PermissionError(
                    "local control peer is not allowed to invoke this action"
                )
            _require_exact_fields(
                payload,
                specification.payload_fields,
                "local control payload fields are invalid",
            )
            result = self._invoke_handler(specification.handler, payload)
            response = self._success_response(request_id, result)
        except Exception as error:
            response = self._error_response(request_id, error)
        self._send_response(connection, response, request_id)

    def _verify_peer(self, connection: socket.socket) -> int:
        if not hasattr(socket, "SO_PEERCRED"):
            raise PermissionError("local peer credential validation is unavailable")
        raw = connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", raw)
        if uid not in self.allowed_uids:
            raise PermissionError("local control peer UID is not allowed")
        return uid

    def _invoke_handler(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        completed: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                completed.put((True, handler(payload)), block=False)
            except BaseException as error:
                completed.put((False, error), block=False)

        worker = threading.Thread(
            target=invoke,
            daemon=True,
            name=f"local-control-handler-{self.protocol_name}",
        )
        worker.start()
        deadline = time.monotonic() + self.processing_timeout_seconds
        while True:
            if self._stop_event.is_set():
                raise LocalControlActionError(
                    "SERVICE_STOPPING",
                    "local control service is stopping",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Python cannot safely terminate a running handler thread.  A
                # timed-out handler may therefore still finish after this
                # response.  Report the outcome as unknown instead of claiming
                # that processing itself stopped; future mutating actions must
                # be idempotent under a stable request identity.
                raise LocalControlActionError(
                    "RESULT_UNKNOWN",
                    "local control action result is unknown; retry with the same request identity",
                )
            try:
                succeeded, value = completed.get(timeout=min(0.1, remaining))
                break
            except queue.Empty:
                continue
        if not succeeded:
            if isinstance(value, Exception):
                raise value
            raise RuntimeError("local control handler terminated abnormally")
        if not isinstance(value, dict):
            raise RuntimeError("local control action result must be an object")
        return value

    def _success_response(
        self,
        request_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "protocolName": self.protocol_name,
            "protocolMajor": self.protocol_major,
            "protocolMinor": self.protocol_minor,
            "requestId": request_id,
            "ok": True,
            "result": result,
        }

    def _error_response(
        self,
        request_id: str | None,
        error: Exception,
    ) -> dict[str, Any]:
        code, message = _server_error(error)
        return {
            "protocolName": self.protocol_name,
            "protocolMajor": self.protocol_major,
            "protocolMinor": self.protocol_minor,
            "requestId": request_id,
            "ok": False,
            "errorCode": code,
            "message": message,
        }

    def _send_response(
        self,
        connection: socket.socket,
        response: dict[str, Any],
        request_id: str | None,
    ) -> None:
        try:
            encoded = encode_message(response)
        except (TypeError, ValueError):
            encoded = encode_message(
                self._error_response(
                    request_id,
                    RuntimeError("local control response is invalid"),
                )
            )
        try:
            connection.sendall(encoded)
        except OSError:
            pass

    def _unlink_owned_socket(self) -> None:
        expected = self._bound_identity
        if expected is None:
            return
        try:
            metadata = self.socket_path.lstat()
        except FileNotFoundError:
            self._bound_identity = None
            return
        if (
            stat.S_ISSOCK(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == expected
        ):
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
        self._bound_identity = None


def encode_message(document: dict[str, Any]) -> bytes:
    """Encode one strict newline-delimited JSON document."""

    try:
        encoded = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("local control document is not valid JSON") from error
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("local control message exceeds 256 KiB")
    return encoded


def receive_message(connection: socket.socket) -> dict[str, Any]:
    """Receive exactly one bounded newline-delimited JSON object."""

    buffer = bytearray()
    while len(buffer) < MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        newline = buffer.find(b"\n")
        if newline >= 0:
            if newline != len(buffer) - 1:
                raise ValueError("local control frame contains trailing data")
            break
    if not buffer or len(buffer) > MAX_MESSAGE_BYTES or not buffer.endswith(b"\n"):
        raise ValueError("local control frame is incomplete or too large")
    if b"\n" in buffer[:-1]:
        raise ValueError("local control frame contains more than one message")
    try:
        text = buffer[:-1].decode("utf-8")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("local control JSON is invalid") from error
    if not isinstance(document, dict):
        raise ValueError("local control document must be an object")
    return document


def _validate_request(
    request: dict[str, Any],
    *,
    protocol_name: str,
    protocol_major: int,
) -> tuple[str, dict[str, Any]]:
    _require_exact_fields(
        request,
        frozenset(
            {
                "protocolName",
                "protocolMajor",
                "protocolMinor",
                "requestId",
                "action",
                "payload",
            }
        ),
        "local control request fields are invalid",
    )
    if request["protocolName"] != protocol_name:
        raise _ProtocolError(
            "PROTOCOL_INCOMPATIBLE",
            "local control protocol name is incompatible",
        )
    major = request["protocolMajor"]
    if isinstance(major, bool) or not isinstance(major, int) or major != protocol_major:
        raise _ProtocolError(
            "PROTOCOL_INCOMPATIBLE",
            "local control protocol major version is incompatible",
        )
    minor = request["protocolMinor"]
    if isinstance(minor, bool) or not isinstance(minor, int) or minor < 0:
        raise _ProtocolError(
            "REQUEST_INVALID",
            "local control protocol minor version is invalid",
        )
    _require_uuid4(request["requestId"], "requestId")
    action = request["action"]
    if not isinstance(action, str) or _ACTION_PATTERN.fullmatch(action) is None:
        raise _ProtocolError("REQUEST_INVALID", "local control action is invalid")
    payload = request["payload"]
    if not isinstance(payload, dict):
        raise _ProtocolError("REQUEST_INVALID", "local control payload is invalid")
    return action, payload


def _validate_response(
    response: dict[str, Any],
    *,
    protocol_name: str,
    protocol_major: int,
    request_id: str,
) -> dict[str, Any]:
    if response.get("protocolName") != protocol_name:
        raise LocalControlUnavailable("local control response protocol name differs")
    major = response.get("protocolMajor")
    if major != protocol_major or isinstance(major, bool):
        raise LocalControlUnavailable("local control response protocol major differs")
    minor = response.get("protocolMinor")
    if isinstance(minor, bool) or not isinstance(minor, int) or minor < 0:
        raise LocalControlUnavailable("local control response protocol minor is invalid")
    if response.get("requestId") != request_id:
        raise LocalControlUnavailable("local control response requestId differs")
    if response.get("ok") is True:
        if set(response) != {
            "protocolName",
            "protocolMajor",
            "protocolMinor",
            "requestId",
            "ok",
            "result",
        }:
            raise LocalControlUnavailable(
                "local control success response fields are invalid"
            )
        result = response["result"]
        if not isinstance(result, dict):
            raise LocalControlUnavailable("local control response result is invalid")
        return result
    if response.get("ok") is not False:
        raise LocalControlUnavailable("local control response outcome is invalid")
    if set(response) != {
        "protocolName",
        "protocolMajor",
        "protocolMinor",
        "requestId",
        "ok",
        "errorCode",
        "message",
    }:
        raise LocalControlUnavailable(
            "local control error response fields are invalid"
        )
    code = response["errorCode"]
    message = response["message"]
    try:
        _validate_error_code(code)
        _validate_safe_message(message)
    except ValueError as error:
        raise LocalControlUnavailable(
            "local control error response is invalid"
        ) from error
    raise LocalControlRemoteError(code, message, request_id)


def _extract_request_id(request: dict[str, Any]) -> str | None:
    candidate = request.get("requestId")
    try:
        return _require_uuid4(candidate, "requestId")
    except ValueError:
        return None


def _validate_actions(
    actions: Mapping[str, LocalControlAction],
) -> dict[str, LocalControlAction]:
    if not isinstance(actions, Mapping) or not actions:
        raise ValueError("local control actions must not be empty")
    result: dict[str, LocalControlAction] = {}
    for name, specification in actions.items():
        if not isinstance(name, str) or _ACTION_PATTERN.fullmatch(name) is None:
            raise ValueError("local control action name is invalid")
        if not isinstance(specification, LocalControlAction):
            raise TypeError("local control action specification is invalid")
        result[name] = specification
    return result


def _validate_allowed_uids(
    allowed_uids: Iterable[int],
) -> frozenset[int]:
    if allowed_uids is None:
        raise ValueError("allowed local control UIDs must not be disabled")
    result = frozenset(allowed_uids)
    if not result or any(
        isinstance(uid, bool) or not isinstance(uid, int) or uid < 0
        for uid in result
    ):
        raise ValueError("allowed local control UIDs must be non-negative integers")
    return result


def _validate_protocol_name(value: str) -> str:
    if (
        not isinstance(value, str)
        or _PROTOCOL_NAME_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("local control protocol name is invalid")
    return value


def _validate_version(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _validate_timeout(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field} must be positive")
    candidate = float(value)
    if not math.isfinite(candidate):
        raise ValueError(f"{field} must be finite")
    return candidate


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a lowercase UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _require_exact_fields(
    document: Mapping[str, Any],
    expected: frozenset[str],
    message: str,
) -> None:
    if set(document) != expected:
        raise _ProtocolError("REQUEST_INVALID", message)


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value} is not allowed")


def _validate_error_code(value: str) -> None:
    if (
        not isinstance(value, str)
        or _ERROR_CODE_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("local control error code is invalid")


def _validate_safe_message(value: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError("local control error message is invalid")


def _server_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, LocalControlActionError):
        return error.code, error.message
    if isinstance(error, _ProtocolError):
        return error.code, str(error)
    if isinstance(error, PermissionError):
        return "PEER_NOT_AUTHORIZED", "local control peer is not authorized"
    if isinstance(error, (ValueError, TypeError)):
        return "REQUEST_INVALID", "local control request is invalid"
    return "INTERNAL_ERROR", "local control request could not be completed"
