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
import re
import socket
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from camera_selection import CameraSelectionError, resolve_camera_roles

MAX_MESSAGE_BYTES = 256 * 1024
COMMUNICATION_PROTOCOL = "ecobin.communication.control"
UPDATER_PROTOCOL = "ecobin.updater.control"
_DEVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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


def probe_business_identity(path_value: str) -> None:
    """Prove the candidate can read only its strict non-secret identity."""

    path = Path(path_value)
    try:
        details = path.lstat()
        raw = path.read_bytes()
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"business device identity is unavailable: {path}"
        ) from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o600
        or not 1 <= len(raw) <= 4096
    ):
        raise BusinessRuntimePreflightError(
            "business device identity ownership or file shape is unsafe"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BusinessRuntimePreflightError(
            "business device identity is not valid JSON"
        ) from error
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schemaVersion",
            "assetUid",
            "deviceName",
            "modelCode",
            "expectedPortCount",
            "deviceEntryUrl",
        }
        or type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or not isinstance(document["deviceName"], str)
        or _DEVICE_NAME.fullmatch(document["deviceName"]) is None
        or document["modelCode"] != "EC-M0"
        or type(document["expectedPortCount"]) is not int
        or document["expectedPortCount"] != 1
        or "deviceKey" in raw.decode("utf-8", errors="ignore")
    ):
        raise BusinessRuntimePreflightError(
            "business device identity content is invalid"
        )


def probe_device_capabilities(
    path_value: str,
    *,
    expected_owner_uid: int = 0,
    expected_group_gid: int | None = None,
) -> None:
    """Verify the root-published, non-secret MCU capability fact."""

    if expected_group_gid is None:
        expected_group_gid = os.getegid()
    path = Path(path_value)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != expected_owner_uid
            or details.st_gid != expected_group_gid
            or stat.S_IMODE(details.st_mode) != 0o640
            or not 1 <= details.st_size <= 4096
        ):
            raise BusinessRuntimePreflightError(
                "device capability ownership or file shape is unsafe"
            )
        remaining = details.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise BusinessRuntimePreflightError(
                    "device capability file ended unexpectedly"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except BusinessRuntimePreflightError:
        raise
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"device capability file cannot be read by ecobin-business: {path}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BusinessRuntimePreflightError(
            "device capability file is not valid JSON"
        ) from error
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schemaVersion",
            "mcuRemoteUpdateCapable",
            "factoryReportSha256",
        }
        or type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or type(document["mcuRemoteUpdateCapable"]) is not bool
        or not isinstance(document["factoryReportSha256"], str)
        or _SHA256.fullmatch(document["factoryReportSha256"]) is None
    ):
        raise BusinessRuntimePreflightError(
            "device capability content is invalid"
        )


def probe_factory_seal(
    path_value: str,
    *,
    expected_owner_uid: int = 0,
    expected_group_gid: int | None = None,
) -> None:
    """Prove the managed business process can only read the root seal fact."""

    if expected_group_gid is None:
        try:
            import grp

            expected_group_gid = grp.getgrnam("ecobin-factory-web").gr_gid
        except (ImportError, KeyError) as error:
            raise BusinessRuntimePreflightError(
                "factory seal reader group is unavailable"
            ) from error
    path = Path(path_value)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != expected_owner_uid
            or details.st_gid != expected_group_gid
            or stat.S_IMODE(details.st_mode) != 0o640
            or not 2 <= details.st_size <= 8192
        ):
            raise BusinessRuntimePreflightError(
                "factory seal ownership or file shape is unsafe"
            )
        remaining = details.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise BusinessRuntimePreflightError(
                    "factory seal file ended unexpectedly"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except BusinessRuntimePreflightError:
        raise
    except OSError as error:
        raise BusinessRuntimePreflightError(
            f"factory seal cannot be read by ecobin-business: {path}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BusinessRuntimePreflightError(
            "factory seal is not valid JSON"
        ) from error
    if (
        not isinstance(document, dict)
        or document.get("schemaVersion") not in {1, 2}
        or document.get("status") != "SEALED"
    ):
        raise BusinessRuntimePreflightError(
            "factory seal content is invalid"
        )


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
        or updater.get("maintenancePhase") is not None
        or updater.get("maintenanceFenceToken") is not None
        or updater.get("reconciliationRequired") is not False
        or updater.get("blockReasonCode") != "STAGE4_CANDIDATE_DISABLED"
        or updater.get("activeJobPermitCount") != 0
        or updater.get("unreconciledPhysicalActionCount") != 0
        or updater.get("businessUpdateEnabled") is not False
        or updater.get("mcuUpdateEnabled") is not False
        or updater.get("mcuUpdateCandidateEnabled") is not False
        or updater.get("privilegedHelperMutationEnabled") is not False
    ):
        raise BusinessRuntimePreflightError(
            "device updater did not report the safe stage-three posture"
        )


