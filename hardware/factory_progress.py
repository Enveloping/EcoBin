"""Small, root-owned progress projections for factory onboarding.

The files written here are deliberately much narrower than the underlying
enrollment and runtime state.  They are safe inputs for the root factory-flow
projector: no identifiers, URLs, credentials, payloads, or free-form error
messages can enter either document.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1
DEFAULT_ENROLLMENT_PROGRESS_PATH = Path(
    "/run/ecobin/enrollment/progress.json"
)
DEFAULT_RUNTIME_PROGRESS_PATH = Path(
    "/run/ecobin/hardware/factory-progress.json"
)

ENROLLMENT_PHASES = frozenset(
    {
        "IDENTITY_PREPARATION",
        "CHALLENGE_REQUEST",
        "ENROLLMENT_SUBMISSION",
        "CREDENTIAL_INSTALLATION",
        "K1_CLEANUP",
        "COMPLETE",
        "RETRY_WAIT",
        "FAILED",
    }
)
ENROLLMENT_ERROR_CODES = frozenset(
    {
        "ACTIVE_SWAP_DETECTED",
        "BACKEND_URL_MISSING",
        "CHALLENGE_EXPIRED",
        "CHALLENGE_RESPONSE_INVALID",
        "CREDENTIAL_INSTALL_FAILED",
        "ENROLLMENT_BACKEND_REJECTED",
        "ENROLLMENT_BACKEND_TEMPORARY",
        "ENROLLMENT_INTERNAL_ERROR",
        "ENROLLMENT_KEY_INVALID",
        "ENROLLMENT_NETWORK_UNAVAILABLE",
        "ENROLLMENT_PENDING",
        "ENROLLMENT_RESPONSE_INVALID",
        "ENROLLMENT_STATE_INVALID",
        "ENROLLMENT_STATE_PERMISSIONS_INVALID",
        "K1_CLEANUP_FAILED",
    }
)

SERVICE_STATES = frozenset(
    {"STARTING", "RUNNING", "STOPPING", "STOPPED", "FAILED"}
)
UART_STATES = frozenset(
    {"STARTING", "READY", "RECOVERING", "DISCONNECTED", "FAILED"}
)
MQTT_STATES = frozenset(
    {"DISCONNECTED", "CONNECTING", "CONNECTED", "FAILED"}
)
P8_PHASES = frozenset(
    {
        "IDLE",
        "REQUEST_RECEIVED",
        "PERSISTENT_STORE_CHECK",
        "CONFIGURATION_CHECK",
        "MCU_SENSOR_CHECK",
        "CAMERA_CAPTURE",
        "COS_UPLOAD_READBACK",
        "EVIDENCE_PERSISTENCE",
        "EVIDENCE_RECORDED",
        "FAILED",
    }
)
RUNTIME_ERROR_CODES = frozenset(
    {
        "MQTT_CONNECT_FAILED",
        "MQTT_DISCONNECTED",
        "P8_CAMERA_CAPTURE_FAILED",
        "P8_CONFIGURATION_CHECK_FAILED",
        "P8_COS_UPLOAD_READBACK_FAILED",
        "P8_DEVICE_ENTRY_URL_FAILED",
        "P8_EVIDENCE_PERSISTENCE_FAILED",
        "P8_EXECUTION_FAILED",
        "P8_GRANT_NOT_AVAILABLE",
        "P8_MCU_SENSOR_CHECK_FAILED",
        "P8_STORAGE_CHECK_FAILED",
        "RUNTIME_BOOT_FAILED",
        "UART_HANDSHAKE_FAILED",
        "UART_OPEN_FAILED",
        "UART_QUERY_STATE_FAILED",
        "UART_RECOVERY_FAILED",
    }
)

_ENROLLMENT_FIELDS = frozenset(
    {"schemaVersion", "phase", "lastErrorCode", "retryable"}
)
_RUNTIME_FIELDS = frozenset(
    {
        "schemaVersion",
        "serviceState",
        "uartState",
        "mqttState",
        "p8Phase",
        "lastErrorCode",
        "observedMonotonicMs",
    }
)
_ENROLLMENT_MAX_BYTES = 256
_RUNTIME_MAX_BYTES = 512
_UNCHANGED = object()


def validate_enrollment_progress(document: dict) -> None:
    _require_exact_fields(document, _ENROLLMENT_FIELDS)
    if document["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("unsupported enrollment progress schema")
    if document["phase"] not in ENROLLMENT_PHASES:
        raise ValueError("invalid enrollment progress phase")
    _require_error_code(
        document["lastErrorCode"],
        ENROLLMENT_ERROR_CODES,
    )
    if not isinstance(document["retryable"], bool):
        raise ValueError("enrollment retryable must be boolean")
    if document["phase"] == "RETRY_WAIT":
        if not document["retryable"] or document["lastErrorCode"] is None:
            raise ValueError("retry wait requires a retryable error")
    elif document["retryable"]:
        raise ValueError("only retry wait may be retryable")
    if document["phase"] == "FAILED" and document["lastErrorCode"] is None:
        raise ValueError("failed enrollment requires an error code")
    if document["phase"] not in {"RETRY_WAIT", "FAILED"}:
        if document["lastErrorCode"] is not None:
            raise ValueError("active/completed enrollment cannot carry an error")


def validate_runtime_progress(document: dict) -> None:
    _require_exact_fields(document, _RUNTIME_FIELDS)
    if document["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError("unsupported runtime progress schema")
    if document["serviceState"] not in SERVICE_STATES:
        raise ValueError("invalid runtime service state")
    if document["uartState"] not in UART_STATES:
        raise ValueError("invalid runtime UART state")
    if document["mqttState"] not in MQTT_STATES:
        raise ValueError("invalid runtime MQTT state")
    if document["p8Phase"] not in P8_PHASES:
        raise ValueError("invalid P8 phase")
    _require_error_code(document["lastErrorCode"], RUNTIME_ERROR_CODES)
    observed = document["observedMonotonicMs"]
    if (
        not isinstance(observed, int)
        or isinstance(observed, bool)
        or observed < 0
    ):
        raise ValueError("runtime monotonic observation must be non-negative")
    if document["p8Phase"] == "FAILED":
        if document["lastErrorCode"] is None:
            raise ValueError("failed P8 progress requires an error code")


class EnrollmentProgressWriter:
    """Atomically replace the boot-scoped enrollment projection."""

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_ENROLLMENT_PROGRESS_PATH):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def report(
        self,
        phase: str,
        *,
        last_error_code: str | None = None,
        retryable: bool = False,
    ) -> dict:
        document = {
            "schemaVersion": SCHEMA_VERSION,
            "phase": phase,
            "lastErrorCode": last_error_code,
            "retryable": retryable,
        }
        return _write_projection(
            self._path,
            document,
            validate_enrollment_progress,
            _ENROLLMENT_MAX_BYTES,
        )


class RuntimeProgressWriter:
    """Thread-safe heartbeat and P8 progress writer."""

    def __init__(
        self,
        path: str | os.PathLike[str] = DEFAULT_RUNTIME_PROGRESS_PATH,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._path = Path(path)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._document = {
            "schemaVersion": SCHEMA_VERSION,
            "serviceState": "STARTING",
            "uartState": "STARTING",
            "mqttState": "DISCONNECTED",
            "p8Phase": "IDLE",
            "lastErrorCode": None,
            "observedMonotonicMs": 0,
        }

    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._document)

    def report(
        self,
        *,
        service_state: str | object = _UNCHANGED,
        uart_state: str | object = _UNCHANGED,
        mqtt_state: str | object = _UNCHANGED,
        p8_phase: str | object = _UNCHANGED,
        last_error_code: str | None | object = _UNCHANGED,
    ) -> dict:
        with self._lock:
            updated = dict(self._document)
            if service_state is not _UNCHANGED:
                updated["serviceState"] = service_state
            if uart_state is not _UNCHANGED:
                updated["uartState"] = uart_state
            if mqtt_state is not _UNCHANGED:
                updated["mqttState"] = mqtt_state
            if p8_phase is not _UNCHANGED:
                updated["p8Phase"] = p8_phase
            if last_error_code is not _UNCHANGED:
                current_error = self._document["lastErrorCode"]
                preserve_p8_error = bool(
                    p8_phase is _UNCHANGED
                    and self._document["p8Phase"] == "FAILED"
                    and isinstance(current_error, str)
                    and current_error.startswith("P8_")
                    and not (
                        isinstance(last_error_code, str)
                        and last_error_code.startswith("P8_")
                    )
                )
                if not preserve_p8_error:
                    updated["lastErrorCode"] = last_error_code
            updated["observedMonotonicMs"] = max(
                0,
                int(self._monotonic() * 1000),
            )
            persisted = _write_projection(
                self._path,
                updated,
                validate_runtime_progress,
                _RUNTIME_MAX_BYTES,
            )
            self._document = persisted
            return dict(persisted)

    def report_p8(
        self,
        phase: str,
        error_code: str | None = None,
    ) -> dict:
        return self.report(
            p8_phase=phase,
            last_error_code=error_code,
        )


def _write_projection(
    path: Path,
    document: dict,
    validator: Callable[[dict], None],
    maximum_bytes: int,
) -> dict:
    validator(document)
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) + 1 > maximum_bytes:
        raise ValueError("progress projection exceeds its size limit")
    _prepare_destination(path)
    _atomic_write_json(path, document)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    validator(persisted)
    if persisted != document:
        raise RuntimeError("atomically persisted progress did not verify")
    if path.stat().st_size > maximum_bytes:
        raise RuntimeError("persisted progress projection exceeds its size limit")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("progress projection permissions are not root-only")
    return persisted


def _prepare_destination(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("progress projection directory must not be a symlink")
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(current.st_mode):
        raise ValueError("progress projection target must be a regular file")


def _atomic_write_json(path: Path, document: dict) -> None:
    content = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(directory: Path) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        # Windows cannot fsync a directory. Production is Debian Linux.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _require_exact_fields(document: dict, expected: frozenset[str]) -> None:
    if not isinstance(document, dict) or frozenset(document) != expected:
        raise ValueError("progress projection fields do not match the schema")


def _require_error_code(
    value: str | None,
    allowed: frozenset[str],
) -> None:
    if value is not None and value not in allowed:
        raise ValueError("progress projection error code is not allowlisted")
