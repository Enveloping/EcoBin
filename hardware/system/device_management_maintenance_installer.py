#!/usr/bin/env python3
"""Controlled one-time installation of the permanent device-management layer.

This program is deliberately separate from both the factory image installer and
the replaceable business-runtime installer.  It exists for the narrow case in
which an already provisioned legacy device must receive the Stage-3 permanent
communication/updater services through an authenticated maintenance session.

The command line is live-root only.  Library callers may use an alternate root
and command runner for tests, but there is no command-line ``--root`` escape
hatch.  ``install`` and ``rollback`` are dry-runs unless ``--apply`` is given.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # The library-level validator is unit-tested on Windows; the CLI is Linux-only.
    import fcntl
except ImportError:  # pragma: no cover - exercised implicitly by Windows imports.
    fcntl = None  # type: ignore[assignment]


HARDWARE_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(HARDWARE_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(HARDWARE_SOURCE_ROOT))

from install.runtime_payload_manifest import (  # noqa: E402
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
    DEVICE_UPDATER_HELPER_UNIT_FILES,
)
from updater_store import (  # noqa: E402
    PristineRollbackStateUsed,
    inspect_pristine_stage3_rollback_state,
)


MANIFEST_NAME = "device-management-maintenance-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
BACKUP_SCHEMA_VERSION = 2
MARKER_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PAYLOAD_FILE_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_TRUST_KEYS = 64
MAX_UPDATER_STATE_FILE_BYTES = 256 * 1024 * 1024

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.pem$")
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

LEGACY_SERVICE = "ecobin-hardware.service"
RUNTIME_TARGET = "ecobin-runtime.target"
MCU_SAFE_GPIO_SERVICE = "ecobin-mcu-safe-gpio.service"
MCU_SAFE_GPIO_UNIT_PATH = "/etc/systemd/system/ecobin-mcu-safe-gpio.service"
MCU_SAFE_GPIO_UNIT_SHA256 = (
    "2c448407bab9ce7ff5403298491094e3ef2ffbe9e8a9034c3ab0659f2ceeca5d"
)
MCU_SAFE_GPIO_FACT_PATH = "/run/ecobin/mcu-safe-gpio/boot-safe.json"
KERNEL_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
BOOT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MAIN_UNIT_FILES = (
    "ecobin-communication.service",
    "ecobin-updater.service",
    "ecobin-device-management-preflight.service",
    "ecobin-business-permission-preflight.service",
)
HELPER_UNIT_FILES = tuple(DEVICE_UPDATER_HELPER_UNIT_FILES)
HELPER_INSTANCE_PREFIXES = (
    "ecobin-business-activation-helper@",
    "ecobin-mcu-flash-helper@",
)
START_UNITS = (
    "ecobin-business-activation-helper.socket",
    "ecobin-mcu-flash-helper.socket",
    "ecobin-communication.service",
    "ecobin-updater.service",
    "ecobin-device-management-preflight.service",
)
ROLLBACK_MANAGED_UNITS = tuple(dict.fromkeys((*START_UNITS, *MAIN_UNIT_FILES)))
AUDITED_ROLLBACK_RECOVERY_STATUSES = frozenset(
    {
        "INSTALLED",
        "ROLLBACK_BLOCKED",
        "ROLLBACK_REFUSED_RESTORING",
        "ROLLBACK_REMOVING",
    }
)
TRACKED_UNITS = (
    LEGACY_SERVICE,
    MCU_SAFE_GPIO_SERVICE,
    RUNTIME_TARGET,
    *MAIN_UNIT_FILES,
    *HELPER_UNIT_FILES,
)
ACCOUNT_NAMES = (
    "ecobin-communication",
    "ecobin-business",
    "ecobin-updater",
)
GROUP_NAMES = (
    "ecobin-communication-ipc",
    "ecobin-business-ipc",
    "ecobin-updater-ipc",
    "ecobin-privileged-helper-ipc",
)
REQUIRED_EXISTING_GROUPS = ("dialout", "video")
ACCOUNT_HOMES: Mapping[str, str] = {
    "ecobin-communication": "/var/lib/ecobin/communication",
    "ecobin-business": "/var/lib/ecobin/business",
    "ecobin-updater": "/var/lib/ecobin/updater",
}
ACCOUNT_GROUPS: Mapping[str, frozenset[str]] = {
    "ecobin-communication": frozenset(
        {
            "ecobin-communication",
            "ecobin-communication-ipc",
            "ecobin-business-ipc",
            "ecobin-updater-ipc",
        }
    ),
    "ecobin-business": frozenset(
        {
            "ecobin-business",
            "ecobin-communication-ipc",
            "ecobin-business-ipc",
            "ecobin-updater-ipc",
            "dialout",
            "video",
        }
    ),
    "ecobin-updater": frozenset(
        {
            "ecobin-updater",
            "ecobin-communication-ipc",
            "ecobin-business-ipc",
            "ecobin-updater-ipc",
            "ecobin-privileged-helper-ipc",
        }
    ),
}
IPC_GROUP_MEMBERS: Mapping[str, frozenset[str]] = {
    "ecobin-communication-ipc": frozenset(ACCOUNT_NAMES),
    "ecobin-business-ipc": frozenset(ACCOUNT_NAMES),
    "ecobin-updater-ipc": frozenset(ACCOUNT_NAMES),
    "ecobin-privileged-helper-ipc": frozenset({"ecobin-updater"}),
}

DROP_IN_RELATIVE = (
    "systemd/ecobin-runtime.target.d/50-device-management-maintenance.conf"
)
RUNTIME_DROP_IN_PATH = (
    "/etc/systemd/system/ecobin-runtime.target.d/"
    "50-device-management-maintenance.conf"
)
LEGACY_GATE_DROP_IN_PATH = (
    "/etc/systemd/system/ecobin-hardware.service.d/20-first-boot-gate.conf"
)
LEGACY_GATE_DROP_IN_SHA256 = (
    "26660ce84b4465fd23f01f546fd78b72e4159be095d3a52e886560bba168f4d9"
)
LEGACY_GATE_DROP_IN_CONTENT = (
    "[Unit]\n"
    "Requires=ecobin-runtime-gate.service\n"
    "After=ecobin-runtime-gate.service\n"
    "Conflicts=ecobin-factory-test.service\n"
    "\n"
    "[Service]\n"
    "Environment=PYTHONPATH=/opt/ecobin/factory-test/current/app\n"
    "ExecCondition=/opt/ecobin/factory-test/current/.venv/bin/python "
    "-m first_boot.gate --require runtime\n"
).encode("utf-8")
DROP_IN_CONTENT = (
    "[Unit]\n"
    "Wants=ecobin-communication.service ecobin-updater.service "
    "ecobin-business-activation-helper.socket ecobin-mcu-flash-helper.socket\n"
    "Wants=ecobin-device-management-preflight.service\n"
    "After=ecobin-device-management-preflight.service\n"
).encode("utf-8")

SYSUSERS_PAYLOAD = "config/sysusers.d/ecobin-device-runtime.conf"
TMPFILES_PAYLOAD = "config/tmpfiles.d/ecobin-device-runtime.conf"
PREFLIGHT_PAYLOAD = "support/business_runtime_preflight.py"
RELEASE_ENV_PAYLOAD = "share/device-management-release.env"

ACTIVE_MARKER = "/var/lib/ecobin/device-management-maintenance/active.json"
PENDING_MARKER = "/var/lib/ecobin/device-management-maintenance/pending.json"
RUNTIME_START_FENCE = (
    "/var/lib/ecobin/device-management-maintenance/runtime-start-blocked.json"
)
RUNTIME_START_FENCE_CONDITION = f"ConditionPathExists=!{RUNTIME_START_FENCE}"
ROLLBACK_UNIT_FENCE_DROP_IN_NAME = "99-ecobin-rollback-start-fence.conf"
ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT = (
    "[Unit]\n" f"{RUNTIME_START_FENCE_CONDITION}\n"
).encode("utf-8")
BACKUP_PARENT = "/var/lib/ecobin/device-management-maintenance/backups"
LOCK_PATH = "/run/ecobin-device-management-maintenance/installer.lock"
UPDATER_STATE_DATABASE_PATH = "/var/lib/ecobin/updater/updater.db"
UPDATER_STATE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
MCU_RECOVERY_MARKER = (
    "/run/ecobin/privileged/mcu-application-recovery-required"
)

# Every unit lookup location which can supply a fragment ahead of, or after
# removal of, the controlled /etc fragment.  A lower-priority fragment can be
# invisible in FragmentPath while the /etc file exists, then unexpectedly take
# over on daemon-reload after rollback.  Reject it before the first unlink.
SYSTEMD_UNIT_FRAGMENT_BASES = (
    "/etc/systemd/system.control",
    "/run/systemd/system.control",
    "/run/systemd/transient",
    "/run/systemd/generator.early",
    "/etc/systemd/system",
    "/etc/systemd/system.attached",
    "/run/systemd/system",
    "/run/systemd/system.attached",
    "/run/systemd/generator",
    "/usr/local/lib/systemd/system",
    "/usr/lib/systemd/system",
    "/lib/systemd/system",
    "/run/systemd/generator.late",
)
TRUSTED_HOST_TOOLS: Mapping[str, str] = {
    "systemctl": "/usr/bin/systemctl",
    "systemd-sysusers": "/usr/bin/systemd-sysusers",
    "systemd-tmpfiles": "/usr/bin/systemd-tmpfiles",
    "getent": "/usr/bin/getent",
    "id": "/usr/bin/id",
}
SETUP_COMMAND_ARGUMENTS: Mapping[str, tuple[str, ...]] = {
    "systemd-sysusers": (
        "systemd-sysusers",
        "/usr/lib/sysusers.d/ecobin-device-runtime.conf",
    ),
    "systemd-tmpfiles": (
        "systemd-tmpfiles",
        "--create",
        "/usr/lib/tmpfiles.d/ecobin-device-runtime.conf",
    ),
}
SETUP_COMMAND_ORDER = tuple(SETUP_COMMAND_ARGUMENTS)

IMMUTABLE_PATHS = frozenset(
    {
        "/etc/ecobin/device-credentials.json",
        "/etc/ecobin/image-release.json",
        "/usr/share/ecobin/image-release.json",
        "/etc/ecobin/hardware.env",
        "/etc/systemd/system/ecobin-hardware.service.d",
        LEGACY_GATE_DROP_IN_PATH,
        "/opt/ecobin/hardware/current",
        "/var/lib/ecobin/hardware/edge.db",
        "/var/lib/ecobin/hardware/edge.db-wal",
        "/var/lib/ecobin/hardware/edge.db-shm",
    }
)

TMPFILES_DIRECTORY_MODES: Mapping[str, int] = {
    "/var/lib/ecobin/communication": 0o700,
    "/var/lib/ecobin/business": 0o700,
    "/var/lib/ecobin/business/photos": 0o700,
    "/var/lib/ecobin/updater": 0o700,
    "/var/lib/ecobin/updater/staging": 0o700,
    "/var/lib/ecobin/updater/mcu-firmware": 0o700,
    "/var/lib/ecobin/privileged": 0o700,
    "/var/lib/ecobin/privileged/business-snapshots": 0o700,
    "/opt/ecobin/business": 0o750,
    "/opt/ecobin/business/releases": 0o750,
    "/run/ecobin/communication": 0o750,
    "/run/ecobin/business": 0o750,
    "/run/ecobin/updater": 0o750,
    "/run/ecobin/privileged": 0o750,
}
TMPFILES_REGULAR_PATHS = (
    "/run/ecobin/privileged/mutation.lock",
)


class MaintenanceInstallError(RuntimeError):
    """The maintenance payload or live device violates a safety invariant."""


class RollbackRefusedForUsedState(MaintenanceInstallError):
    """The permanent layer is healthy but has facts that prohibit removal."""


class RollbackDeferredForBusyState(MaintenanceInstallError):
    """Rollback made no destructive change because a service was transitional."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[Sequence[str]], CommandResult]
MetadataIdentity = Callable[[str, os.stat_result], tuple[int, int]]
MetadataMode = Callable[[str, os.stat_result], int]


@dataclass(frozen=True)
class ManifestFile:
    path: str
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True)
class MaintenanceManifest:
    payload_id: str
    source_git_commit: str
    expected_image_release_id: str
    expected_image_version: str
    legacy_service: str
    communication_release_id: str
    updater_release_id: str
    files: Mapping[str, ManifestFile]
    manifest_sha256: str


@dataclass(frozen=True)
class TargetFile:
    source_relative: str
    destination: str
    mode: int


