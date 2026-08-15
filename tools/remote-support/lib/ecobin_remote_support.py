#!/usr/bin/env python3
"""Shared, dependency-free primitives for the EcoBin SSH lease boundary."""

from __future__ import annotations

import base64
import binascii
from contextlib import AbstractContextManager
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path("/etc/ecobin/remote-support/server-policy.json")
LEASE_SCHEMA_VERSION = 1
ACTUAL_SCHEMA_VERSION = 1
SUPPORTED_KEY_TYPE = "ssh-ed25519"
FIXED_LISTEN_HOST = "127.0.0.1"
FIXED_PORTS = (22011, 22012, 22013, 22014)
MAX_JSON_BYTES = 8192
SESSION_UID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
HARDWARE_SN_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
FINGERPRINT_PATTERN = re.compile(r"SHA256:[A-Za-z0-9+/]{43}\Z")

POLICY_FIELDS = {
    "schemaVersion",
    "backendUid",
    "backendGid",
    "leaseReaderUid",
    "leaseReaderGid",
    "tunnelUid",
    "desiredLeaseDirectory",
    "actualLeaseDirectory",
    "listenHost",
    "ports",
    "maxLeaseSeconds",
    "clockSkewSeconds",
    "guardPollSeconds",
    "listenerStartupSeconds",
}
LEASE_FIELDS = {
    "schemaVersion",
    "sessionUid",
    "hardwareSn",
    "keyType",
    "keyBase64",
    "keyFingerprint",
    "listenHost",
    "listenPort",
    "createdAtEpochSecond",
    "expiresAtEpochSecond",
}
ACTUAL_FIELDS = {
    "schemaVersion",
    "sessionUid",
    "hardwareSn",
    "keyFingerprint",
    "listenHost",
    "listenPort",
    "connectedAtEpochSecond",
    "expiresAtEpochSecond",
    "guardPid",
}


class LeaseError(RuntimeError):
    """A lease or its security boundary is invalid."""


class LeaseNotFound(LeaseError):
    """The requested lease does not exist."""


class LeaseExpired(LeaseError):
    """The requested lease is no longer active."""


def _require_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeaseError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise LeaseError(f"{name} is outside the permitted range")
    return value


