"""Verify the future business runtime's real low-privilege environment.

This is a manually invoked image/HIL gate.  It must run as
``ecobin-business`` while the legacy hardware service is stopped.  It opens
the configured UART and cameras, proves the future private directories are
writable, and performs authenticated health calls to both permanent agents.
It never falls back to root and never changes a failed resource's ownership.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


MAX_MESSAGE_BYTES = 256 * 1024
COMMUNICATION_PROTOCOL = "ecobin.communication.control"
UPDATER_PROTOCOL = "ecobin.updater.control"


class BusinessRuntimePreflightError(RuntimeError):
    """The final non-root runtime lacks one of its required resources."""


def verify_legacy_runtime_stopped(
    *,
    runner: Any = subprocess.run,
) -> None:
    """Refuse to probe physical devices while the real business app runs."""

    try:
        result = runner(
            (
                "/usr/bin/systemctl",
                "is-active",
                "--quiet",
                "ecobin-hardware.service",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BusinessRuntimePreflightError(
            "cannot confirm that the legacy business runtime is stopped"
        ) from error
    if result.returncode == 0:
        raise BusinessRuntimePreflightError(
            "ecobin-hardware.service must be stopped before permission preflight"
        )
    if result.returncode != 3:
        raise BusinessRuntimePreflightError(
            "legacy business runtime state cannot be confirmed"
        )


def verify_runtime_identity(expected_user: str) -> None:
    try:
        import pwd
    except ImportError as error:  # pragma: no cover - target OS is Linux
        raise BusinessRuntimePreflightError(
            "system account lookup is unavailable"
        ) from error
    try:
        account = pwd.getpwnam(expected_user)
    except KeyError as error:
        raise BusinessRuntimePreflightError(
            f"required runtime account does not exist: {expected_user}"
        ) from error
    effective_uid = os.geteuid()
    if effective_uid == 0:
        raise BusinessRuntimePreflightError(
            "business runtime permission preflight must not run as root"
        )
    if effective_uid != account.pw_uid:
        raise BusinessRuntimePreflightError(
            f"permission preflight is not running as {expected_user}"
        )


def probe_device(path_value: str, label: str) -> None:
    path = Path(path_value)
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"{label} is unavailable: {path}"
        ) from error
    if not stat.S_ISCHR(metadata.st_mode):
        raise BusinessRuntimePreflightError(
            f"{label} is not a character device: {path}"
        )
    flags = os.O_RDWR | os.O_NONBLOCK
    flags |= getattr(os, "O_NOCTTY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"{label} cannot be opened read/write by ecobin-business: {path}"
        ) from error
    os.close(descriptor)


def probe_private_directory(path_value: str, label: str) -> None:
    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"{label} does not exist: {path}"
        ) from error
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise BusinessRuntimePreflightError(
            f"{label} is not a real directory: {path}"
        )
    if metadata.st_uid != os.geteuid():
        raise BusinessRuntimePreflightError(
            f"{label} is not owned by ecobin-business: {path}"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise BusinessRuntimePreflightError(
            f"{label} is writable by another account: {path}"
        )

    probe = path / f".permission-probe-{uuid.uuid4()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(probe, flags, 0o600)
        details = os.fstat(descriptor)
        identity = (details.st_dev, details.st_ino)
        os.write(descriptor, b"ecobin-business-permission-probe\n")
        os.fsync(descriptor)
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"{label} is not writable by ecobin-business: {path}"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if identity is not None:
            try:
                current = probe.lstat()
            except FileNotFoundError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == identity:
                probe.unlink()


def probe_socket_directory(path_value: str) -> None:
    path = Path(path_value)
    probe_private_directory(str(path), "business control socket directory")
    socket_path = path / f".socket-probe-{uuid.uuid4()}.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    identity: tuple[int, int] | None = None
    try:
        listener.bind(str(socket_path))
        details = socket_path.lstat()
        if not stat.S_ISSOCK(details.st_mode):
            raise BusinessRuntimePreflightError(
                "business control probe did not create a Unix socket"
            )
        identity = (details.st_dev, details.st_ino)
        listener.listen(1)
    except OSError as error:
        raise BusinessRuntimePreflightError(
            "business control socket cannot be created by ecobin-business"
        ) from error
    finally:
        listener.close()
        if identity is not None:
            try:
                current = socket_path.lstat()
            except FileNotFoundError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == identity:
                socket_path.unlink()


def request_health(path_value: str, protocol_name: str) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    request = {
        "protocolName": protocol_name,
        "protocolMajor": 1,
        "protocolMinor": 0,
        "requestId": request_id,
        "action": "HEALTH",
        "payload": {},
    }
    encoded = (
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(1.0)
        connection.connect(path_value)
        connection.settimeout(5.0)
        connection.sendall(encoded)
        response = _receive_message(connection)
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"local control service is unavailable: {path_value}"
        ) from error
    finally:
        connection.close()

    expected_fields = {
        "protocolName",
        "protocolMajor",
        "protocolMinor",
        "requestId",
        "ok",
        "result",
    }
    if (
        set(response) != expected_fields
        or response["protocolName"] != protocol_name
        or response["protocolMajor"] != 1
        or isinstance(response["protocolMajor"], bool)
        or not isinstance(response["protocolMinor"], int)
        or isinstance(response["protocolMinor"], bool)
        or response["protocolMinor"] < 0
        or response["requestId"] != request_id
        or response["ok"] is not True
        or not isinstance(response["result"], dict)
    ):
        raise BusinessRuntimePreflightError(
            f"local control health response is invalid: {path_value}"
        )
    return response["result"]


def verify_stage_three_health(
    communication: dict[str, Any],
    updater: dict[str, Any],
) -> None:
    if (
        communication.get("component") != "COMMUNICATION_AGENT"
        or communication.get("status") != "READY"
        or communication.get("onenetOwnership") != "DISABLED"
        or communication.get("remoteUpdateRouting") != "DISABLED"
    ):
        raise BusinessRuntimePreflightError(
            "communication agent did not report the safe stage-three posture"
        )
    if (
        updater.get("component") != "DEVICE_UPDATER"
        or updater.get("status") != "READY"
        or updater.get("schemaVersion") != 3
        or updater.get("jobGateControlExtensionVersion") != 1
        or updater.get("candidateActivationState") != "REQUIRED"
        or updater.get("stage4CandidateEnabled") is not False
        or updater.get("updatesEnabled") is not False
        or updater.get("jobGateMode") != "DISABLED"
        or updater.get("jobGateState") != "LOCKED"
        or updater.get("jobPermitRpcEnabled") is not False
        or updater.get("maintenanceState") != "LOCKED"
        or updater.get("maintenanceOwnerUid") is not None
        or updater.get("maintenanceType") is not None
        or updater.get("maintenanceFenceToken") is not None
        or updater.get("reconciliationRequired") is not False
        or updater.get("blockReasonCode") != "STAGE4_CANDIDATE_DISABLED"
        or updater.get("activeJobPermitCount") != 0
        or updater.get("unreconciledPhysicalActionCount") != 0
        or updater.get("businessUpdateEnabled") is not False
        or updater.get("mcuUpdateEnabled") is not False
        or updater.get("privilegedHelperMutationEnabled") is not False
    ):
        raise BusinessRuntimePreflightError(
            "device updater did not report the safe stage-three posture"
        )


def _receive_message(connection: socket.socket) -> dict[str, Any]:
    buffer = bytearray()
    while len(buffer) < MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        newline = buffer.find(b"\n")
        if newline >= 0:
            if newline != len(buffer) - 1:
                raise BusinessRuntimePreflightError(
                    "local control response contains trailing data"
                )
            break
    if not buffer or not buffer.endswith(b"\n"):
        raise BusinessRuntimePreflightError(
            "local control response is incomplete or too large"
        )
    try:
        document = json.loads(buffer[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BusinessRuntimePreflightError(
            "local control response is not valid JSON"
        ) from error
    if not isinstance(document, dict):
        raise BusinessRuntimePreflightError(
            "local control response is not an object"
        )
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-user", default="ecobin-business")
    parser.add_argument(
        "--serial-device",
        default=os.getenv("ECOBIN_SERIAL_PORT", "/dev/ttyS5"),
    )
    parser.add_argument(
        "--outside-camera",
        default=os.getenv("ECOBIN_CAMERA_OUTSIDE"),
        required=os.getenv("ECOBIN_CAMERA_OUTSIDE") is None,
    )
    parser.add_argument(
        "--inside-camera",
        default=os.getenv("ECOBIN_CAMERA_INSIDE"),
        required=os.getenv("ECOBIN_CAMERA_INSIDE") is None,
    )
    parser.add_argument(
        "--business-state-directory",
        default="/var/lib/ecobin/business",
    )
    parser.add_argument(
        "--business-photo-directory",
        default="/var/lib/ecobin/business/photos",
    )
    parser.add_argument(
        "--business-socket-directory",
        default="/run/ecobin/business",
    )
    parser.add_argument(
        "--communication-socket",
        default="/run/ecobin/communication/control.sock",
    )
    parser.add_argument(
        "--updater-socket",
        default="/run/ecobin/updater/control.sock",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        verify_runtime_identity(args.expected_user)
        verify_legacy_runtime_stopped()
        probe_device(args.serial_device, "MCU UART")
        probe_device(args.outside_camera, "outside camera")
        probe_device(args.inside_camera, "inside camera")
        probe_private_directory(
            args.business_state_directory,
            "business state directory",
        )
        probe_private_directory(
            args.business_photo_directory,
            "business photo directory",
        )
        probe_socket_directory(args.business_socket_directory)
        communication = request_health(
            args.communication_socket,
            COMMUNICATION_PROTOCOL,
        )
        updater = request_health(args.updater_socket, UPDATER_PROTOCOL)
        verify_stage_three_health(communication, updater)
        # Close the race with an operator starting the legacy runtime during
        # the probe.  This does not stop or otherwise mutate that service.
        verify_legacy_runtime_stopped()
    except BusinessRuntimePreflightError as error:
        print(f"business-runtime-permission-preflight=FAIL: {error}", file=sys.stderr)
        return 1
    print("business-runtime-permission-preflight=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
