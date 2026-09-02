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
import shutil
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


MANIFEST_NAME = "device-management-maintenance-manifest.json"
MANIFEST_SCHEMA_VERSION = 1
BACKUP_SCHEMA_VERSION = 1
MARKER_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PAYLOAD_FILE_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_TRUST_KEYS = 64

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
RUNTIME_KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.pem$")
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

LEGACY_SERVICE = "ecobin-hardware.service"
RUNTIME_TARGET = "ecobin-runtime.target"
MCU_SAFE_GPIO_SERVICE = "ecobin-mcu-safe-gpio.service"
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
BACKUP_PARENT = "/var/lib/ecobin/device-management-maintenance/backups"
LOCK_PATH = "/run/lock/ecobin-device-management-maintenance.lock"
MCU_RECOVERY_MARKER = (
    "/run/ecobin/privileged/mcu-application-recovery-required"
)

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
    completed = subprocess.run(
        list(arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
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

    def _path(self, absolute: str) -> Path:
        return _rooted(self.rootfs, absolute)

    def _run(self, arguments: Sequence[str], *, check: bool = True) -> CommandResult:
        result = self.runner(arguments)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
            raise MaintenanceInstallError(
                f"command failed ({arguments[0]}): {detail or result.returncode}"
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
        ):
            raise MaintenanceInstallError(
                "MCU safe-GPIO service is not active and loaded"
            )
        if self._reported_drop_in_paths(safe_gpio):
            raise MaintenanceInstallError(
                "MCU safe-GPIO service has uncontrolled drop-ins"
            )
        return safe_gpio

    def _assert_host_tools(self) -> None:
        if not self.check_host_tools:
            return
        for name in (
            "systemctl",
            "systemd-sysusers",
            "systemd-tmpfiles",
            "getent",
            "id",
        ):
            resolved = shutil.which(name)
            if resolved is None or not Path(resolved).is_absolute():
                raise MaintenanceInstallError(f"required host tool is unavailable: {name}")

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

    def _existing_marker(self) -> tuple[str, Mapping[str, Any]] | None:
        for kind, absolute in (("active", ACTIVE_MARKER), ("pending", PENDING_MARKER)):
            path = self._path(absolute)
            if not _lexists(path):
                continue
            self._assert_safe_regular(path, f"{kind} maintenance marker")
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
            return kind, marker
        return None

    def _base_directory_checks(self) -> None:
        for absolute in (
            "/etc/systemd/system",
            "/usr/lib",
            "/usr/lib/sysusers.d",
            "/usr/lib/tmpfiles.d",
            "/usr/share",
            "/var/lib",
            "/run/lock",
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

    def _assert_dropin_filesystem(self, *, installed: bool) -> None:
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
        legacy_found = False
        for base_absolute in (
            "/etc/systemd/system",
            "/run/systemd/system",
            "/usr/lib/systemd/system",
        ):
            base = self._path(base_absolute)
            if not _lexists(base):
                continue
            self._assert_safe_directory(base, f"systemd unit directory {base_absolute}")
            for child in base.iterdir():
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
                    if len(entries) != 1 or entries[0].name != runtime_file_name:
                        raise MaintenanceInstallError(
                            "runtime target drop-in directory has uncontrolled entries"
                        )
                    self._assert_safe_regular(
                        entries[0], "controlled runtime target drop-in"
                    )
                    continue
                raise MaintenanceInstallError(
                    f"uncontrolled systemd drop-in state exists: "
                    f"{base_absolute}/{name}"
                )
        if not legacy_found:
            raise MaintenanceInstallError("v13 legacy gate drop-in is absent")

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
                kind == "active"
                and document["payloadId"] == manifest.payload_id
                and document["manifestSha256"] == manifest.manifest_sha256
            ):
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
                )
                return {
                    "status": "ALREADY_INSTALLED",
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
                self._assert_safe_directory(path, f"maintenance storage {absolute}")
                if absolute != "/var/lib/ecobin" and stat.S_IMODE(path.lstat().st_mode) != mode:
                    raise MaintenanceInstallError(
                        f"maintenance storage has unsafe mode: {absolute}"
                    )
            else:
                path.mkdir(mode=mode)
                os.chmod(path, mode)
                if self.enforce_root_ownership:
                    os.chown(path, 0, 0)
        operation = self._path(f"{BACKUP_PARENT}/{operation_id}")
        if _lexists(operation):
            raise MaintenanceInstallError("maintenance operation backup already exists")
        operation.mkdir(mode=0o700)
        os.chmod(operation, 0o700)
        if self.enforce_root_ownership:
            os.chown(operation, 0, 0)
        return operation

    def _write_atomic_json(self, path: Path, document: Any, mode: int = 0o600) -> None:
        raw = _canonical_json(document)
        temporary = path.with_name(f".{path.name}.incoming-{os.getpid()}")
        if _lexists(temporary):
            raise MaintenanceInstallError(f"stale temporary file exists: {temporary}")
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
            mode,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            if self.enforce_root_ownership:
                os.chown(temporary, 0, 0)
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
            os.chmod(temporary, mode)
            if self.enforce_root_ownership:
                os.chown(temporary, 0, 0)
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
            return
        path.mkdir(mode=mode)
        os.chmod(path, mode)
        if self.enforce_root_ownership:
            os.chown(path, 0, 0)
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
        if report["status"] == "ALREADY_INSTALLED":
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
        storage_before = {
            path: self._snapshot(path)
            for path in (
                "/var/lib/ecobin/device-management-maintenance",
                BACKUP_PARENT,
            )
        }
        self._ensure_maintenance_storage(operation_id)
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
                    IMMUTABLE_PATHS
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
            "rollback": None,
        }
        self._persist_record(record)
        self._write_marker(PENDING_MARKER, record)

        try:
            # Restore only traversal on the already installed business release.
            # Every individual directory was captured above; files and current
            # symlink are deliberately untouched.
            for directory in hardware_directories:
                details = self._assert_safe_directory(
                    directory, "hardware runtime traversal directory"
                )
                if stat.S_IMODE(details.st_mode) != 0o755:
                    os.chmod(directory, 0o755)
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

            self._run(
                (
                    "systemd-sysusers",
                    "/usr/lib/sysusers.d/ecobin-device-runtime.conf",
                )
            )
            record["commandsCompleted"].append("systemd-sysusers")
            self._persist_record(record)
            self._run(
                (
                    "systemd-tmpfiles",
                    "--create",
                    "/usr/lib/tmpfiles.d/ecobin-device-runtime.conf",
                )
            )
            record["commandsCompleted"].append("systemd-tmpfiles")
            for absolute in TMPFILES_DIRECTORY_MODES:
                record["directoriesAfter"][absolute] = self._snapshot(absolute)
            self._persist_record(record)
            self._run(("systemctl", "daemon-reload"))
            record["commandsCompleted"].append("systemctl daemon-reload")
            self._persist_record(record)
            for unit in START_UNITS:
                self._run(("systemctl", "start", unit))
                record["commandsCompleted"].append(f"systemctl start {unit}")
                self._persist_record(record)
                state = self._systemctl_show(unit)
                if state["activeState"] != "active":
                    raise MaintenanceInstallError(f"installed unit did not become active: {unit}")

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
            self._write_marker(ACTIVE_MARKER, record)
            pending = self._path(PENDING_MARKER)
            if pending.read_bytes() != _canonical_json(self._marker_document(record)):
                raise MaintenanceInstallError("pending maintenance marker changed")
            pending.unlink()
            self._fsync_directory(pending.parent)
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
            try:
                record["status"] = "INSTALL_FAILED"
                record["installError"] = type(install_error).__name__
                self._persist_record(record)
                self._rollback_record(record, automatic=True)
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
        self._assert_safe_regular(record_path, "maintenance backup record")
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

    def _audit_runtime_permissions(
        self, identities: Mapping[str, Any], record: Mapping[str, Any]
    ) -> None:
        users = identities["users"]
        primary_groups = identities["primaryGroups"]
        groups = identities["groups"]
        directory_owners: Mapping[str, tuple[int, frozenset[int]]] = {
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
                frozenset(
                    {
                        primary_groups["ecobin-updater"],
                        groups["ecobin-updater-ipc"],
                    }
                ),
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
    ) -> dict[str, Any]:
        kind, record, _marker = self._load_record_from_marker()
        if kind != "active" or record.get("status") != "INSTALLED":
            raise MaintenanceInstallError("maintenance installation is incomplete")
        identity = self._verify_image_against_record(
            record,
            expected_image_release_id,
            expected_image_version,
            expected_legacy_service,
        )
        self._assert_mcu_safety_gate()
        self._assert_dropin_filesystem(installed=True)
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
        return {
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

    def _block_rollback(
        self,
        record: dict[str, Any],
        *,
        automatic: bool,
        errors: Sequence[str],
        retained: Sequence[str],
    ) -> None:
        record["status"] = "ROLLBACK_BLOCKED"
        record["rollback"] = {
            "attemptedAt": _utc_now(),
            "automatic": automatic,
            "errors": list(errors),
            "retainedPaths": sorted(set(retained)),
        }
        self._persist_record(record)
        raise MaintenanceInstallError("; ".join(errors))

    def _rollback_record(
        self, record: dict[str, Any], *, automatic: bool = False
    ) -> dict[str, Any]:
        errors: list[str] = []
        retained: list[str] = []

        # Code must never be removed while any newly introduced process can
        # still execute it.  Stopping and the inactive recheck form a hard
        # phase boundary: any failure leaves every installed artifact intact.
        sockets = tuple(unit for unit in START_UNITS if unit.endswith(".socket"))
        ordinary_units = tuple(
            unit for unit in reversed(START_UNITS) if not unit.endswith(".socket")
        )

        def stop_and_verify(units: Iterable[str]) -> None:
            for unit in units:
                before = record["servicesBefore"].get(unit, {})
                if before.get("activeState") == "active":
                    continue
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
            self._block_rollback(
                record,
                automatic=automatic,
                errors=errors,
                retained=retained,
            )
        stop_and_verify(ordinary_units)
        try:
            helper_instances = self._helper_instances()
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
            helper_instances = ()
        stop_and_verify(helper_instances)
        try:
            for unit in self._helper_instances():
                if self._systemctl_show(unit).get("activeState") != "inactive":
                    errors.append(f"helper instance did not become inactive: {unit}")
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
        if errors:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=errors,
                retained=retained,
            )

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
                errors.append(str(exc))

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
                    details = path.lstat()
                    if stat.S_ISREG(details.st_mode) and details.st_size == 0:
                        path.unlink()
                    else:
                        errors.append(
                            f"rollback refuses changed tmpfiles artifact: {absolute}"
                        )
                except OSError as exc:
                    errors.append(f"cannot inspect tmpfiles artifact {absolute}: {exc}")

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
                errors.append(f"rollback found non-directory at {absolute}")
                continue
            if after and os.name == "posix" and (
                actual.get("device") != after.get("device")
                or actual.get("inode") != after.get("inode")
            ):
                errors.append(f"rollback refuses replaced directory: {absolute}")
                continue
            if before.get("exists"):
                if os.name == "posix" and (
                    actual.get("device") != before.get("device")
                    or actual.get("inode") != before.get("inode")
                ):
                    errors.append(f"rollback cannot identify original directory: {absolute}")
                    continue
                try:
                    os.chmod(path, int(before["mode"]))
                    if self.enforce_root_ownership:
                        os.chown(path, int(before["uid"]), int(before["gid"]))
                except OSError as exc:
                    errors.append(f"cannot restore directory metadata {absolute}: {exc}")
            else:
                try:
                    path.rmdir()
                except OSError as exc:
                    if absolute in TMPFILES_DIRECTORY_MODES:
                        retained.append(absolute)
                    else:
                        errors.append(
                            f"cannot remove newly created code directory {absolute}: {exc}"
                        )

        reload_result = self._run(("systemctl", "daemon-reload"), check=False)
        if reload_result.returncode != 0:
            errors.append("cannot reload systemd after artifact removal")

        deleted_unit_paths = {
            f"/etc/systemd/system/{unit}"
            for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES)
        }
        for unit in (*MAIN_UNIT_FILES, *HELPER_UNIT_FILES):
            try:
                state = self._systemctl_show(unit)
            except MaintenanceInstallError as exc:
                errors.append(f"cannot verify removed unit {unit}: {exc}")
                continue
            if state.get("activeState") != "inactive":
                errors.append(f"removed unit is not inactive: {unit}")
            if state.get("fragmentPath") in deleted_unit_paths:
                errors.append(f"removed unit still points at deleted fragment: {unit}")
            if self._reported_drop_in_paths(state):
                errors.append(f"removed unit still has drop-ins: {unit}")
        try:
            residual_instances = self._helper_instances()
        except MaintenanceInstallError as exc:
            errors.append(str(exc))
            residual_instances = ()
        for unit in residual_instances:
            try:
                state = self._systemctl_show(unit)
            except MaintenanceInstallError as exc:
                errors.append(f"cannot verify removed helper instance {unit}: {exc}")
                continue
            if state.get("activeState") != "inactive":
                errors.append(f"removed helper instance is not inactive: {unit}")
            if state.get("fragmentPath") in deleted_unit_paths:
                errors.append(
                    f"removed helper instance still points at deleted fragment: {unit}"
                )
            if self._reported_drop_in_paths(state):
                errors.append(f"removed helper instance still has drop-ins: {unit}")

        try:
            self._assert_dropin_filesystem(installed=False)
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

        legacy_before = record["servicesBefore"].get(record["legacyService"], {})
        if legacy_before.get("activeState") == "active":
            result = self._run(
                ("systemctl", "start", str(record["legacyService"])), check=False
            )
            if result.returncode != 0:
                errors.append("cannot restore legacy service active state")

        try:
            legacy_state = self._systemctl_show(str(record["legacyService"]))
            if self._reported_drop_in_paths(legacy_state) != (
                LEGACY_GATE_DROP_IN_PATH,
            ):
                errors.append("legacy gate drop-in report changed during rollback")
            self._assert_legacy_gate_drop_in(
                self._path(str(PurePosixPath(LEGACY_GATE_DROP_IN_PATH).parent))
            )
            self._assert_protected_paths_unchanged(record)
            self._assert_hardware_current_unchanged(record)
        except MaintenanceInstallError as exc:
            errors.append(f"legacy gate drop-in changed during rollback: {exc}")

        if errors:
            self._block_rollback(
                record,
                automatic=automatic,
                errors=errors,
                retained=retained,
            )

        for absolute in (ACTIVE_MARKER, PENDING_MARKER):
            marker_path = self._path(absolute)
            if not _lexists(marker_path):
                continue
            marker = _load_json_bytes(marker_path.read_bytes(), description="rollback marker")
            if marker != self._marker_document(record):
                raise MaintenanceInstallError("rollback refuses a changed maintenance marker")
            marker_path.unlink()
            self._fsync_directory(marker_path.parent)
        record["status"] = "ROLLED_BACK"
        record["rollback"] = {
            "completedAt": _utc_now(),
            "automatic": automatic,
            "errors": [],
            "retainedPaths": sorted(set(retained)),
            "accountsRetainedInert": list(ACCOUNT_NAMES),
            "groupsRetainedInert": list(GROUP_NAMES),
        }
        self._persist_record(record)
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
        if not apply:
            artifact_intents = record.get("artifactIntents", [])
            return {
                "status": "ROLLBACK_READY",
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
            }
        return self._rollback_record(record)


class _LiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stream: Any = None

    def __enter__(self) -> "_LiveLock":
        if fcntl is None:  # pragma: no cover - rejected earlier by the live CLI.
            raise MaintenanceInstallError("POSIX file locking is unavailable")
        self.stream = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            raise MaintenanceInstallError("another maintenance installer is running") from exc
        return self

    def __exit__(self, *_args: object) -> None:
        if self.stream is not None:
            assert fcntl is not None
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


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
        with _LiveLock(_rooted(Path("/"), LOCK_PATH)):
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