def _require_string(value: Any, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise LeaseError(f"{name} has an invalid format")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LeaseError("JSON contains a duplicate field")
        result[key] = value
    return result


def _parse_json(raw: bytes, expected_fields: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeaseError("file is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise LeaseError("JSON fields do not match the versioned schema")
    return value


def _read_secure_regular_file(
    path: Path,
    *,
    expected_uid: int,
    maximum_bytes: int = MAX_JSON_BYTES,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise LeaseNotFound(f"required file is absent: {path}") from exc
    except OSError as exc:
        raise LeaseError(f"cannot securely open {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LeaseError(f"{path} is not a regular file")
        if metadata.st_uid != expected_uid:
            raise LeaseError(f"{path} has an unexpected owner")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise LeaseError(f"{path} is writable by group or others")
        if metadata.st_nlink != 1:
            raise LeaseError(f"{path} must have exactly one hard link")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > maximum_bytes:
            raise LeaseError(f"{path} exceeds the size limit")
        return raw
    finally:
        os.close(descriptor)


def _validate_directory(
    path: Path, *, expected_uid: int, expected_gid: int, expected_mode: int
) -> None:
    if not path.is_absolute():
        raise LeaseError("policy directories must use absolute paths")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise LeaseError(f"required directory is absent: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise LeaseError(f"{path} is not a real directory")
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise LeaseError(f"{path} has unexpected ownership")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise LeaseError(f"{path} must have mode {expected_mode:04o}")


@dataclass(frozen=True)
class Policy:
    backend_uid: int
    backend_gid: int
    lease_reader_uid: int
    lease_reader_gid: int
    tunnel_uid: int
    desired_directory: Path
    actual_directory: Path
    listen_host: str
    ports: tuple[int, ...]
    max_lease_seconds: int
    clock_skew_seconds: int
    guard_poll_seconds: int
    listener_startup_seconds: int

    def desired_path(self, port: int) -> Path:
        self.require_port(port)
        return self.desired_directory / f"{port}.json"

    def actual_path(self, port: int) -> Path:
        self.require_port(port)
        return self.actual_directory / f"{port}.json"

    def require_port(self, port: int) -> None:
        if port not in self.ports:
            raise LeaseError("listen port is not in the fixed pool")


def load_policy(
    path: Path = DEFAULT_POLICY_PATH,
    *,
    expected_policy_uid: int = 0,
    validate_directories: bool = True,
) -> Policy:
    raw = _read_secure_regular_file(path, expected_uid=expected_policy_uid)
    value = _parse_json(raw, POLICY_FIELDS)
    if (
        _require_int(value["schemaVersion"], "schemaVersion", 1, 2**31 - 1)
        != LEASE_SCHEMA_VERSION
    ):
        raise LeaseError("unsupported policy schema version")
    ports_value = value["ports"]
    if not isinstance(ports_value, list) or tuple(ports_value) != FIXED_PORTS:
        raise LeaseError("policy must use the fixed port pool 22011-22014")
    listen_host = value["listenHost"]
    if listen_host != FIXED_LISTEN_HOST:
        raise LeaseError("policy must bind leases to IPv4 loopback")
    desired_directory_value = value["desiredLeaseDirectory"]
    actual_directory_value = value["actualLeaseDirectory"]
    if not isinstance(desired_directory_value, str) or not isinstance(
        actual_directory_value, str
    ):
        raise LeaseError("policy directories must be strings")
    policy = Policy(
        backend_uid=_require_int(value["backendUid"], "backendUid", 1, 2**31 - 1),
        backend_gid=_require_int(value["backendGid"], "backendGid", 1, 2**31 - 1),
        lease_reader_uid=_require_int(
            value["leaseReaderUid"], "leaseReaderUid", 1, 2**31 - 1
        ),
        lease_reader_gid=_require_int(
            value["leaseReaderGid"], "leaseReaderGid", 1, 2**31 - 1
        ),
        tunnel_uid=_require_int(value["tunnelUid"], "tunnelUid", 1, 2**31 - 1),
        desired_directory=Path(desired_directory_value),
        actual_directory=Path(actual_directory_value),
        listen_host=listen_host,
        ports=FIXED_PORTS,
        max_lease_seconds=_require_int(
            value["maxLeaseSeconds"], "maxLeaseSeconds", 60, 1800
        ),
        clock_skew_seconds=_require_int(
            value["clockSkewSeconds"], "clockSkewSeconds", 0, 120
        ),
        guard_poll_seconds=_require_int(
            value["guardPollSeconds"], "guardPollSeconds", 1, 5
        ),
        listener_startup_seconds=_require_int(
            value["listenerStartupSeconds"], "listenerStartupSeconds", 1, 15
        ),
    )
    if validate_directories:
        _validate_directory(
            policy.desired_directory,
            expected_uid=policy.backend_uid,
            expected_gid=policy.lease_reader_gid,
            expected_mode=0o2750,
        )
        _validate_directory(
            policy.actual_directory,
            expected_uid=policy.tunnel_uid,
            expected_gid=policy.backend_gid,
            expected_mode=0o2750,
        )
    return policy


def decode_ed25519_key(key_type: str, key_base64: str) -> bytes:
    if key_type != SUPPORTED_KEY_TYPE:
        raise LeaseError("only ssh-ed25519 device keys are accepted")
    if not isinstance(key_base64, str) or len(key_base64) > 256:
        raise LeaseError("public key encoding has an invalid size")
    try:
        blob = base64.b64decode(key_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LeaseError("public key is not valid base64") from exc

    def read_ssh_string(offset: int) -> tuple[bytes, int]:
        if offset + 4 > len(blob):
            raise LeaseError("public key wire encoding is truncated")
        length = struct.unpack(">I", blob[offset : offset + 4])[0]
        start = offset + 4
        end = start + length
        if end > len(blob):
            raise LeaseError("public key wire encoding is truncated")
        return blob[start:end], end

    algorithm, offset = read_ssh_string(0)
    key_bytes, offset = read_ssh_string(offset)
    if algorithm != b"ssh-ed25519" or len(key_bytes) != 32 or offset != len(blob):
        raise LeaseError("public key is not a canonical Ed25519 SSH key")
    return blob


def public_key_fingerprint(key_type: str, key_base64: str) -> str:
    blob = decode_ed25519_key(key_type, key_base64)
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return "SHA256:" + digest.rstrip("=")


@dataclass(frozen=True)
class Lease:
    session_uid: str
    hardware_sn: str
    key_type: str
    key_base64: str
    key_fingerprint: str
    listen_host: str
    listen_port: int
    created_at: int
    expires_at: int

    def as_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": LEASE_SCHEMA_VERSION,
            "sessionUid": self.session_uid,
            "hardwareSn": self.hardware_sn,
            "keyType": self.key_type,
            "keyBase64": self.key_base64,
            "keyFingerprint": self.key_fingerprint,
            "listenHost": self.listen_host,
            "listenPort": self.listen_port,
            "createdAtEpochSecond": self.created_at,
            "expiresAtEpochSecond": self.expires_at,
        }


def validate_lease_value(
    value: dict[str, Any], policy: Policy, *, now: int, require_active: bool
) -> Lease:
    if (
        _require_int(value["schemaVersion"], "schemaVersion", 1, 2**31 - 1)
        != LEASE_SCHEMA_VERSION
    ):
        raise LeaseError("unsupported lease schema version")
    session_uid = _require_string(
        value["sessionUid"], "sessionUid", SESSION_UID_PATTERN
    )
    hardware_sn = _require_string(
        value["hardwareSn"], "hardwareSn", HARDWARE_SN_PATTERN
    )
    key_type = value["keyType"]
    key_base64 = value["keyBase64"]
    computed_fingerprint = public_key_fingerprint(key_type, key_base64)
    key_fingerprint = value["keyFingerprint"]
    if not isinstance(key_fingerprint, str) or not hmac.compare_digest(
        computed_fingerprint, key_fingerprint
    ):
        raise LeaseError("public key fingerprint does not match the key")
    if value["listenHost"] != policy.listen_host:
        raise LeaseError("lease does not use the policy listen address")
    listen_port = _require_int(value["listenPort"], "listenPort", 1, 65535)
    policy.require_port(listen_port)
    created_at = _require_int(
        value["createdAtEpochSecond"], "createdAtEpochSecond", 1, 2**63 - 1
    )
    expires_at = _require_int(
        value["expiresAtEpochSecond"], "expiresAtEpochSecond", 1, 2**63 - 1
    )
    if created_at > now + policy.clock_skew_seconds:
        raise LeaseError("lease creation time is too far in the future")
    if expires_at <= created_at:
        raise LeaseError("lease expiration must follow creation")
    if expires_at - created_at > policy.max_lease_seconds:
        raise LeaseError("lease exceeds the maximum lifetime")
    if require_active and expires_at <= now:
        raise LeaseExpired("lease has expired")
    return Lease(
        session_uid=session_uid,
        hardware_sn=hardware_sn,
        key_type=key_type,
        key_base64=key_base64,
        key_fingerprint=key_fingerprint,
        listen_host=policy.listen_host,
        listen_port=listen_port,
        created_at=created_at,
        expires_at=expires_at,
    )


def read_lease(
    policy: Policy,
    port: int,
    *,
    now: int | None = None,
    require_active: bool = True,
) -> Lease:
    path = policy.desired_path(port)
    raw = _read_secure_regular_file(path, expected_uid=policy.backend_uid)
    value = _parse_json(raw, LEASE_FIELDS)
    lease = validate_lease_value(
        value,
        policy,
        now=int(time.time()) if now is None else now,
        require_active=require_active,
    )
    if lease.listen_port != port:
        raise LeaseError("lease filename and listen port disagree")
    return lease


def find_lease_for_key(
    policy: Policy,
    *,
    key_type: str,
    key_base64: str,
    fingerprint: str,
    now: int | None = None,
) -> tuple[Lease | None, list[str]]:
    current_time = int(time.time()) if now is None else now
    computed = public_key_fingerprint(key_type, key_base64)
    if not hmac.compare_digest(computed, fingerprint):
        return None, ["presented fingerprint does not match presented key"]
    matches: list[Lease] = []
    diagnostics: list[str] = []
    for port in policy.ports:
        try:
            lease = read_lease(policy, port, now=current_time, require_active=True)
        except LeaseNotFound:
            continue
        except LeaseExpired:
            continue
        except LeaseError as exc:
            diagnostics.append(f"lease {port} rejected: {exc}")
            continue
        if hmac.compare_digest(lease.key_base64, key_base64):
            matches.append(lease)
    if len(matches) > 1:
        diagnostics.append("the same device key has more than one active lease")
        return None, diagnostics
    return (matches[0] if matches else None), diagnostics


def authorized_key_line(lease: Lease) -> str:
    expires = dt.datetime.fromtimestamp(
        lease.expires_at, tz=dt.timezone.utc
    ).strftime("%Y%m%d%H%M%SZ")
    guard = (
        "/usr/local/libexec/ecobin-remote-support/lease-guard "
        f"--session-uid {lease.session_uid} --port {lease.listen_port}"
    )
    options = (
        "restrict,port-forwarding,"
        f'permitlisten="{lease.listen_host}:{lease.listen_port}",'
        f'expiry-time="{expires}",command="{guard}"'
    )
    return (
        f"{options} {lease.key_type} {lease.key_base64} "
        f"ecobin:{lease.hardware_sn}:{lease.session_uid}"
    )


def atomic_json_write(
    directory: Path,
    filename: str,
    value: dict[str, Any],
    *,
    expected_uid: int,
    expected_gid: int,
    file_mode: int = 0o640,
) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    temporary_name = f".{filename}.{os.getpid()}.{time.time_ns()}.tmp"
    descriptor = -1
    try:
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            create_flags |= os.O_NOFOLLOW
        descriptor = os.open(
            temporary_name, create_flags, file_mode, dir_fd=directory_fd
        )
        payload = (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise LeaseError("atomic file write made no progress")
            offset += written
        os.fchmod(descriptor, file_mode)
        if os.geteuid() == 0:
            os.fchown(descriptor, expected_uid, expected_gid)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
            raise LeaseError("atomic file inherited unexpected ownership")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            filename,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def unlink_and_fsync(directory: Path, filename: str) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_fd = os.open(directory, flags)
    try:
        os.unlink(filename, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def writer_lock(policy: Policy) -> AbstractContextManager[int]:
    """Return a context manager holding the single lease-writer lock."""
    return _WriterLock(policy)


class _WriterLock:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy
        self.descriptor = -1

    def __enter__(self) -> int:
        lock_path = self.policy.desired_directory / ".writer.lock"
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.descriptor = os.open(lock_path, flags, 0o600)
        if os.geteuid() == 0:
            os.fchown(
                self.descriptor,
                self.policy.backend_uid,
                self.policy.lease_reader_gid,
            )
        metadata = os.fstat(self.descriptor)
        if metadata.st_uid != self.policy.backend_uid:
            raise LeaseError("writer lock has an unexpected owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise LeaseError("writer lock permissions are too broad")
        fcntl.flock(self.descriptor, fcntl.LOCK_EX)
        return self.descriptor

    def __exit__(self, *_: object) -> None:
        if self.descriptor >= 0:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = -1


def acquire_guard_lock(policy: Policy, port: int) -> int:
    policy.require_port(port)
    lock_path = policy.actual_directory / f".{port}.guard.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != policy.tunnel_uid or stat.S_IMODE(metadata.st_mode) & 0o077:
        os.close(descriptor)
        raise LeaseError("guard lock has unsafe ownership or permissions")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise LeaseError("another guard already owns this listen port") from exc
    return descriptor


def listener_is_loopback(port: int) -> bool:
    """Return true only for an IPv4 127.0.0.1 TCP LISTEN socket."""
    expected_port = f"{port:04X}"
    try:
        lines = Path("/proc/net/tcp").read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return False
    for line in lines:
        columns = line.split()
        if len(columns) < 4 or columns[3] != "0A":
            continue
        local_address, separator, local_port = columns[1].partition(":")
        if (
            separator
            and local_address.upper() == "0100007F"
            and local_port.upper() == expected_port
        ):
            return True
    return False


def wait_for_listener(policy: Policy, port: int) -> bool:
    deadline = time.monotonic() + policy.listener_startup_seconds
    while time.monotonic() < deadline:
        if listener_is_loopback(port):
            return True
        time.sleep(0.1)
    return listener_is_loopback(port)


def actual_marker(lease: Lease, guard_pid: int, connected_at: int) -> dict[str, Any]:
    return {
        "schemaVersion": ACTUAL_SCHEMA_VERSION,
        "sessionUid": lease.session_uid,
        "hardwareSn": lease.hardware_sn,
        "keyFingerprint": lease.key_fingerprint,
        "listenHost": lease.listen_host,
        "listenPort": lease.listen_port,
        "connectedAtEpochSecond": connected_at,
        "expiresAtEpochSecond": lease.expires_at,
        "guardPid": guard_pid,
    }


def read_actual(policy: Policy, port: int) -> dict[str, Any]:
    raw = _read_secure_regular_file(
        policy.actual_path(port), expected_uid=policy.tunnel_uid
    )
    value = _parse_json(raw, ACTUAL_FIELDS)
    if (
        _require_int(value["schemaVersion"], "schemaVersion", 1, 2**31 - 1)
        != ACTUAL_SCHEMA_VERSION
    ):
        raise LeaseError("unsupported actual marker schema version")
    if value["listenHost"] != policy.listen_host or value["listenPort"] != port:
        raise LeaseError("actual marker does not match its slot")
    _require_string(value["sessionUid"], "sessionUid", SESSION_UID_PATTERN)
    _require_string(value["hardwareSn"], "hardwareSn", HARDWARE_SN_PATTERN)
    _require_string(
        value["keyFingerprint"], "keyFingerprint", FINGERPRINT_PATTERN
    )
    _require_int(value["guardPid"], "guardPid", 1, 2**31 - 1)
    _require_int(
        value["connectedAtEpochSecond"], "connectedAtEpochSecond", 1, 2**63 - 1
    )
    _require_int(
        value["expiresAtEpochSecond"], "expiresAtEpochSecond", 1, 2**63 - 1
    )
    return value


def remove_own_actual_marker(policy: Policy, lease: Lease, guard_pid: int) -> None:
    try:
        marker = read_actual(policy, lease.listen_port)
    except LeaseError:
        return
    if marker["sessionUid"] != lease.session_uid or marker["guardPid"] != guard_pid:
        return
    try:
        unlink_and_fsync(policy.actual_directory, f"{lease.listen_port}.json")
    except FileNotFoundError:
        pass


def parse_public_key_file(path: Path) -> tuple[str, str, str]:
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError as exc:
        raise LeaseError("cannot read public key file") from exc
    if len(lines) != 1:
        raise LeaseError("public key file must contain exactly one key")
    fields = lines[0].split()
    if len(fields) < 2:
        raise LeaseError("public key line is incomplete")
    key_type, key_base64 = fields[0], fields[1]
    fingerprint = public_key_fingerprint(key_type, key_base64)
    return key_type, key_base64, fingerprint


def make_lease(
    policy: Policy,
    *,
    session_uid: str,
    hardware_sn: str,
    key_type: str,
    key_base64: str,
    listen_port: int,
    created_at: int,
    expires_at: int,
) -> Lease:
    value = {
        "schemaVersion": LEASE_SCHEMA_VERSION,
        "sessionUid": session_uid,
        "hardwareSn": hardware_sn,
        "keyType": key_type,
        "keyBase64": key_base64,
        "keyFingerprint": public_key_fingerprint(key_type, key_base64),
        "listenHost": policy.listen_host,
        "listenPort": listen_port,
        "createdAtEpochSecond": created_at,
        "expiresAtEpochSecond": expires_at,
    }
    return validate_lease_value(value, policy, now=created_at, require_active=True)