class MaintenanceProcessInterrupted(BaseException):
    """Test-only model of power/process loss after an atomic publication."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(document: Any) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MaintenanceInstallError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json_bytes(raw: bytes, *, description: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaintenanceInstallError(f"{description} is not valid UTF-8 JSON") from exc


def _require_exact_keys(
    value: object, expected: set[str], *, description: str
) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise MaintenanceInstallError(f"{description} has unsupported fields")
    return value


def _safe_relative_path(value: object, *, description: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MaintenanceInstallError(f"{description} is invalid")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise MaintenanceInstallError(f"{description} is unsafe")
    return candidate.as_posix()


def _safe_id(value: object, *, description: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise MaintenanceInstallError(f"{description} is invalid")
    return value


def _rooted(rootfs: Path, absolute: str) -> Path:
    if not absolute.startswith("/") or "\\" in absolute:
        raise MaintenanceInstallError(f"internal target is not absolute: {absolute}")
    relative = PurePosixPath(absolute).relative_to("/")
    if any(part in ("", ".", "..") for part in relative.parts):
        raise MaintenanceInstallError(f"internal target is unsafe: {absolute}")
    return rootfs.joinpath(*relative.parts)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _validate_runtime_fence_unit_bytes(raw: bytes, unit_name: str) -> None:
    try:
        unit_lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise MaintenanceInstallError(
            f"systemd unit is not valid UTF-8: {unit_name}"
        ) from exc
    try:
        if unit_lines.count("[Unit]") != 1:
            raise ValueError
        unit_section_start = unit_lines.index("[Unit]")
        unit_section_end = next(
            index
            for index in range(unit_section_start + 1, len(unit_lines))
            if unit_lines[index].startswith("[")
            and unit_lines[index].endswith("]")
        )
    except (ValueError, StopIteration):
        raise MaintenanceInstallError(
            f"systemd unit has no bounded Unit section: {unit_name}"
        )
    # A continuation immediately before a section header makes that header
    # part of the previous logical line.  Checking only the nominal [Unit]
    # slice would therefore accept a file in which systemd never enters the
    # Unit section at all.  Managed units do not need continuations, so reject
    # them everywhere and keep the parser/manager interpretation identical.
    for line in unit_lines:
        physical = line.rstrip()
        if physical.endswith("\\"):
            raise MaintenanceInstallError(
                f"systemd unit uses unsafe line continuation: {unit_name}"
            )
    condition_entries = [
        (index, line.strip())
        for index, line in enumerate(unit_lines)
        if line.strip().startswith("ConditionPathExists")
    ]
    if condition_entries != [
        (unit_section_start + offset, RUNTIME_START_FENCE_CONDITION)
        for offset, line in enumerate(
            unit_lines[unit_section_start:unit_section_end]
        )
        if line.strip() == RUNTIME_START_FENCE_CONDITION
    ] or len(condition_entries) != 1:
        raise MaintenanceInstallError(
            f"systemd unit lacks the exact runtime start fence: {unit_name}"
        )


def _validate_ed25519_public_key(path: Path) -> None:
    try:
        raw = path.read_bytes()
        text = raw.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise MaintenanceInstallError("runtime trust key is not ASCII PEM") from exc
    lines = text.splitlines()
    if (
        not 100 <= len(raw) <= 1024
        or len(lines) < 3
        or lines[0] != "-----BEGIN PUBLIC KEY-----"
        or lines[-1] != "-----END PUBLIC KEY-----"
        or any(not line or len(line) > 64 for line in lines[1:-1])
    ):
        raise MaintenanceInstallError("runtime trust key is not a public-key PEM")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MaintenanceInstallError("runtime trust key PEM is malformed") from exc
    if len(der) != len(ED25519_SPKI_PREFIX) + 32 or not der.startswith(
        ED25519_SPKI_PREFIX
    ):
        raise MaintenanceInstallError("runtime trust key is not Ed25519")


def _expected_fixed_payload_files() -> set[str]:
    result = {
        *(f"communication/app/{name}" for name in COMMUNICATION_AGENT_FILES),
        *(f"updater/app/{name}" for name in DEVICE_UPDATER_FILES),
        *(f"updater/helpers/{name}" for name in DEVICE_UPDATER_HELPER_FILES),
        *(f"systemd/{name}" for name in MAIN_UNIT_FILES),
        *(f"systemd/{name}" for name in HELPER_UNIT_FILES),
        DROP_IN_RELATIVE,
        SYSUSERS_PAYLOAD,
        TMPFILES_PAYLOAD,
        PREFLIGHT_PAYLOAD,
        RELEASE_ENV_PAYLOAD,
    }
    return set(result)


def expected_payload_directories(file_paths: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for raw in file_paths:
        path = PurePosixPath(raw)
        for parent in path.parents:
            if parent != PurePosixPath("."):
                directories.add(parent.as_posix())
    return directories


def _walk_payload(root: Path) -> tuple[set[str], set[str]]:
    try:
        root_details = root.lstat()
    except OSError as exc:
        raise MaintenanceInstallError("maintenance payload is unavailable") from exc
    if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
        raise MaintenanceInstallError("maintenance payload must be an ordinary directory")

    directories: set[str] = set()
    files: set[str] = set()
    stack: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while stack:
        directory, relative_directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise MaintenanceInstallError("maintenance payload cannot be enumerated") from exc
        for entry in entries:
            relative = (
                PurePosixPath(entry.name)
                if relative_directory == PurePosixPath(".")
                else relative_directory / entry.name
            )
            relative_text = relative.as_posix()
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise MaintenanceInstallError(
                    f"maintenance payload entry cannot be inspected: {relative_text}"
                ) from exc
            if stat.S_ISLNK(details.st_mode):
                raise MaintenanceInstallError(
                    f"maintenance payload contains a symbolic link: {relative_text}"
                )
            if stat.S_ISDIR(details.st_mode):
                directories.add(relative_text)
                stack.append((Path(entry.path), relative))
            elif stat.S_ISREG(details.st_mode):
                if os.name == "posix" and details.st_nlink != 1:
                    raise MaintenanceInstallError(
                        f"maintenance payload contains a hard-linked file: {relative_text}"
                    )
                files.add(relative_text)
            else:
                raise MaintenanceInstallError(
                    f"maintenance payload contains a special file: {relative_text}"
                )
    return directories, files


def _parse_manifest_document(document: object, manifest_sha256: str) -> MaintenanceManifest:
    root = _require_exact_keys(
        document,
        {
            "schemaVersion",
            "payloadId",
            "sourceGitCommit",
            "expectedImage",
            "components",
            "files",
        },
        description="maintenance manifest",
    )
    if root["schemaVersion"] != MANIFEST_SCHEMA_VERSION:
        raise MaintenanceInstallError("maintenance manifest schema version is unsupported")
    payload_id = _safe_id(root["payloadId"], description="payload ID")
    source_git_commit = root["sourceGitCommit"]
    if not isinstance(source_git_commit, str) or not GIT_COMMIT.fullmatch(
        source_git_commit
    ):
        raise MaintenanceInstallError("source Git commit is invalid")

    expected_image = _require_exact_keys(
        root["expectedImage"],
        {"releaseId", "version", "legacyService"},
        description="expected image",
    )
    expected_release_id = _safe_id(
        expected_image["releaseId"], description="expected image release ID"
    )
    expected_version = _safe_id(
        expected_image["version"], description="expected image version"
    )
    if expected_image["legacyService"] != LEGACY_SERVICE:
        raise MaintenanceInstallError("only the known legacy hardware service is supported")

    components = _require_exact_keys(
        root["components"],
        {"communicationAgent", "deviceUpdater"},
        description="maintenance components",
    )
    parsed_components: dict[str, str] = {}
    for component_name in ("communicationAgent", "deviceUpdater"):
        component = _require_exact_keys(
            components[component_name],
            {"releaseId"},
            description=f"{component_name} component",
        )
        parsed_components[component_name] = _safe_id(
            component["releaseId"], description=f"{component_name} release ID"
        )

    raw_files = root["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise MaintenanceInstallError("maintenance manifest file list is invalid")
    parsed_files: dict[str, ManifestFile] = {}
    total_size = 0
    for index, raw_file in enumerate(raw_files):
        item = _require_exact_keys(
            raw_file,
            {"path", "sha256", "size", "mode"},
            description=f"maintenance file {index}",
        )
        path = _safe_relative_path(item["path"], description="maintenance file path")
        if path == MANIFEST_NAME or path in parsed_files:
            raise MaintenanceInstallError("maintenance manifest has a duplicate file path")
        digest = item["sha256"]
        if not isinstance(digest, str) or not HEX_64.fullmatch(digest):
            raise MaintenanceInstallError("maintenance file SHA-256 is invalid")
        size = item["size"]
        mode = item["mode"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > MAX_PAYLOAD_FILE_BYTES
        ):
            raise MaintenanceInstallError("maintenance file size is invalid")
        if isinstance(mode, bool) or not isinstance(mode, int) or mode != 0o644:
            raise MaintenanceInstallError("maintenance payload files must declare mode 0644")
        total_size += size
        parsed_files[path] = ManifestFile(path, digest, size, mode)
    if total_size > MAX_PAYLOAD_BYTES:
        raise MaintenanceInstallError("maintenance payload is too large")

    fixed = _expected_fixed_payload_files()
    actual = set(parsed_files)
    trust_files = actual - fixed
    if actual - trust_files != fixed:
        raise MaintenanceInstallError("maintenance payload fixed allowlist is incomplete")
    if not 1 <= len(trust_files) <= MAX_TRUST_KEYS or any(
        not path.startswith("trust/runtime-release-keys/")
        or not RUNTIME_KEY_NAME.fullmatch(PurePosixPath(path).name)
        or len(PurePosixPath(path).parts) != 3
        for path in trust_files
    ):
        raise MaintenanceInstallError("maintenance runtime trust allowlist is invalid")

    return MaintenanceManifest(
        payload_id=payload_id,
        source_git_commit=source_git_commit,
        expected_image_release_id=expected_release_id,
        expected_image_version=expected_version,
        legacy_service=LEGACY_SERVICE,
        communication_release_id=parsed_components["communicationAgent"],
        updater_release_id=parsed_components["deviceUpdater"],
        files=parsed_files,
        manifest_sha256=manifest_sha256,
    )


def load_and_validate_payload(
    payload_root: Path, expected_manifest_sha256: str
) -> MaintenanceManifest:
    """Authenticate and completely validate a maintenance payload directory."""

    if not HEX_64.fullmatch(expected_manifest_sha256):
        raise MaintenanceInstallError("expected manifest SHA-256 is invalid")
    manifest_path = payload_root / MANIFEST_NAME
    try:
        details = manifest_path.lstat()
    except OSError as exc:
        raise MaintenanceInstallError("maintenance manifest is unavailable") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or (os.name == "posix" and details.st_nlink != 1)
        or details.st_size > MAX_MANIFEST_BYTES
    ):
        raise MaintenanceInstallError("maintenance manifest is not a safe regular file")
    raw = manifest_path.read_bytes()
    actual_manifest_sha256 = _sha256_bytes(raw)
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise MaintenanceInstallError("maintenance manifest SHA-256 differs")
    manifest = _parse_manifest_document(
        _load_json_bytes(raw, description="maintenance manifest"),
        actual_manifest_sha256,
    )

    directories, files = _walk_payload(payload_root)
    expected_files = set(manifest.files) | {MANIFEST_NAME}
    if files != expected_files:
        missing = sorted(expected_files - files)
        extra = sorted(files - expected_files)
        raise MaintenanceInstallError(
            f"maintenance payload inventory differs (missing={missing}, extra={extra})"
        )
    expected_directories = expected_payload_directories(expected_files)
    if directories != expected_directories:
        raise MaintenanceInstallError("maintenance payload directory allowlist differs")

    for relative, locked in manifest.files.items():
        source = payload_root.joinpath(*PurePosixPath(relative).parts)
        details = source.lstat()
        if details.st_size != locked.size or _sha256_file(source) != locked.sha256:
            raise MaintenanceInstallError(
                f"maintenance payload file differs from manifest: {relative}"
            )
        if os.name == "posix" and stat.S_IMODE(details.st_mode) != locked.mode:
            raise MaintenanceInstallError(
                f"maintenance payload file mode differs from manifest: {relative}"
            )

    trust_paths = sorted(
        path for path in manifest.files if path.startswith("trust/runtime-release-keys/")
    )
    for relative in trust_paths:
        _validate_ed25519_public_key(
            payload_root.joinpath(*PurePosixPath(relative).parts)
        )

    expected_env = (
        f"ECOBIN_COMMUNICATION_AGENT_VERSION={manifest.communication_release_id}\n"
        f"ECOBIN_DEVICE_UPDATER_VERSION={manifest.updater_release_id}\n"
    ).encode("utf-8")
    if (payload_root / RELEASE_ENV_PAYLOAD).read_bytes() != expected_env:
        raise MaintenanceInstallError("device-management release environment is invalid")
    if (payload_root / DROP_IN_RELATIVE).read_bytes() != DROP_IN_CONTENT:
        raise MaintenanceInstallError("runtime target drop-in has unexpected semantics")
    for unit_name in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
        unit_path = payload_root / "systemd" / unit_name
        try:
            unit_raw = unit_path.read_bytes()
        except OSError as exc:
            raise MaintenanceInstallError(
                f"systemd unit is unavailable: {unit_name}"
            ) from exc
        _validate_runtime_fence_unit_bytes(unit_raw, unit_name)
    return manifest


def build_payload_manifest(
    payload_root: Path,
    *,
    payload_id: str,
    source_git_commit: str,
    expected_image_release_id: str,
    expected_image_version: str,
    communication_release_id: str,
    updater_release_id: str,
) -> tuple[dict[str, Any], str]:
    """Build a canonical allowlist on a trusted workstation.

    The returned document still has to be reviewed and its returned digest must
    be transported out of band.  The live-device CLI never calls this helper.
    """

    directories, files = _walk_payload(payload_root)
    del directories
    if MANIFEST_NAME in files:
        raise MaintenanceInstallError("remove the old maintenance manifest first")
    entries: list[dict[str, Any]] = []
    for relative in sorted(files):
        path = payload_root.joinpath(*PurePosixPath(relative).parts)
        details = path.lstat()
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "size": details.st_size,
                "mode": (
                    stat.S_IMODE(details.st_mode) if os.name == "posix" else 0o644
                ),
            }
        )
    document: dict[str, Any] = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "payloadId": payload_id,
        "sourceGitCommit": source_git_commit,
        "expectedImage": {
            "releaseId": expected_image_release_id,
            "version": expected_image_version,
            "legacyService": LEGACY_SERVICE,
        },
        "components": {
            "communicationAgent": {"releaseId": communication_release_id},
            "deviceUpdater": {"releaseId": updater_release_id},
        },
        "files": entries,
    }
    raw = _canonical_json(document)
    # Parse before returning so the build host applies the same strict schema.
    _parse_manifest_document(document, _sha256_bytes(raw))
    return document, _sha256_bytes(raw)


def write_payload_manifest(payload_root: Path, **identity: str) -> str:
    document, digest = build_payload_manifest(payload_root, **identity)
    destination = payload_root / MANIFEST_NAME
    if _lexists(destination):
        raise MaintenanceInstallError("maintenance manifest already exists")
    destination.write_bytes(_canonical_json(document))
    os.chmod(destination, 0o644)
    return digest


def target_files(manifest: MaintenanceManifest) -> tuple[TargetFile, ...]:
    targets: list[TargetFile] = []
    for name in COMMUNICATION_AGENT_FILES:
        targets.append(
            TargetFile(
                f"communication/app/{name}",
                f"/opt/ecobin/communication/releases/{manifest.communication_release_id}/app/{name}",
                0o644,
            )
        )
    for name in DEVICE_UPDATER_FILES:
        targets.append(
            TargetFile(
                f"updater/app/{name}",
                f"/opt/ecobin/updater/releases/{manifest.updater_release_id}/app/{name}",
                0o644,
            )
        )
    for name in DEVICE_UPDATER_HELPER_FILES:
        targets.extend(
            (
                TargetFile(
                    f"updater/helpers/{name}",
                    f"/opt/ecobin/updater/releases/{manifest.updater_release_id}/helpers/{name}",
                    0o644,
                ),
                TargetFile(
                    f"updater/helpers/{name}",
                    f"/usr/lib/ecobin/device-management/helpers/{name}",
                    0o644,
                ),
            )
        )
    # The helper imports the same local protocol module as the updater.  It is
    # deliberately copied, never linked into the replaceable release tree.
    targets.append(
        TargetFile(
            "updater/app/local_control.py",
            "/usr/lib/ecobin/device-management/local_control.py",
            0o644,
        )
    )
    for name in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
        targets.append(TargetFile(f"systemd/{name}", f"/etc/systemd/system/{name}", 0o644))
    targets.extend(
        (
            TargetFile(
                DROP_IN_RELATIVE,
                RUNTIME_DROP_IN_PATH,
                0o644,
            ),
            TargetFile(
                SYSUSERS_PAYLOAD,
                "/usr/lib/sysusers.d/ecobin-device-runtime.conf",
                0o644,
            ),
            TargetFile(
                TMPFILES_PAYLOAD,
                "/usr/lib/tmpfiles.d/ecobin-device-runtime.conf",
                0o644,
            ),
            TargetFile(
                PREFLIGHT_PAYLOAD,
                "/usr/lib/ecobin/business_runtime_preflight.py",
                0o644,
            ),
            TargetFile(
                RELEASE_ENV_PAYLOAD,
                "/usr/share/ecobin/device-management-release.env",
                0o644,
            ),
        )
    )
    for relative in sorted(manifest.files):
        if relative.startswith("trust/runtime-release-keys/"):
            targets.append(
                TargetFile(
                    relative,
                    f"/usr/share/ecobin/runtime-release-keys/{PurePosixPath(relative).name}",
                    0o644,
                )
            )
    destinations = [target.destination for target in targets]
    if len(destinations) != len(set(destinations)) or any(
        destination in IMMUTABLE_PATHS for destination in destinations
    ):
        raise MaintenanceInstallError("internal maintenance target mapping is unsafe")
    return tuple(targets)


def _default_runner(arguments: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(arguments),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            # The legacy hardware service intentionally permits a 180 second
            # cold start.  Leave bounded headroom while still converting a
            # hung host tool into the installer's controlled error contract.
            timeout=210,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            124,
            str(exc.stdout or ""),
            f"host command timed out after {exc.timeout} seconds",
        )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class MaintenanceInstaller:
    """Validated installer with injectable root and process boundary for tests."""

    def __init__(
        self,
        *,
        rootfs: Path = Path("/"),
        runner: CommandRunner = _default_runner,
        enforce_root_ownership: bool = True,
        check_host_tools: bool = True,
        metadata_identity: MetadataIdentity | None = None,
        metadata_mode: MetadataMode | None = None,
        before_publish_hook: Callable[[Mapping[str, Any]], None] | None = None,
        after_publish_hook: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.rootfs = rootfs.resolve()
        self.runner = runner
        self.enforce_root_ownership = enforce_root_ownership
        self.check_host_tools = check_host_tools
        self.metadata_identity = metadata_identity
        self.metadata_mode = metadata_mode
        self.before_publish_hook = before_publish_hook
        self.after_publish_hook = after_publish_hook
        self._trusted_host_tools: dict[str, str] = {}
        if self.check_host_tools:
            self._assert_host_tools()

    def _path(self, absolute: str) -> Path:
        return _rooted(self.rootfs, absolute)

    def _run(self, arguments: Sequence[str], *, check: bool = True) -> CommandResult:
        command = tuple(arguments)
        if self.check_host_tools and command and command[0] in TRUSTED_HOST_TOOLS:
            resolved = self._trusted_host_tools.get(command[0])
            if resolved is None:
                raise MaintenanceInstallError(
                    f"required host tool was not frozen: {command[0]}"
                )
            command = (resolved, *command[1:])
        result = self.runner(command)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
            raise MaintenanceInstallError(
                f"command failed ({command[0]}): {detail or result.returncode}"
            )
        return result

    def _systemctl_show(self, unit: str) -> dict[str, str]:
        query_unit = (
            unit.replace("@.service", "@maintenance-audit.service")
            if unit.endswith("@.service")
            else unit
        )
        result = self._run(
            (
                "systemctl",
                "show",
                query_unit,
                "--property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths",
                "--no-pager",
            ),
            check=False,
        )
        values: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        required_properties = {
            "Id",
            "LoadState",
            "ActiveState",
            "FragmentPath",
            "DropInPaths",
        }
        if result.returncode not in (0, 1) or not required_properties.issubset(values):
            raise MaintenanceInstallError(f"cannot inspect systemd unit: {unit}")
        return {
            "id": values.get("Id", query_unit),
            "loadState": values.get("LoadState", "not-found"),
            "activeState": values.get("ActiveState", "inactive"),
            "subState": values.get("SubState", "dead"),
            "unitFileState": values.get("UnitFileState", ""),
            "fragmentPath": values.get("FragmentPath", ""),
            "dropInPaths": values["DropInPaths"],
        }

    def _helper_instances(self) -> tuple[str, ...]:
        patterns = tuple(f"{prefix}*.service" for prefix in HELPER_INSTANCE_PREFIXES)
        result = self._run(
            (
                "systemctl",
                "list-units",
                "--all",
                "--plain",
                "--no-legend",
                "--full",
                *patterns,
            ),
            check=False,
        )
        if result.returncode != 0:
            raise MaintenanceInstallError("cannot enumerate privileged helper instances")
        instances: set[str] = set()
        for line in result.stdout.splitlines():
            fields = line.split()
            if not fields:
                continue
            unit = fields[0]
            if not (
                unit.endswith(".service")
                and unit not in HELPER_UNIT_FILES
                and any(unit.startswith(prefix) for prefix in HELPER_INSTANCE_PREFIXES)
                and not any(character.isspace() or character == "/" for character in unit)
            ):
                raise MaintenanceInstallError(
                    "systemd returned an unexpected privileged helper instance"
                )
            instances.add(unit)
        return tuple(sorted(instances))

    @staticmethod
    def _reported_drop_in_paths(state: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(state.get("dropInPaths", "").split())

    def _metadata_ids(self, path: Path, details: os.stat_result) -> tuple[int, int]:
        absolute = "/" + path.relative_to(self.rootfs).as_posix()
        if self.metadata_identity is not None:
            return self.metadata_identity(absolute, details)
        return details.st_uid, details.st_gid

    def _metadata_mode(self, path: Path, details: os.stat_result) -> int:
        absolute = "/" + path.relative_to(self.rootfs).as_posix()
        if self.metadata_mode is not None:
            return self.metadata_mode(absolute, details)
        return stat.S_IMODE(details.st_mode)

    def _assert_root_metadata(
        self, path: Path, details: os.stat_result, description: str
    ) -> None:
        uid, gid = self._metadata_ids(path, details)
        if (self.enforce_root_ownership or self.metadata_identity is not None) and (
            uid != 0 or gid != 0
        ):
            raise MaintenanceInstallError(f"{description} is not owned by root")

    def _assert_safe_regular(self, path: Path, description: str) -> os.stat_result:
        try:
            details = path.lstat()
        except OSError as exc:
            raise MaintenanceInstallError(f"{description} is unavailable") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or (os.name == "posix" and details.st_nlink != 1)
        ):
            raise MaintenanceInstallError(f"{description} is not a safe regular file")
        self._assert_root_metadata(path, details, description)
        return details

    def _assert_safe_directory(
        self, path: Path, description: str, *, expected_mode: int | None = None
    ) -> os.stat_result:
        try:
            details = path.lstat()
        except OSError as exc:
            raise MaintenanceInstallError(f"{description} is unavailable") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise MaintenanceInstallError(f"{description} is not a safe directory")
        self._assert_root_metadata(path, details, description)
        if self._metadata_mode(path, details) & 0o022:
            raise MaintenanceInstallError(
                f"{description} is writable by a non-root identity"
            )
        if expected_mode is not None and self._metadata_mode(path, details) != expected_mode:
            raise MaintenanceInstallError(
                f"{description} mode is not {expected_mode:04o}"
            )
        return details

    def _read_image_identity(self) -> dict[str, str]:
        identities: list[dict[str, str]] = []
        for absolute in (
            "/usr/share/ecobin/image-release.json",
            "/etc/ecobin/image-release.json",
        ):
            path = self._path(absolute)
            details = self._assert_safe_regular(path, f"image identity {absolute}")
            if details.st_size > 64 * 1024:
                raise MaintenanceInstallError("image identity is unexpectedly large")
            document = _load_json_bytes(path.read_bytes(), description="image identity")
            if not isinstance(document, dict):
                raise MaintenanceInstallError("image identity is malformed")
            release_id = document.get("releaseId")
            version = document.get("version")
            if not isinstance(release_id, str) or not isinstance(version, str):
                raise MaintenanceInstallError("image identity is incomplete")
            identities.append({"releaseId": release_id, "version": version})
        if identities[0] != identities[1]:
            raise MaintenanceInstallError("public and protected image identities differ")
        return identities[0]

    def _assert_expected_device(
        self,
        manifest: MaintenanceManifest,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
        require_legacy_active: bool = True,
    ) -> tuple[dict[str, str], dict[str, str]]:
        if (
            expected_image_release_id != manifest.expected_image_release_id
            or expected_image_version != manifest.expected_image_version
            or expected_legacy_service != manifest.legacy_service
        ):
            raise MaintenanceInstallError(
                "operator confirmation differs from the authenticated manifest"
            )
        identity = self._read_image_identity()
        if identity != {
            "releaseId": expected_image_release_id,
            "version": expected_image_version,
        }:
            raise MaintenanceInstallError("live image identity differs from confirmation")

        legacy = self._systemctl_show(expected_legacy_service)
        if legacy["loadState"] != "loaded" or (
            require_legacy_active and legacy["activeState"] != "active"
        ):
            raise MaintenanceInstallError("known legacy hardware service is not active and loaded")
        if self._reported_drop_in_paths(legacy) != (LEGACY_GATE_DROP_IN_PATH,):
            raise MaintenanceInstallError(
                "known legacy hardware service drop-in report differs from v13 baseline"
            )
        fragment = legacy["fragmentPath"]
        if not fragment.startswith("/"):
            raise MaintenanceInstallError("legacy hardware service fragment path is invalid")
        self._assert_safe_regular(
            self._path(fragment), "legacy hardware service fragment"
        )
        return identity, legacy

    def _assert_mcu_safety_gate(self) -> dict[str, str]:
        if _lexists(self._path(MCU_RECOVERY_MARKER)):
            raise MaintenanceInstallError(
                "MCU application recovery marker is present; helper sockets must stay closed"
            )
        safe_gpio = self._systemctl_show(MCU_SAFE_GPIO_SERVICE)
        if (
            safe_gpio["loadState"] != "loaded"
            or safe_gpio["activeState"] != "active"
            or safe_gpio["fragmentPath"] != MCU_SAFE_GPIO_UNIT_PATH
        ):
            raise MaintenanceInstallError(
                "MCU safe-GPIO service is not the active image-owned unit"
            )
        if self._reported_drop_in_paths(safe_gpio):
            raise MaintenanceInstallError(
                "MCU safe-GPIO service has uncontrolled drop-ins"
            )
        unit_path = self._path(MCU_SAFE_GPIO_UNIT_PATH)
        unit_details = self._assert_safe_regular(
            unit_path, "MCU safe-GPIO service fragment"
        )
        if (
            self._metadata_mode(unit_path, unit_details) != 0o644
            or _sha256_file(unit_path) != MCU_SAFE_GPIO_UNIT_SHA256
        ):
            raise MaintenanceInstallError(
                "MCU safe-GPIO service fragment differs from the image baseline"
            )

        fact_path = self._path(MCU_SAFE_GPIO_FACT_PATH)
        self._assert_safe_directory(
            fact_path.parent,
            "MCU current-boot safety fact directory",
            expected_mode=0o700,
        )
        fact_details = self._assert_safe_regular(
            fact_path, "MCU current-boot safety fact"
        )
        if (
            self._metadata_mode(fact_path, fact_details) != 0o600
            or fact_details.st_size <= 0
            or fact_details.st_size > 4096
        ):
            raise MaintenanceInstallError(
                "MCU current-boot safety fact metadata differs"
            )
        boot_id_path = self._path(KERNEL_BOOT_ID_PATH)
        boot_details = self._assert_safe_regular(
            boot_id_path, "kernel boot identity"
        )
        if boot_details.st_size > 128:
            raise MaintenanceInstallError("kernel boot identity is unexpectedly large")
        try:
            boot_id = boot_id_path.read_text(encoding="ascii").strip().lower()
            fact = _load_json_bytes(
                fact_path.read_bytes(), description="MCU current-boot safety fact"
            )
        except OSError as exc:
            raise MaintenanceInstallError(
                "MCU current-boot safety evidence is unreadable"
            ) from exc
        if (
            BOOT_ID.fullmatch(boot_id) is None
            or not isinstance(fact, dict)
            or set(fact)
            != {"schemaVersion", "status", "bootId", "boot0", "resetGate"}
            or fact.get("schemaVersion") != 1
            or fact.get("status") != "SAFE_APPLICATION"
            or fact.get("bootId") != boot_id
            or fact.get("boot0") != {"wpi": 2, "level": 0}
            or fact.get("resetGate") != {"wpi": 5, "level": 0}
        ):
            raise MaintenanceInstallError(
                "MCU current-boot safety evidence is invalid or stale"
            )
        return safe_gpio

    def _assert_host_tools(self) -> None:
        if not self.check_host_tools:
            return
        frozen: dict[str, str] = {}
        for name, absolute in TRUSTED_HOST_TOOLS.items():
            path = Path(absolute)
            try:
                details = path.lstat()
            except OSError as exc:
                raise MaintenanceInstallError(f"required host tool is unavailable: {name}")
            if (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != 0
                or details.st_gid != 0
                or stat.S_IMODE(details.st_mode) & 0o022
                or not os.access(path, os.X_OK)
            ):
                raise MaintenanceInstallError(
                    f"required host tool is not trusted: {absolute}"
                )
            for parent in (path.parent, path.parent.parent):
                parent_details = parent.lstat()
                if (
                    stat.S_ISLNK(parent_details.st_mode)
                    or not stat.S_ISDIR(parent_details.st_mode)
                    or parent_details.st_uid != 0
                    or parent_details.st_gid != 0
                    or stat.S_IMODE(parent_details.st_mode) & 0o022
                ):
                    raise MaintenanceInstallError(
                        f"required host tool parent is not trusted: {parent}"
                    )
            frozen[name] = absolute
        self._trusted_host_tools = frozen

    def _getent_exists(self, database: str, name: str) -> bool:
        result = self._run(("getent", database, name), check=False)
        if result.returncode not in (0, 2):
            raise MaintenanceInstallError(f"cannot inspect {database} identity: {name}")
        return result.returncode == 0

    def _getent_line(self, database: str, name: str) -> str | None:
        result = self._run(("getent", database, name), check=False)
        if result.returncode == 2:
            return None
        if result.returncode != 0:
            raise MaintenanceInstallError(f"cannot inspect {database} identity: {name}")
        lines = [line for line in result.stdout.splitlines() if line]
        if len(lines) != 1:
            raise MaintenanceInstallError(f"{database} identity is ambiguous: {name}")
        return lines[0]

    def _identity_installation_state(
        self, *, require_present: bool
    ) -> dict[str, Any] | None:
        """Validate either a wholly absent or the exact retained sysusers set."""

        account_lines = {
            name: self._getent_line("passwd", name) for name in ACCOUNT_NAMES
        }
        created_group_names = (*ACCOUNT_NAMES, *GROUP_NAMES)
        group_lines = {
            name: self._getent_line("group", name) for name in created_group_names
        }
        presence = [
            line is not None
            for line in (*account_lines.values(), *group_lines.values())
        ]
        if not any(presence):
            if require_present:
                raise MaintenanceInstallError("device-management identities are absent")
            return None
        if not all(presence):
            raise MaintenanceInstallError(
                "device-management identities are only partially present"
            )

        group_ids: dict[str, int] = {}
        for name, line in group_lines.items():
            assert line is not None
            fields = line.split(":")
            if len(fields) != 4 or fields[0] != name:
                raise MaintenanceInstallError(f"installed group is malformed: {name}")
            try:
                group_id = int(fields[2])
            except ValueError as exc:
                raise MaintenanceInstallError(
                    f"installed group ID is invalid: {name}"
                ) from exc
            if group_id <= 0:
                raise MaintenanceInstallError(f"installed group is privileged: {name}")
            group_ids[name] = group_id
            if name in IPC_GROUP_MEMBERS:
                members = frozenset(filter(None, fields[3].split(",")))
                if members != IPC_GROUP_MEMBERS[name]:
                    raise MaintenanceInstallError(
                        f"installed IPC group membership differs: {name}"
                    )

        seen_uids: set[int] = set()
        user_ids: dict[str, int] = {}
        primary_group_ids: dict[str, int] = {}
        for name, line in account_lines.items():
            assert line is not None
            fields = line.split(":")
            if len(fields) != 7 or fields[0] != name:
                raise MaintenanceInstallError(f"installed account is malformed: {name}")
            try:
                user_id = int(fields[2])
                primary_group_id = int(fields[3])
            except ValueError as exc:
                raise MaintenanceInstallError(
                    f"installed account numeric identity is invalid: {name}"
                ) from exc
            if user_id <= 0 or user_id in seen_uids:
                raise MaintenanceInstallError(f"installed account UID is unsafe: {name}")
            seen_uids.add(user_id)
            user_ids[name] = user_id
            primary_group_ids[name] = primary_group_id
            if (
                primary_group_id != group_ids[name]
                or fields[5] != ACCOUNT_HOMES[name]
                or fields[6] not in ("/usr/sbin/nologin", "/sbin/nologin")
            ):
                raise MaintenanceInstallError(
                    f"installed account attributes differ: {name}"
                )
            numeric_account = self._run(
                ("getent", "passwd", str(user_id)), check=False
            )
            numeric_account_lines = [
                value for value in numeric_account.stdout.splitlines() if value
            ]
            if (
                numeric_account.returncode != 0
                or len(numeric_account_lines) != 1
                or numeric_account_lines[0].split(":", 1)[0] != name
            ):
                raise MaintenanceInstallError(
                    f"installed account UID is shared or unresolved: {name}"
                )
            numeric_group = self._run(
                ("getent", "group", str(primary_group_id)), check=False
            )
            numeric_group_lines = [
                value for value in numeric_group.stdout.splitlines() if value
            ]
            if (
                numeric_group.returncode != 0
                or len(numeric_group_lines) != 1
                or numeric_group_lines[0].split(":", 1)[0] != name
            ):
                raise MaintenanceInstallError(
                    f"installed account primary group is shared or unresolved: {name}"
                )
            groups = self._run(("id", "-nG", name), check=False)
            if groups.returncode != 0:
                raise MaintenanceInstallError(
                    f"cannot inspect installed account groups: {name}"
                )
            actual_groups = frozenset(groups.stdout.split())
            if actual_groups != ACCOUNT_GROUPS[name]:
                raise MaintenanceInstallError(
                    f"installed account supplementary groups differ: {name}"
                )
        return {
            "users": user_ids,
            "primaryGroups": primary_group_ids,
            "groups": group_ids,
        }

    def _assert_partial_identity_state_safe(self) -> None:
        """Accept only an exact, conflict-free prefix of the sysusers result."""

        account_lines = {
            name: self._getent_line("passwd", name) for name in ACCOUNT_NAMES
        }
        group_names = (*ACCOUNT_NAMES, *GROUP_NAMES)
        group_lines = {
            name: self._getent_line("group", name) for name in group_names
        }
        seen_uids: set[int] = set()
        seen_gids: set[int] = set()
        parsed_groups: dict[str, int] = {}
        for name, line in group_lines.items():
            if line is None:
                continue
            fields = line.split(":")
            if len(fields) != 4 or fields[0] != name:
                raise MaintenanceInstallError(
                    f"partial installed group is malformed: {name}"
                )
            try:
                gid = int(fields[2])
            except ValueError as exc:
                raise MaintenanceInstallError(
                    f"partial installed group ID is invalid: {name}"
                ) from exc
            expected_members = IPC_GROUP_MEMBERS.get(name, frozenset())
            actual_members = frozenset(filter(None, fields[3].split(",")))
            if (
                gid <= 0
                or gid in seen_gids
                or not actual_members.issubset(expected_members)
            ):
                raise MaintenanceInstallError(
                    f"partial installed group conflicts with the declaration: {name}"
                )
            reverse = self._run(("getent", "group", str(gid)), check=False)
            reverse_lines = [line for line in reverse.stdout.splitlines() if line]
            if (
                reverse.returncode != 0
                or len(reverse_lines) != 1
                or reverse_lines[0].split(":", 1)[0] != name
            ):
                raise MaintenanceInstallError(
                    f"partial installed group ID is shared or unresolved: {name}"
                )
            seen_gids.add(gid)
            parsed_groups[name] = gid
        for name, line in account_lines.items():
            if line is None:
                continue
            fields = line.split(":")
            if len(fields) != 7 or fields[0] != name:
                raise MaintenanceInstallError(
                    f"partial installed account is malformed: {name}"
                )
            try:
                uid = int(fields[2])
                primary_gid = int(fields[3])
            except ValueError as exc:
                raise MaintenanceInstallError(
                    f"partial installed account ID is invalid: {name}"
                ) from exc
            if (
                uid <= 0
                or uid in seen_uids
                or parsed_groups.get(name) != primary_gid
                or fields[5] != ACCOUNT_HOMES[name]
                or fields[6] not in {"/usr/sbin/nologin", "/sbin/nologin"}
            ):
                raise MaintenanceInstallError(
                    f"partial installed account conflicts with the declaration: {name}"
                )
            reverse = self._run(("getent", "passwd", str(uid)), check=False)
            reverse_lines = [line for line in reverse.stdout.splitlines() if line]
            if (
                reverse.returncode != 0
                or len(reverse_lines) != 1
                or reverse_lines[0].split(":", 1)[0] != name
            ):
                raise MaintenanceInstallError(
                    f"partial installed account UID is shared or unresolved: {name}"
                )
            memberships = self._run(("id", "-nG", name), check=False)
            if memberships.returncode != 0 or not frozenset(
                memberships.stdout.split()
            ).issubset(ACCOUNT_GROUPS[name]):
                raise MaintenanceInstallError(
                    f"partial installed account groups conflict: {name}"
                )
            seen_uids.add(uid)

    def _assert_partial_tmpfiles_state_safe(
        self, identities: Mapping[str, Any]
    ) -> None:
        owner_expectations = self._runtime_directory_owner_expectations(identities)
        planned_paths = {
            *TMPFILES_DIRECTORY_MODES,
            *TMPFILES_REGULAR_PATHS,
        }
        allowed_children: dict[str, set[str]] = {
            absolute: set() for absolute in TMPFILES_DIRECTORY_MODES
        }
        for candidate in planned_paths:
            parent = str(PurePosixPath(candidate).parent)
            if parent in allowed_children:
                allowed_children[parent].add(PurePosixPath(candidate).name)
        for absolute, expected_mode in TMPFILES_DIRECTORY_MODES.items():
            path = self._path(absolute)
            if not _lexists(path):
                continue
            try:
                details = path.lstat()
            except OSError as exc:
                raise MaintenanceInstallError(
                    f"partial tmpfiles directory is unavailable: {absolute}"
                ) from exc
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise MaintenanceInstallError(
                    f"partial tmpfiles path is not a real directory: {absolute}"
                )
            uid, gid = self._metadata_ids(path, details)
            mode = self._metadata_mode(path, details)
            expected_uid, expected_gids = owner_expectations[absolute]
            final_metadata = uid == expected_uid and gid in expected_gids and mode == expected_mode
            safe_creation_prefix = (
                uid == 0
                and gid == 0
                and mode & ~expected_mode == 0
                and mode & 0o022 == 0
            )
            if not (final_metadata or safe_creation_prefix):
                raise MaintenanceInstallError(
                    f"partial tmpfiles directory conflicts with declaration: {absolute}"
                )
            try:
                children = {entry.name for entry in path.iterdir()}
            except OSError as exc:
                raise MaintenanceInstallError(
                    f"cannot inspect partial tmpfiles directory: {absolute}"
                ) from exc
            if not children.issubset(allowed_children[absolute]):
                raise MaintenanceInstallError(
                    f"partial tmpfiles directory has unknown content: {absolute}"
                )
        for absolute in TMPFILES_REGULAR_PATHS:
            path = self._path(absolute)
            if not _lexists(path):
                continue
            details = self._assert_safe_regular(
                path, f"partial tmpfiles file {absolute}"
            )
            uid, gid = self._metadata_ids(path, details)
            mode = self._metadata_mode(path, details)
            if (
                details.st_size != 0
                or uid != 0
                or gid != 0
                or mode & ~0o600
                or mode & 0o022
            ):
                raise MaintenanceInstallError(
                    f"partial tmpfiles file conflicts with declaration: {absolute}"
                )

    def _hardware_runtime_directories(self) -> tuple[list[Path], dict[str, Any]]:
        current = self._path("/opt/ecobin/hardware/current")
        try:
            details = current.lstat()
        except OSError as exc:
            raise MaintenanceInstallError("hardware current release link is unavailable") from exc
        if not stat.S_ISLNK(details.st_mode):
            raise MaintenanceInstallError("hardware current release is not a symbolic link")
        target = os.readlink(current)
        target_path = PurePosixPath(target)
        if (
            target_path.is_absolute()
            or len(target_path.parts) != 2
            or target_path.parts[0] != "releases"
            or not SAFE_ID.fullmatch(target_path.parts[1])
        ):
            raise MaintenanceInstallError("hardware current release link target is unsafe")
        release = current.parent.joinpath(*target_path.parts)
        self._assert_safe_directory(release, "hardware current release")

        directories = [
            self._path("/opt"),
            self._path("/opt/ecobin"),
            self._path("/opt/ecobin/hardware"),
            self._path("/opt/ecobin/hardware/releases"),
        ]
        for directory in directories:
            self._assert_safe_directory(directory, f"hardware ancestor {directory}")
        for directory, child_names, file_names in os.walk(release, followlinks=False):
            directory_path = Path(directory)
            self._assert_safe_directory(directory_path, "hardware release directory")
            directories.append(directory_path)
            retained_children: list[str] = []
            for name in child_names:
                child = directory_path / name
                child_details = child.lstat()
                if stat.S_ISLNK(child_details.st_mode):
                    continue
                if not stat.S_ISDIR(child_details.st_mode):
                    raise MaintenanceInstallError("hardware release contains an unsafe directory entry")
                retained_children.append(name)
            child_names[:] = retained_children
            for name in file_names:
                child = directory_path / name
                child_details = child.lstat()
                if stat.S_ISLNK(child_details.st_mode):
                    continue
                if not stat.S_ISREG(child_details.st_mode):
                    raise MaintenanceInstallError("hardware release contains a special file")
                self._assert_root_metadata(child, child_details, "hardware release file")
        unique = sorted(set(directories), key=lambda path: (len(path.parts), str(path)))
        return unique, {
            "path": "/opt/ecobin/hardware/current",
            "target": target,
            "device": details.st_dev,
            "inode": details.st_ino,
        }

    def _assert_maintenance_storage_ancestry(
        self, operation_id: str | None = None
    ) -> None:
        maintenance_root = self._path(
            "/var/lib/ecobin/device-management-maintenance"
        )
        if not _lexists(maintenance_root):
            if operation_id is not None:
                raise MaintenanceInstallError("maintenance storage root is absent")
            return
        for absolute in ("/var/lib", "/var/lib/ecobin"):
            self._assert_safe_directory(
                self._path(absolute), f"maintenance storage ancestor {absolute}"
            )
        self._assert_safe_directory(
            maintenance_root,
            "maintenance storage root",
            expected_mode=0o700,
        )
        if operation_id is None:
            return
        _safe_id(operation_id, description="operation ID")
        self._assert_safe_directory(
            self._path(BACKUP_PARENT),
            "maintenance backup parent",
            expected_mode=0o700,
        )
        self._assert_safe_directory(
            self._path(f"{BACKUP_PARENT}/{operation_id}"),
            "maintenance operation backup directory",
            expected_mode=0o700,
        )

    def _assert_maintenance_state_file(
        self, path: Path, description: str
    ) -> os.stat_result:
        details = self._assert_safe_regular(path, description)
        if (
            self._metadata_mode(path, details) != 0o600
            or details.st_size > MAX_MANIFEST_BYTES
        ):
            raise MaintenanceInstallError(
                f"{description} mode or size is unsafe"
            )
        return details

    def _existing_marker(self) -> tuple[str, Mapping[str, Any]] | None:
        self._assert_maintenance_storage_ancestry()
        found: dict[str, Mapping[str, Any]] = {}
        for kind, absolute in (("active", ACTIVE_MARKER), ("pending", PENDING_MARKER)):
            path = self._path(absolute)
            if not _lexists(path):
                continue
            self._assert_maintenance_state_file(
                path, f"{kind} maintenance marker"
            )
            document = _load_json_bytes(path.read_bytes(), description=f"{kind} marker")
            marker = _require_exact_keys(
                document,
                {"schemaVersion", "operationId", "payloadId", "manifestSha256"},
                description=f"{kind} marker",
            )
            if marker["schemaVersion"] != MARKER_SCHEMA_VERSION:
                raise MaintenanceInstallError("maintenance marker schema is unsupported")
            _safe_id(marker["operationId"], description="operation ID")
            _safe_id(marker["payloadId"], description="marker payload ID")
            if not isinstance(marker["manifestSha256"], str) or not HEX_64.fullmatch(
                marker["manifestSha256"]
            ):
                raise MaintenanceInstallError("marker manifest SHA-256 is invalid")
            found[kind] = marker
        if not found:
            return None
        if len(found) == 1:
            kind, marker = next(iter(found.items()))
            self._assert_maintenance_storage_ancestry(
                str(marker["operationId"])
            )
            return kind, marker
        if found["active"] != found["pending"]:
            raise MaintenanceInstallError(
                "active and pending maintenance markers conflict"
            )
        marker = found["pending"]
        self._assert_maintenance_storage_ancestry(
            str(marker["operationId"])
        )
        record_path = self._record_path(str(marker["operationId"]))
        self._assert_maintenance_state_file(
            record_path, "maintenance backup record"
        )
        record = _load_json_bytes(
            record_path.read_bytes(), description="backup record"
        )
        if not isinstance(record, dict):
            raise MaintenanceInstallError("maintenance backup record is invalid")
        # During final marker promotion, a normal I/O failure can leave both
        # equal markers and then record a forward-recovery status.  Pending is
        # the locator for every non-terminal state; INSTALLED may safely use
        # active and finish deleting pending on a later audit.
        if record.get("status") == "INSTALLED":
            return "active", found["active"]
        return "pending", marker

    def _discover_unlocated_operation(self) -> dict[str, Any] | None:
        """Read-only discovery of a crash before the first marker rename."""

        maintenance_root = self._path(
            "/var/lib/ecobin/device-management-maintenance"
        )
        if not _lexists(maintenance_root):
            return None
        self._assert_maintenance_storage_ancestry()
        singleton_names = {
            f".{PurePosixPath(path).name}.incoming"
            for path in (ACTIVE_MARKER, PENDING_MARKER, RUNTIME_START_FENCE)
        }
        allowed_root_names = {
            PurePosixPath(BACKUP_PARENT).name,
            PurePosixPath(ACTIVE_MARKER).name,
            PurePosixPath(PENDING_MARKER).name,
            PurePosixPath(RUNTIME_START_FENCE).name,
            *singleton_names,
        }
        root_entries = {entry.name: entry for entry in maintenance_root.iterdir()}
        unknown_root_entries = set(root_entries) - allowed_root_names
        if unknown_root_entries:
            raise MaintenanceInstallError(
                "maintenance storage contains unknown state: "
                + ", ".join(sorted(unknown_root_entries))
            )
        singleton_entries = {
            name: entry
            for name, entry in root_entries.items()
            if name in singleton_names
        }

        backup_parent = self._path(BACKUP_PARENT)
        if not _lexists(backup_parent):
            if singleton_entries:
                raise MaintenanceInstallError(
                    "unlocated maintenance marker incoming has no owning record"
                )
            return None
        self._assert_safe_directory(
            backup_parent,
            "maintenance backup parent",
            expected_mode=0o700,
        )
        candidates: list[dict[str, Any]] = []
        for operation_dir in sorted(backup_parent.iterdir()):
            if not SAFE_ID.fullmatch(operation_dir.name):
                raise MaintenanceInstallError(
                    "maintenance backup contains an invalid operation directory"
                )
            self._assert_safe_directory(
                operation_dir,
                "maintenance operation backup directory",
                expected_mode=0o700,
            )
            entries = {entry.name: entry for entry in operation_dir.iterdir()}
            if not set(entries).issubset({"record.json", ".record.json.incoming"}):
                raise MaintenanceInstallError(
                    "maintenance operation backup contains unknown state"
                )
            incoming = entries.get(".record.json.incoming")
            if incoming is not None:
                incoming_details = self._assert_safe_regular(
                    incoming, "maintenance backup record incoming"
                )
                uid, gid = self._metadata_ids(incoming, incoming_details)
                if (
                    uid != 0
                    or gid != 0
                    or self._metadata_mode(incoming, incoming_details) != 0o600
                    or incoming_details.st_size > MAX_MANIFEST_BYTES
                ):
                    raise MaintenanceInstallError(
                        "maintenance backup record incoming is unsafe"
                    )
            record_path = entries.get("record.json")
            if record_path is None:
                if incoming is not None or not entries:
                    candidates.append(
                        {
                            "kind": (
                                "EMPTY_RECORD_INCOMING"
                                if incoming is not None
                                else "EMPTY_OPERATION"
                            ),
                            "operationId": operation_dir.name,
                            "operationDirectory": operation_dir,
                        }
                    )
                elif entries:
                    raise MaintenanceInstallError(
                        "maintenance operation backup has no record"
                    )
                continue
            self._assert_maintenance_state_file(
                record_path, "maintenance backup record"
            )
            record = _load_json_bytes(
                record_path.read_bytes(), description="backup record"
            )
            if (
                not isinstance(record, dict)
                or record.get("schemaVersion") != BACKUP_SCHEMA_VERSION
                or record.get("operationId") != operation_dir.name
            ):
                raise MaintenanceInstallError(
                    "unlocated maintenance backup record is invalid"
                )
            if record.get("status") != "ROLLED_BACK":
                candidates.append(
                    {
                        "kind": "RECORD",
                        "operationId": operation_dir.name,
                        "operationDirectory": operation_dir,
                        "record": record,
                    }
                )
        if len(candidates) > 1:
            raise MaintenanceInstallError(
                "multiple unlocated maintenance operations require manual recovery"
            )
        candidate = candidates[0] if candidates else None
        if singleton_entries:
            if candidate is None or candidate["kind"] != "RECORD":
                raise MaintenanceInstallError(
                    "unlocated maintenance marker incoming has no owning record"
                )
            marker_raw = _canonical_json(
                self._marker_document(candidate["record"])
            )
            for entry in singleton_entries.values():
                details = self._assert_safe_regular(
                    entry, "unlocated maintenance singleton incoming"
                )
                uid, gid = self._metadata_ids(entry, details)
                if details.st_size > len(marker_raw):
                    raise MaintenanceInstallError(
                        "unlocated maintenance singleton incoming is unsafe"
                    )
                raw = entry.read_bytes()
                if (
                    uid != 0
                    or gid != 0
                    or self._metadata_mode(entry, details) != 0o600
                    or raw != marker_raw[: len(raw)]
                ):
                    raise MaintenanceInstallError(
                        "unlocated maintenance singleton incoming is unsafe"
                    )
        return candidate

    def _base_directory_checks(self) -> None:
        for absolute in (
            "/etc/systemd/system",
            "/usr/lib",
            "/usr/lib/sysusers.d",
            "/usr/lib/tmpfiles.d",
            "/usr/share",
            "/var/lib",
        ):
            self._assert_safe_directory(self._path(absolute), f"base directory {absolute}")
        for optional in ("/usr/lib/ecobin", "/usr/share/ecobin", "/var/lib/ecobin"):
            path = self._path(optional)
            if _lexists(path):
                self._assert_safe_directory(path, f"existing base directory {optional}")

    def _assert_legacy_gate_drop_in(self, directory: Path) -> None:
        directory_details = self._assert_safe_directory(
            directory,
            "v13 legacy gate drop-in directory",
            expected_mode=0o755,
        )
        if os.name == "posix" and directory_details.st_nlink != 2:
            raise MaintenanceInstallError(
                "v13 legacy gate drop-in directory link count differs"
            )
        entries = list(directory.iterdir())
        expected_name = PurePosixPath(LEGACY_GATE_DROP_IN_PATH).name
        if len(entries) != 1 or entries[0].name != expected_name:
            raise MaintenanceInstallError(
                "v13 legacy gate drop-in directory has uncontrolled entries"
            )
        gate = entries[0]
        details = self._assert_safe_regular(gate, "v13 legacy gate drop-in")
        if (
            details.st_nlink != 1
            or self._metadata_mode(gate, details) != 0o644
            or details.st_size != len(LEGACY_GATE_DROP_IN_CONTENT)
            or _sha256_file(gate) != LEGACY_GATE_DROP_IN_SHA256
            or gate.read_bytes() != LEGACY_GATE_DROP_IN_CONTENT
        ):
            raise MaintenanceInstallError(
                "v13 legacy gate drop-in bytes or metadata differ"
            )

    def _assert_dropin_filesystem(
        self,
        *,
        installed: bool,
        rollback_fenced: bool = False,
        rollback_fence_may_be_partial: bool = False,
        expected_runtime_directory_entries: frozenset[str] | None = None,
    ) -> None:
        exact_names = {
            f"{unit}.d"
            for unit in (
                LEGACY_SERVICE,
                MCU_SAFE_GPIO_SERVICE,
                RUNTIME_TARGET,
                *MAIN_UNIT_FILES,
                *HELPER_UNIT_FILES,
            )
        }
        legacy_directory_name = f"{LEGACY_SERVICE}.d"
        runtime_directory_name = f"{RUNTIME_TARGET}.d"
        runtime_file_name = PurePosixPath(RUNTIME_DROP_IN_PATH).name
        rollback_directory_names = {
            f"{unit}.d" for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES)
        }
        rollback_directories_found: set[str] = set()
        legacy_found = False
        for base_absolute in SYSTEMD_UNIT_FRAGMENT_BASES:
            base = self._path(base_absolute)
            if not _lexists(base):
                continue
            try:
                base_details = base.lstat()
                if stat.S_ISDIR(base_details.st_mode):
                    self._assert_safe_directory(
                        base, f"systemd unit directory {base_absolute}"
                    )
                elif not stat.S_ISLNK(base_details.st_mode):
                    raise MaintenanceInstallError(
                        f"systemd unit lookup path is unsafe: {base_absolute}"
                    )
                children = tuple(base.iterdir())
            except OSError as exc:
                raise MaintenanceInstallError(
                    f"cannot inspect systemd unit lookup path: {base_absolute}"
                ) from exc
            for child in children:
                name = child.name
                related = name in exact_names or any(
                    name.startswith(prefix) and name.endswith(".service.d")
                    for prefix in HELPER_INSTANCE_PREFIXES
                )
                if not related:
                    continue
                if (
                    base_absolute == "/etc/systemd/system"
                    and name == legacy_directory_name
                ):
                    self._assert_legacy_gate_drop_in(child)
                    legacy_found = True
                    continue
                if (
                    installed
                    and base_absolute == "/etc/systemd/system"
                    and name == runtime_directory_name
                ):
                    self._assert_safe_directory(
                        child,
                        "controlled runtime target drop-in directory",
                        expected_mode=0o755,
                    )
                    entries = list(child.iterdir())
                    if expected_runtime_directory_entries is not None:
                        if {entry.name for entry in entries} != set(
                            expected_runtime_directory_entries
                        ):
                            raise MaintenanceInstallError(
                                "runtime target drop-in directory has uncontrolled entries"
                            )
                        continue
                    if len(entries) != 1 or entries[0].name != runtime_file_name:
                        raise MaintenanceInstallError(
                            "runtime target drop-in directory has uncontrolled entries"
                        )
                    self._assert_safe_regular(
                        entries[0], "controlled runtime target drop-in"
                    )
                    continue
                if (
                    (rollback_fenced or rollback_fence_may_be_partial)
                    and base_absolute == "/etc/systemd/system"
                    and name in rollback_directory_names
                ):
                    rollback_directory_details = self._assert_safe_directory(
                        child,
                        "controlled rollback unit-fence directory",
                    )
                    rollback_directory_mode = self._metadata_mode(
                        child, rollback_directory_details
                    )
                    if (
                        rollback_directory_mode != 0o755
                        and not (
                            rollback_fence_may_be_partial
                            and rollback_directory_mode & 0o700 == 0o700
                            and rollback_directory_mode & ~0o755 == 0
                        )
                    ):
                        raise MaintenanceInstallError(
                            "rollback unit-fence directory has unsafe mode"
                        )
                    entries = list(child.iterdir())
                    guard_entries = [
                        entry
                        for entry in entries
                        if entry.name == ROLLBACK_UNIT_FENCE_DROP_IN_NAME
                    ]
                    incoming_entries = [
                        entry
                        for entry in entries
                        if entry.name
                        == f".{ROLLBACK_UNIT_FENCE_DROP_IN_NAME}.incoming"
                    ]
                    if len(guard_entries) > 1 or set(entries) != set(
                        (*guard_entries, *incoming_entries)
                    ):
                        raise MaintenanceInstallError(
                            "rollback unit-fence directory has uncontrolled entries"
                        )
                    for incoming in incoming_entries:
                        details = self._assert_safe_regular(
                            incoming, "rollback unit-fence incoming file"
                        )
                        if (
                            not rollback_fence_may_be_partial
                            or self._metadata_mode(incoming, details)
                            not in {0o600, 0o644}
                            or details.st_size
                            > len(ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT)
                        ):
                            raise MaintenanceInstallError(
                                "rollback unit-fence incoming file is unsafe"
                            )
                        incoming_bytes = incoming.read_bytes()
                        if incoming_bytes != ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT[
                            : len(incoming_bytes)
                        ]:
                            raise MaintenanceInstallError(
                                "rollback unit-fence incoming file is unsafe"
                            )
                    if guard_entries:
                        guard = guard_entries[0]
                        details = self._assert_safe_regular(
                            guard, "controlled rollback unit fence"
                        )
                        if (
                            self._metadata_mode(guard, details) != 0o644
                            or details.st_size
                            != len(ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT)
                            or guard.read_bytes()
                            != ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT
                        ):
                            raise MaintenanceInstallError(
                                "rollback unit-fence bytes or metadata differ"
                            )
                        rollback_directories_found.add(name)
                    elif not rollback_fence_may_be_partial:
                        raise MaintenanceInstallError(
                            "rollback unit-fence directory has no guard"
                        )
                    continue
                raise MaintenanceInstallError(
                    f"uncontrolled systemd drop-in state exists: "
                    f"{base_absolute}/{name}"
                )
        if not legacy_found:
            raise MaintenanceInstallError("v13 legacy gate drop-in is absent")
        if rollback_fenced and rollback_directories_found != rollback_directory_names:
            raise MaintenanceInstallError(
                "one or more rollback unit-fence drop-ins are absent"
            )

    def _assert_targets_absent(self, manifest: MaintenanceManifest) -> None:
        for target in target_files(manifest):
            if _lexists(self._path(target.destination)):
                raise MaintenanceInstallError(
                    f"refusing to overwrite an unknown target: {target.destination}"
                )
        for absolute in (
            "/opt/ecobin/communication/current",
            "/opt/ecobin/updater/current",
        ):
            if _lexists(self._path(absolute)):
                raise MaintenanceInstallError(f"refusing to replace unknown link: {absolute}")
        for root in (
            "/opt/ecobin/communication",
            "/opt/ecobin/updater",
            "/usr/lib/ecobin/device-management",
            "/usr/share/ecobin/runtime-release-keys",
        ):
            if _lexists(self._path(root)):
                raise MaintenanceInstallError(f"refusing to take over unknown tree: {root}")
        for absolute in (*TMPFILES_DIRECTORY_MODES, *TMPFILES_REGULAR_PATHS):
            if _lexists(self._path(absolute)):
                raise MaintenanceInstallError(
                    f"clean legacy device has unexplained permanent-layer state: {absolute}"
                )

    def _assert_new_units_absent(self) -> None:
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            state = self._systemctl_show(unit)
            if (
                state["loadState"] != "not-found"
                or state["activeState"] != "inactive"
                or state["fragmentPath"]
                or self._reported_drop_in_paths(state)
            ):
                raise MaintenanceInstallError(
                    f"new permanent unit is already known to systemd: {unit}"
                )

    def preflight(
        self,
        payload_root: Path,
        expected_manifest_sha256: str,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
    ) -> dict[str, Any]:
        manifest = load_and_validate_payload(payload_root, expected_manifest_sha256)
        self._assert_host_tools()
        identity, legacy = self._assert_expected_device(
            manifest,
            expected_image_release_id=expected_image_release_id,
            expected_image_version=expected_image_version,
            expected_legacy_service=expected_legacy_service,
        )
        safe_gpio = self._assert_mcu_safety_gate()
        self._base_directory_checks()
        hardware_directories, current_link = self._hardware_runtime_directories()
        marker = self._existing_marker()
        if marker is not None:
            kind, document = marker
            if (
                document["payloadId"] == manifest.payload_id
                and document["manifestSha256"] == manifest.manifest_sha256
            ):
                loaded_kind, record, _loaded_marker = self._load_record_from_marker()
                if loaded_kind != kind:
                    raise MaintenanceInstallError(
                        "maintenance marker selection changed during preflight"
                    )
                if record.get("status") == "ROLLED_BACK":
                    return {
                        "status": "ROLLED_BACK_MARKER_CLEANUP_REQUIRED",
                        "payloadId": manifest.payload_id,
                        "manifestSha256": manifest.manifest_sha256,
                        "operationId": record["operationId"],
                        "image": identity,
                        "legacyService": legacy,
                        "mcuSafeGpioService": safe_gpio,
                        "hardwareCurrent": current_link,
                    }
                if record.get("status") != "INSTALLED":
                    activation = self._activation_journal(record, create=False)
                    forward_only = bool(
                        activation is not None
                        and activation.get("state") in {"STARTING", "VERIFIED"}
                        and not isinstance(
                            record.get("installationAuditPassedAt"), str
                        )
                    )
                    if (
                        kind != "pending"
                        or (
                            not forward_only
                            and record.get("status")
                            not in {
                                "INSTALLING",
                                "INSTALL_RESUME_REQUIRED",
                                "INSTALL_RESUME_BLOCKED",
                            }
                        )
                    ):
                        raise MaintenanceInstallError(
                            "maintenance installation is incomplete; rollback first"
                        )
                    self._verify_image_against_record(
                        record,
                        expected_image_release_id,
                        expected_image_version,
                        expected_legacy_service,
                    )
                    return {
                        "status": "INCOMPLETE_INSTALLATION_CAN_RESUME",
                        "payloadId": manifest.payload_id,
                        "manifestSha256": manifest.manifest_sha256,
                        "operationId": record["operationId"],
                        "image": identity,
                        "legacyService": legacy,
                        "mcuSafeGpioService": safe_gpio,
                        "hardwareCurrent": current_link,
                        "hardwareDirectoriesNeedingModeChange": sum(
                            stat.S_IMODE(path.lstat().st_mode) != 0o755
                            for path in hardware_directories
                        ),
                    }
                self._assert_dropin_filesystem(installed=True)
                runtime_target = self._systemctl_show(RUNTIME_TARGET)
                if self._reported_drop_in_paths(runtime_target) != (
                    RUNTIME_DROP_IN_PATH,
                ):
                    raise MaintenanceInstallError(
                        "installed runtime target drop-in state differs"
                    )
                self.audit(
                    expected_image_release_id=expected_image_release_id,
                    expected_image_version=expected_image_version,
                    expected_legacy_service=expected_legacy_service,
                    _finalize_markers=False,
                )
                return {
                    "status": (
                        "INSTALLED_PENDING_FINALIZATION"
                        if kind == "pending"
                        else "ALREADY_INSTALLED"
                    ),
                    "payloadId": manifest.payload_id,
                    "image": identity,
                    "legacyService": legacy,
                    "mcuSafeGpioService": safe_gpio,
                    "hardwareCurrent": current_link,
                    "hardwareDirectoriesNeedingModeChange": sum(
                        stat.S_IMODE(path.lstat().st_mode) != 0o755
                        for path in hardware_directories
                    ),
                }
            raise MaintenanceInstallError(
                "another maintenance installation is active or incomplete; rollback first"
            )
        orphan = self._discover_unlocated_operation()
        if orphan is not None:
            orphan_record = orphan.get("record")
            return {
                "status": "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED",
                "payloadId": manifest.payload_id,
                "manifestSha256": manifest.manifest_sha256,
                "operationId": orphan["operationId"],
                "orphanKind": orphan["kind"],
                "samePayload": bool(
                    isinstance(orphan_record, Mapping)
                    and orphan_record.get("payloadId") == manifest.payload_id
                    and orphan_record.get("manifestSha256")
                    == manifest.manifest_sha256
                ),
                "image": identity,
                "legacyService": legacy,
                "mcuSafeGpioService": safe_gpio,
                "hardwareCurrent": current_link,
                "hardwareDirectoriesNeedingModeChange": sum(
                    stat.S_IMODE(path.lstat().st_mode) != 0o755
                    for path in hardware_directories
                ),
            }
        if _lexists(self._path(RUNTIME_START_FENCE)):
            raise MaintenanceInstallError(
                "an unexplained runtime start fence is present"
            )
        self._assert_dropin_filesystem(installed=False)
        runtime_target = self._systemctl_show(RUNTIME_TARGET)
        if (
            runtime_target["loadState"] != "loaded"
            or self._reported_drop_in_paths(runtime_target)
        ):
            raise MaintenanceInstallError(
                "runtime target is unavailable or has uncontrolled drop-ins"
            )
        self._assert_targets_absent(manifest)
        self._assert_new_units_absent()
        for group_name in REQUIRED_EXISTING_GROUPS:
            if not self._getent_exists("group", group_name):
                raise MaintenanceInstallError(f"required hardware group is absent: {group_name}")
        retained_identities = self._identity_installation_state(
            require_present=False
        )
        code_directories = self._planned_code_directories(manifest)
        return {
            "status": "READY",
            "payloadId": manifest.payload_id,
            "manifestSha256": manifest.manifest_sha256,
            "image": identity,
            "legacyService": legacy,
            "mcuSafeGpioService": safe_gpio,
            "hardwareCurrent": current_link,
            "rootCodeDirectories": sorted(
                {
                    *code_directories,
                    *(
                        "/" + path.relative_to(self.rootfs).as_posix()
                        for path in hardware_directories
                    ),
                }
            ),
            "retainedSafeIdentities": retained_identities is not None,
            "targetFileCount": len(target_files(manifest)),
            "hardwareDirectoryCount": len(hardware_directories),
            "hardwareDirectoriesNeedingModeChange": sum(
                stat.S_IMODE(path.lstat().st_mode) != 0o755
                for path in hardware_directories
            ),
        }

    def _snapshot(self, absolute: str) -> dict[str, Any]:
        path = self._path(absolute)
        if not _lexists(path):
            return {"exists": False}
        details = path.lstat()
        if stat.S_ISDIR(details.st_mode):
            kind = "directory"
        elif stat.S_ISREG(details.st_mode):
            kind = "regular"
        elif stat.S_ISLNK(details.st_mode):
            kind = "symlink"
        else:
            kind = "special"
        uid, gid = self._metadata_ids(path, details)
        result: dict[str, Any] = {
            "exists": True,
            "kind": kind,
            "uid": uid,
            "gid": gid,
            "mode": self._metadata_mode(path, details),
            "device": details.st_dev,
            "inode": details.st_ino,
        }
        if kind == "regular":
            result.update(size=details.st_size, sha256=_sha256_file(path))
        elif kind == "symlink":
            result["target"] = os.readlink(path)
        return result

    def _ensure_maintenance_storage(self, operation_id: str) -> Path:
        paths = (
            ("/var/lib/ecobin", 0o755),
            ("/var/lib/ecobin/device-management-maintenance", 0o700),
            (BACKUP_PARENT, 0o700),
        )
        for absolute, mode in paths:
            path = self._path(absolute)
            if _lexists(path):
                details = self._assert_safe_directory(
                    path, f"maintenance storage {absolute}"
                )
                if (
                    absolute != "/var/lib/ecobin"
                    and self._metadata_mode(path, details) != mode
                ):
                    raise MaintenanceInstallError(
                        f"maintenance storage has unsafe mode: {absolute}"
                    )
            else:
                path.mkdir(mode=mode)
                os.chmod(path, mode)
                if self.enforce_root_ownership:
                    os.chown(path, 0, 0)
                self._fsync_directory(path)
                self._fsync_directory(path.parent)
        operation = self._path(f"{BACKUP_PARENT}/{operation_id}")
        if _lexists(operation):
            raise MaintenanceInstallError("maintenance operation backup already exists")
        operation.mkdir(mode=0o700)
        os.chmod(operation, 0o700)
        if self.enforce_root_ownership:
            os.chown(operation, 0, 0)
        self._fsync_directory(operation)
        self._fsync_directory(operation.parent)
        return operation

    def _write_atomic_json(self, path: Path, document: Any, mode: int = 0o600) -> None:
        self._write_atomic_bytes(path, _canonical_json(document), mode=mode)

    def _write_atomic_bytes(self, path: Path, raw: bytes, *, mode: int) -> None:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise MaintenanceInstallError(
                f"atomic state file is unexpectedly large: {path}"
            )
        self._assert_safe_directory(
            path.parent, f"atomic target directory for {path.name}"
        )
        # One fixed name per target avoids PID-reuse ambiguity after reboot.
        # The live CLI lock serializes writers; an existing file can only be a
        # prior interrupted write inside this root-controlled directory.
        temporary = path.with_name(f".{path.name}.incoming")
        if _lexists(temporary):
            details = self._assert_safe_regular(
                temporary, f"atomic incoming file for {path.name}"
            )
            uid, gid = self._metadata_ids(temporary, details)
            if details.st_size > max(len(raw), MAX_MANIFEST_BYTES):
                raise MaintenanceInstallError(
                    f"stale atomic incoming file is unsafe: {temporary}"
                )
            existing = temporary.read_bytes()
            if (
                (os.name == "posix" and details.st_nlink != 1)
                or uid != 0
                or gid != 0
                or (
                    self._metadata_mode(temporary, details)
                    not in ({0o600, mode} if mode != 0o600 else {0o600})
                )
                or (
                    not _lexists(path)
                    and existing != raw[: len(existing)]
                )
            ):
                raise MaintenanceInstallError(
                    f"stale atomic incoming file is unsafe: {temporary}"
                )
            temporary.unlink()
            self._fsync_directory(path.parent)
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            if os.name == "posix":
                os.fchmod(descriptor, mode)
                if self.enforce_root_ownership:
                    os.fchown(descriptor, 0, 0)
            else:
                os.chmod(temporary, mode)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if _lexists(path):
                details = path.lstat()
                if not stat.S_ISREG(details.st_mode):
                    raise MaintenanceInstallError(f"refusing to replace unsafe JSON file: {path}")
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        except BaseException:
            if _lexists(temporary):
                temporary.unlink()
            raise

    def _reconcile_atomic_incoming(
        self,
        path: Path,
        expected_raw: bytes,
        *,
        mode: int,
        allow_same_operation_record: str | None = None,
        remove: bool = True,
    ) -> None:
        temporary = path.with_name(f".{path.name}.incoming")
        if not _lexists(temporary):
            return
        self._assert_safe_directory(
            path.parent, f"atomic target directory for {path.name}"
        )
        details = self._assert_safe_regular(
            temporary, f"atomic incoming file for {path.name}"
        )
        uid, gid = self._metadata_ids(temporary, details)
        if details.st_size > max(len(expected_raw), MAX_MANIFEST_BYTES):
            raise MaintenanceInstallError(
                "atomic incoming file cannot be attributed to this operation: "
                f"{temporary}"
            )
        raw = temporary.read_bytes()
        content_owned = raw == expected_raw[: len(raw)]
        if not content_owned and allow_same_operation_record is not None:
            if _lexists(path):
                # record.json is the sole committed authority.  Its fixed temp
                # lives in a per-operation, root-only directory; after loading
                # a valid final record for that same operation, arbitrary
                # partial bytes in the uncommitted temp are safely discardable.
                if path.parent.name != allow_same_operation_record:
                    raise MaintenanceInstallError(
                        "backup record directory does not match operation"
                    )
                parent_details = self._assert_safe_directory(
                    path.parent, "maintenance operation backup directory"
                )
                if (
                    os.name == "posix"
                    and self._metadata_mode(path.parent, parent_details) != 0o700
                ):
                    raise MaintenanceInstallError(
                        "maintenance operation backup directory mode is unsafe"
                    )
                content_owned = True
            else:
                candidate = None
                try:
                    candidate = _load_json_bytes(
                        raw, description="atomic incoming backup record"
                    )
                except MaintenanceInstallError:
                    pass
                content_owned = bool(
                    isinstance(candidate, dict)
                    and candidate.get("schemaVersion") == BACKUP_SCHEMA_VERSION
                    and candidate.get("operationId") == allow_same_operation_record
                )
        if (
            uid != 0
            or gid != 0
            or (os.name == "posix" and details.st_nlink != 1)
            or (
                self._metadata_mode(temporary, details)
                not in ({0o600, mode} if mode != 0o600 else {0o600})
            )
            or not content_owned
        ):
            raise MaintenanceInstallError(
                f"atomic incoming file cannot be attributed to this operation: {temporary}"
            )
        if remove:
            temporary.unlink()
            self._fsync_directory(path.parent)

    def _reconcile_operation_singleton_incomings(
        self,
        record: Mapping[str, Any],
        *,
        include_guards: bool = True,
        remove: bool = True,
    ) -> None:
        operation_id = str(record["operationId"])
        self._reconcile_atomic_incoming(
            self._record_path(operation_id),
            _canonical_json(record),
            mode=0o600,
            allow_same_operation_record=operation_id,
            remove=remove,
        )
        marker_raw = _canonical_json(self._marker_document(record))
        for absolute in (ACTIVE_MARKER, PENDING_MARKER, RUNTIME_START_FENCE):
            self._reconcile_atomic_incoming(
                self._path(absolute), marker_raw, mode=0o600, remove=remove
            )
        if include_guards:
            for absolute in self._rollback_unit_fence_paths():
                path = self._path(absolute)
                if _lexists(path.parent):
                    self._reconcile_atomic_incoming(
                        path,
                        ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT,
                        mode=0o644,
                        remove=remove,
                    )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _incoming_absolute(destination: str, operation_id: str) -> str:
        target = PurePosixPath(destination)
        return str(target.parent / f".{target.name}.incoming-{operation_id}")

    def _copy_verified(
        self,
        source: Path,
        locked: ManifestFile,
        destination: Path,
        temporary: Path,
        intent: Mapping[str, Any],
        mode: int,
    ) -> None:
        if _lexists(destination):
            raise MaintenanceInstallError(f"refusing to overwrite target: {destination}")
        if _lexists(temporary):
            raise MaintenanceInstallError(f"stale incoming path exists: {temporary}")
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
        target_fd: int | None = None
        preserve_temporary = False
        try:
            source_details = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_details.st_mode)
                or (os.name == "posix" and source_details.st_nlink != 1)
                or source_details.st_size != locked.size
            ):
                raise MaintenanceInstallError("payload source changed during installation")
            target_fd = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
            if os.name == "posix":
                os.fchmod(target_fd, mode)
                if self.enforce_root_ownership:
                    os.fchown(target_fd, 0, 0)
            else:
                os.chmod(temporary, mode)
            digest = hashlib.sha256()
            copied = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                copied += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    view = view[written:]
            if copied != locked.size or digest.hexdigest() != locked.sha256:
                raise MaintenanceInstallError("payload source hash changed during installation")
            os.fsync(target_fd)
            os.close(target_fd)
            target_fd = None
            if self.before_publish_hook is not None:
                self.before_publish_hook(intent)
            if _lexists(destination):
                raise MaintenanceInstallError("target appeared during installation")
            os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
        except MaintenanceProcessInterrupted:
            preserve_temporary = True
            raise
        finally:
            os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)
            if _lexists(temporary) and not preserve_temporary:
                temporary.unlink()

    def _planned_code_directories(
        self, manifest: MaintenanceManifest
    ) -> dict[str, int]:
        managed_roots = {
            "/opt/ecobin": 0o755,
            "/opt/ecobin/communication": 0o755,
            "/opt/ecobin/communication/releases": 0o755,
            f"/opt/ecobin/communication/releases/{manifest.communication_release_id}": 0o755,
            f"/opt/ecobin/communication/releases/{manifest.communication_release_id}/app": 0o755,
            "/opt/ecobin/updater": 0o755,
            "/opt/ecobin/updater/releases": 0o755,
            f"/opt/ecobin/updater/releases/{manifest.updater_release_id}": 0o755,
            f"/opt/ecobin/updater/releases/{manifest.updater_release_id}/app": 0o755,
            f"/opt/ecobin/updater/releases/{manifest.updater_release_id}/helpers": 0o755,
            "/usr/lib/ecobin": 0o755,
            "/usr/lib/ecobin/device-management": 0o755,
            "/usr/lib/ecobin/device-management/helpers": 0o755,
            "/usr/share/ecobin": 0o755,
            "/usr/share/ecobin/runtime-release-keys": 0o755,
            "/etc/systemd/system/ecobin-runtime.target.d": 0o755,
        }
        return managed_roots

    def _ensure_directory(self, absolute: str, mode: int) -> None:
        path = self._path(absolute)
        if _lexists(path):
            details = self._assert_safe_directory(path, f"managed directory {absolute}")
            if stat.S_IMODE(details.st_mode) != mode:
                os.chmod(path, mode)
                self._fsync_directory(path)
            return
        path.mkdir(mode=mode)
        os.chmod(path, mode)
        if self.enforce_root_ownership:
            os.chown(path, 0, 0)
        self._fsync_directory(path)
        self._fsync_directory(path.parent)

    def _record_path(self, operation_id: str) -> Path:
        _safe_id(operation_id, description="operation ID")
        return self._path(f"{BACKUP_PARENT}/{operation_id}/record.json")

    def _persist_record(self, record: dict[str, Any]) -> None:
        self._write_atomic_json(self._record_path(record["operationId"]), record)

    def _marker_document(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": MARKER_SCHEMA_VERSION,
            "operationId": record["operationId"],
            "payloadId": record["payloadId"],
            "manifestSha256": record["manifestSha256"],
        }

    def _write_marker(self, absolute: str, record: Mapping[str, Any]) -> None:
        self._write_atomic_json(self._path(absolute), self._marker_document(record))

    def _runtime_fence_journal(
        self,
        record: dict[str, Any],
        *,
        create: bool,
    ) -> dict[str, Any] | None:
        entry = record.get("runtimeStartFence")
        if entry is None and create:
            entry = {
                "schemaVersion": 1,
                "operationId": record["operationId"],
                "path": RUNTIME_START_FENCE,
                "state": "PREPARED",
                "preparedAt": _utc_now(),
            }
            record["runtimeStartFence"] = entry
            # The intent is durable before the fence can become visible.  A
            # recovery process can therefore distinguish its own fence from
            # an unexplained file at the same privileged path.
            self._persist_record(record)
        if entry is None:
            return None
        if (
            not isinstance(entry, dict)
            or entry.get("schemaVersion") != 1
            or entry.get("operationId") != record.get("operationId")
            or entry.get("path") != RUNTIME_START_FENCE
            or entry.get("state")
            not in {"PREPARED", "ACTIVE", "RELEASING", "RELEASED"}
            or not isinstance(entry.get("preparedAt"), str)
        ):
            raise MaintenanceInstallError("runtime start fence journal is malformed")
        return entry

    @staticmethod
    def _rollback_unit_fence_paths() -> tuple[str, ...]:
        return tuple(
            f"/etc/systemd/system/{unit}.d/{ROLLBACK_UNIT_FENCE_DROP_IN_NAME}"
            for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES)
        )

    def _rollback_unit_fence_journal(
        self, record: dict[str, Any], *, create: bool
    ) -> dict[str, Any] | None:
        journal = record.get("rollbackUnitFences")
        if journal is None and create:
            journal = {
                "schemaVersion": 1,
                "state": "PREPARED",
                "paths": list(self._rollback_unit_fence_paths()),
                "preparedAt": _utc_now(),
            }
            record["rollbackUnitFences"] = journal
            self._persist_record(record)
        if journal is None:
            return None
        if (
            not isinstance(journal, dict)
            or journal.get("schemaVersion") != 1
            or journal.get("state")
            not in {"PREPARED", "ACTIVE", "REMOVING", "REMOVED"}
            or journal.get("paths") != list(self._rollback_unit_fence_paths())
            or not isinstance(journal.get("preparedAt"), str)
        ):
            raise MaintenanceInstallError(
                "rollback unit-fence journal is malformed"
            )
        return journal

    def _rollback_unit_fence_state(self, record: Mapping[str, Any]) -> str | None:
        journal = record.get("rollbackUnitFences")
        if journal is None:
            return None
        if not isinstance(record, dict):
            raise MaintenanceInstallError("rollback record is not mutable")
        validated = self._rollback_unit_fence_journal(record, create=False)
        assert validated is not None
        return str(validated["state"])

    def _establish_rollback_unit_fences(self, record: dict[str, Any]) -> None:
        journal = self._rollback_unit_fence_journal(record, create=True)
        assert journal is not None
        if journal["state"] in {"REMOVING", "REMOVED"}:
            journal["state"] = "PREPARED"
            journal["recoveryPreparedAt"] = _utc_now()
            self._persist_record(record)
        for absolute in self._rollback_unit_fence_paths():
            path = self._path(absolute)
            directory = path.parent
            if _lexists(directory):
                details = self._assert_safe_directory(
                    directory,
                    f"rollback unit-fence directory {directory}",
                )
                mode = self._metadata_mode(directory, details)
                if mode != 0o755:
                    if mode & 0o700 != 0o700 or mode & ~0o755:
                        raise MaintenanceInstallError(
                            f"rollback unit-fence directory mode is unsafe: {directory}"
                        )
                    os.chmod(directory, 0o755)
                    self._fsync_directory(directory)
            else:
                directory.mkdir(mode=0o755)
                os.chmod(directory, 0o755)
                if self.enforce_root_ownership:
                    os.chown(directory, 0, 0)
                self._fsync_directory(directory)
                self._fsync_directory(directory.parent)
            for entry in tuple(directory.iterdir()):
                incoming_name = (
                    f".{ROLLBACK_UNIT_FENCE_DROP_IN_NAME}.incoming"
                )
                if entry.name != incoming_name:
                    continue
                details = self._assert_safe_regular(
                    entry, "rollback unit-fence incoming file"
                )
                if (
                    self._metadata_mode(entry, details) not in {0o600, 0o644}
                    or details.st_size > len(ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT)
                ):
                    raise MaintenanceInstallError(
                        "rollback unit-fence incoming file is unsafe"
                    )
                incoming_bytes = entry.read_bytes()
                if incoming_bytes != ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT[
                    : len(incoming_bytes)
                ]:
                    raise MaintenanceInstallError(
                        "rollback unit-fence incoming file is unsafe"
                    )
                entry.unlink()
                self._fsync_directory(directory)
            if _lexists(path):
                details = self._assert_safe_regular(
                    path, f"rollback unit fence {absolute}"
                )
                if (
                    self._metadata_mode(path, details) != 0o644
                    or details.st_size
                    != len(ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT)
                    or path.read_bytes() != ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT
                ):
                    raise MaintenanceInstallError(
                        f"rollback unit fence changed: {absolute}"
                    )
            else:
                self._write_atomic_bytes(
                    path,
                    ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT,
                    mode=0o644,
                )
        journal["state"] = "ACTIVE"
        journal["activatedAt"] = _utc_now()
        self._persist_record(record)

    def _remove_rollback_unit_fences(
        self, record: dict[str, Any], *, installed: bool
    ) -> None:
        journal = self._rollback_unit_fence_journal(record, create=False)
        if journal is None or journal["state"] == "REMOVED":
            return
        self._assert_dropin_filesystem(
            installed=installed, rollback_fenced=True
        )
        journal["state"] = "REMOVING"
        journal["removalStartedAt"] = _utc_now()
        self._persist_record(record)
        for absolute in self._rollback_unit_fence_paths():
            path = self._path(absolute)
            if _lexists(path):
                details = self._assert_safe_regular(
                    path, f"rollback unit fence {absolute}"
                )
                if (
                    self._metadata_mode(path, details) != 0o644
                    or path.read_bytes() != ROLLBACK_UNIT_FENCE_DROP_IN_CONTENT
                ):
                    raise MaintenanceInstallError(
                        f"rollback unit fence changed: {absolute}"
                    )
                path.unlink()
                self._fsync_directory(path.parent)
            try:
                path.parent.rmdir()
                self._fsync_directory(path.parent.parent)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise MaintenanceInstallError(
                    f"cannot remove rollback unit-fence directory: {path.parent}"
                ) from exc
        journal["state"] = "REMOVED"
        journal["removedAt"] = _utc_now()
        self._persist_record(record)

    def _assert_runtime_start_fence(self, record: Mapping[str, Any]) -> None:
        path = self._path(RUNTIME_START_FENCE)
        details = self._assert_safe_regular(path, "runtime start fence")
        uid, gid = self._metadata_ids(path, details)
        if (
            os.name == "posix"
            and (
                details.st_nlink != 1
                or self._metadata_mode(path, details) != 0o600
                or uid != 0
                or gid != 0
            )
            or path.read_bytes() != _canonical_json(self._marker_document(record))
        ):
            raise MaintenanceInstallError("runtime start fence differs")

    def _establish_runtime_start_fence(self, record: dict[str, Any]) -> None:
        entry = self._runtime_fence_journal(record, create=True)
        assert entry is not None
        path = self._path(RUNTIME_START_FENCE)
        if _lexists(path):
            self._assert_runtime_start_fence(record)
        else:
            if entry["state"] == "ACTIVE":
                # ACTIVE is persisted only after the file publication and its
                # parent fsync.  Absence is therefore external removal, not a
                # recoverable write tear; units may already have started.
                raise MaintenanceInstallError(
                    "active runtime start fence disappeared"
                )
            self._write_atomic_json(path, self._marker_document(record), mode=0o600)
            self._assert_runtime_start_fence(record)
        if entry["state"] != "ACTIVE":
            entry["state"] = "ACTIVE"
            entry["activatedAt"] = _utc_now()
            entry.pop("releasedAt", None)
            self._persist_record(record)

    def _release_runtime_start_fence(self, record: dict[str, Any]) -> None:
        entry = self._runtime_fence_journal(record, create=False)
        if entry is None:
            raise MaintenanceInstallError("runtime start fence journal is absent")
        path = self._path(RUNTIME_START_FENCE)
        if entry["state"] == "RELEASED":
            if _lexists(path):
                raise MaintenanceInstallError(
                    "released runtime start fence unexpectedly exists"
                )
            return
        if entry["state"] not in {"ACTIVE", "RELEASING"}:
            raise MaintenanceInstallError(
                "runtime start fence is not ready for release"
            )
        if entry["state"] == "ACTIVE":
            self._assert_runtime_start_fence(record)
            entry["state"] = "RELEASING"
            entry["releasePreparedAt"] = _utc_now()
            self._persist_record(record)
        if _lexists(path):
            self._assert_runtime_start_fence(record)
            path.unlink()
            self._fsync_directory(path.parent)
        entry["state"] = "RELEASED"
        entry["releasedAt"] = _utc_now()
        self._persist_record(record)

    def _setup_command_intents(
        self, record: dict[str, Any], *, create: bool
    ) -> list[dict[str, Any]]:
        intents = record.get("setupCommandIntents")
        if intents is None and create:
            intents = []
            record["setupCommandIntents"] = intents
            self._persist_record(record)
        if intents is None:
            return []
        if not isinstance(intents, list):
            raise MaintenanceInstallError("setup command intent journal is malformed")
        names: list[str] = []
        for intent in intents:
            if (
                not isinstance(intent, dict)
                or intent.get("name") not in SETUP_COMMAND_ARGUMENTS
                or intent.get("arguments")
                != list(SETUP_COMMAND_ARGUMENTS[str(intent.get("name"))])
                or intent.get("state") not in {"PREPARED", "DONE"}
                or not isinstance(intent.get("preparedAt"), str)
                or (
                    intent.get("state") == "DONE"
                    and not isinstance(intent.get("completedAt"), str)
                )
            ):
                raise MaintenanceInstallError(
                    "setup command intent journal is malformed"
                )
            names.append(str(intent["name"]))
        if (
            len(names) != len(set(names))
            or names != list(SETUP_COMMAND_ORDER[: len(names)])
        ):
            raise MaintenanceInstallError("setup command intent order is malformed")
        return intents

    def _verify_setup_command_effect(
        self, name: str, record: Mapping[str, Any]
    ) -> None:
        identities = self._identity_installation_state(require_present=True)
        assert identities is not None
        if name == "systemd-tmpfiles":
            self._audit_runtime_permissions(identities, record)

    def _converge_setup_command(
        self,
        record: dict[str, Any],
        name: str,
        *,
        create: bool,
    ) -> None:
        if name not in SETUP_COMMAND_ARGUMENTS:
            raise ValueError("unknown setup command")
        intents = self._setup_command_intents(record, create=create)
        existing = next((item for item in intents if item["name"] == name), None)
        if existing is None:
            if not create:
                return
            expected_index = SETUP_COMMAND_ORDER.index(name)
            if len(intents) != expected_index:
                raise MaintenanceInstallError(
                    "setup commands cannot be prepared out of order"
                )
            existing = {
                "name": name,
                "arguments": list(SETUP_COMMAND_ARGUMENTS[name]),
                "state": "PREPARED",
                "preparedAt": _utc_now(),
            }
            intents.append(existing)
            # The exact command intent is durable before a utility can create
            # even its first account, group, directory or lock file.
            self._persist_record(record)
        if existing["state"] == "PREPARED":
            if name == "systemd-sysusers":
                self._assert_partial_identity_state_safe()
            else:
                identities = self._identity_installation_state(
                    require_present=True
                )
                assert identities is not None
                self._assert_partial_tmpfiles_state_safe(identities)
            self._run(SETUP_COMMAND_ARGUMENTS[name])
            # Both host utilities are declarative and idempotent.  A fresh
            # process may therefore rerun a PREPARED command after power loss,
            # but completion is recorded only after the whole declared state
            # has been checked.
            self._verify_setup_command_effect(name, record)
            existing["state"] = "DONE"
            existing["completedAt"] = _utc_now()
            commands_completed = record.get("commandsCompleted")
            if not isinstance(commands_completed, list) or any(
                not isinstance(command, str) for command in commands_completed
            ):
                raise MaintenanceInstallError(
                    "maintenance command journal is malformed"
                )
            if name not in commands_completed:
                commands_completed.append(name)
            self._persist_record(record)
        else:
            # A completed command is evidence, not permission to repair later
            # drift.  Any mismatch here is treated as external modification.
            self._verify_setup_command_effect(name, record)

    def _reconcile_setup_commands_for_recovery(
        self, record: dict[str, Any]
    ) -> None:
        intents = self._setup_command_intents(record, create=False)
        for name in SETUP_COMMAND_ORDER:
            if any(intent["name"] == name for intent in intents):
                self._converge_setup_command(record, name, create=False)

    def _assert_setup_configuration_artifacts(
        self, record: Mapping[str, Any]
    ) -> None:
        setup_intents = record.get("setupCommandIntents")
        if not setup_intents:
            return
        artifact_intents = record.get("artifactIntents")
        if not isinstance(artifact_intents, list):
            raise MaintenanceInstallError("artifact publication journal is absent")
        required_destinations = {
            "/usr/lib/sysusers.d/ecobin-device-runtime.conf"
        }
        if any(
            isinstance(item, Mapping) and item.get("name") == "systemd-tmpfiles"
            for item in setup_intents
        ):
            required_destinations.add(
                "/usr/lib/tmpfiles.d/ecobin-device-runtime.conf"
            )
        found: set[str] = set()
        for item in artifact_intents:
            if not isinstance(item, Mapping) or item.get("path") not in required_destinations:
                continue
            absolute = str(item["path"])
            if absolute in found:
                raise MaintenanceInstallError(
                    "setup configuration artifact is duplicated"
                )
            self._assert_artifact_intent(
                item,
                require_done=False,
                operation_id=str(record["operationId"]),
            )
            temporary = item.get("temporaryPath")
            if not isinstance(temporary, str) or _lexists(self._path(temporary)):
                raise MaintenanceInstallError(
                    "setup configuration publication is unfinished"
                )
            found.add(absolute)
        if found != required_destinations:
            raise MaintenanceInstallError(
                "authenticated setup configuration artifact is absent"
            )

    def _activation_journal(
        self, record: dict[str, Any], *, create: bool
    ) -> dict[str, Any] | None:
        activation = record.get("activation")
        if activation is None and create:
            activation = {
                "schemaVersion": 1,
                "state": "NOT_STARTED",
                "intendedUnits": list(START_UNITS),
                "preparedAt": _utc_now(),
            }
            record["activation"] = activation
            self._persist_record(record)
        if activation is None:
            return None
        if (
            not isinstance(activation, dict)
            or activation.get("schemaVersion") != 1
            or activation.get("state")
            not in {"NOT_STARTED", "PREPARED", "STARTING", "VERIFIED"}
            or activation.get("intendedUnits") != list(START_UNITS)
            or not isinstance(activation.get("preparedAt"), str)
        ):
            raise MaintenanceInstallError("activation journal is malformed")
        return activation

    def _prepare_activation(self, record: dict[str, Any]) -> dict[str, Any]:
        activation = self._activation_journal(record, create=True)
        assert activation is not None
        if activation["state"] == "NOT_STARTED":
            activation["state"] = "PREPARED"
            activation["activationPreparedAt"] = _utc_now()
            record["unitStartIntents"] = [
                {"unit": unit, "recordedAt": _utc_now()}
                for unit in START_UNITS
            ]
            self._persist_record(record)
        return activation

    @staticmethod
    def _activation_forbids_destructive_rollback(
        record: Mapping[str, Any],
        activation: Mapping[str, Any] | None,
    ) -> bool:
        if activation is None or activation.get("state") not in {
            "STARTING",
            "VERIFIED",
        }:
            return False
        # Once an installation has passed the complete durable audit, the
        # explicit rollback transaction owns recovery.  Its finite status
        # allowlist includes interrupted quiesce, refusal restoration and
        # artifact removal.  Unknown states still fail closed and an
        # unaudited installation must always converge forward.
        return (
            not isinstance(record.get("installationAuditPassedAt"), str)
            or record.get("status") not in AUDITED_ROLLBACK_RECOVERY_STATUSES
        )

    def _start_and_verify_all_units(self, record: dict[str, Any]) -> None:
        activation = self._prepare_activation(record)
        if activation["state"] == "PREPARED":
            # STARTING is the durable may-have-run boundary.  From this point
            # onward recovery is forward-only, even if updater.db is absent.
            activation["state"] = "STARTING"
            activation["startingAt"] = _utc_now()
            self._persist_record(record)
        if activation["state"] not in {"STARTING", "VERIFIED"}:
            raise MaintenanceInstallError("activation cannot be resumed")
        try:
            self._release_runtime_start_fence(record)
            commands_completed = record.get("commandsCompleted")
            if not isinstance(commands_completed, list) or any(
                not isinstance(command, str) for command in commands_completed
            ):
                raise MaintenanceInstallError(
                    "maintenance command journal is malformed"
                )
            for unit in START_UNITS:
                # Always issue the idempotent start on recovery.  A prior
                # process may have reached systemd but died before recording
                # its result.
                self._run(("systemctl", "start", unit))
                command = f"systemctl start {unit}"
                if command not in commands_completed:
                    commands_completed.append(command)
                self._persist_record(record)
                state = self._systemctl_show(unit)
                if state["activeState"] != "active":
                    raise MaintenanceInstallError(
                        f"installed unit did not become active: {unit}"
                    )
            activation["state"] = "VERIFIED"
            activation["verifiedAt"] = _utc_now()
            self._persist_record(record)
        except MaintenanceProcessInterrupted:
            raise
        except BaseException:
            # A normal command/I/O failure is recoverable without waiting for
            # reboot.  Re-close admission for units that have not yet started;
            # already-running services are deliberately not interrupted.
            try:
                self._establish_runtime_start_fence(record)
                self._run(("systemctl", "daemon-reload"))
                record["status"] = "INSTALL_RESUME_REQUIRED"
                record["installResumeRequiredAt"] = _utc_now()
                self._persist_record(record)
            except BaseException:
                # Preserve the original failure; the durable STARTING fact is
                # still sufficient to forbid destructive rollback.
                pass
            raise

    def _resume_incomplete_installation(
        self,
        manifest: MaintenanceManifest,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
    ) -> dict[str, Any]:
        kind, record, _marker = self._load_record_from_marker()
        existing_activation = self._activation_journal(record, create=False)
        forward_only = bool(
            existing_activation is not None
            and existing_activation.get("state") in {"STARTING", "VERIFIED"}
            and not isinstance(record.get("installationAuditPassedAt"), str)
        )
        if (
            kind != "pending"
            or (
                not forward_only
                and record.get("status")
                not in {
                    "INSTALLING",
                    "INSTALL_RESUME_REQUIRED",
                    "INSTALL_RESUME_BLOCKED",
                }
            )
            or record.get("payloadId") != manifest.payload_id
            or record.get("manifestSha256") != manifest.manifest_sha256
        ):
            raise MaintenanceInstallError(
                "incomplete installation does not match this authenticated payload"
            )
        self._assert_installed_marker_set(record)
        self._verify_image_against_record(
            record,
            expected_image_release_id,
            expected_image_version,
            expected_legacy_service,
        )
        self._assert_mcu_safety_gate()
        self._assert_hardware_current_unchanged(record)
        self._assert_protected_paths_unchanged(record)

        expected_regular = {target.destination: target for target in target_files(manifest)}
        expected_links = {
            "/opt/ecobin/communication/current": (
                f"releases/{manifest.communication_release_id}"
            ),
            "/opt/ecobin/updater/current": f"releases/{manifest.updater_release_id}",
        }
        intents = record.get("artifactIntents")
        if not isinstance(intents, list) or len(intents) != (
            len(expected_regular) + len(expected_links)
        ):
            raise MaintenanceInstallError(
                "installation stopped before every authenticated artifact was published; "
                "rollback is required"
            )
        seen: set[str] = set()
        for item in intents:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise MaintenanceInstallError(
                    "artifact publication journal is malformed"
                )
            absolute = str(item["path"])
            if absolute in seen:
                raise MaintenanceInstallError(
                    "artifact publication journal contains duplicate paths"
                )
            seen.add(absolute)
            if absolute in expected_regular:
                target = expected_regular[absolute]
                locked = manifest.files[target.source_relative]
                if (
                    item.get("kind") != "regular"
                    or item.get("source") != target.source_relative
                    or item.get("sha256") != locked.sha256
                    or item.get("size") != locked.size
                    or item.get("mode") != target.mode
                    or item.get("temporaryPath")
                    != self._incoming_absolute(absolute, str(record["operationId"]))
                ):
                    raise MaintenanceInstallError(
                        f"artifact intent differs from authenticated payload: {absolute}"
                    )
            elif absolute in expected_links:
                if (
                    item.get("kind") != "symlink"
                    or item.get("target") != expected_links[absolute]
                ):
                    raise MaintenanceInstallError(
                        f"link intent differs from authenticated payload: {absolute}"
                    )
            else:
                raise MaintenanceInstallError(
                    f"artifact intent is outside the authenticated payload: {absolute}"
                )
            self._assert_artifact_intent(
                item,
                require_done=False,
                operation_id=str(record["operationId"]),
            )
            if item.get("kind") == "regular" and _lexists(
                self._path(str(item["temporaryPath"]))
            ):
                raise MaintenanceInstallError(
                    "installation has an unfinished artifact copy; rollback is required"
                )
            if item.get("state") == "PREPARED":
                published = self._path(absolute).lstat()
                item.update(
                    state="DONE",
                    device=published.st_dev,
                    inode=published.st_ino,
                    publishedAt=_utc_now(),
                )
                self._persist_record(record)
        if seen != {*expected_regular, *expected_links}:
            raise MaintenanceInstallError(
                "authenticated artifact publication is incomplete"
            )

        # From here onward the same payload is complete enough to converge.
        # The global fence is repaired before declarative effects or systemd
        # state are replayed.  Already-running units are never stopped.
        self._establish_runtime_start_fence(record)
        record["status"] = "INSTALLING"
        record["installResumedAt"] = _utc_now()
        self._persist_record(record)
        self._assert_dropin_filesystem(installed=True)
        self._assert_no_managed_unit_fallback_files()
        self._converge_setup_command(record, "systemd-sysusers", create=True)
        self._converge_setup_command(record, "systemd-tmpfiles", create=True)
        self._run(("systemctl", "daemon-reload"))
        commands_completed = record.get("commandsCompleted")
        if not isinstance(commands_completed, list):
            raise MaintenanceInstallError("maintenance command journal is malformed")
        if "systemctl daemon-reload" not in commands_completed:
            commands_completed.append("systemctl daemon-reload")
            self._persist_record(record)
        self._assert_managed_unit_fence_contract(allow_absent=False)
        runtime_target = self._systemctl_show(RUNTIME_TARGET)
        if self._reported_drop_in_paths(runtime_target) != (RUNTIME_DROP_IN_PATH,):
            raise MaintenanceInstallError(
                "runtime target did not load the authenticated maintenance drop-in"
            )
        self._prepare_activation(record)
        self._start_and_verify_all_units(record)

        directories_before = record.get("directoriesBefore")
        if not isinstance(directories_before, Mapping):
            raise MaintenanceInstallError("managed directory journal is malformed")
        record["directoriesAfter"] = {
            path: self._snapshot(path) for path in sorted(directories_before)
        }
        record["tmpfilesAfter"] = {
            path: self._snapshot(path)
            for path in (*TMPFILES_DIRECTORY_MODES, *TMPFILES_REGULAR_PATHS)
        }
        record["status"] = "INSTALLED"
        record["installedAt"] = _utc_now()
        self._persist_record(record)
        return self.audit(
            expected_image_release_id=expected_image_release_id,
            expected_image_version=expected_image_version,
            expected_legacy_service=expected_legacy_service,
        )

    def _recover_unlocated_installation(
        self,
        manifest: MaintenanceManifest,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
        payload_root: Path,
        expected_manifest_sha256: str,
    ) -> dict[str, Any]:
        orphan = self._discover_unlocated_operation()
        if orphan is None:
            raise MaintenanceInstallError(
                "unlocated installation disappeared during recovery"
            )
        operation_dir = orphan["operationDirectory"]
        if orphan["kind"] in {"EMPTY_RECORD_INCOMING", "EMPTY_OPERATION"}:
            incoming = operation_dir / ".record.json.incoming"
            entries = tuple(operation_dir.iterdir())
            if orphan["kind"] == "EMPTY_RECORD_INCOMING" and (
                len(entries) != 1 or entries[0] != incoming
            ):
                raise MaintenanceInstallError(
                    "empty maintenance operation gained unknown state"
                )
            if orphan["kind"] == "EMPTY_OPERATION" and entries:
                raise MaintenanceInstallError(
                    "empty maintenance operation gained unknown state"
                )
            if _lexists(incoming):
                incoming.unlink()
                self._fsync_directory(operation_dir)
            operation_dir.rmdir()
            self._fsync_directory(operation_dir.parent)
            return self.install(
                payload_root,
                expected_manifest_sha256,
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
                apply=True,
            )

        record = orphan.get("record")
        if (
            not isinstance(record, dict)
            or record.get("payloadId") != manifest.payload_id
            or record.get("manifestSha256") != manifest.manifest_sha256
        ):
            raise MaintenanceInstallError(
                "unlocated installation belongs to another authenticated payload"
            )
        self._reconcile_operation_singleton_incomings(record)
        if self._existing_marker() is None:
            self._write_marker(PENDING_MARKER, record)

        activation = self._activation_journal(record, create=False)
        expected_artifact_count = len(target_files(manifest)) + 2
        artifact_intents = record.get("artifactIntents")
        can_resume_forward = bool(
            record.get("status") == "INSTALLED"
            or (
                activation is not None
                and activation.get("state") in {"STARTING", "VERIFIED"}
            )
            or (
                isinstance(artifact_intents, list)
                and len(artifact_intents) == expected_artifact_count
                and record.get("status")
                in {
                    "INSTALLING",
                    "INSTALL_RESUME_REQUIRED",
                    "INSTALL_RESUME_BLOCKED",
                }
            )
        )
        if not can_resume_forward:
            self.rollback(
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
                apply=True,
            )
        return self.install(
            payload_root,
            expected_manifest_sha256,
            expected_image_release_id=expected_image_release_id,
            expected_image_version=expected_image_version,
            expected_legacy_service=expected_legacy_service,
            apply=True,
        )

    def install(
        self,
        payload_root: Path,
        expected_manifest_sha256: str,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        report = self.preflight(
            payload_root,
            expected_manifest_sha256,
            expected_image_release_id=expected_image_release_id,
            expected_image_version=expected_image_version,
            expected_legacy_service=expected_legacy_service,
        )
        if report["status"] == "UNLOCATED_INSTALLATION_RECOVERY_REQUIRED":
            if not apply:
                return {**report, "dryRun": True}
            manifest = load_and_validate_payload(
                payload_root, expected_manifest_sha256
            )
            return self._recover_unlocated_installation(
                manifest,
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
                payload_root=payload_root,
                expected_manifest_sha256=expected_manifest_sha256,
            )
        if report["status"] == "ROLLED_BACK_MARKER_CLEANUP_REQUIRED":
            if not apply:
                return {**report, "dryRun": True}
            _kind, record, _marker = self._load_record_from_marker()
            self._finalize_rolled_back_record(record, apply=True)
            return self.install(
                payload_root,
                expected_manifest_sha256,
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
                apply=True,
            )
        if report["status"] == "INCOMPLETE_INSTALLATION_CAN_RESUME":
            if not apply:
                return {**report, "dryRun": True}
            manifest = load_and_validate_payload(
                payload_root, expected_manifest_sha256
            )
            return self._resume_incomplete_installation(
                manifest,
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
            )
        if report["status"] in {
            "ALREADY_INSTALLED",
            "INSTALLED_PENDING_FINALIZATION",
        }:
            if apply:
                return self.audit(
                    expected_image_release_id=expected_image_release_id,
                    expected_image_version=expected_image_version,
                    expected_legacy_service=expected_legacy_service,
                )
            return {**report, "dryRun": True}
        if not apply:
            return {**report, "dryRun": True}

        manifest = load_and_validate_payload(payload_root, expected_manifest_sha256)
        hardware_directories, current_link = self._hardware_runtime_directories()
        operation_id = (
            f"op-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')}-"
            f"{manifest.payload_id[:24]}-{secrets.token_hex(4)}"
        )
        _safe_id(operation_id, description="operation ID")

        code_directories = self._planned_code_directories(manifest)
        tracked_directories = {
            *code_directories,
            *TMPFILES_DIRECTORY_MODES,
            *("/" + path.relative_to(self.rootfs).as_posix() for path in hardware_directories),
        }
        tracked_paths = {
            *(target.destination for target in target_files(manifest)),
            "/opt/ecobin/communication/current",
            "/opt/ecobin/updater/current",
            *TMPFILES_REGULAR_PATHS,
        }
        directory_before = {
            path: self._snapshot(path) for path in sorted(tracked_directories)
        }
        path_before = {path: self._snapshot(path) for path in sorted(tracked_paths)}
        if any(snapshot["exists"] for snapshot in path_before.values()):
            raise MaintenanceInstallError("a managed target appeared after preflight")
        services_before = {unit: self._systemctl_show(unit) for unit in TRACKED_UNITS}
        preflight_legacy = report["legacyService"]
        transaction_legacy = services_before[expected_legacy_service]
        if (
            transaction_legacy.get("loadState") != "loaded"
            or transaction_legacy.get("activeState") != "active"
            or transaction_legacy.get("fragmentPath")
            != preflight_legacy.get("fragmentPath")
            or self._reported_drop_in_paths(transaction_legacy)
            != (LEGACY_GATE_DROP_IN_PATH,)
        ):
            raise MaintenanceInstallError(
                "legacy service changed before the installation transaction"
            )
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            state = services_before[unit]
            if (
                state.get("loadState") != "not-found"
                or state.get("activeState") != "inactive"
                or state.get("fragmentPath")
                or self._reported_drop_in_paths(state)
            ):
                raise MaintenanceInstallError(
                    f"new permanent unit appeared before installation: {unit}"
                )
        storage_before = {
            path: self._snapshot(path)
            for path in (
                "/var/lib/ecobin/device-management-maintenance",
                BACKUP_PARENT,
            )
        }
        self._ensure_maintenance_storage(operation_id)
        legacy_fragment = report["legacyService"].get("fragmentPath")
        if not isinstance(legacy_fragment, str) or not legacy_fragment.startswith("/"):
            raise MaintenanceInstallError("legacy hardware service fragment path is invalid")
        record: dict[str, Any] = {
            "schemaVersion": BACKUP_SCHEMA_VERSION,
            "operationId": operation_id,
            "status": "INSTALLING",
            "createdAt": _utc_now(),
            "payloadId": manifest.payload_id,
            "manifestSha256": manifest.manifest_sha256,
            "sourceGitCommit": manifest.source_git_commit,
            "image": report["image"],
            "legacyService": expected_legacy_service,
            "hardwareCurrent": current_link,
            "protectedBefore": {
                path: self._snapshot(path)
                for path in sorted(
                    (IMMUTABLE_PATHS | {legacy_fragment})
                    - {
                        "/var/lib/ecobin/hardware/edge.db",
                        "/var/lib/ecobin/hardware/edge.db-wal",
                        "/var/lib/ecobin/hardware/edge.db-shm",
                    }
                )
            },
            "servicesBefore": services_before,
            "storageBefore": storage_before,
            "directoriesBefore": directory_before,
            "pathsBefore": path_before,
            # Each publication is durably recorded before it becomes visible.
            # A PREPARED entry therefore remains sufficient for a fresh
            # process to identify and remove an artifact after sudden power
            # loss between rename/symlink and the DONE journal update.
            "artifactIntents": [],
            "directoriesAfter": {},
            "rootCodeDirectories": report["rootCodeDirectories"],
            "filesReplaced": [],
            "commandsCompleted": [],
            "setupCommandIntents": [],
            # A start intent is fsynced before invoking systemctl.  Recovery
            # may treat a missing updater database as harmless only when this
            # journal proves the updater start was never attempted.
            "unitStartIntents": [],
            "activation": {
                "schemaVersion": 1,
                "state": "NOT_STARTED",
                "intendedUnits": list(START_UNITS),
                "preparedAt": _utc_now(),
            },
            "runtimeStartFence": None,
            "rollback": None,
        }
        self._persist_record(record)
        self._write_marker(PENDING_MARKER, record)

        try:
            # This persistent, root-owned fence is visible to every new unit's
            # ConditionPathExists check.  It is established before any unit
            # file can be published, so the continuously running first-boot
            # coordinator cannot win a start-vs-journal race.  Unlike a
            # runtime mask, it also survives power loss and reboot.
            self._establish_runtime_start_fence(record)

            # Restore only traversal on the already installed business release.
            # Every individual directory was captured above; files and current
            # symlink are deliberately untouched.
            for directory in hardware_directories:
                details = self._assert_safe_directory(
                    directory, "hardware runtime traversal directory"
                )
                if stat.S_IMODE(details.st_mode) != 0o755:
                    os.chmod(directory, 0o755)
                    self._fsync_directory(directory)
            for directory in hardware_directories:
                absolute = "/" + directory.relative_to(self.rootfs).as_posix()
                record["directoriesAfter"][absolute] = self._snapshot(absolute)
            self._persist_record(record)

            for absolute, mode in sorted(
                code_directories.items(), key=lambda item: len(PurePosixPath(item[0]).parts)
            ):
                self._ensure_directory(absolute, mode)
                record["directoriesAfter"][absolute] = self._snapshot(absolute)
                self._persist_record(record)

            for target in target_files(manifest):
                source = payload_root.joinpath(*PurePosixPath(target.source_relative).parts)
                locked = manifest.files[target.source_relative]
                destination = self._path(target.destination)
                incoming_absolute = self._incoming_absolute(
                    target.destination, operation_id
                )
                intent: dict[str, Any] = {
                    "kind": "regular",
                    "state": "PREPARED",
                    "path": target.destination,
                    "source": target.source_relative,
                    "sha256": locked.sha256,
                    "size": locked.size,
                    "mode": target.mode,
                    "temporaryPath": incoming_absolute,
                    "preparedAt": _utc_now(),
                }
                record["artifactIntents"].append(intent)
                self._persist_record(record)
                self._copy_verified(
                    source,
                    locked,
                    destination,
                    self._path(incoming_absolute),
                    intent,
                    target.mode,
                )
                if self.after_publish_hook is not None:
                    self.after_publish_hook(intent)
                published = destination.lstat()
                intent.update(
                    state="DONE",
                    device=published.st_dev,
                    inode=published.st_ino,
                    publishedAt=_utc_now(),
                )
                self._persist_record(record)

            for absolute, link_target in (
                (
                    "/opt/ecobin/communication/current",
                    f"releases/{manifest.communication_release_id}",
                ),
                (
                    "/opt/ecobin/updater/current",
                    f"releases/{manifest.updater_release_id}",
                ),
            ):
                path = self._path(absolute)
                if _lexists(path):
                    raise MaintenanceInstallError(f"managed link appeared: {absolute}")
                intent = {
                    "kind": "symlink",
                    "state": "PREPARED",
                    "path": absolute,
                    "target": link_target,
                    "preparedAt": _utc_now(),
                }
                record["artifactIntents"].append(intent)
                self._persist_record(record)
                path.symlink_to(link_target)
                self._fsync_directory(path.parent)
                if self.after_publish_hook is not None:
                    self.after_publish_hook(intent)
                details = path.lstat()
                intent.update(
                    state="DONE",
                    device=details.st_dev,
                    inode=details.st_ino,
                    publishedAt=_utc_now(),
                )
                self._persist_record(record)

            self._converge_setup_command(
                record, "systemd-sysusers", create=True
            )
            self._converge_setup_command(
                record, "systemd-tmpfiles", create=True
            )
            for absolute in TMPFILES_DIRECTORY_MODES:
                record["directoriesAfter"][absolute] = self._snapshot(absolute)
            self._persist_record(record)
            self._run(("systemctl", "daemon-reload"))
            record["commandsCompleted"].append("systemctl daemon-reload")
            self._persist_record(record)
            self._prepare_activation(record)
            self._start_and_verify_all_units(record)

            record["directoriesAfter"] = {
                path: self._snapshot(path) for path in sorted(tracked_directories)
            }
            record["tmpfilesAfter"] = {
                path: self._snapshot(path)
                for path in (*TMPFILES_DIRECTORY_MODES, *TMPFILES_REGULAR_PATHS)
            }
            record["status"] = "INSTALLED"
            record["installedAt"] = _utc_now()
            self._persist_record(record)
            # audit() validates the entire installed surface while the durable
            # pending marker still makes an interrupted finalization
            # discoverable.  Only a complete installation is promoted.
            return self.audit(
                expected_image_release_id=expected_image_release_id,
                expected_image_version=expected_image_version,
                expected_legacy_service=expected_legacy_service,
            )
        except MaintenanceProcessInterrupted:
            # This models SIGKILL/power loss: no in-process cleanup is
            # possible.  The durable pending marker and PREPARED intent are
            # deliberately left for a newly started installer to recover.
            raise
        except BaseException as install_error:
            activation = self._activation_journal(record, create=False)
            if activation is not None and activation["state"] in {
                "STARTING",
                "VERIFIED",
            }:
                # At least one start request may already have reached systemd.
                # Destructive rollback can no longer prove that updater state
                # is unused, so retain the authenticated payload and converge
                # it forward on the next identical install invocation.
                try:
                    self._establish_runtime_start_fence(record)
                    self._run(("systemctl", "daemon-reload"))
                    record["status"] = "INSTALL_RESUME_REQUIRED"
                    record["installError"] = type(install_error).__name__
                    self._persist_record(record)
                except BaseException as fence_error:
                    record["status"] = "INSTALL_RESUME_BLOCKED"
                    record["installError"] = type(install_error).__name__
                    record["installResumeFenceError"] = type(fence_error).__name__
                    self._persist_record(record)
                raise MaintenanceInstallError(
                    "installation crossed the service-start boundary and must "
                    "be resumed with the same authenticated payload"
                ) from install_error
            try:
                record["status"] = "INSTALL_FAILED"
                record["installError"] = type(install_error).__name__
                self._persist_record(record)
                self._rollback_record(record, automatic=True)
            except RollbackRefusedForUsedState as rollback_error:
                raise MaintenanceInstallError(
                    "installation failed and automatic rollback was safely refused; "
                    "previously active management services were restored: "
                    f"{type(install_error).__name__}; {rollback_error}"
                ) from install_error
            except BaseException as rollback_error:
                raise MaintenanceInstallError(
                    f"installation failed and automatic rollback was blocked: "
                    f"{type(install_error).__name__}; {rollback_error}"
                ) from install_error
            raise

    def _load_record_from_marker(self) -> tuple[str, dict[str, Any], Mapping[str, Any]]:
        marker_result = self._existing_marker()
        if marker_result is None:
            raise MaintenanceInstallError("no active or incomplete maintenance installation exists")
        kind, marker = marker_result
        record_path = self._record_path(str(marker["operationId"]))
        self._assert_maintenance_state_file(
            record_path, "maintenance backup record"
        )
        document = _load_json_bytes(record_path.read_bytes(), description="backup record")
        if not isinstance(document, dict) or document.get("schemaVersion") != BACKUP_SCHEMA_VERSION:
            raise MaintenanceInstallError("maintenance backup record is invalid")
        if any(
            document.get(key) != marker[marker_key]
            for key, marker_key in (
                ("operationId", "operationId"),
                ("payloadId", "payloadId"),
                ("manifestSha256", "manifestSha256"),
            )
        ):
            raise MaintenanceInstallError("maintenance marker and backup record differ")
        return kind, document, marker

    def _verify_image_against_record(
        self,
        record: Mapping[str, Any],
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
    ) -> dict[str, str]:
        if (
            record.get("image")
            != {
                "releaseId": expected_image_release_id,
                "version": expected_image_version,
            }
            or record.get("legacyService") != expected_legacy_service
        ):
            raise MaintenanceInstallError("rollback/audit confirmation differs from backup")
        identity = self._read_image_identity()
        if identity != record["image"]:
            raise MaintenanceInstallError("device image identity changed after installation")
        return identity

    def _assert_regular_intent(
        self, item: Mapping[str, Any], *, require_done: bool
    ) -> os.stat_result:
        state = item.get("state")
        if state not in ("PREPARED", "DONE") or (require_done and state != "DONE"):
            raise MaintenanceInstallError("artifact publication journal is incomplete")
        absolute = str(item["path"])
        path = self._path(absolute)
        details = self._assert_safe_regular(path, f"installed file {absolute}")
        if (
            _sha256_file(path) != item["sha256"]
            or details.st_size != item["size"]
            or self._metadata_mode(path, details) != item["mode"]
            or (
                state == "DONE"
                and (
                    details.st_dev != item["device"]
                    or details.st_ino != item["inode"]
                )
            )
        ):
            raise MaintenanceInstallError(f"installed file changed: {absolute}")
        return details

    def _assert_symlink_intent(
        self, item: Mapping[str, Any], *, require_done: bool
    ) -> os.stat_result:
        state = item.get("state")
        if state not in ("PREPARED", "DONE") or (require_done and state != "DONE"):
            raise MaintenanceInstallError("artifact publication journal is incomplete")
        absolute = str(item["path"])
        path = self._path(absolute)
        try:
            details = path.lstat()
        except OSError as exc:
            raise MaintenanceInstallError(
                f"installed link is unavailable: {absolute}"
            ) from exc
        self._assert_root_metadata(path, details, f"installed link {absolute}")
        if (
            not stat.S_ISLNK(details.st_mode)
            or os.readlink(path) != item["target"]
            or (
                state == "DONE"
                and (
                    details.st_dev != item["device"]
                    or details.st_ino != item["inode"]
                )
            )
        ):
            raise MaintenanceInstallError(f"installed link changed: {absolute}")
        return details

    def _assert_artifact_intent(
        self,
        item: Mapping[str, Any],
        *,
        require_done: bool,
        operation_id: str,
    ) -> None:
        kind = item.get("kind")
        if kind == "regular":
            absolute = str(item.get("path", ""))
            temporary = item.get("temporaryPath")
            if temporary != self._incoming_absolute(absolute, operation_id):
                raise MaintenanceInstallError(
                    "artifact publication journal has unsafe temporary path"
                )
            self._assert_regular_intent(item, require_done=require_done)
            if require_done and _lexists(self._path(str(temporary))):
                raise MaintenanceInstallError(
                    f"incoming artifact remains after publication: {temporary}"
                )
        elif kind == "symlink":
            self._assert_symlink_intent(item, require_done=require_done)
        else:
            raise MaintenanceInstallError("artifact publication journal has unknown kind")

    def _assert_hardware_current_unchanged(self, record: Mapping[str, Any]) -> None:
        expected = record["hardwareCurrent"]
        actual = self._snapshot("/opt/ecobin/hardware/current")
        if (
            actual.get("kind") != "symlink"
            or actual.get("target") != expected["target"]
            or actual.get("device") != expected["device"]
            or actual.get("inode") != expected["inode"]
        ):
            raise MaintenanceInstallError("hardware current release link changed")

    def _assert_protected_paths_unchanged(self, record: Mapping[str, Any]) -> None:
        for absolute, before in record.get("protectedBefore", {}).items():
            if absolute == "/opt/ecobin/hardware/current":
                continue
            if self._snapshot(absolute) != before:
                raise MaintenanceInstallError(
                    f"protected device path changed during maintenance: {absolute}"
                )

    def _assert_accounts(self) -> None:
        self._identity_installation_state(require_present=True)

    @staticmethod
    def _runtime_directory_owner_expectations(
        identities: Mapping[str, Any],
    ) -> Mapping[str, tuple[int, frozenset[int]]]:
        users = identities["users"]
        primary_groups = identities["primaryGroups"]
        groups = identities["groups"]
        return {
            "/var/lib/ecobin/communication": (
                users["ecobin-communication"],
                frozenset(
                    {
                        primary_groups["ecobin-communication"],
                        groups["ecobin-communication-ipc"],
                    }
                ),
            ),
            "/var/lib/ecobin/business": (
                users["ecobin-business"],
                frozenset({primary_groups["ecobin-business"]}),
            ),
            "/var/lib/ecobin/business/photos": (
                users["ecobin-business"],
                frozenset({primary_groups["ecobin-business"]}),
            ),
            "/var/lib/ecobin/updater": (
                users["ecobin-updater"],
                frozenset({groups["ecobin-updater-ipc"]}),
            ),
            "/var/lib/ecobin/updater/staging": (
                users["ecobin-updater"],
                frozenset({primary_groups["ecobin-updater"]}),
            ),
            "/var/lib/ecobin/updater/mcu-firmware": (
                users["ecobin-updater"],
                frozenset({primary_groups["ecobin-updater"]}),
            ),
            "/var/lib/ecobin/privileged": (0, frozenset({0})),
            "/var/lib/ecobin/privileged/business-snapshots": (0, frozenset({0})),
            "/opt/ecobin/business": (
                0,
                frozenset({primary_groups["ecobin-business"]}),
            ),
            "/opt/ecobin/business/releases": (
                0,
                frozenset({primary_groups["ecobin-business"]}),
            ),
            "/run/ecobin/communication": (
                users["ecobin-communication"],
                frozenset({groups["ecobin-communication-ipc"]}),
            ),
            "/run/ecobin/business": (
                users["ecobin-business"],
                frozenset({groups["ecobin-business-ipc"]}),
            ),
            "/run/ecobin/updater": (
                users["ecobin-updater"],
                frozenset({groups["ecobin-updater-ipc"]}),
            ),
            "/run/ecobin/privileged": (
                0,
                frozenset({groups["ecobin-privileged-helper-ipc"]}),
            ),
        }

    def _audit_runtime_permissions(
        self, identities: Mapping[str, Any], record: Mapping[str, Any]
    ) -> None:
        directory_owners = self._runtime_directory_owner_expectations(identities)
        for absolute, mode in TMPFILES_DIRECTORY_MODES.items():
            actual = self._snapshot(absolute)
            expected_uid, expected_gids = directory_owners[absolute]
            if (
                actual.get("kind") != "directory"
                or actual.get("mode") != mode
                or actual.get("uid") != expected_uid
                or actual.get("gid") not in expected_gids
            ):
                raise MaintenanceInstallError(
                    f"managed runtime directory permissions differ: {absolute}"
                )
        mutation_lock = self._snapshot(TMPFILES_REGULAR_PATHS[0])
        if (
            mutation_lock.get("kind") != "regular"
            or mutation_lock.get("mode") != 0o600
            or mutation_lock.get("uid") != 0
            or mutation_lock.get("gid") != 0
            or mutation_lock.get("size") != 0
        ):
            raise MaintenanceInstallError(
                "privileged mutation lock permissions differ"
            )
        for absolute in record["rootCodeDirectories"]:
            self._assert_safe_directory(
                self._path(absolute),
                f"root-owned code directory {absolute}",
                expected_mode=0o755,
            )

    def audit(
        self,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
        _finalize_markers: bool = True,
    ) -> dict[str, Any]:
        kind, record, _marker = self._load_record_from_marker()
        if kind not in {"active", "pending"} or record.get("status") != "INSTALLED":
            raise MaintenanceInstallError("maintenance installation is incomplete")
        self._assert_installed_marker_set(record)
        fence_journal = self._runtime_fence_journal(record, create=False)
        if (
            fence_journal is None
            or fence_journal.get("state") != "RELEASED"
            or _lexists(self._path(RUNTIME_START_FENCE))
        ):
            raise MaintenanceInstallError(
                "installed runtime start fence was not safely released"
            )
        activation = self._activation_journal(record, create=False)
        if activation is None or activation.get("state") != "VERIFIED":
            raise MaintenanceInstallError(
                "installed service activation was not completely verified"
            )
        setup_intents = self._setup_command_intents(record, create=False)
        if [intent.get("name") for intent in setup_intents] != list(
            SETUP_COMMAND_ORDER
        ) or any(intent.get("state") != "DONE" for intent in setup_intents):
            raise MaintenanceInstallError(
                "installed declarative setup was not completely verified"
            )
        identity = self._verify_image_against_record(
            record,
            expected_image_release_id,
            expected_image_version,
            expected_legacy_service,
        )
        self._assert_mcu_safety_gate()
        self._assert_dropin_filesystem(installed=True)
        self._assert_managed_unit_fence_contract(allow_absent=False)
        self._assert_hardware_current_unchanged(record)
        self._assert_protected_paths_unchanged(record)
        artifact_intents = record.get("artifactIntents")
        if not isinstance(artifact_intents, list) or not artifact_intents:
            raise MaintenanceInstallError("artifact publication journal is absent")
        for item in artifact_intents:
            if not isinstance(item, dict):
                raise MaintenanceInstallError("artifact publication journal is malformed")
            self._assert_artifact_intent(
                item,
                require_done=True,
                operation_id=str(record["operationId"]),
            )
        for absolute, after in record["directoriesAfter"].items():
            actual = self._snapshot(absolute)
            if not actual.get("exists") or actual.get("kind") != "directory":
                raise MaintenanceInstallError(f"managed directory is absent: {absolute}")
            if absolute.startswith("/run/"):
                expected_mode = TMPFILES_DIRECTORY_MODES.get(absolute)
                if (
                    os.name == "posix"
                    and expected_mode is not None
                    and actual.get("mode") != expected_mode
                ):
                    raise MaintenanceInstallError(f"runtime directory mode changed: {absolute}")
            elif (
                (
                    os.name == "posix"
                    and (
                        actual.get("device") != after.get("device")
                        or actual.get("inode") != after.get("inode")
                    )
                )
                or actual.get("mode") != after.get("mode")
            ):
                raise MaintenanceInstallError(f"managed directory metadata changed: {absolute}")
            expected_mode = TMPFILES_DIRECTORY_MODES.get(absolute)
            if (
                os.name == "posix"
                and expected_mode is not None
                and actual.get("mode") != expected_mode
            ):
                raise MaintenanceInstallError(f"managed directory mode changed: {absolute}")
        identities = self._identity_installation_state(require_present=True)
        assert identities is not None
        self._audit_runtime_permissions(identities, record)
        states = {unit: self._systemctl_show(unit) for unit in TRACKED_UNITS}
        if (
            states[expected_legacy_service]["activeState"] != "active"
            or self._reported_drop_in_paths(states[expected_legacy_service])
            != (LEGACY_GATE_DROP_IN_PATH,)
        ):
            raise MaintenanceInstallError("legacy business service stopped during installation")
        if self._reported_drop_in_paths(states[RUNTIME_TARGET]) != (
            RUNTIME_DROP_IN_PATH,
        ):
            raise MaintenanceInstallError(
                "runtime target does not have the one controlled drop-in"
            )
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            expected_fragment = f"/etc/systemd/system/{unit}"
            if (
                states[unit]["loadState"] != "loaded"
                or states[unit]["fragmentPath"] != expected_fragment
                or self._reported_drop_in_paths(states[unit])
            ):
                raise MaintenanceInstallError(
                    f"installed unit fragment is not the controlled file: {unit}"
                )
        for unit in START_UNITS:
            if states[unit]["activeState"] != "active":
                raise MaintenanceInstallError(f"permanent unit is not active: {unit}")
        if (
            _finalize_markers
            and not isinstance(record.get("installationAuditPassedAt"), str)
        ):
            record["installationAuditPassedAt"] = _utc_now()
            self._persist_record(record)
        result = {
            "status": "PASS",
            "payloadId": record["payloadId"],
            "manifestSha256": record["manifestSha256"],
            "operationId": record["operationId"],
            "image": identity,
            "legacyServiceActive": True,
            "installedFileCount": sum(
                item.get("kind") == "regular" for item in artifact_intents
            ),
            "installedLinkCount": sum(
                item.get("kind") == "symlink" for item in artifact_intents
            ),
            "activePermanentUnits": list(START_UNITS),
        }
        if _finalize_markers:
            self._reconcile_operation_singleton_incomings(record)
            self._finalize_installed_markers(record)
        return result

    def _assert_installed_marker_set(self, record: Mapping[str, Any]) -> None:
        expected = _canonical_json(self._marker_document(record))
        found = 0
        for absolute in (ACTIVE_MARKER, PENDING_MARKER):
            path = self._path(absolute)
            if not _lexists(path):
                continue
            found += 1
            self._assert_maintenance_state_file(
                path, f"maintenance marker {absolute}"
            )
            if path.read_bytes() != expected:
                raise MaintenanceInstallError(
                    f"maintenance marker changed: {absolute}"
                )
        if found == 0:
            raise MaintenanceInstallError("installed maintenance marker is absent")

    def _finalize_installed_markers(self, record: Mapping[str, Any]) -> None:
        expected = _canonical_json(self._marker_document(record))
        active = self._path(ACTIVE_MARKER)
        pending = self._path(PENDING_MARKER)
        if _lexists(active):
            self._assert_maintenance_state_file(
                active, "active maintenance marker"
            )
            if active.read_bytes() != expected:
                raise MaintenanceInstallError("active maintenance marker changed")
        else:
            self._write_marker(ACTIVE_MARKER, record)
        if _lexists(pending):
            self._assert_maintenance_state_file(
                pending, "pending maintenance marker"
            )
            if pending.read_bytes() != expected:
                raise MaintenanceInstallError("pending maintenance marker changed")
            pending.unlink()
            self._fsync_directory(pending.parent)

    def _remove_artifact_intent(
        self, item: Mapping[str, Any], *, operation_id: str
    ) -> None:
        try:
            path = self._path(str(item["path"]))
            state = item.get("state")
            kind = item.get("kind")
            if state not in ("PREPARED", "DONE") or kind not in (
                "regular",
                "symlink",
            ):
                raise MaintenanceInstallError(
                    "artifact publication journal is malformed"
                )
            if kind == "regular" and item.get(
                "temporaryPath"
            ) != self._incoming_absolute(str(item["path"]), operation_id):
                raise MaintenanceInstallError(
                    "artifact publication journal has unsafe temporary path"
                )
            if _lexists(path):
                try:
                    self._assert_artifact_intent(
                        item,
                        require_done=False,
                        operation_id=operation_id,
                    )
                except MaintenanceInstallError as exc:
                    raise MaintenanceInstallError(
                        f"rollback refuses changed published artifact: {item['path']}"
                    ) from exc
                path.unlink()
                self._fsync_directory(path.parent)
        except (KeyError, TypeError, ValueError) as exc:
            raise MaintenanceInstallError(
                "rollback artifact journal entry is malformed"
            ) from exc
        if item.get("kind") == "regular":
            temporary = self._path(str(item["temporaryPath"]))
            if _lexists(temporary):
                details = self._assert_safe_regular(
                    temporary,
                    f"rollback incoming artifact {item['temporaryPath']}",
                )
                if self._metadata_mode(temporary, details) not in (
                    0o600,
                    int(item["mode"]),
                ):
                    raise MaintenanceInstallError(
                        "rollback refuses incoming artifact with unsafe mode: "
                        f"{item['temporaryPath']}"
                    )
                temporary.unlink()
                self._fsync_directory(temporary.parent)

    @staticmethod
    def _posix_descendant(path: str, parent: str) -> bool:
        candidate = PurePosixPath(path)
        return candidate != PurePosixPath(parent) and PurePosixPath(parent) in candidate.parents

    def _assert_artifact_safe_for_removal(
        self,
        item: Mapping[str, Any],
        *,
        operation_id: str,
        require_done: bool,
        allow_removed: bool,
    ) -> None:
        try:
            state = item.get("state")
            kind = item.get("kind")
            absolute = item.get("path")
            if (
                state not in {"PREPARED", "DONE"}
                or (require_done and state != "DONE")
                or kind not in {"regular", "symlink"}
                or not isinstance(absolute, str)
            ):
                raise MaintenanceInstallError(
                    "artifact publication journal is malformed"
                )
            # _path rejects traversal, backslashes and non-absolute paths.
            destination = self._path(absolute)
            destination_exists = _lexists(destination)
            if destination_exists:
                self._assert_artifact_intent(
                    item,
                    require_done=False,
                    operation_id=operation_id,
                )
            elif state == "DONE" and not allow_removed:
                raise MaintenanceInstallError(
                    f"installed artifact disappeared before rollback: {absolute}"
                )
            elif require_done:
                raise MaintenanceInstallError(
                    f"installed artifact is absent: {absolute}"
                )

            if kind == "regular":
                temporary_value = item.get("temporaryPath")
                if temporary_value != self._incoming_absolute(
                    absolute, operation_id
                ):
                    raise MaintenanceInstallError(
                        "artifact publication journal has unsafe temporary path"
                    )
                temporary = self._path(str(temporary_value))
                if _lexists(temporary):
                    details = self._assert_safe_regular(
                        temporary,
                        f"rollback incoming artifact {temporary_value}",
                    )
                    mode = item.get("mode")
                    size = item.get("size")
                    if (
                        not isinstance(mode, int)
                        or not isinstance(size, int)
                        or size < 0
                        or self._metadata_mode(temporary, details)
                        not in {0o600, mode}
                        or details.st_size > size
                        or state == "DONE"
                    ):
                        raise MaintenanceInstallError(
                            "rollback refuses changed incoming artifact: "
                            f"{temporary_value}"
                        )
            elif "temporaryPath" in item:
                raise MaintenanceInstallError(
                    "symlink publication journal has a temporary path"
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise MaintenanceInstallError(
                "rollback artifact journal entry is malformed"
            ) from exc

    def _assert_new_code_trees_contain_only_planned_entries(
        self,
        record: Mapping[str, Any],
        artifact_intents: Sequence[Mapping[str, Any]],
    ) -> None:
        directories_before = record.get("directoriesBefore")
        root_code_directories = record.get("rootCodeDirectories")
        if not isinstance(directories_before, Mapping) or not isinstance(
            root_code_directories, list
        ):
            raise MaintenanceInstallError(
                "rollback directory journal is malformed"
            )
        if any(
            not isinstance(value, str) or value not in directories_before
            for value in root_code_directories
        ):
            raise MaintenanceInstallError(
                "rollback code-directory journal is malformed"
            )
        code_directories = set(root_code_directories)
        planned_entries = set(code_directories)
        for item in artifact_intents:
            path = item.get("path")
            if isinstance(path, str):
                planned_entries.add(path)
            temporary = item.get("temporaryPath")
            if isinstance(temporary, str):
                planned_entries.add(temporary)

        new_directories = {
            absolute
            for absolute in code_directories
            if isinstance(directories_before.get(absolute), Mapping)
            and not directories_before[absolute].get("exists")
        }
        roots = sorted(
            (
                absolute
                for absolute in new_directories
                if not any(
                    self._posix_descendant(absolute, other)
                    for other in new_directories
                )
            ),
            key=lambda value: len(PurePosixPath(value).parts),
        )
        for root in roots:
            root_path = self._path(root)
            if not _lexists(root_path):
                continue
            self._assert_safe_directory(
                root_path,
                f"new rollback code directory {root}",
            )
            pending = [root]
            while pending:
                parent = pending.pop()
                try:
                    entries = tuple(os.scandir(self._path(parent)))
                except OSError as exc:
                    raise MaintenanceInstallError(
                        f"cannot enumerate rollback code directory: {parent}"
                    ) from exc
                for entry in entries:
                    absolute = f"{parent.rstrip('/')}/{entry.name}"
                    if absolute not in planned_entries:
                        raise MaintenanceInstallError(
                            "rollback found an untracked code entry: "
                            f"{absolute}"
                        )
                    try:
                        details = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise MaintenanceInstallError(
                            f"cannot inspect rollback code entry: {absolute}"
                        ) from exc
                    if stat.S_ISDIR(details.st_mode):
                        if absolute not in code_directories:
                            raise MaintenanceInstallError(
                                "rollback found an untracked code directory: "
                                f"{absolute}"
                            )
                        pending.append(absolute)

    def _assert_rollback_directories_safe(
        self, record: Mapping[str, Any]
    ) -> None:
        directories_before = record.get("directoriesBefore")
        directories_after = record.get("directoriesAfter")
        root_code_directories = record.get("rootCodeDirectories")
        if (
            not isinstance(directories_before, Mapping)
            or not isinstance(directories_after, Mapping)
            or not isinstance(root_code_directories, list)
        ):
            raise MaintenanceInstallError(
                "rollback directory journal is malformed"
            )
        code_set = set(root_code_directories)
        removal_started = isinstance(record.get("rollbackRemovalStartedAt"), str)
        installation_committed = isinstance(record.get("installedAt"), str)
        runtime_owners: Mapping[str, tuple[int, frozenset[int]]] | None = None
        if any(
            _lexists(self._path(absolute))
            for absolute in TMPFILES_DIRECTORY_MODES
        ):
            identities = self._identity_installation_state(require_present=True)
            assert identities is not None
            runtime_owners = self._runtime_directory_owner_expectations(
                identities
            )
        for absolute, before in directories_before.items():
            if (
                not isinstance(absolute, str)
                or not isinstance(before, Mapping)
                or absolute.startswith("/../")
            ):
                raise MaintenanceInstallError(
                    "rollback directory journal is malformed"
                )
            path = self._path(absolute)
            exists = _lexists(path)
            if not exists:
                if before.get("exists"):
                    raise MaintenanceInstallError(
                        f"original rollback directory disappeared: {absolute}"
                    )
                continue
            if absolute in TMPFILES_DIRECTORY_MODES:
                try:
                    details = path.lstat()
                except OSError as exc:
                    raise MaintenanceInstallError(
                        f"rollback managed directory is unavailable: {absolute}"
                    ) from exc
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(
                    details.st_mode
                ):
                    raise MaintenanceInstallError(
                        f"rollback managed directory is unsafe: {absolute}"
                    )
            else:
                details = self._assert_safe_directory(
                    path, f"rollback managed directory {absolute}"
                )
            actual = self._snapshot(absolute)
            after = directories_after.get(absolute)
            expected_identity = after if isinstance(after, Mapping) else before
            # /run tmpfiles are intentionally recreated on reboot.  Their
            # inode is not stable, but their type, owner and fixed mode are.
            if (
                os.name == "posix"
                and not absolute.startswith("/run/")
                and expected_identity.get("exists")
                and (
                    actual.get("device") != expected_identity.get("device")
                    or actual.get("inode") != expected_identity.get("inode")
                )
            ):
                raise MaintenanceInstallError(
                    f"rollback managed directory identity changed: {absolute}"
                )
            if absolute in TMPFILES_DIRECTORY_MODES:
                assert runtime_owners is not None
                expected_uid, expected_gids = runtime_owners[absolute]
                if (
                    actual.get("uid") != expected_uid
                    or actual.get("gid") not in expected_gids
                ):
                    raise MaintenanceInstallError(
                        f"rollback managed directory ownership changed: {absolute}"
                    )
            expected_mode = TMPFILES_DIRECTORY_MODES.get(absolute)
            if absolute in code_set:
                expected_mode = 0o755
            if expected_mode is not None:
                actual_mode = self._metadata_mode(path, details)
                mode_is_expected = actual_mode == expected_mode
                mode_is_original = (
                    before.get("exists")
                    and (removal_started or not installation_committed)
                    and actual_mode == before.get("mode")
                )
                mode_is_interrupted_creation = (
                    not installation_committed
                    and not before.get("exists")
                    and not isinstance(after, Mapping)
                    and actual_mode & 0o700 == 0o700
                    and actual_mode & ~expected_mode == 0
                )
                if not (
                    mode_is_expected
                    or mode_is_original
                    or mode_is_interrupted_creation
                ):
                    raise MaintenanceInstallError(
                        f"rollback managed directory permissions changed: {absolute}"
                    )

    def _validate_rollback_removal_plan(self, record: Mapping[str, Any]) -> None:
        """Prove the complete removal set without changing services or files."""

        expected_marker = _canonical_json(self._marker_document(record))
        marker_count = 0
        for absolute in (ACTIVE_MARKER, PENDING_MARKER):
            marker_path = self._path(absolute)
            if not _lexists(marker_path):
                continue
            marker_count += 1
            self._assert_maintenance_state_file(
                marker_path, f"rollback marker {absolute}"
            )
            if marker_path.read_bytes() != expected_marker:
                raise MaintenanceInstallError(
                    f"rollback refuses a changed maintenance marker: {absolute}"
                )
        if marker_count == 0:
            raise MaintenanceInstallError(
                "rollback has no durable locating marker"
            )

        artifact_intents_value = record.get("artifactIntents")
        if not isinstance(artifact_intents_value, list):
            raise MaintenanceInstallError(
                "artifact publication journal is absent"
            )
        if record.get("status") == "INSTALLED" and not artifact_intents_value:
            raise MaintenanceInstallError(
                "artifact publication journal is absent"
            )
        if any(not isinstance(item, Mapping) for item in artifact_intents_value):
            raise MaintenanceInstallError(
                "artifact publication journal is malformed"
            )
        artifact_intents = list(artifact_intents_value)
        operation_id = str(record.get("operationId", ""))
        _safe_id(operation_id, description="operation ID")
        paths = [item.get("path") for item in artifact_intents]
        temporary_paths = [
            item.get("temporaryPath")
            for item in artifact_intents
            if item.get("kind") == "regular"
        ]
        if (
            any(not isinstance(path, str) for path in paths)
            or len(paths) != len(set(paths))
            or any(not isinstance(path, str) for path in temporary_paths)
            or len(temporary_paths) != len(set(temporary_paths))
            or set(paths) & set(temporary_paths)
        ):
            raise MaintenanceInstallError(
                "artifact publication journal contains duplicate paths"
            )

        require_done = record.get("status") == "INSTALLED"
        allow_removed = isinstance(record.get("rollbackRemovalStartedAt"), str)
        for item in artifact_intents:
            self._assert_artifact_safe_for_removal(
                item,
                operation_id=operation_id,
                require_done=require_done,
                allow_removed=allow_removed,
            )

        self._base_directory_checks()
        self._assert_rollback_directories_safe(record)
        self._assert_new_code_trees_contain_only_planned_entries(
            record, artifact_intents
        )
        for absolute in TMPFILES_REGULAR_PATHS:
            before = record.get("pathsBefore", {}).get(
                absolute, {"exists": False}
            )
            if not isinstance(before, Mapping):
                raise MaintenanceInstallError(
                    "rollback path journal is malformed"
                )
            path = self._path(absolute)
            if before.get("exists") or not _lexists(path):
                continue
            details = self._assert_safe_regular(
                path, f"rollback tmpfiles artifact {absolute}"
            )
            uid, gid = self._metadata_ids(path, details)
            if (
                details.st_size != 0
                or self._metadata_mode(path, details) != 0o600
                or uid != 0
                or gid != 0
            ):
                raise MaintenanceInstallError(
                    f"rollback refuses changed tmpfiles artifact: {absolute}"
                )

        # This filesystem proof is intentionally made before /etc fragments
        # disappear: systemctl's current FragmentPath cannot reveal a shadowed
        # lower-priority unit which would take over after daemon-reload.
        self._assert_no_managed_unit_fallback_files()
        rollback_fence_state = self._rollback_unit_fence_state(record)
        runtime_directory_entries: set[str] = set()
        for item in artifact_intents:
            if item.get("path") != RUNTIME_DROP_IN_PATH:
                continue
            if _lexists(self._path(RUNTIME_DROP_IN_PATH)):
                runtime_directory_entries.add(
                    PurePosixPath(RUNTIME_DROP_IN_PATH).name
                )
            temporary = item.get("temporaryPath")
            if isinstance(temporary, str) and _lexists(self._path(temporary)):
                runtime_directory_entries.add(PurePosixPath(temporary).name)
        self._assert_dropin_filesystem(
            installed=True,
            rollback_fenced=rollback_fence_state == "ACTIVE",
            rollback_fence_may_be_partial=rollback_fence_state
            in {"PREPARED", "REMOVING"},
            expected_runtime_directory_entries=frozenset(
                runtime_directory_entries
            ),
        )
        runtime_drop_ins = self._reported_drop_in_paths(
            self._systemctl_show(RUNTIME_TARGET)
        )
        runtime_drop_in_exists = _lexists(self._path(RUNTIME_DROP_IN_PATH))
        commands_completed = record.get("commandsCompleted")
        reload_completed = isinstance(commands_completed, list) and (
            "systemctl daemon-reload" in commands_completed
        )
        allowed_runtime_drop_ins = (
            {(RUNTIME_DROP_IN_PATH,)}
            if runtime_drop_in_exists and reload_completed
            else (
                {(), (RUNTIME_DROP_IN_PATH,)}
                if runtime_drop_in_exists or allow_removed
                else {()}
            )
        )
        if runtime_drop_ins not in allowed_runtime_drop_ins:
            raise MaintenanceInstallError(
                "runtime target drop-in report changed before rollback"
            )

    @staticmethod
    def _updater_start_was_attempted(record: Mapping[str, Any]) -> bool:
        activation = record.get("activation")
        if activation is not None:
            if (
                not isinstance(activation, Mapping)
                or activation.get("schemaVersion") != 1
                or activation.get("state")
                not in {"NOT_STARTED", "PREPARED", "STARTING", "VERIFIED"}
                or activation.get("intendedUnits") != list(START_UNITS)
            ):
                raise MaintenanceInstallError("activation journal is malformed")
            return activation.get("state") in {"STARTING", "VERIFIED"}
        intents = record.get("unitStartIntents")
        if intents is None:
            # Records produced before the durable-intent field are only
            # considered unattempted while they are still before daemon-reload.
            # After that boundary a crash could have occurred around systemctl
            # start, so absence is ambiguous and must fail closed.
            commands = record.get("commandsCompleted")
            if not isinstance(commands, list) or any(
                not isinstance(command, str) for command in commands
            ):
                raise MaintenanceInstallError(
                    "maintenance command journal is malformed"
                )
            return (
                record.get("status") == "INSTALLED"
                or "systemctl daemon-reload" in commands
                or f"systemctl start {MAIN_UNIT_FILES[1]}" in commands
            )
        if not isinstance(intents, list):
            raise MaintenanceInstallError("unit start intent journal is malformed")
        units: list[str] = []
        for intent in intents:
            if (
                not isinstance(intent, dict)
                or not isinstance(intent.get("unit"), str)
                or not isinstance(intent.get("recordedAt"), str)
                or intent["unit"] not in START_UNITS
            ):
                raise MaintenanceInstallError("unit start intent journal is malformed")
            units.append(intent["unit"])
        if len(units) != len(set(units)):
            raise MaintenanceInstallError("unit start intent journal is malformed")
        return record.get("status") == "INSTALLED" or MAIN_UNIT_FILES[1] in units

    def _assert_updater_state_file(
        self,
        path: Path,
        *,
        description: str,
        database: bool,
        expected_uid: int,
        expected_gids: frozenset[int],
    ) -> dict[str, int]:
        try:
            details = path.lstat()
        except OSError as exc:
            raise MaintenanceInstallError(f"{description} is unavailable") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or (os.name == "posix" and details.st_nlink != 1)
        ):
            raise MaintenanceInstallError(
                f"{description} is not a safe regular file"
            )
        uid, gid = self._metadata_ids(path, details)
        mode = self._metadata_mode(path, details)
        if uid != expected_uid or gid not in expected_gids:
            raise MaintenanceInstallError(f"{description} ownership differs")
        if database:
            if mode != 0o600 or details.st_size < 1:
                raise MaintenanceInstallError(
                    f"{description} permissions or size differ"
                )
        elif mode not in (0o600, 0o640, 0o660):
            raise MaintenanceInstallError(f"{description} permissions differ")
        if details.st_size > MAX_UPDATER_STATE_FILE_BYTES:
            raise MaintenanceInstallError(f"{description} is unexpectedly large")
        return {
            "device": details.st_dev,
            "inode": details.st_ino,
            "size": details.st_size,
            "mode": mode,
            "uid": uid,
            "gid": gid,
        }

    def _inspect_updater_rollback_eligibility(
        self,
        record: Mapping[str, Any],
        *,
        phase: str,
        previous: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if phase not in {"DRY_RUN_METADATA", "PRE_FENCE", "POST_STOP"}:
            raise ValueError("unknown updater rollback inspection phase")
        inspect_logical_state = phase == "POST_STOP"
        database_path = self._path(UPDATER_STATE_DATABASE_PATH)
        sidecar_paths = tuple(
            Path(f"{database_path}{suffix}")
            for suffix in UPDATER_STATE_SIDECAR_SUFFIXES
        )
        database_exists = _lexists(database_path)
        existing_sidecars = tuple(path for path in sidecar_paths if _lexists(path))
        attempted = self._updater_start_was_attempted(record)

        if not database_exists:
            if existing_sidecars:
                raise MaintenanceInstallError(
                    "updater rollback found state sidecars without the main database"
                )
            if not attempted:
                result: dict[str, Any] = {"kind": "ABSENT_UNATTEMPTED"}
            else:
                raise MaintenanceInstallError(
                    "updater rollback database is absent after updater start could have occurred"
                )
            if previous is not None and previous.get("kind") in {
                "DATABASE",
                "DATABASE_METADATA_ONLY",
            }:
                raise MaintenanceInstallError(
                    "updater rollback database disappeared during quiesce"
                )
            return result

        identities = self._identity_installation_state(require_present=True)
        assert identities is not None
        expected_uid = int(identities["users"]["ecobin-updater"])
        expected_gids = frozenset(
            {
                int(identities["primaryGroups"]["ecobin-updater"]),
                int(identities["groups"]["ecobin-updater-ipc"]),
            }
        )
        parent = self._snapshot("/var/lib/ecobin/updater")
        if (
            parent.get("kind") != "directory"
            or parent.get("mode") != 0o700
            or parent.get("uid") != expected_uid
            or parent.get("gid") not in expected_gids
        ):
            raise MaintenanceInstallError(
                "updater rollback database parent permissions differ"
            )
        identity_before = self._assert_updater_state_file(
            database_path,
            description="updater rollback database",
            database=True,
            expected_uid=expected_uid,
            expected_gids=expected_gids,
        )
        for path in existing_sidecars:
            self._assert_updater_state_file(
                path,
                description=f"updater rollback state sidecar {path.name}",
                database=False,
                expected_uid=expected_uid,
                expected_gids=expected_gids,
            )
        inspection: dict[str, Any] | None = None
        if inspect_logical_state:
            # Root must never open the live updater database.  Even a SQLite
            # mode=ro connection can create WAL coordination files if the
            # updater checkpoints between sidecar discovery and connect().
            # This branch is therefore reachable only after the persistent
            # start fence is active and every updater ingress is inactive.
            try:
                inspection = inspect_pristine_stage3_rollback_state(database_path)
            except PristineRollbackStateUsed as exc:
                raise RollbackRefusedForUsedState(
                    f"updater rollback is not permitted: {exc}"
                ) from exc
            except (OSError, RuntimeError) as exc:
                raise MaintenanceInstallError(
                    f"updater rollback eligibility check failed: {exc}"
                ) from exc

        identity_after = self._assert_updater_state_file(
            database_path,
            description="updater rollback database",
            database=True,
            expected_uid=expected_uid,
            expected_gids=expected_gids,
        )
        if identity_after != identity_before:
            raise MaintenanceInstallError(
                "updater rollback database changed during inspection"
            )
        current_sidecars = tuple(
            path for path in sidecar_paths if _lexists(path)
        )
        for path in current_sidecars:
            self._assert_updater_state_file(
                path,
                description=f"updater rollback state sidecar {path.name}",
                database=False,
                expected_uid=expected_uid,
                expected_gids=expected_gids,
            )
        result = {
            "kind": (
                "DATABASE" if inspect_logical_state else "DATABASE_METADATA_ONLY"
            ),
            "databaseIdentity": identity_after,
        }
        if inspection is not None:
            result["inspection"] = inspection
        if previous is not None:
            previous_kind = previous.get("kind")
            if previous_kind in {"DATABASE", "DATABASE_METADATA_ONLY"}:
                previous_identity = previous.get("databaseIdentity")
                if not isinstance(previous_identity, Mapping) or any(
                    previous_identity.get(field) != identity_after[field]
                    for field in ("device", "inode")
                ):
                    raise MaintenanceInstallError(
                        "updater rollback database was replaced during quiesce"
                    )
            if previous_kind == "ABSENT_UNATTEMPTED":
                raise MaintenanceInstallError(
                    "updater rollback database appeared after an unattempted start"
                )
        return result

    def _assert_managed_unit_fence_contract(
        self,
        *,
        allow_absent: bool,
        rollback_fenced: bool = False,
    ) -> None:
        self._assert_no_managed_unit_fallback_files()
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            unit_path = self._path(f"/etc/systemd/system/{unit}")
            state = self._systemctl_show(unit)
            expected_drop_ins = (
                (
                    f"/etc/systemd/system/{unit}.d/"
                    f"{ROLLBACK_UNIT_FENCE_DROP_IN_NAME}",
                )
                if rollback_fenced
                else ()
            )
            if not _lexists(unit_path):
                if not allow_absent or (
                    state.get("loadState") != "not-found"
                    or state.get("activeState") != "inactive"
                    or state.get("fragmentPath")
                    or self._reported_drop_in_paths(state) != expected_drop_ins
                ):
                    raise MaintenanceInstallError(
                        f"managed fenced unit is absent or stale: {unit}"
                    )
                continue
            details = self._assert_safe_regular(
                unit_path,
                f"managed fenced unit {unit}",
            )
            if self._metadata_mode(unit_path, details) != 0o644:
                raise MaintenanceInstallError(
                    f"managed fenced unit permissions differ: {unit}"
                )
            _validate_runtime_fence_unit_bytes(unit_path.read_bytes(), unit)
            if (
                state.get("loadState") != "loaded"
                or state.get("fragmentPath") != f"/etc/systemd/system/{unit}"
                or self._reported_drop_in_paths(state) != expected_drop_ins
            ):
                raise MaintenanceInstallError(
                    f"systemd did not load the exact fenced unit: {unit}"
                )

    def _assert_no_managed_unit_fallback_files(self) -> None:
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            controlled = f"/etc/systemd/system/{unit}"
            for base in SYSTEMD_UNIT_FRAGMENT_BASES:
                candidate = f"{base}/{unit}"
                if candidate == controlled:
                    continue
                if _lexists(self._path(candidate)):
                    raise MaintenanceInstallError(
                        "managed unit has an uncontrolled fallback definition: "
                        f"{candidate}"
                    )

    def _assert_rollback_quiesced(self, record: Mapping[str, Any]) -> None:
        self._assert_runtime_start_fence(record)
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            if self._systemctl_show(unit).get("activeState") != "inactive":
                raise MaintenanceInstallError(
                    f"managed unit restarted during rollback: {unit}"
                )
        for unit in self._helper_instances():
            if self._systemctl_show(unit).get("activeState") != "inactive":
                raise MaintenanceInstallError(
                    f"helper instance restarted during rollback: {unit}"
                )

    def _assert_legacy_fragment_protected(
        self,
        record: Mapping[str, Any],
    ) -> str:
        services = record.get("servicesBefore")
        before = (
            services.get(str(record.get("legacyService")))
            if isinstance(services, Mapping)
            else None
        )
        fragment = before.get("fragmentPath") if isinstance(before, Mapping) else None
        protected = record.get("protectedBefore")
        if (
            not isinstance(fragment, str)
            or not fragment.startswith("/")
            or not isinstance(protected, Mapping)
            or fragment not in protected
            or not isinstance(protected[fragment], Mapping)
            or protected[fragment].get("kind") != "regular"
        ):
            raise MaintenanceInstallError(
                "legacy service fragment lacks an installation-time snapshot"
            )
        return fragment

    def _assert_legacy_restore_ready(self, record: Mapping[str, Any]) -> None:
        fragment = self._assert_legacy_fragment_protected(record)
        legacy_service = str(record["legacyService"])
        legacy_state = self._systemctl_show(legacy_service)
        if (
            legacy_state.get("loadState") != "loaded"
            or legacy_state.get("fragmentPath") != fragment
            or self._reported_drop_in_paths(legacy_state)
            != (LEGACY_GATE_DROP_IN_PATH,)
        ):
            raise MaintenanceInstallError(
                "legacy service definition changed before restore"
            )
        self._assert_legacy_gate_drop_in(
            self._path(str(PurePosixPath(LEGACY_GATE_DROP_IN_PATH).parent))
        )
        self._assert_legacy_fragment_protected(record)
        self._assert_protected_paths_unchanged(record)
        self._assert_hardware_current_unchanged(record)

    def _rollback_service_states_before_stop(
        self,
        record: dict[str, Any],
        *,
        create: bool = True,
    ) -> Mapping[str, Mapping[str, Any]]:
        journal = record.get("rollbackServiceStatesBeforeStop")
        if journal is None and not create:
            raise MaintenanceInstallError(
                "rollback service-state journal is absent"
            )
        if journal is None:
            states = {
                unit: self._systemctl_show(unit)
                for unit in ROLLBACK_MANAGED_UNITS
            }
            transitional = sorted(
                unit
                for unit, state in states.items()
                if state.get("activeState") not in {"active", "inactive", "failed"}
            )
            if transitional:
                # Never durably record a transient systemd state as a restore
                # target.  No service has been stopped at this point, so the
                # caller can release its newly-created fence and retry later.
                raise RollbackDeferredForBusyState(
                    "rollback must wait for stable service state: "
                    + ", ".join(transitional)
                )
            unhealthy_installed = sorted(
                unit
                for unit in START_UNITS
                if record.get("status") == "INSTALLED"
                and states[unit].get("activeState") != "active"
            )
            if unhealthy_installed:
                # Do not stop healthy peers when the installed baseline is
                # already degraded.  Exact subset restoration cannot be made
                # crash-safe without persistent per-unit masks, while starting
                # the degraded unit would change state the operator did not
                # authorize.  No service has been touched yet, so defer.
                raise RollbackDeferredForBusyState(
                    "rollback requires every permanent service to be healthy "
                    "before quiesce: " + ", ".join(unhealthy_installed)
                )
            journal = {
                "capturedAt": _utc_now(),
                "states": states,
            }
            record["rollbackServiceStatesBeforeStop"] = journal
            self._persist_record(record)
        states = journal.get("states") if isinstance(journal, Mapping) else None
        if (
            not isinstance(journal, Mapping)
            or not isinstance(journal.get("capturedAt"), str)
            or not isinstance(states, Mapping)
            or set(states) != set(ROLLBACK_MANAGED_UNITS)
            or any(
                not isinstance(states[unit], Mapping)
                or states[unit].get("activeState")
                not in {"active", "inactive", "failed"}
                for unit in ROLLBACK_MANAGED_UNITS
            )
        ):
            raise MaintenanceInstallError(
                "rollback service-state journal is malformed"
            )
        return states  # type: ignore[return-value]

    def _defer_rollback_before_any_stop(
        self,
        record: dict[str, Any],
        *,
        automatic: bool,
        reason: str,
    ) -> None:
        if record.get("rollbackServiceStatesBeforeStop") is not None:
            raise MaintenanceInstallError(
                "cannot defer rollback after a stop-state journal was committed"
            )
        previous_status = str(record.get("status"))
        try:
            self._release_runtime_start_fence(record)
            record["status"] = previous_status
            record["lastRollbackDeferral"] = {
                "deferredAt": _utc_now(),
                "automatic": automatic,
                "reason": reason,
                "servicesStopped": False,
            }
            self._persist_record(record)
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(reason, f"cannot release rollback fence: {exc}"),
                retained=(
                    RUNTIME_START_FENCE,
                    "/opt/ecobin/updater",
                    UPDATER_STATE_DATABASE_PATH,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )
        raise RollbackDeferredForBusyState(reason)

    def _refuse_rollback_and_restore_services(
        self,
        record: dict[str, Any],
        *,
        automatic: bool,
        reason: str,
    ) -> None:
        if isinstance(record.get("rollbackRemovalStartedAt"), str):
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(
                    reason,
                    "rollback cannot restore an installation after artifact removal began",
                ),
                retained=(
                    RUNTIME_START_FENCE,
                    "/opt/ecobin/updater",
                    UPDATER_STATE_DATABASE_PATH,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )
        states = self._rollback_service_states_before_stop(record, create=False)
        restore_journal = record.get("rollbackRefusalRestore")
        if restore_journal is None:
            restore_journal = {
                "schemaVersion": 1,
                "resumeStatus": (
                    "INSTALLED"
                    if record.get("status") == "INSTALLED"
                    else "ROLLBACK_REFUSED"
                ),
                "restoreUnits": [
                    unit
                    for unit in ROLLBACK_MANAGED_UNITS
                    if states[unit].get("activeState") == "active"
                ],
                "restoredUnits": [],
                "state": "PREPARED",
                "preparedAt": _utc_now(),
            }
            record["rollbackRefusalRestore"] = restore_journal
        if (
            not isinstance(restore_journal, dict)
            or restore_journal.get("schemaVersion") != 1
            or restore_journal.get("resumeStatus")
            not in {"INSTALLED", "ROLLBACK_REFUSED"}
            or not isinstance(restore_journal.get("restoreUnits"), list)
            or not isinstance(restore_journal.get("restoredUnits"), list)
            or restore_journal.get("state")
            not in {"PREPARED", "FENCE_RELEASED", "RESTORING"}
            or any(
                unit not in ROLLBACK_MANAGED_UNITS
                for unit in (
                    *restore_journal["restoreUnits"],
                    *restore_journal["restoredUnits"],
                )
            )
            or len(set(restore_journal["restoreUnits"]))
            != len(restore_journal["restoreUnits"])
        ):
            raise MaintenanceInstallError(
                "rollback refusal restore journal is malformed"
            )
        restore_units = list(restore_journal["restoreUnits"])
        missing_healthy_units = sorted(set(START_UNITS) - set(restore_units))
        if missing_healthy_units:
            # Releasing the global fence and restoring only a mixed historical
            # subset is not reboot-safe: runtime.target could start the
            # originally inactive units after a crash but before this process
            # finishes its exact restoration.  Keep every start gate closed
            # and require an operator to resolve the abnormal baseline.
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(
                    reason,
                    "cannot safely restore a non-healthy managed-service "
                    "baseline: " + ", ".join(missing_healthy_units),
                ),
                retained=(
                    RUNTIME_START_FENCE,
                    "/opt/ecobin/updater",
                    UPDATER_STATE_DATABASE_PATH,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )
        record["status"] = "ROLLBACK_REFUSED_RESTORING"
        record["rollback"] = {
            "attemptedAt": _utc_now(),
            "automatic": automatic,
            "errors": [reason],
            "restoreUnitIntents": restore_units,
            "retainedPaths": [
                "/opt/ecobin/updater",
                UPDATER_STATE_DATABASE_PATH,
                ACTIVE_MARKER,
                PENDING_MARKER,
            ],
        }
        # Restore intent is durable before the fence is released.  A crash at
        # any later point leaves a marker that can re-establish the fence and
        # retry from the same exact unit list.
        self._persist_record(record)
        try:
            self._reconcile_operation_singleton_incomings(record)
            self._assert_mcu_safety_gate()
            rollback_fence_state = self._rollback_unit_fence_state(record)
            if rollback_fence_state in {"PREPARED", "REMOVING"}:
                self._establish_rollback_unit_fences(record)
                self._run(("systemctl", "daemon-reload"))
                rollback_fence_state = "ACTIVE"
            self._assert_managed_unit_fence_contract(
                allow_absent=False,
                rollback_fenced=rollback_fence_state == "ACTIVE",
            )
            if rollback_fence_state == "ACTIVE":
                self._remove_rollback_unit_fences(record, installed=True)
                self._run(("systemctl", "daemon-reload"))
                self._assert_managed_unit_fence_contract(allow_absent=False)
            self._release_runtime_start_fence(record)
            restore_journal["state"] = "FENCE_RELEASED"
            restore_journal["fenceReleasedAt"] = _utc_now()
            self._persist_record(record)
            for unit in restore_units:
                restore_journal["state"] = "RESTORING"
                result = self._run(("systemctl", "start", unit), check=False)
                if result.returncode != 0:
                    raise MaintenanceInstallError(
                        f"cannot restore managed unit after rollback refusal: {unit}"
                    )
                if self._systemctl_show(unit).get("activeState") != "active":
                    raise MaintenanceInstallError(
                        f"managed unit did not recover after rollback refusal: {unit}"
                    )
                if unit not in restore_journal["restoredUnits"]:
                    restore_journal["restoredUnits"].append(unit)
                self._persist_record(record)
        except MaintenanceInstallError as restore_error:
            recovery_errors = [reason, str(restore_error)]
            try:
                self._establish_runtime_start_fence(record)
                self._run(("systemctl", "daemon-reload"))
                for unit in reversed(ROLLBACK_MANAGED_UNITS):
                    if self._systemctl_show(unit).get("activeState") == "active":
                        result = self._run(
                            ("systemctl", "stop", unit),
                            check=False,
                        )
                        if result.returncode != 0:
                            recovery_errors.append(
                                f"cannot re-quiesce managed unit: {unit}"
                            )
            except MaintenanceInstallError as fence_error:
                recovery_errors.append(str(fence_error))
            self._block_rollback(
                record,
                automatic=automatic,
                errors=recovery_errors,
                retained=(
                    RUNTIME_START_FENCE,
                    "/opt/ecobin/updater",
                    UPDATER_STATE_DATABASE_PATH,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        refusal = {
            "refusedAt": _utc_now(),
            "automatic": automatic,
            "errors": [reason],
            "restoredUnits": restore_units,
            "retainedPaths": [
                "/opt/ecobin/updater",
                UPDATER_STATE_DATABASE_PATH,
                ACTIVE_MARKER,
                PENDING_MARKER,
            ],
        }
        record["status"] = str(restore_journal["resumeStatus"])
        record["rollback"] = refusal
        record["lastRollbackRefusal"] = refusal
        record.pop("rollbackServiceStatesBeforeStop", None)
        record.pop("rollbackRefusalRestore", None)
        record.pop("rollbackUnitFences", None)
        self._persist_record(record)
        if (
            record["status"] == "INSTALLED"
            and _lexists(self._path(PENDING_MARKER))
        ):
            # A crash may have occurred after the durable INSTALLED commit but
            # before marker promotion.  Since rollback was refused, retain the
            # new layer only after the same complete audit used by install.
            self.audit(
                expected_image_release_id=str(record["image"]["releaseId"]),
                expected_image_version=str(record["image"]["version"]),
                expected_legacy_service=str(record["legacyService"]),
            )
        raise RollbackRefusedForUsedState(reason)

    def _block_rollback(
        self,
        record: dict[str, Any],
        *,
        automatic: bool,
        errors: Sequence[str],
        retained: Sequence[str],
    ) -> None:
        retained_with_fence = list(retained)
        if _lexists(self._path(RUNTIME_START_FENCE)):
            retained_with_fence.append(RUNTIME_START_FENCE)
        record["status"] = "ROLLBACK_BLOCKED"
        record["rollback"] = {
            "attemptedAt": _utc_now(),
            "automatic": automatic,
            "errors": list(errors),
            "retainedPaths": sorted(set(retained_with_fence)),
        }
        self._persist_record(record)
        raise MaintenanceInstallError("; ".join(errors))

    def _finalize_rolled_back_record(
        self, record: dict[str, Any], *, apply: bool
    ) -> dict[str, Any]:
        rollback = record.get("rollback")
        fence = self._runtime_fence_journal(record, create=False)
        unit_fences = self._rollback_unit_fence_journal(record, create=False)
        if (
            record.get("status") != "ROLLED_BACK"
            or not isinstance(rollback, Mapping)
            or not isinstance(rollback.get("completedAt"), str)
            or rollback.get("errors") != []
            or fence is None
            or fence.get("state") != "RELEASED"
            or _lexists(self._path(RUNTIME_START_FENCE))
            or unit_fences is None
            or unit_fences.get("state") != "REMOVED"
        ):
            raise MaintenanceInstallError(
                "completed rollback journal is inconsistent"
            )
        self._assert_protected_paths_unchanged(record)
        self._assert_hardware_current_unchanged(record)
        artifact_intents = record.get("artifactIntents")
        if not isinstance(artifact_intents, list):
            raise MaintenanceInstallError("artifact publication journal is absent")
        for item in artifact_intents:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                raise MaintenanceInstallError(
                    "artifact publication journal is malformed"
                )
            if _lexists(self._path(str(item["path"]))):
                raise MaintenanceInstallError(
                    f"rolled-back artifact unexpectedly exists: {item['path']}"
                )
            if item.get("kind") == "regular":
                temporary = item.get("temporaryPath")
                if not isinstance(temporary, str) or _lexists(self._path(temporary)):
                    raise MaintenanceInstallError(
                        "rolled-back artifact incoming file unexpectedly exists"
                    )
        for absolute in self._rollback_unit_fence_paths():
            path = self._path(absolute)
            if _lexists(path):
                raise MaintenanceInstallError(
                    f"rolled-back unit fence unexpectedly exists: {absolute}"
                )
        self._assert_mcu_safety_gate()
        self._assert_legacy_restore_ready(record)
        legacy_before = record["servicesBefore"].get(
            record["legacyService"], {}
        )
        legacy_state = self._systemctl_show(str(record["legacyService"]))
        if (
            legacy_before.get("activeState") == "active"
            and legacy_state.get("activeState") != "active"
        ):
            raise MaintenanceInstallError(
                "rolled-back legacy service is not active"
            )
        self._assert_dropin_filesystem(installed=False)
        self._assert_new_units_absent()
        # A dry run must prove that every interrupted fixed-name write belongs
        # to this operation.  Applying the same preview may then only delete
        # bytes that were already authenticated here.
        self._reconcile_operation_singleton_incomings(record, remove=False)
        expected_marker = self._marker_document(record)
        marker_paths: list[Path] = []
        for absolute in (ACTIVE_MARKER, PENDING_MARKER):
            path = self._path(absolute)
            if not _lexists(path):
                continue
            self._assert_maintenance_state_file(
                path, "rolled-back maintenance marker"
            )
            marker = _load_json_bytes(
                path.read_bytes(), description="rolled-back maintenance marker"
            )
            if marker != expected_marker:
                raise MaintenanceInstallError(
                    "rollback refuses a changed maintenance marker"
                )
            marker_paths.append(path)
        result = {
            "status": "ROLLED_BACK",
            "operationId": record["operationId"],
            "retainedPaths": list(rollback.get("retainedPaths", [])),
            "accountsRetainedInert": list(ACCOUNT_NAMES),
        }
        if not apply:
            return {**result, "dryRun": True, "markerCleanupRequired": True}
        self._reconcile_operation_singleton_incomings(record)
        for path in marker_paths:
            self._assert_maintenance_state_file(
                path, "rolled-back maintenance marker"
            )
            marker = _load_json_bytes(
                path.read_bytes(), description="rolled-back maintenance marker"
            )
            if marker != expected_marker:
                raise MaintenanceInstallError(
                    "rollback refuses a changed maintenance marker"
                )
            path.unlink()
            self._fsync_directory(path.parent)
        return result

    def _rollback_record(
        self, record: dict[str, Any], *, automatic: bool = False
    ) -> dict[str, Any]:
        errors: list[str] = []
        retained: list[str] = []

        activation_before_rollback = self._activation_journal(
            record, create=False
        )
        if self._activation_forbids_destructive_rollback(
            record, activation_before_rollback
        ):
            # This is an immutable may-have-run fact.  Do not rewrite it into
            # ROLLBACK_BLOCKED: the identical authenticated install must remain
            # able to converge forward after an accidental rollback request.
            raise MaintenanceInstallError(
                "an interrupted installation crossed the service-start "
                "boundary and must be resumed forward"
            )

        try:
            self._assert_mcu_safety_gate()
            self._assert_legacy_fragment_protected(record)
            self._establish_runtime_start_fence(record)
            reload_result = self._run(
                ("systemctl", "daemon-reload"), check=False
            )
            if reload_result.returncode != 0:
                raise MaintenanceInstallError(
                    "cannot load the persistent runtime start fence"
                )
            setup_intents = self._setup_command_intents(record, create=False)
            if isinstance(record.get("rollbackRemovalStartedAt"), str):
                # Configuration files may already have been deleted by the
                # authenticated removal loop.  At that point setup must have
                # been fully verified before deletion; never try to replay a
                # command from a now-absent configuration file.
                if any(intent["state"] != "DONE" for intent in setup_intents):
                    raise MaintenanceInstallError(
                        "rollback removal began before setup effects were verified"
                    )
            else:
                # A power cut can leave declarative host utilities between
                # their first and last side effect.  PREPARED authorizes an
                # exact idempotent replay only while its authenticated config
                # artifact has first been re-verified.
                self._base_directory_checks()
                self._assert_setup_configuration_artifacts(record)
                self._reconcile_setup_commands_for_recovery(record)
            self._validate_rollback_removal_plan(record)
            updater_state_before = self._inspect_updater_rollback_eligibility(
                record,
                phase="PRE_FENCE",
            )
            rollback_fence_state = self._rollback_unit_fence_state(record)
            if rollback_fence_state in {"PREPARED", "REMOVING", "REMOVED"}:
                # Repair every persistent per-unit guard on disk before the
                # first daemon-reload of a recovery attempt.  Otherwise a
                # partially removed guard set could briefly expose a hidden
                # fallback unit to the still-running first-boot coordinator.
                self._establish_rollback_unit_fences(record)
                rollback_fence_state = "ACTIVE"
            reload_result = self._run(
                ("systemctl", "daemon-reload"),
                check=False,
            )
            if reload_result.returncode != 0:
                raise MaintenanceInstallError(
                    "cannot load the persistent runtime start fence"
                )
            self._assert_managed_unit_fence_contract(
                allow_absent=True,
                rollback_fenced=rollback_fence_state == "ACTIVE",
            )
            if record.get("rollbackRefusalRestore") is not None:
                rollback = record.get("rollback")
                rollback_errors = (
                    rollback.get("errors")
                    if isinstance(rollback, Mapping)
                    else None
                )
                resume_reason = (
                    str(rollback_errors[0])
                    if isinstance(rollback_errors, list) and rollback_errors
                    else "resume interrupted rollback refusal"
                )
                self._refuse_rollback_and_restore_services(
                    record,
                    automatic=automatic,
                    reason=resume_reason,
                )
            self._rollback_service_states_before_stop(record)
        except RollbackDeferredForBusyState as exc:
            self._defer_rollback_before_any_stop(
                record,
                automatic=automatic,
                reason=str(exc),
            )
        except RollbackRefusedForUsedState as exc:
            if record.get("rollbackServiceStatesBeforeStop") is None:
                # A resumed refusal has already converged and deliberately
                # raises its public outcome.  Do not interpret that outcome as
                # a second request to restore from a journal just removed.
                raise
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason=str(exc),
            )
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(str(exc),),
                retained=(
                    UPDATER_STATE_DATABASE_PATH,
                    "/opt/ecobin/updater",
                    RUNTIME_START_FENCE,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        # Code must never be removed while any newly introduced process can
        # still execute it.  The persistent unit-level fence is already
        # durable and loaded before any member is stopped.  The first-boot
        # coordinator and the legacy runtime stay available, but every
        # maintenance-owned unit rejects their repeated start attempts at the
        # systemd job boundary.  The same fence survives a crash/reboot.
        sockets = tuple(unit for unit in START_UNITS if unit.endswith(".socket"))
        ordinary_units = tuple(
            unit
            for unit in reversed(
                (*START_UNITS, "ecobin-business-permission-preflight.service")
            )
            if not unit.endswith(".socket")
        )

        def stop_and_verify(units: Iterable[str]) -> None:
            for unit in units:
                try:
                    current = self._systemctl_show(unit)
                    if current.get("activeState") != "inactive":
                        result = self._run(("systemctl", "stop", unit), check=False)
                        if result.returncode != 0:
                            errors.append(f"cannot stop {unit}")
                    rechecked = self._systemctl_show(unit)
                    if rechecked.get("activeState") != "inactive":
                        errors.append(f"unit did not become inactive: {unit}")
                except MaintenanceInstallError as exc:
                    errors.append(f"cannot verify stopped unit {unit}: {exc}")

        # Close the listening sockets before enumerating accepted helper
        # instances; otherwise a connection could create a new instance
        # between enumeration and socket shutdown.
        stop_and_verify(sockets)
        if errors:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason="; ".join(errors),
            )
        try:
            helper_instances = self._helper_instances()
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
            helper_instances = ()
        busy_helpers: list[str] = []
        failed_helpers: list[str] = []
        for unit in helper_instances:
            try:
                state = self._systemctl_show(unit).get("activeState")
                if state == "failed":
                    failed_helpers.append(unit)
                elif state != "inactive":
                    busy_helpers.append(f"{unit} ({state or 'unknown'})")
            except MaintenanceInstallError as exc:
                errors.append(f"cannot inspect helper instance {unit}: {exc}")
        if busy_helpers:
            errors.append(
                "privileged operation is still running; rollback must be retried: "
                + ", ".join(busy_helpers)
            )
        if errors:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason="; ".join(errors),
            )
        for unit in failed_helpers:
            result = self._run(("systemctl", "reset-failed", unit), check=False)
            if result.returncode != 0 or self._systemctl_show(unit).get(
                "activeState"
            ) != "inactive":
                errors.append(f"cannot reset failed helper instance: {unit}")
        if errors:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason="; ".join(errors),
            )

        # Once listener sockets are closed and no accepted helper is active,
        # ordinary services can be stopped.  Accepted helpers are never sent
        # SIGTERM: interrupting an MCU flash or business/database switch could
        # leave the device unrecoverable.
        stop_and_verify(ordinary_units)
        if errors:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason="; ".join(errors),
            )
        try:
            failed_helpers = []
            for unit in self._helper_instances():
                state = self._systemctl_show(unit).get("activeState")
                if state == "failed":
                    failed_helpers.append(unit)
                elif state != "inactive":
                    errors.append(
                        "privileged operation appeared during rollback drain: "
                        f"{unit} ({state or 'unknown'})"
                    )
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
        for unit in failed_helpers:
            result = self._run(("systemctl", "reset-failed", unit), check=False)
            if result.returncode != 0 or self._systemctl_show(unit).get(
                "activeState"
            ) != "inactive":
                errors.append(f"cannot reset failed helper instance: {unit}")
        if errors:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason="; ".join(errors),
            )

        try:
            self._establish_rollback_unit_fences(record)
            self._run(("systemctl", "daemon-reload"))
            self._assert_managed_unit_fence_contract(
                allow_absent=True, rollback_fenced=True
            )
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(str(exc),),
                retained=(
                    RUNTIME_START_FENCE,
                    "/opt/ecobin/updater",
                    UPDATER_STATE_DATABASE_PATH,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        # The earlier snapshot inspected only path identity and permissions;
        # root never opens a live SQLite database.  With the durable fence,
        # coordinator, updater, sockets and accepted helpers all inactive, the
        # exact logical predicate can now be read without a WAL race.
        try:
            self._assert_rollback_quiesced(record)
            self._assert_mcu_safety_gate()
            updater_state_after = self._inspect_updater_rollback_eligibility(
                record,
                phase="POST_STOP",
                previous=updater_state_before,
            )
            # A test or external root action can race the SQLite snapshot.
            # Recheck immediately before committing to artifact removal; even
            # after this check, every unit start remains blocked by its own
            # systemd ConditionPathExists fence.
            self._assert_rollback_quiesced(record)
            self._validate_rollback_removal_plan(record)
        except RollbackRefusedForUsedState as exc:
            self._refuse_rollback_and_restore_services(
                record,
                automatic=automatic,
                reason=str(exc),
            )
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(str(exc),),
                retained=(
                    UPDATER_STATE_DATABASE_PATH,
                    "/opt/ecobin/updater",
                    RUNTIME_START_FENCE,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        record["rollbackRemovalStartedAt"] = _utc_now()
        record["status"] = "ROLLBACK_REMOVING"
        record["rollback"] = {
            "startedAt": _utc_now(),
            "automatic": automatic,
            "errors": [],
            "retainedPaths": sorted(set(retained)),
        }
        # This recovery intent is durable before the first unlink.  If power
        # is lost later, the marker still locates a record whose removal loop
        # is deliberately idempotent.
        self._persist_record(record)

        artifact_intents = record.get("artifactIntents")
        if not isinstance(artifact_intents, list):
            self._block_rollback(
                record,
                automatic=automatic,
                errors=("artifact publication journal is absent",),
                retained=retained,
            )
        for item in reversed(artifact_intents):
            try:
                if not isinstance(item, dict):
                    raise MaintenanceInstallError(
                        "artifact publication journal is malformed"
                    )
                self._remove_artifact_intent(
                    item, operation_id=str(record["operationId"])
                )
            except MaintenanceInstallError as exc:
                # A second pre-delete proof already ran while every writer was
                # quiesced.  If a privileged external actor still races an
                # item, stop at the first mismatch instead of dismantling the
                # remaining installation.
                self._block_rollback(
                    record,
                    automatic=automatic,
                    errors=(str(exc),),
                    retained=retained,
                )

        # Remove fixed empty tmpfiles artifacts before attempting their parent
        # directories.  Mutable databases are never unlinked by this tool.
        for absolute in sorted(
            TMPFILES_REGULAR_PATHS,
            key=lambda value: len(PurePosixPath(value).parts),
            reverse=True,
        ):
            before = record.get("pathsBefore", {}).get(absolute, {"exists": False})
            if before.get("exists"):
                continue
            path = self._path(absolute)
            if _lexists(path):
                try:
                    # The complete identity/metadata proof ran immediately
                    # before ROLLBACK_REMOVING was committed.
                    path.unlink()
                    self._fsync_directory(path.parent)
                except OSError as exc:
                    self._block_rollback(
                        record,
                        automatic=automatic,
                        errors=(f"cannot remove tmpfiles artifact {absolute}: {exc}",),
                        retained=retained,
                    )

        directories_after = record.get("directoriesAfter", {})
        directories_before = record.get("directoriesBefore", {})
        for absolute in sorted(
            directories_before,
            key=lambda value: len(PurePosixPath(value).parts),
            reverse=True,
        ):
            before = directories_before[absolute]
            path = self._path(absolute)
            if not _lexists(path):
                continue
            actual = self._snapshot(absolute)
            after = directories_after.get(absolute)
            if actual.get("kind") != "directory":
                self._block_rollback(
                    record,
                    automatic=automatic,
                    errors=(f"rollback found non-directory at {absolute}",),
                    retained=retained,
                )
            if after and os.name == "posix" and not absolute.startswith("/run/") and (
                actual.get("device") != after.get("device")
                or actual.get("inode") != after.get("inode")
            ):
                self._block_rollback(
                    record,
                    automatic=automatic,
                    errors=(f"rollback refuses replaced directory: {absolute}",),
                    retained=retained,
                )
            if before.get("exists"):
                if os.name == "posix" and (
                    actual.get("device") != before.get("device")
                    or actual.get("inode") != before.get("inode")
                ):
                    self._block_rollback(
                        record,
                        automatic=automatic,
                        errors=(
                            f"rollback cannot identify original directory: {absolute}",
                        ),
                        retained=retained,
                    )
                try:
                    os.chmod(path, int(before["mode"]))
                    if self.enforce_root_ownership:
                        os.chown(path, int(before["uid"]), int(before["gid"]))
                    self._fsync_directory(path)
                    self._fsync_directory(path.parent)
                except OSError as exc:
                    self._block_rollback(
                        record,
                        automatic=automatic,
                        errors=(
                            f"cannot restore directory metadata {absolute}: {exc}",
                        ),
                        retained=retained,
                    )
            else:
                try:
                    path.rmdir()
                    self._fsync_directory(path.parent)
                except OSError as exc:
                    if absolute in TMPFILES_DIRECTORY_MODES:
                        retained.append(absolute)
                    else:
                        self._block_rollback(
                            record,
                            automatic=automatic,
                            errors=(
                                "cannot remove newly created code directory "
                                f"{absolute}: {exc}",
                            ),
                            retained=retained,
                        )

        reload_result = self._run(("systemctl", "daemon-reload"), check=False)
        if reload_result.returncode != 0:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=("cannot reload systemd after artifact removal",),
                retained=retained,
            )

        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            try:
                state = self._systemctl_show(unit)
            except MaintenanceInstallError as exc:
                errors.append(f"cannot verify removed unit {unit}: {exc}")
                continue
            if (
                state.get("loadState") != "not-found"
                or state.get("activeState") != "inactive"
                or state.get("fragmentPath")
                or self._reported_drop_in_paths(state)
                != (
                    f"/etc/systemd/system/{unit}.d/"
                    f"{ROLLBACK_UNIT_FENCE_DROP_IN_NAME}",
                )
            ):
                errors.append(
                    f"removed unit still has a loadable definition: {unit}"
                )
        try:
            residual_instances = self._helper_instances()
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
            residual_instances = ()
        for unit in residual_instances:
            errors.append(f"removed helper instance remains loadable: {unit}")

        try:
            self._assert_no_managed_unit_fallback_files()
            self._assert_dropin_filesystem(
                installed=False, rollback_fenced=True
            )
            runtime_target = self._systemctl_show(RUNTIME_TARGET)
            if self._reported_drop_in_paths(runtime_target):
                errors.append("runtime target still has maintenance drop-ins")
        except MaintenanceInstallError as exc:
            errors.append(f"cannot verify removed drop-ins: {exc}")

        for item in artifact_intents:
            path_value = item.get("path") if isinstance(item, dict) else None
            if not isinstance(path_value, str) or _lexists(self._path(path_value)):
                errors.append(
                    f"published artifact remains after rollback: {path_value or '<invalid>'}"
                )
            if isinstance(item, dict) and item.get("kind") == "regular":
                temporary_value = item.get("temporaryPath")
                if not isinstance(temporary_value, str) or _lexists(
                    self._path(temporary_value)
                ):
                    errors.append(
                        "incoming artifact remains after rollback: "
                        f"{temporary_value or '<invalid>'}"
                    )

        if errors:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=errors,
                retained=retained,
            )
        try:
            # Nothing may reopen a managed unit between the final state proof
            # and fence release.  The code is already absent, but the fence is
            # retained until SQLite/MCU and the exact legacy definition have
            # all been verified against their installation-time identities.
            self._assert_rollback_quiesced(record)
            self._assert_mcu_safety_gate()
            self._inspect_updater_rollback_eligibility(
                record,
                phase="POST_STOP",
                previous=updater_state_after,
            )
            self._assert_legacy_restore_ready(record)
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(str(exc),),
                retained=(
                    MCU_RECOVERY_MARKER,
                    UPDATER_STATE_DATABASE_PATH,
                    RUNTIME_START_FENCE,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        try:
            self._remove_rollback_unit_fences(record, installed=False)
            self._run(("systemctl", "daemon-reload"))
            self._assert_managed_unit_fence_contract(allow_absent=True)
            self._assert_dropin_filesystem(installed=False)
        except MaintenanceInstallError as exc:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=(str(exc),),
                retained=(
                    MCU_RECOVERY_MARKER,
                    UPDATER_STATE_DATABASE_PATH,
                    RUNTIME_START_FENCE,
                    ACTIVE_MARKER,
                    PENDING_MARKER,
                ),
            )

        legacy_before = record["servicesBefore"].get(record["legacyService"], {})
        self._release_runtime_start_fence(record)
        legacy_state = self._systemctl_show(str(record["legacyService"]))
        if (
            legacy_before.get("activeState") == "active"
            and legacy_state.get("activeState") != "active"
        ):
            result = self._run(
                ("systemctl", "start", str(record["legacyService"])), check=False
            )
            if result.returncode != 0:
                errors.append("cannot restore legacy service active state")

        try:
            legacy_state = self._systemctl_show(str(record["legacyService"]))
            if (
                legacy_before.get("activeState") == "active"
                and legacy_state.get("activeState") != "active"
            ):
                errors.append("legacy service active state was not restored")
            self._assert_legacy_restore_ready(record)
        except MaintenanceInstallError as exc:
            errors.append(f"legacy gate drop-in changed during rollback: {exc}")

        if errors:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=errors,
                retained=retained,
            )

        record["status"] = "ROLLED_BACK"
        record["rollback"] = {
            "completedAt": _utc_now(),
            "automatic": automatic,
            "errors": [],
            "retainedPaths": sorted(set(retained)),
            "accountsRetainedInert": list(ACCOUNT_NAMES),
            "groupsRetainedInert": list(GROUP_NAMES),
        }
        # Commit the completed state while at least one locating marker still
        # exists.  Marker removal is the final idempotent cleanup: a crash or
        # fsync failure can only leave a marker pointing to ROLLED_BACK, never
        # erase discoverability while the durable record is stale.
        self._persist_record(record)
        self._reconcile_operation_singleton_incomings(record)
        for absolute in (ACTIVE_MARKER, PENDING_MARKER):
            marker_path = self._path(absolute)
            if not _lexists(marker_path):
                continue
            self._assert_maintenance_state_file(
                marker_path, "rollback maintenance marker"
            )
            marker = _load_json_bytes(marker_path.read_bytes(), description="rollback marker")
            if marker != self._marker_document(record):
                raise MaintenanceInstallError("rollback refuses a changed maintenance marker")
            marker_path.unlink()
            self._fsync_directory(marker_path.parent)
        return {
            "status": "ROLLED_BACK",
            "operationId": record["operationId"],
            "retainedPaths": sorted(set(retained)),
            "accountsRetainedInert": list(ACCOUNT_NAMES),
        }

    def rollback(
        self,
        *,
        expected_image_release_id: str,
        expected_image_version: str,
        expected_legacy_service: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        kind, record, _marker = self._load_record_from_marker()
        self._verify_image_against_record(
            record,
            expected_image_release_id,
            expected_image_version,
            expected_legacy_service,
        )
        if record.get("status") == "ROLLED_BACK":
            return self._finalize_rolled_back_record(record, apply=apply)
        activation = self._activation_journal(record, create=False)
        if self._activation_forbids_destructive_rollback(record, activation):
            raise MaintenanceInstallError(
                "an interrupted installation crossed the service-start "
                "boundary and must be resumed forward"
            )
        legacy_state = self._systemctl_show(expected_legacy_service)
        if self._reported_drop_in_paths(legacy_state) != (
            LEGACY_GATE_DROP_IN_PATH,
        ):
            raise MaintenanceInstallError(
                "legacy gate drop-in report changed before rollback"
            )
        self._assert_legacy_gate_drop_in(
            self._path(str(PurePosixPath(LEGACY_GATE_DROP_IN_PATH).parent))
        )
        self._assert_protected_paths_unchanged(record)
        self._assert_hardware_current_unchanged(record)
        self._assert_mcu_safety_gate()
        updater_eligibility = self._inspect_updater_rollback_eligibility(
            record,
            phase="PRE_FENCE" if apply else "DRY_RUN_METADATA",
        )
        if not apply:
            artifact_intents = record.get("artifactIntents", [])
            return {
                "status": "ROLLBACK_REQUIRES_FENCED_INSPECTION",
                "dryRun": True,
                "marker": kind,
                "operationId": record["operationId"],
                "installedFileCount": sum(
                    isinstance(item, dict) and item.get("kind") == "regular"
                    for item in artifact_intents
                ),
                "installedLinkCount": sum(
                    isinstance(item, dict) and item.get("kind") == "symlink"
                    for item in artifact_intents
                ),
                "updaterRollbackState": updater_eligibility["kind"],
                "logicalEligibilityChecked": False,
            }
        return self._rollback_record(record)


class _LiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: Any = None

    def __enter__(self) -> "_LiveLock":
        if fcntl is None:  # pragma: no cover - rejected earlier by the live CLI.
            raise MaintenanceInstallError("POSIX file locking is unavailable")
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        created = False
        try:
            descriptor = os.open(self.path, flags, 0o600)
            created = True
        except FileExistsError:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
            except OSError as exc:
                raise MaintenanceInstallError(
                    "maintenance lock path is unsafe"
                ) from exc
        try:
            if created:
                os.fchmod(descriptor, 0o600)
                os.fchown(descriptor, 0, 0)
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_nlink != 1
                or details.st_uid != 0
                or details.st_gid != 0
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise MaintenanceInstallError(
                    "maintenance lock file metadata is unsafe"
                )
            self.stream = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = -1
            if created:
                os.fsync(self.stream.fileno())
                parent_descriptor = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            self.stream = None
            raise MaintenanceInstallError(
                "another maintenance installer is running"
            ) from exc
        return self

    def __exit__(self, *_args: object) -> None:
        if self.stream is not None:
            assert fcntl is not None
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


def _prepare_live_lock_directory(path: Path) -> None:
    parent = path.parent
    created = False
    try:
        os.mkdir(parent, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise MaintenanceInstallError(
            "cannot create maintenance lock directory"
        ) from exc
    try:
        details = parent.lstat()
    except OSError as exc:
        raise MaintenanceInstallError(
            "maintenance lock directory is unavailable"
        ) from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise MaintenanceInstallError(
            "maintenance lock directory metadata is unsafe"
        )
    if created:
        descriptor = os.open(
            parent.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _assert_live_linux_root() -> None:
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise MaintenanceInstallError("maintenance CLI requires Linux root")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or roll back the EcoBin Stage-3 permanent layer"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_confirmation(target: argparse.ArgumentParser) -> None:
        target.add_argument("--expected-image-release-id", required=True)
        target.add_argument("--expected-image-version", required=True)
        target.add_argument(
            "--expected-legacy-service", required=True, choices=(LEGACY_SERVICE,)
        )

    def add_payload(target: argparse.ArgumentParser) -> None:
        target.add_argument("--payload", required=True, type=Path)
        target.add_argument("--manifest-sha256", required=True)
        add_confirmation(target)

    preflight = subparsers.add_parser("preflight", help="validate without changing the device")
    add_payload(preflight)
    install_parser = subparsers.add_parser("install", help="install; dry-run by default")
    add_payload(install_parser)
    install_parser.add_argument("--apply", action="store_true")
    audit_parser = subparsers.add_parser("audit", help="audit the active installation")
    add_confirmation(audit_parser)
    rollback_parser = subparsers.add_parser(
        "rollback", help="roll back; dry-run by default"
    )
    add_confirmation(rollback_parser)
    rollback_parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _assert_live_linux_root()
        arguments = _parser().parse_args(argv)
        installer = MaintenanceInstaller()
        common = {
            "expected_image_release_id": arguments.expected_image_release_id,
            "expected_image_version": arguments.expected_image_version,
            "expected_legacy_service": arguments.expected_legacy_service,
        }
        lock_path = _rooted(Path("/"), LOCK_PATH)
        _prepare_live_lock_directory(lock_path)
        with _LiveLock(lock_path):
            if arguments.command == "preflight":
                result = installer.preflight(
                    arguments.payload, arguments.manifest_sha256, **common
                )
            elif arguments.command == "install":
                result = installer.install(
                    arguments.payload,
                    arguments.manifest_sha256,
                    apply=arguments.apply,
                    **common,
                )
            elif arguments.command == "audit":
                result = installer.audit(**common)
            elif arguments.command == "rollback":
                result = installer.rollback(apply=arguments.apply, **common)
            else:  # pragma: no cover - argparse enforces the finite set.
                raise AssertionError(arguments.command)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except MaintenanceInstallError as exc:
        print(f"device-management-maintenance=FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
