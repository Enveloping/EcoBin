"""One-request systemd socket-activated control endpoint for root helpers.

The listener and connection lifetime belong to systemd.  This module handles
one already-connected Unix socket, authenticates its kernel-reported peer UID,
validates the same bounded JSON envelope as :mod:`local_control`, writes one
response, and returns.  It never opens a network socket or accepts a command,
path, service name, device name, or executable from the caller.
"""

from __future__ import annotations

import os
import re
import socket
import stat
import struct
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - deployment is Linux-only
    fcntl = None  # type: ignore[assignment]

try:  # ``pwd`` is deliberately unavailable on the Windows development host.
    import pwd
except ImportError:  # pragma: no cover - exercised through an injected resolver
    pwd = None  # type: ignore[assignment]

from local_control import (
    LOCAL_PROTOCOL_MAJOR,
    LOCAL_PROTOCOL_MINOR,
    LocalControlActionError,
    encode_message,
    receive_message,
)

UPDATER_ACCOUNT = "ecobin-updater"
REQUEST_TIMEOUT_SECONDS = 5.0
MUTATION_LOCK_PATH = Path("/run/ecobin/privileged/mutation.lock")
_REQUEST_FIELDS = frozenset(
    {
        "protocolName",
        "protocolMajor",
        "protocolMinor",
        "requestId",
        "action",
        "payload",
    }
)
_ACTION_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class HelperRequestError(ValueError):
    """A stable protocol error that is safe to return to a local caller."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HelperAction:
    """One fixed primitive and its exact caller-supplied field names."""

    handler: Callable[[dict[str, Any]], dict[str, Any]]
    payload_fields: frozenset[str]

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise TypeError("privileged helper action handler must be callable")
        if not isinstance(self.payload_fields, frozenset) or any(
            not isinstance(field, str) or not field
            for field in self.payload_fields
        ):
            raise TypeError("privileged helper payload fields are invalid")


@dataclass(frozen=True)
class HelperPolicy:
    """Immutable discovery document for a staged privileged helper."""

    protocol_name: str
    component: str
    fixed_configuration: Mapping[str, Any]
    primitive_actions: tuple[str, ...]
    stage: int = 3
    mutation_enabled: bool = False

    def __post_init__(self) -> None:
        if not self.protocol_name or not self.component:
            raise ValueError("helper protocol and component must be configured")
        if isinstance(self.stage, bool) or not isinstance(self.stage, int):
            raise ValueError("helper stage must be an integer")
        if self.stage not in {3, 4}:
            raise ValueError("helper stage is unsupported")
        if not isinstance(self.mutation_enabled, bool):
            raise ValueError("helper mutation flag must be boolean")
        action_names = self.primitive_actions
        if any(
            not isinstance(action, str) or _ACTION_PATTERN.fullmatch(action) is None
            for action in action_names
        ):
            raise ValueError("privileged helper action is invalid")
        if len(action_names) != len(set(action_names)):
            raise ValueError("privileged helper actions must be unique")
        if {"HEALTH", "GET_CAPABILITIES"}.intersection(action_names):
            raise ValueError("discovery actions cannot be mutations")

    def health(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "component": self.component,
            "status": "READY",
            "stage": self.stage,
            "mutationEnabled": self.mutation_enabled,
            "remoteTriggerEnabled": False,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            **self.health(),
            "protocol": {
                "name": self.protocol_name,
                "major": LOCAL_PROTOCOL_MAJOR,
                "minor": LOCAL_PROTOCOL_MINOR,
            },
            "readActions": ["HEALTH", "GET_CAPABILITIES"],
            "localPrimitiveActions": list(self.primitive_actions),
            "fixedConfiguration": dict(self.fixed_configuration),
        }


class OneShotPrivilegedHelper:
    """Serve exactly one connected local-control request and then return."""

    def __init__(
        self,
        policy: HelperPolicy,
        *,
        updater_uid: int,
        actions: Mapping[str, HelperAction],
        peer_uid_reader: Callable[[socket.socket], int] | None = None,
        mutation_guard: Callable[[], AbstractContextManager[None]] | None = None,
        mutation_authorizer: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if isinstance(updater_uid, bool) or not isinstance(updater_uid, int):
            raise TypeError("updater UID must be an integer")
        if updater_uid < 0:
            raise ValueError("updater UID must be non-negative")
        self.policy = policy
        self.updater_uid = updater_uid
        self.actions = _validate_actions(actions, policy)
        self._peer_uid_reader = peer_uid_reader or _read_peer_uid
        self._mutation_guard = mutation_guard or _exclusive_mutation_lock
        if self.policy.mutation_enabled and not callable(mutation_authorizer):
            raise ValueError(
                "enabled privileged helper requires an updater authorizer"
            )
        self._mutation_authorizer = mutation_authorizer

    def handle(self, connection: socket.socket) -> None:
        request_id: str | None = None
        try:
            self._verify_peer(connection)
            request = receive_message(connection)
            request_id = _extract_request_id(request)
            action, payload = _validate_request(
                request,
                protocol_name=self.policy.protocol_name,
            )
            result = self._dispatch(action, payload)
            response = _success_response(self.policy.protocol_name, request_id, result)
        except Exception as error:  # noqa: BLE001 - protocol boundary sanitizes all failures
            response = _error_response(self.policy.protocol_name, request_id, error)
        try:
            connection.sendall(encode_message(response))
        except (OSError, ValueError):
            # The per-connection service has no useful recovery after its caller
            # disappears or an internal response cannot be represented safely.
            return

    def _verify_peer(self, connection: socket.socket) -> None:
        uid = self._peer_uid_reader(connection)
        if uid != self.updater_uid:
            raise PermissionError("local helper peer UID is not allowed")

    def _dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "HEALTH":
            _require_exact_fields(payload, frozenset())
            return self.policy.health()
        if action == "GET_CAPABILITIES":
            _require_exact_fields(payload, frozenset())
            return self.policy.capabilities()
        specification = self.actions.get(action)
        if specification is None:
            raise HelperRequestError(
                "REQUEST_INVALID",
                "local helper action is not supported",
            )
        if not self.policy.mutation_enabled:
            # Stage three rejects before inspecting caller-controlled mutation
            # fields, acquiring the shared lock, or entering a root primitive.
            raise LocalControlActionError(
                "FEATURE_DISABLED",
                "privileged helper mutations are not enabled in this image stage",
            )
        _require_exact_fields(payload, specification.payload_fields)
        authorizer = self._mutation_authorizer
        if authorizer is None:  # guarded by __init__; keep dispatch fail-closed
            raise LocalControlActionError(
                "HELPER_AUTHORIZATION_UNAVAILABLE",
                "privileged helper authorization is unavailable",
            )
        authorizer(action, payload)
        with self._mutation_guard():
            result = specification.handler(payload)
        if not isinstance(result, dict):
            raise RuntimeError("privileged helper action returned an invalid result")
        return result


def resolve_updater_uid(
    resolver: Callable[[str], Any] | None = None,
) -> int:
    """Resolve the one allowed caller by account name, failing closed."""

    if resolver is None:
        if pwd is None:
            raise RuntimeError("system account lookup is unavailable")
        resolver = pwd.getpwnam
    try:
        record = resolver(UPDATER_ACCOUNT)
    except KeyError as error:
        raise RuntimeError("required ecobin-updater account does not exist") from error
    uid = getattr(record, "pw_uid", None)
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise RuntimeError("ecobin-updater account has an invalid UID")
    return uid


def serve_systemd_connection(
    policy: HelperPolicy,
    actions_factory: Callable[[int], Mapping[str, HelperAction]],
    mutation_authorizer_factory: (
        Callable[[int], Callable[[str, dict[str, Any]], None]] | None
    ) = None,
) -> None:
    """Handle the connected socket supplied by an ``Accept=yes`` unit."""

    updater_uid = resolve_updater_uid()
    try:
        connection = socket.socket(fileno=0)
    except OSError as error:
        raise RuntimeError("systemd did not supply a connected Unix socket") from error
    with connection:
        if connection.family != socket.AF_UNIX:
            raise RuntimeError("systemd helper connection is not a Unix socket")
        connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        mutation_authorizer = (
            mutation_authorizer_factory(updater_uid)
            if mutation_authorizer_factory is not None
            else None
        )
        OneShotPrivilegedHelper(
            policy,
            updater_uid=updater_uid,
            actions=actions_factory(updater_uid),
            mutation_authorizer=mutation_authorizer,
        ).handle(connection)


def _read_peer_uid(connection: socket.socket) -> int:
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        raise PermissionError("local peer credential validation is unavailable")
    raw = connection.getsockopt(
        socket.SOL_SOCKET,
        socket.SO_PEERCRED,
        struct.calcsize("3i"),
    )
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


@contextmanager
def _exclusive_mutation_lock() -> Any:
    if os.name != "posix" or fcntl is None:
        raise LocalControlActionError(
            "HELPER_LOCK_UNAVAILABLE",
            "privileged helper locking is unavailable",
        )
    parent = MUTATION_LOCK_PATH.parent
    try:
        parent_metadata = parent.lstat()
    except FileNotFoundError as error:
        raise LocalControlActionError(
            "HELPER_LOCK_UNAVAILABLE",
            "privileged helper runtime directory does not exist",
        ) from error
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or parent_metadata.st_uid != 0
        or parent_metadata.st_mode & 0o022
    ):
        raise LocalControlActionError(
            "HELPER_LOCK_UNAVAILABLE",
            "privileged helper runtime directory is unsafe",
        )
    descriptor = os.open(
        MUTATION_LOCK_PATH,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise LocalControlActionError(
                "HELPER_LOCK_UNAVAILABLE",
                "privileged helper lock file is unsafe",
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LocalControlActionError(
                "HELPER_BUSY",
                "another privileged helper operation is in progress",
            ) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _validate_request(
    request: dict[str, Any],
    *,
    protocol_name: str,
) -> tuple[str, dict[str, Any]]:
    _require_exact_fields(request, _REQUEST_FIELDS)
    if request["protocolName"] != protocol_name:
        raise HelperRequestError(
            "PROTOCOL_INCOMPATIBLE",
            "local helper protocol name is incompatible",
        )
    major = request["protocolMajor"]
    if (
        isinstance(major, bool)
        or not isinstance(major, int)
        or major != LOCAL_PROTOCOL_MAJOR
    ):
        raise HelperRequestError(
            "PROTOCOL_INCOMPATIBLE",
            "local helper protocol major version is incompatible",
        )
    minor = request["protocolMinor"]
    if isinstance(minor, bool) or not isinstance(minor, int) or minor < 0:
        raise HelperRequestError(
            "REQUEST_INVALID",
            "local helper protocol minor version is invalid",
        )
    _require_uuid4(request["requestId"], "requestId")
    action = request["action"]
    if not isinstance(action, str) or _ACTION_PATTERN.fullmatch(action) is None:
        raise HelperRequestError("REQUEST_INVALID", "local helper action is invalid")
    payload = request["payload"]
    if not isinstance(payload, dict):
        raise HelperRequestError("REQUEST_INVALID", "local helper payload is invalid")
    return action, payload


def _validate_actions(
    actions: Mapping[str, HelperAction],
    policy: HelperPolicy,
) -> dict[str, HelperAction]:
    if not isinstance(actions, Mapping):
        raise TypeError("privileged helper actions must be a mapping")
    if set(actions) != set(policy.primitive_actions):
        raise ValueError("privileged helper action policy and handlers differ")
    result: dict[str, HelperAction] = {}
    for name, specification in actions.items():
        if not isinstance(specification, HelperAction):
            raise TypeError("privileged helper action specification is invalid")
        result[name] = specification
    return result


def _extract_request_id(request: dict[str, Any]) -> str | None:
    try:
        return _require_uuid4(request.get("requestId"), "requestId")
    except HelperRequestError:
        return None


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise HelperRequestError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise HelperRequestError(
            "REQUEST_INVALID",
            f"{field} must be a UUIDv4",
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise HelperRequestError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    return value


def _require_exact_fields(
    document: Mapping[str, Any],
    expected: frozenset[str],
) -> None:
    if set(document) != expected:
        raise HelperRequestError(
            "REQUEST_INVALID",
            "local helper fields are invalid",
        )


def _success_response(
    protocol_name: str,
    request_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocolName": protocol_name,
        "protocolMajor": LOCAL_PROTOCOL_MAJOR,
        "protocolMinor": LOCAL_PROTOCOL_MINOR,
        "requestId": request_id,
        "ok": True,
        "result": result,
    }


def _error_response(
    protocol_name: str,
    request_id: str | None,
    error: Exception,
) -> dict[str, Any]:
    if isinstance(error, PermissionError):
        code = "PEER_NOT_AUTHORIZED"
        message = "local helper peer is not authorized"
    elif isinstance(error, (HelperRequestError, LocalControlActionError)):
        code = error.code
        message = str(error)
    elif isinstance(error, ValueError):
        code = "REQUEST_INVALID"
        message = "local helper request is invalid"
    else:
        code = "HELPER_INTERNAL_ERROR"
        message = "local privileged helper request failed"
    return {
        "protocolName": protocol_name,
        "protocolMajor": LOCAL_PROTOCOL_MAJOR,
        "protocolMinor": LOCAL_PROTOCOL_MINOR,
        "requestId": request_id,
        "ok": False,
        "errorCode": code,
        "message": message,
    }