def verify_proxy_candidate_health(
    communication: dict[str, Any],
    updater: dict[str, Any],
    *,
    ledger_only: bool = False,
) -> None:
    """Require permanent ownership while allowing fail-closed recovery states."""

    mcu_candidate = updater.get("mcuUpdateCandidate")
    business_candidate = updater.get("businessUpdateCandidate")
    if (
        communication.get("component") != "COMMUNICATION_AGENT"
        or communication.get("status") != "READY"
        or communication.get("onenetOwnership") != "ENABLED"
        or communication.get("businessEventIngress") != "ENABLED"
        or communication.get("cloudConnectionState")
        not in {"CONNECTED", "DISCONNECTED"}
        or communication.get("remoteUpdateRouting")
        != ("DISABLED" if ledger_only else "BUSINESS_RUNTIME_ONLY")
    ):
        raise BusinessRuntimePreflightError(
            "communication proxy did not report permanent OneNet ownership"
        )
    if (
        updater.get("component") != "DEVICE_UPDATER"
        or updater.get("status") != "READY"
        or updater.get("schemaVersion") != 3
        or updater.get("jobGateControlExtensionVersion") != 1
        or updater.get("candidateActivationState") != "ACTIVE"
        or updater.get("stage4CandidateEnabled") is not True
        or updater.get("jobGateMode") != "ENFORCED"
        or updater.get("jobPermitRpcEnabled") is not True
        or updater.get("jobGateState")
        not in {"OPEN", "DRAINING", "MAINTENANCE", "LOCKED"}
        or updater.get("maintenanceState") not in {
            "IDLE",
            "DRAINING",
            "MAINTENANCE",
            "LOCKED",
        }
        or updater.get("businessUpdateEnabled") is not False
        or updater.get("mcuUpdateEnabled") is not False
        or updater.get("mcuUpdateCandidateEnabled") is not (not ledger_only)
        or updater.get("businessUpdateCandidateEnabled") is not True
        or updater.get("privilegedHelperMutationEnabled") is not True
        or (ledger_only and mcu_candidate is not None)
        or (
            not ledger_only
            and (
                not isinstance(mcu_candidate, dict)
                or mcu_candidate.get("schemaVersion") != 1
                or mcu_candidate.get("candidateEnabled") is not True
                or mcu_candidate.get("remoteTriggerEnabled") is not False
                or not isinstance(mcu_candidate.get("unresolvedPrivilegedActionCount"), int)
                or isinstance(mcu_candidate.get("unresolvedPrivilegedActionCount"), bool)
                or mcu_candidate.get("unresolvedPrivilegedActionCount") < 0
            )
        )
        or not isinstance(business_candidate, dict)
        or business_candidate.get("schemaVersion") != 1
        or business_candidate.get("businessUpdateCandidateEnabled") is not True
        or business_candidate.get("remoteTriggerEnabled") is not (not ledger_only)
        or (ledger_only and business_candidate.get("activeUpdate") is not None)
    ):
        raise BusinessRuntimePreflightError(
            "device updater did not report the activated job-safety posture"
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
        "--posture",
        choices=("stage-three", "proxy-candidate", "ledger-only"),
        default="stage-three",
    )
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
        "--outside-camera-fallback",
        default=os.getenv("ECOBIN_CAMERA_OUTSIDE_FALLBACK", ""),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--inside-camera-fallback",
        default=os.getenv("ECOBIN_CAMERA_INSIDE_FALLBACK", ""),
        help=argparse.SUPPRESS,
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
        "--business-identity",
        default="/var/lib/ecobin/business/device-identity.json",
    )
    parser.add_argument(
        "--device-capabilities",
        default="/var/lib/ecobin/device-capabilities.json",
    )
    parser.add_argument(
        "--factory-seal",
        default="/var/lib/ecobin/first-boot/sealed.json",
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
        cameras = resolve_camera_roles(
            outside_primary=args.outside_camera,
            inside_primary=args.inside_camera,
            outside_fallback=args.outside_camera_fallback,
            inside_fallback=args.inside_camera_fallback,
        )
        verify_runtime_identity(args.expected_user)
        verify_legacy_runtime_stopped()
        probe_device(args.serial_device, "MCU UART")
        probe_device(cameras.outside_source, "outside camera")
        probe_device(cameras.inside_source, "inside camera")
        probe_private_directory(
            args.business_state_directory,
            "business state directory",
        )
        probe_private_directory(
            args.business_photo_directory,
            "business photo directory",
        )
        probe_socket_directory(args.business_socket_directory)
        probe_device_capabilities(args.device_capabilities)
        if args.posture in {"proxy-candidate", "ledger-only"}:
            probe_business_identity(args.business_identity)
            probe_factory_seal(args.factory_seal)
        communication = request_health(
            args.communication_socket,
            COMMUNICATION_PROTOCOL,
        )
        updater = request_health(args.updater_socket, UPDATER_PROTOCOL)
        if args.posture in {"proxy-candidate", "ledger-only"}:
            verify_proxy_candidate_health(
                communication, updater, ledger_only=args.posture == "ledger-only"
            )
        else:
            verify_stage_three_health(communication, updater)
        # Close the race with an operator starting the legacy runtime during
        # the probe.  This does not stop or otherwise mutate that service.
        verify_legacy_runtime_stopped()
    except (BusinessRuntimePreflightError, CameraSelectionError) as error:
        print(f"business-runtime-permission-preflight=FAIL: {error}", file=sys.stderr)
        return 1
    print("business-runtime-permission-preflight=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
