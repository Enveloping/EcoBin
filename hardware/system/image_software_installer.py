#!/usr/bin/env python3
"""Install and audit the immutable EcoBin software layer in an image rootfs.

The builder deliberately does not create Python environments.  A release
engineer must provide a separately built software payload whose complete file
inventory is locked by ``software-payload.lock.json`` and whose lock SHA-256 is
passed out of band to this program.  This is an outer, controlled build-input
pin; it is not a replacement for the signed hardware-runtime release flow.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit


HARDWARE_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(HARDWARE_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(HARDWARE_SOURCE_ROOT))

from install.runtime_payload_manifest import (  # noqa: E402
    COMMUNICATION_AGENT_FILES,
    DEVICE_UPDATER_FILES,
    DEVICE_UPDATER_HELPER_FILES,
    DEVICE_UPDATER_HELPER_UNIT_FILES,
    FACTORY_APP_RUNTIME_FILES,
    LEGACY_RUNTIME_APP_FILES,
    RUNTIME_APP_FILES,
)


LOCK_NAME = "software-payload.lock.json"
LEGACY_LOCK_SCHEMA_VERSION = 1
LOCK_SCHEMA_VERSION = 2
RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
ENROLLMENT_FIELDS = frozenset(
    {
        "ECOBIN_ENROLLMENT_BACKEND_URL",
        "ECOBIN_ENROLLMENT_KEY_ID",
        "ECOBIN_ENROLLMENT_MODE",
    }
)
CELLULAR_FIELDS = frozenset(
    {
        "ECOBIN_CELLULAR_SCHEMA_VERSION",
        "ECOBIN_CELLULAR_HIL_APPROVED",
        "ECOBIN_CELLULAR_CONNECTION_ID",
        "ECOBIN_CELLULAR_USB_DRIVER",
        "ECOBIN_CELLULAR_USB_PROFILE",
        "ECOBIN_CELLULAR_AUTO_APN",
        "ECOBIN_CELLULAR_PROBE_IPV4",
        "ECOBIN_CELLULAR_HTTPS_PROBE_URL",
    }
)
SAFE_CELLULAR_CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MCU_PUBLIC_KEY_NAME = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}\.pem$")
RUNTIME_PUBLIC_KEY_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.pem$")
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
MIN_PUBLIC_KEY_PEM_BYTES = 100
MAX_PUBLIC_KEY_PEM_BYTES = 1024
MAX_PUBLIC_KEYS_PER_STORE = 64

LEGACY_COMPONENT_NAMES = (
    "hardwareRuntime",
    "enrollment",
    "remoteSupport",
    "factoryTest",
    "firstBoot",
)

PERMANENT_COMPONENT_NAMES = (
    "communicationAgent",
    "deviceUpdater",
)

COMPONENT_NAMES = LEGACY_COMPONENT_NAMES + PERMANENT_COMPONENT_NAMES

FACTORY_APP_PACKAGES = ("factory", "first_boot", "factory_seal")

ENROLLMENT_FILES = (
    "enrollment_bootstrap.py",
    "device_enrollment.py",
    "device_credentials.py",
    "factory_progress.py",
    "maintenance_ssh_setup.py",
    "remote_support_credentials.py",
    "secure_files.py",
    "secret_memory_guard.py",
)

REMOTE_SUPPORT_FILES = (
    "remote_support_agent.py",
    "remote_support.py",
    "remote_support_control.py",
    "remote_support_store.py",
    "device_credentials.py",
    "secure_files.py",
    "trusted_clock.py",
)

LEGACY_MAIN_UNITS = (
    "ecobin-hardware.service",
    "ecobin-enrollment.service",
    "ecobin-remote-support.service",
)

MAIN_UNITS = (
    "ecobin-communication.service",
    *LEGACY_MAIN_UNITS,
    "ecobin-updater.service",
    "ecobin-device-management-preflight.service",
    "ecobin-business-permission-preflight.service",
)

PRIVILEGED_HELPER_UNITS = DEVICE_UPDATER_HELPER_UNIT_FILES

DEVICE_MANAGEMENT_CONFIG_FILES = (
    (
        "device_management/config/sysusers.d/ecobin-device-runtime.conf",
        "usr/lib/sysusers.d/ecobin-device-runtime.conf",
    ),
    (
        "device_management/config/tmpfiles.d/ecobin-device-runtime.conf",
        "usr/lib/tmpfiles.d/ecobin-device-runtime.conf",
    ),
)

DEVICE_MANAGEMENT_SUPPORT_FILES = (
    (
        "system/business_runtime_preflight.py",
        "usr/lib/ecobin/business_runtime_preflight.py",
        0o644,
    ),
)

CONFLICTING_UNITS = (
    "nftables.service",
    "ufw.service",
    "firewalld.service",
    "hostapd.service",
    "dnsmasq.service",
    "wpa_supplicant@wlan0.service",
)

ENABLED_LINKS = {
    "multi-user.target.wants/ecobin-mcu-safe-gpio.service": (
        "../ecobin-mcu-safe-gpio.service"
    ),
    "multi-user.target.wants/ecobin-first-boot.service": (
        "../ecobin-first-boot.service"
    ),
    "sysinit.target.wants/ecobin-factory-egress-lock.service": (
        "../ecobin-factory-egress-lock.service"
    ),
    "network-pre.target.requires/ecobin-factory-egress-lock.service": (
        "../ecobin-factory-egress-lock.service"
    ),
    "network-pre.target.requires/ecobin-first-boot.service": (
        "../ecobin-first-boot.service"
    ),
}

STATIC_UNIT_NAMES = frozenset(
    {
        "ecobin-business-activation-helper.socket",
        "ecobin-business-activation-helper@.service",
        "ecobin-cellular-uplink.service",
        "ecobin-device-management-preflight.service",
        "ecobin-business-permission-preflight.service",
        "ecobin-communication.service",
        "ecobin-enrollment.service",
        "ecobin-edge-store-prepare.service",
        "ecobin-factory-ap-prepare.service",
        "ecobin-factory-ap.service",
        "ecobin-factory-dnsmasq.service",
        "ecobin-factory-handoff.service",
        "ecobin-factory-hostapd.service",
        "ecobin-factory-portal.service",
        "ecobin-factory-test.service",
        "ecobin-factory.target",
        "ecobin-hardware.service",
        "ecobin-mcu-flash-helper.socket",
        "ecobin-mcu-flash-helper@.service",
        "ecobin-remote-support.service",
        "ecobin-runtime-gate.service",
        "ecobin-runtime.target",
        "ecobin-updater.service",
    }
)

STATIC_UNIT_INSTANCE_PREFIXES = (
    "ecobin-business-activation-helper@",
    "ecobin-mcu-flash-helper@",
)


class ImageSoftwareError(RuntimeError):
    """The payload or installed image violates the immutable image policy."""


def _component_names_for_lock_schema(schema_version: object) -> tuple[str, ...]:
    if isinstance(schema_version, bool):
        raise ImageSoftwareError("software payload lock schema version is unsupported")
    if schema_version == LEGACY_LOCK_SCHEMA_VERSION:
        return LEGACY_COMPONENT_NAMES
    if schema_version == LOCK_SCHEMA_VERSION:
        return COMPONENT_NAMES
    raise ImageSoftwareError("software payload lock schema version is unsupported")


def _is_static_unit_name(name: str) -> bool:
    return name in STATIC_UNIT_NAMES or any(
        name.startswith(prefix) and name.endswith(".service")
        for prefix in STATIC_UNIT_INSTANCE_PREFIXES
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ImageSoftwareError(f"JSON document contains duplicate key: {key}")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImageSoftwareError(f"cannot read JSON document: {path.name}") from exc
    if not isinstance(value, dict):
        raise ImageSoftwareError(f"JSON document must be an object: {path.name}")
    return value


def _safe_relative(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ImageSoftwareError(f"{field} must be a nonempty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ImageSoftwareError(f"{field} is unsafe")
    return path


def _require_release_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not RELEASE_ID.fullmatch(value):
        raise ImageSoftwareError(f"{field} is not a real release ID")
    return value


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _scan_tree(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    pending = [root]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            relative = child.relative_to(root).as_posix()
            details = child.lstat()
            mode = stat.S_IMODE(details.st_mode)
            if stat.S_ISDIR(details.st_mode):
                result[relative] = {"type": "directory", "mode": f"{mode:04o}"}
                pending.append(child)
            elif stat.S_ISREG(details.st_mode):
                if details.st_nlink != 1:
                    raise ImageSoftwareError("payload contains a hard-linked file")
                result[relative] = {
                    "type": "file",
                    "mode": f"{mode:04o}",
                    "size": details.st_size,
                    "sha256": _sha256(child),
                }
            elif stat.S_ISLNK(details.st_mode):
                result[relative] = {
                    "type": "symlink",
                    "mode": f"{mode:04o}",
                    "target": os.readlink(child),
                }
            else:
                raise ImageSoftwareError("payload contains a special filesystem entry")
    return result


def _validate_symlink(relative: PurePosixPath, target: object) -> None:
    if not isinstance(target, str) or not target or "\x00" in target:
        raise ImageSoftwareError("payload symlink target is invalid")
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        if target not in ("/usr/bin/python3", "/usr/bin/python3.11"):
            raise ImageSoftwareError("payload symlink escapes its trusted boundary")
        if not relative.as_posix().endswith(
            ("/.venv/bin/python", "/.venv/bin/python3", "/.venv/bin/python3.11")
        ) and not relative.as_posix().endswith(
            ("-venv/bin/python", "-venv/bin/python3", "-venv/bin/python3.11")
        ):
            raise ImageSoftwareError("absolute payload link is not a Python launcher")
        return
    stack = list(relative.parent.parts)
    boundary = 2 if stack and stack[0] == "components" else 1
    for part in target_path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if len(stack) <= boundary:
                raise ImageSoftwareError("payload symlink escapes its component")
            stack.pop()
        else:
            stack.append(part)


def load_and_validate_payload(
    payload_root: Path,
    *,
    expected_sha256: str,
    expected_git_commit: str,
) -> dict[str, Any]:
    if not HEX_64.fullmatch(expected_sha256):
        raise ImageSoftwareError("software payload SHA-256 is malformed")
    if not GIT_COMMIT.fullmatch(expected_git_commit):
        raise ImageSoftwareError("expected Git commit is malformed")
    if payload_root.is_symlink() or not payload_root.is_dir():
        raise ImageSoftwareError("software payload must be a regular directory")
    lock_path = payload_root / LOCK_NAME
    if lock_path.is_symlink() or not lock_path.is_file():
        raise ImageSoftwareError("software payload lock is missing")
    if _sha256(lock_path) != expected_sha256:
        raise ImageSoftwareError("software payload lock SHA-256 does not match")
    lock = _load_json(lock_path)
    required_top = {
        "schemaVersion",
        "lockState",
        "payloadId",
        "sourceGitCommit",
        "components",
        "entries",
    }
    if set(lock) != required_top:
        raise ImageSoftwareError("software payload lock fields are not exact")
    component_names = _component_names_for_lock_schema(lock["schemaVersion"])
    if lock["lockState"] != "LOCKED":
        raise ImageSoftwareError("software payload is not locked")
    _require_release_id(lock["payloadId"], field="payloadId")
    if lock["sourceGitCommit"] != expected_git_commit:
        raise ImageSoftwareError("software payload was built for another Git commit")
    components = lock["components"]
    if not isinstance(components, dict) or set(components) != set(component_names):
        raise ImageSoftwareError("software payload component set is incomplete")
    expected_component_fields = {
        "hardwareRuntime": {"releaseId", "root"},
        "enrollment": {"releaseId", "venv"},
        "remoteSupport": {"releaseId", "venv"},
        "factoryTest": {"releaseId", "venv"},
        "firstBoot": {"releaseId"},
    }
    if lock["schemaVersion"] == LOCK_SCHEMA_VERSION:
        expected_component_fields.update(
            {
                "communicationAgent": {"releaseId", "root"},
                "deviceUpdater": {"releaseId", "root"},
            }
        )
    for name in component_names:
        component = components[name]
        if not isinstance(component, dict) or set(component) != expected_component_fields[name]:
            raise ImageSoftwareError(f"component metadata is not exact: {name}")
        _require_release_id(component["releaseId"], field=f"{name}.releaseId")
        if (
            lock["schemaVersion"] == LOCK_SCHEMA_VERSION
            and name in PERMANENT_COMPONENT_NAMES
            and len(component["releaseId"]) > 32
        ):
            raise ImageSoftwareError(
                f"{name}.releaseId exceeds the device fact limit"
            )
        for path_field in ("root", "venv"):
            if path_field in component:
                relative = _safe_relative(
                    component[path_field], field=f"{name}.{path_field}"
                )
                source = payload_root.joinpath(*relative.parts)
                if source.is_symlink() or not source.is_dir():
                    raise ImageSoftwareError(f"payload component is missing: {name}")

    entries = lock["entries"]
    if not isinstance(entries, list) or not entries:
        raise ImageSoftwareError("software payload inventory is empty")
    declared: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "path" not in entry or "type" not in entry:
            raise ImageSoftwareError("software payload inventory entry is malformed")
        relative = _safe_relative(entry["path"], field="entries.path")
        path_text = relative.as_posix()
        if path_text == LOCK_NAME or path_text in declared:
            raise ImageSoftwareError("software payload inventory contains a duplicate")
        kind = entry["type"]
        required = {
            "directory": {"path", "type", "mode"},
            "file": {"path", "type", "mode", "size", "sha256"},
            "symlink": {"path", "type", "mode", "target"},
        }.get(kind)
        if required is None or set(entry) != required:
            raise ImageSoftwareError("software payload inventory fields are not exact")
        if not isinstance(entry["mode"], str) or not re.fullmatch(r"0[0-7]{3}", entry["mode"]):
            raise ImageSoftwareError("software payload inventory mode is invalid")
        mode = int(entry["mode"], 8)
        if kind != "symlink" and os.name == "posix" and mode & 0o022:
            raise ImageSoftwareError("software payload is group/world writable")
        if kind == "file":
            if (
                not isinstance(entry["size"], int)
                or entry["size"] < 0
                or not isinstance(entry["sha256"], str)
                or not HEX_64.fullmatch(entry["sha256"])
            ):
                raise ImageSoftwareError("software payload file identity is invalid")
        elif kind == "symlink":
            _validate_symlink(relative, entry["target"])
        declared[path_text] = {key: value for key, value in entry.items() if key != "path"}
    actual = _scan_tree(payload_root)
    actual.pop(LOCK_NAME, None)
    if actual != declared:
        raise ImageSoftwareError("software payload inventory does not match its tree")
    _validate_payload_semantics(payload_root, lock)
    return lock


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ImageSoftwareError(f"cannot read required environment file: {path.name}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ImageSoftwareError(f"malformed environment file: {path.name}")
        key, value = stripped.split("=", 1)
        if not key or key != key.strip() or key in values:
            raise ImageSoftwareError(f"duplicate environment key: {path.name}")
        if any(character in value for character in "\r\n\x00"):
            raise ImageSoftwareError(f"malformed environment value: {path.name}")
        values[key] = value.strip()
    return values


def _validate_enrollment_config(path: Path) -> None:
    values = _parse_env(path)
    if set(values) != ENROLLMENT_FIELDS:
        raise ImageSoftwareError("controlled enrollment environment has unexpected fields")
    if values["ECOBIN_ENROLLMENT_KEY_ID"] != "K1":
        raise ImageSoftwareError("controlled enrollment key ID must be K1")
    if values["ECOBIN_ENROLLMENT_MODE"] != "SELF_ENROLLMENT":
        raise ImageSoftwareError("production image only supports SELF_ENROLLMENT")

    backend = values["ECOBIN_ENROLLMENT_BACKEND_URL"]
    parsed = urlsplit(backend)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ImageSoftwareError("controlled enrollment backend URL is invalid") from exc
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
        or len(backend) > 512
        or hostname == "example.com"
        or hostname.endswith(".example.com")
    ):
        raise ImageSoftwareError("controlled enrollment backend URL is not production")


def _validate_cellular_config(path: Path) -> None:
    """Mirror first_boot.cellular_config.parse_cellular_config at image build time."""

    values = _parse_env(path)
    if set(values) != CELLULAR_FIELDS:
        raise ImageSoftwareError("cellular batch facts have unexpected fields")
    if values["ECOBIN_CELLULAR_SCHEMA_VERSION"] != "2":
        raise ImageSoftwareError("cellular batch facts are not schema revision 2")
    if values["ECOBIN_CELLULAR_HIL_APPROVED"] != "true":
        raise ImageSoftwareError("cellular batch facts have not passed HIL approval")
    if values["ECOBIN_CELLULAR_AUTO_APN"] != "true":
        raise ImageSoftwareError("cellular automatic APN mode is required")
    if not SAFE_CELLULAR_CONNECTION_ID.fullmatch(
        values["ECOBIN_CELLULAR_CONNECTION_ID"]
    ):
        raise ImageSoftwareError("cellular NetworkManager connection ID is invalid")
    if values["ECOBIN_CELLULAR_USB_DRIVER"] != "rndis_host":
        raise ImageSoftwareError("cellular USB driver is not supported")
    if values["ECOBIN_CELLULAR_USB_PROFILE"] != "RNDIS":
        raise ImageSoftwareError("cellular USB profile is not supported")

    try:
        probe_ip = ipaddress.IPv4Address(values["ECOBIN_CELLULAR_PROBE_IPV4"])
    except ipaddress.AddressValueError as exc:
        raise ImageSoftwareError("cellular IPv4 probe target is invalid") from exc
    if probe_ip.is_unspecified or probe_ip.is_loopback:
        raise ImageSoftwareError("cellular IPv4 probe target is invalid")

    probe_url = values["ECOBIN_CELLULAR_HTTPS_PROBE_URL"]
    parsed = urlsplit(probe_url)
    try:
        probe_port = parsed.port
    except ValueError as exc:
        raise ImageSoftwareError("cellular HTTPS probe URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or probe_port not in (None, 443)
        or len(probe_url) > 512
    ):
        raise ImageSoftwareError("cellular HTTPS probe URL is invalid")


def _validate_ed25519_public_key_pem(path: Path) -> None:
    try:
        pem = path.read_bytes()
        text = pem.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise ImageSoftwareError("trusted public key is not ASCII PEM") from exc
    lines = text.splitlines()
    if (
        len(lines) < 3
        or lines[0] != "-----BEGIN PUBLIC KEY-----"
        or lines[-1] != "-----END PUBLIC KEY-----"
        or any(not line or len(line) > 64 for line in lines[1:-1])
    ):
        raise ImageSoftwareError("trusted key is not a public-key PEM")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImageSoftwareError("trusted public key PEM is malformed") from exc
    if len(der) != len(ED25519_SPKI_PREFIX) + 32 or not der.startswith(
        ED25519_SPKI_PREFIX
    ):
        raise ImageSoftwareError("trusted public key is not Ed25519")


def _validate_public_key_store(
    path: Path,
    *,
    name_pattern: re.Pattern[str],
    require_root_ownership: bool = False,
) -> None:
    try:
        directory_details = path.lstat()
    except OSError as exc:
        raise ImageSoftwareError("controlled trust directory is unavailable") from exc
    if stat.S_ISLNK(directory_details.st_mode) or not stat.S_ISDIR(
        directory_details.st_mode
    ):
        raise ImageSoftwareError("controlled trust directory is not a regular directory")
    if os.name == "posix" and stat.S_IMODE(directory_details.st_mode) != 0o755:
        raise ImageSoftwareError("controlled trust directory permissions are invalid")
    if (
        os.name == "posix"
        and require_root_ownership
        and (directory_details.st_uid != 0 or directory_details.st_gid != 0)
    ):
        raise ImageSoftwareError("controlled trust directory is not owned by root")
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ImageSoftwareError("controlled trust directory is unavailable") from exc
    if not 1 <= len(entries) <= MAX_PUBLIC_KEYS_PER_STORE:
        raise ImageSoftwareError("controlled trust directory key count is invalid")

    for key in entries:
        try:
            details = key.lstat()
        except OSError as exc:
            raise ImageSoftwareError("controlled trust entry is unavailable") from exc
        if not name_pattern.fullmatch(key.name):
            raise ImageSoftwareError("controlled trust directory contains an unsupported name")
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
        ):
            raise ImageSoftwareError("controlled trust entry is not a regular single-link file")
        if not MIN_PUBLIC_KEY_PEM_BYTES <= details.st_size <= MAX_PUBLIC_KEY_PEM_BYTES:
            raise ImageSoftwareError("controlled trust public key size is invalid")
        if os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o644:
            raise ImageSoftwareError("controlled trust public key permissions are invalid")
        if (
            os.name == "posix"
            and require_root_ownership
            and (details.st_uid != 0 or details.st_gid != 0)
        ):
            raise ImageSoftwareError("controlled trust public key is not owned by root")
        _validate_ed25519_public_key_pem(key)


def validate_trust_directories(mcu_trust: Path, runtime_trust: Path) -> None:
    _validate_public_key_store(mcu_trust, name_pattern=MCU_PUBLIC_KEY_NAME)
    _validate_public_key_store(runtime_trust, name_pattern=RUNTIME_PUBLIC_KEY_NAME)


def _require_python_launcher(venv: Path) -> None:
    launcher = venv / "bin/python"
    if not _lexists(launcher):
        raise ImageSoftwareError("controlled virtual environment has no Python launcher")
    mode = launcher.lstat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise ImageSoftwareError("controlled Python launcher has an invalid type")
    if os.name == "posix" and stat.S_ISREG(mode) and not mode & 0o111:
        raise ImageSoftwareError("controlled Python launcher is not executable")


def _validate_payload_semantics(payload_root: Path, lock: dict[str, Any]) -> None:
    components = lock["components"]
    runtime = payload_root / components["hardwareRuntime"]["root"]
    release_id = components["hardwareRuntime"]["releaseId"]
    for required in (
        "app",
        ".venv",
        ".venv/.ecobin-install-complete",
        "manifest.env",
        "release.env",
        "SHA256SUMS",
    ):
        if not _lexists(runtime / required):
            raise ImageSoftwareError("controlled hardware runtime release is incomplete")
    _require_python_launcher(runtime / ".venv")
    manifest = _parse_env(runtime / "manifest.env")
    release_environment = _parse_env(runtime / "release.env")
    if manifest.get("ECOBIN_RELEASE_ID") != release_id:
        raise ImageSoftwareError("hardware runtime manifest release ID differs from payload")
    if manifest.get("ECOBIN_GIT_COMMIT") != lock["sourceGitCommit"]:
        raise ImageSoftwareError("hardware runtime manifest Git commit differs from payload")
    if manifest.get("ECOBIN_PYTHON_SERIES") != "3.11":
        raise ImageSoftwareError("hardware runtime is not built for Python 3.11")
    if release_environment != {"ECOBIN_EDGE_VERSION": release_id}:
        raise ImageSoftwareError("hardware runtime release.env identity is invalid")
    _verify_runtime_checksums(runtime)
    evidence = _load_json(payload_root / "evidence/runtime-release-verification.json")
    if set(evidence) != {
        "schemaVersion",
        "releaseId",
        "sourceGitCommit",
        "archiveSha256",
        "signatureSha256",
        "signingKeyId",
    }:
        raise ImageSoftwareError("runtime signature verification evidence is malformed")
    if (
        evidence["schemaVersion"] != 1
        or evidence["releaseId"] != release_id
        or evidence["sourceGitCommit"] != lock["sourceGitCommit"]
        or not isinstance(evidence["archiveSha256"], str)
        or not HEX_64.fullmatch(evidence["archiveSha256"])
        or not isinstance(evidence["signatureSha256"], str)
        or not HEX_64.fullmatch(evidence["signatureSha256"])
        or not isinstance(evidence["signingKeyId"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", evidence["signingKeyId"])
    ):
        raise ImageSoftwareError("runtime signature verification evidence identity differs")
    for name in ("enrollment", "remoteSupport", "factoryTest"):
        _require_python_launcher(payload_root / components[name]["venv"])

    if lock["schemaVersion"] == LOCK_SCHEMA_VERSION:
        for component_name, expected_files in (
            ("communicationAgent", COMMUNICATION_AGENT_FILES),
            ("deviceUpdater", DEVICE_UPDATER_FILES),
        ):
            app = payload_root / components[component_name]["root"] / "app"
            actual_files = {
                path.relative_to(app).as_posix()
                for path in _source_files(app)
            }
            if actual_files != set(expected_files):
                raise ImageSoftwareError(
                    "permanent component source allowlist is not exact: "
                    f"{component_name}"
                )
            for name in expected_files:
                if (
                    (app / name).read_bytes()
                    != (HARDWARE_SOURCE_ROOT / name).read_bytes()
                ):
                    raise ImageSoftwareError(
                        "permanent component source differs from repository: "
                        f"{component_name}"
                    )
        updater_root = payload_root / components["deviceUpdater"]["root"]
        for relative, expected_files, repository_directory in (
            (
                "helpers",
                DEVICE_UPDATER_HELPER_FILES,
                HARDWARE_SOURCE_ROOT / "device_management/helpers",
            ),
            (
                "systemd",
                DEVICE_UPDATER_HELPER_UNIT_FILES,
                HARDWARE_SOURCE_ROOT / "device_management/helpers/systemd",
            ),
        ):
            source_root = updater_root / relative
            actual_files = {
                path.relative_to(source_root).as_posix()
                for path in _source_files(source_root)
            }
            if actual_files != set(expected_files):
                raise ImageSoftwareError(
                    f"device updater {relative} allowlist is not exact"
                )
            for name in expected_files:
                if (
                    (source_root / name).read_bytes()
                    != (repository_directory / name).read_bytes()
                ):
                    raise ImageSoftwareError(
                        f"device updater {relative} source differs: {name}"
                    )

    _validate_enrollment_config(payload_root / "config/enrollment.env")
    _validate_cellular_config(payload_root / "config/cellular.env")
    validate_trust_directories(
        payload_root / "trust/mcu-release-keys",
        payload_root / "trust/runtime-release-keys",
    )


def _verify_runtime_checksums(runtime: Path) -> None:
    checksum_path = runtime / "SHA256SUMS"
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ImageSoftwareError("runtime checksum inventory is unreadable") from exc
    seen: set[str] = set()
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ImageSoftwareError("runtime checksum inventory is malformed")
        digest, relative_text = match.groups()
        relative = _safe_relative(relative_text, field="runtime checksum path")
        if relative_text in seen or relative.parts[0] == ".venv":
            raise ImageSoftwareError("runtime checksum inventory contains an unsafe entry")
        target = runtime.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file() or _sha256(target) != digest:
            raise ImageSoftwareError("runtime checksum inventory does not verify")
        seen.add(relative_text)
    if not seen:
        raise ImageSoftwareError("runtime checksum inventory is empty")


def _require_root(rootfs: Path) -> Path:
    resolved = rootfs.resolve(strict=True)
    if rootfs.is_symlink() or not rootfs.is_dir() or resolved == Path(resolved.anchor):
        raise ImageSoftwareError("rootfs must be a dedicated regular directory")
    return resolved


def _mkdir(rootfs: Path, relative: str, mode: int = 0o755) -> Path:
    current = rootfs
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        final = index == len(parts) - 1
        if _lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise ImageSoftwareError(f"install path is not a regular directory: /{relative}")
        else:
            current.mkdir(mode=mode if final else 0o755)
        os.chmod(current, mode if final else 0o755)
        if os.name == "posix":
            os.chown(current, 0, 0)
    return current


def _install_regular(source: Path, destination: Path, mode: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise ImageSoftwareError(f"install source is not a regular file: {source}")
    if _lexists(destination):
        raise ImageSoftwareError(f"image already contains install destination: {destination}")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ImageSoftwareError(f"install destination parent is unsafe: {destination}")
    shutil.copyfile(source, destination, follow_symlinks=False)
    os.chmod(destination, mode)
    if os.name == "posix":
        os.chown(destination, 0, 0)


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir() or _lexists(destination):
        raise ImageSoftwareError("controlled tree source or destination is unsafe")
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    if os.name == "posix":
        for current, directories, files in os.walk(destination, followlinks=False):
            os.chown(current, 0, 0)
            for name in directories + files:
                path = Path(current) / name
                if not path.is_symlink():
                    os.chown(path, 0, 0)


def _source_files(directory: Path) -> list[Path]:
    result: list[Path] = []
    for path in directory.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ImageSoftwareError(f"repository source tree is unsafe: {directory}")
        if path.is_file():
            result.append(path)
    return sorted(result)


def _copy_repository_tree(source: Path, destination: Path) -> None:
    if _lexists(destination):
        raise ImageSoftwareError("repository tree destination already exists")
    destination.mkdir(mode=0o755)
    for source_file in _source_files(source):
        relative = source_file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        os.chmod(target, 0o644)
        if os.name == "posix":
            os.chown(target, 0, 0)
    # mkdir() is affected by the image builder's restrictive umask. Normalize
    # every source directory explicitly so dedicated read-only service users
    # can traverse the installed application tree.
    for current, directories, _files in os.walk(destination, followlinks=False):
        current_path = Path(current)
        os.chmod(current_path, 0o755)
        if os.name == "posix":
            os.chown(current_path, 0, 0)
        for name in directories:
            directory = current_path / name
            if not directory.is_symlink():
                os.chmod(directory, 0o755)
                if os.name == "posix":
                    os.chown(directory, 0, 0)


def _copy_selected(repository_hardware: Path, names: Iterable[str], destination: Path) -> None:
    if _lexists(destination):
        if destination.is_symlink() or not destination.is_dir():
            raise ImageSoftwareError("selected-file destination is unsafe")
    else:
        destination.mkdir(mode=0o755)
    os.chmod(destination, 0o755)
    if os.name == "posix":
        os.chown(destination, 0, 0)
    for name in names:
        source = repository_hardware / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o755)
        if os.name == "posix":
            os.chown(target.parent, 0, 0)
        _install_regular(source, target, 0o644)


def _stage_factory_app(repository_hardware: Path, app: Path) -> None:
    if _lexists(app):
        raise ImageSoftwareError("factory application destination already exists")
    app.mkdir(mode=0o755)
    os.chmod(app, 0o755)
    if os.name == "posix":
        os.chown(app, 0, 0)
    for package in FACTORY_APP_PACKAGES:
        _copy_repository_tree(repository_hardware / package, app / package)
    _copy_selected(repository_hardware, FACTORY_APP_RUNTIME_FILES, app)


def _write_json(destination: Path, value: dict[str, Any], mode: int = 0o644) -> None:
    if _lexists(destination):
        raise ImageSoftwareError(f"image already contains generated metadata: {destination}")
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(destination, mode)
    if os.name == "posix":
        os.chown(destination, 0, 0)


def _device_management_release_environment(components: dict[str, Any]) -> bytes:
    return (
        "ECOBIN_COMMUNICATION_AGENT_VERSION="
        f"{components['communicationAgent']['releaseId']}\n"
        "ECOBIN_DEVICE_UPDATER_VERSION="
        f"{components['deviceUpdater']['releaseId']}\n"
    ).encode("ascii")


def _write_device_management_release_environment(
    destination: Path, components: dict[str, Any]
) -> None:
    if _lexists(destination):
        raise ImageSoftwareError(
            "image already contains device-management release environment"
        )
    destination.write_bytes(_device_management_release_environment(components))
    os.chmod(destination, 0o644)
    if os.name == "posix":
        os.chown(destination, 0, 0)


def _install_units(
    rootfs: Path,
    repository_hardware: Path,
    device_updater_release: Path,
) -> None:
    systemd = _mkdir(rootfs, "etc/systemd/system")
    for name in MAIN_UNITS:
        _install_regular(repository_hardware / name, systemd / name, 0o644)
    for name in PRIVILEGED_HELPER_UNITS:
        _install_regular(
            device_updater_release / "systemd" / name,
            systemd / name,
            0o644,
        )
    for source_directory in (
        repository_hardware / "factory/systemd",
        repository_hardware / "first_boot/systemd",
    ):
        for source in _source_files(source_directory):
            relative = source.relative_to(source_directory)
            target = systemd / relative
            _mkdir(rootfs, target.parent.relative_to(rootfs).as_posix())
            _install_regular(source, target, 0o644)

    _install_regular(
        repository_hardware / "factory/config/sysusers.d/ecobin-factory.conf",
        _mkdir(rootfs, "usr/lib/sysusers.d") / "ecobin-factory.conf",
        0o644,
    )
    _install_regular(
        repository_hardware / "factory/config/tmpfiles.d/ecobin-factory.conf",
        _mkdir(rootfs, "usr/lib/tmpfiles.d") / "ecobin-factory.conf",
        0o644,
    )
    for source_relative, target_relative in DEVICE_MANAGEMENT_CONFIG_FILES:
        target = rootfs / target_relative
        _mkdir(rootfs, target.parent.relative_to(rootfs).as_posix())
        _install_regular(
            repository_hardware / source_relative,
            target,
            0o644,
        )
    for source_relative, target_relative, mode in DEVICE_MANAGEMENT_SUPPORT_FILES:
        target = rootfs / target_relative
        _mkdir(rootfs, target.parent.relative_to(rootfs).as_posix())
        _install_regular(repository_hardware / source_relative, target, mode)
    _install_regular(
        repository_hardware / "factory/config/NetworkManager/conf.d/90-ecobin-factory-wlan.conf",
        _mkdir(rootfs, "etc/NetworkManager/conf.d") / "90-ecobin-factory-wlan.conf",
        0o644,
    )

    for unit_name in CONFLICTING_UNITS:
        target = systemd / unit_name
        if _lexists(target):
            if not target.is_symlink() or os.readlink(target) != "/dev/null":
                raise ImageSoftwareError(f"conflicting service is not safely masked: {unit_name}")
        else:
            target.symlink_to("/dev/null")
    for relative, target_value in ENABLED_LINKS.items():
        link = systemd / relative
        link.parent.mkdir(mode=0o755, exist_ok=True)
        if _lexists(link):
            if not link.is_symlink() or os.readlink(link) != target_value:
                raise ImageSoftwareError(f"enabled unit link is ambiguous: {relative}")
        else:
            link.symlink_to(target_value)


def _assert_target_programs(rootfs: Path) -> None:
    for relative in ("usr/sbin/hostapd", "usr/sbin/dnsmasq"):
        path = rootfs / relative
        try:
            details = path.lstat()
        except OSError as exc:
            raise ImageSoftwareError(f"required target program is absent: /{relative}") from exc
        if (
            not stat.S_ISREG(details.st_mode)
            or (
                os.name == "posix"
                and (
                    details.st_mode & 0o022
                    or not details.st_mode & 0o111
                    or details.st_uid != 0
                    or details.st_gid != 0
                )
            )
        ):
            raise ImageSoftwareError(f"required target program is unsafe: /{relative}")


def _factory_contract_facts(repository_root: Path) -> dict[str, Any]:
    command = _load_json(
        repository_root
        / "contracts/examples/onenet/authorize-factory-seal.command.json"
    )
    payload = command.get("payload")
    if (
        command.get("commandType") != "AUTHORIZE_FACTORY_SEAL"
        or command.get("schemaVersion") != 2
        or command.get("payloadSchemaVersion") != 2
        or not isinstance(payload, dict)
        or payload.get("sealAuthorizationSchemaVersion") != 1
    ):
        raise ImageSoftwareError("factory-seal command contract revisions are incompatible")
    migration = (
        repository_root
        / "ecobin-bootstrap/src/main/resources/db/p0-migration/"
        "V56__factory_seal_authorization.sql"
    )
    if migration.is_symlink() or not migration.is_file():
        raise ImageSoftwareError("factory-seal V56 database migration is absent")
    migration_text = migration.read_text(encoding="utf-8")
    for required in (
        "CREATE TABLE dev_factory_seal_authorization",
        "uq_dev_factory_seal_asset_generation",
        "FOREIGN KEY (reliable_task_uid)",
    ):
        if required not in migration_text:
            raise ImageSoftwareError("factory-seal V56 database migration is incomplete")
    return {
        "oneNetCommandEnvelopeSchemaVersion": 2,
        "factorySealAuthorizationSchemaVersion": 1,
        "factorySealDatabaseMigration": "V56",
    }


def install_image_software(
    rootfs: Path,
    repository_root: Path,
    payload_root: Path,
    *,
    payload_sha256: str,
    release_id: str,
    version: str,
    git_commit: str,
) -> dict[str, Any]:
    rootfs = _require_root(rootfs)
    repository_root = repository_root.resolve(strict=True)
    repository_hardware = repository_root / "hardware"
    _require_release_id(release_id, field="image release ID")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ImageSoftwareError("image version is invalid")
    lock = load_and_validate_payload(
        payload_root.resolve(strict=True),
        expected_sha256=payload_sha256,
        expected_git_commit=git_commit,
    )
    if lock["schemaVersion"] != LOCK_SCHEMA_VERSION:
        raise ImageSoftwareError(
            "new image installation requires schema-v2 permanent components"
        )
    contract_facts = _factory_contract_facts(repository_root)
    _assert_target_programs(rootfs)
    components = lock["components"]

    # Some vendor images ship /opt/ecobin as 0700. Factory daemons deliberately
    # run as dedicated unprivileged users, so the shared code root must be
    # traversable while all mutable credentials remain protected under /etc.
    _mkdir(rootfs, "opt/ecobin", 0o755)
    _mkdir(rootfs, "opt/ecobin/factory-test", 0o755)
    _mkdir(rootfs, "opt/ecobin/remote-support", 0o755)
    _mkdir(rootfs, "opt/ecobin/communication", 0o755)
    _mkdir(rootfs, "opt/ecobin/updater", 0o755)
    hardware_parent = _mkdir(rootfs, "opt/ecobin/hardware/releases")
    hardware_release = hardware_parent / components["hardwareRuntime"]["releaseId"]
    _copy_tree(payload_root / components["hardwareRuntime"]["root"], hardware_release)
    current = rootfs / "opt/ecobin/hardware/current"
    current.symlink_to(f"releases/{components['hardwareRuntime']['releaseId']}")

    factory_parent = _mkdir(rootfs, "opt/ecobin/factory-test/releases")
    factory_release = factory_parent / components["factoryTest"]["releaseId"]
    factory_release.mkdir(mode=0o755)
    os.chmod(factory_release, 0o755)
    if os.name == "posix":
        os.chown(factory_release, 0, 0)
    _copy_tree(payload_root / components["factoryTest"]["venv"], factory_release / ".venv")
    app = factory_release / "app"
    _stage_factory_app(repository_hardware, app)
    (rootfs / "opt/ecobin/factory-test/current").symlink_to(
        f"releases/{components['factoryTest']['releaseId']}"
    )

    enrollment = _mkdir(rootfs, "opt/ecobin/enrollment")
    _copy_tree(payload_root / components["enrollment"]["venv"], enrollment / ".venv")
    for name in ENROLLMENT_FILES:
        _install_regular(repository_hardware / name, enrollment / name, 0o644)

    remote_parent = _mkdir(rootfs, "opt/ecobin/remote-support/releases")
    remote_release = remote_parent / components["remoteSupport"]["releaseId"]
    remote_release.mkdir(mode=0o755)
    os.chmod(remote_release, 0o755)
    if os.name == "posix":
        os.chown(remote_release, 0, 0)
    _copy_tree(payload_root / components["remoteSupport"]["venv"], remote_release / ".venv")
    _copy_selected(repository_hardware, REMOTE_SUPPORT_FILES, remote_release / "app")
    (rootfs / "opt/ecobin/remote-support/current").symlink_to(
        f"releases/{components['remoteSupport']['releaseId']}"
    )

    for component_name, install_name in (
        ("communicationAgent", "communication"),
        ("deviceUpdater", "updater"),
    ):
        component_parent = _mkdir(
            rootfs, f"opt/ecobin/{install_name}/releases"
        )
        component_release = (
            component_parent / components[component_name]["releaseId"]
        )
        _copy_tree(
            payload_root / components[component_name]["root"],
            component_release,
        )
        (rootfs / f"opt/ecobin/{install_name}/current").symlink_to(
            f"releases/{components[component_name]['releaseId']}"
        )

    device_updater_release = (
        rootfs
        / "opt/ecobin/updater/releases"
        / components["deviceUpdater"]["releaseId"]
    )
    helper_library = _mkdir(rootfs, "usr/lib/ecobin/device-management")
    _install_regular(
        device_updater_release / "app/local_control.py",
        helper_library / "local_control.py",
        0o644,
    )
    _copy_tree(
        device_updater_release / "helpers",
        helper_library / "helpers",
    )

    etc_ecobin = _mkdir(rootfs, "etc/ecobin", 0o750)
    _install_regular(repository_hardware / "install/hardware.env.example", etc_ecobin / "hardware.env", 0o640)
    _install_regular(payload_root / "config/enrollment.env", etc_ecobin / "enrollment.env", 0o600)
    _install_regular(payload_root / "config/cellular.env", etc_ecobin / "cellular.env", 0o600)
    for trust_name in ("mcu-release-keys", "runtime-release-keys"):
        _copy_tree(payload_root / "trust" / trust_name, etc_ecobin / trust_name)
    share_ecobin = _mkdir(rootfs, "usr/share/ecobin")
    _install_regular(payload_root / LOCK_NAME, share_ecobin / LOCK_NAME, 0o644)
    _copy_tree(
        payload_root / "trust/runtime-release-keys",
        share_ecobin / "runtime-release-keys",
    )
    _write_device_management_release_environment(
        share_ecobin / "device-management-release.env",
        components,
    )
    image_release = {
        # This is the long-lived factory image identity document schema, not
        # the software payload lock schema.  Factory acceptance and sealing
        # deliberately continue to consume schema v1; the independently
        # versioned payload format is declared by the field below.
        "schemaVersion": 1,
        "releaseId": release_id,
        "version": version,
        "gitCommit": git_commit,
        "softwarePayloadId": lock["payloadId"],
        "softwarePayloadLockSha256": payload_sha256,
        "softwarePayloadSchemaVersion": lock["schemaVersion"],
        "components": {
            name: {"releaseId": components[name]["releaseId"]}
            for name in COMPONENT_NAMES
        },
        "contracts": contract_facts,
    }
    _write_json(etc_ecobin / "image-release.json", image_release)
    # /etc/ecobin remains non-traversable to unprivileged services because it
    # also carries K1, setup access and device credentials.  The factory portal
    # receives only this immutable, non-secret release identity projection.
    _write_json(share_ecobin / "image-release.json", image_release)
    _install_units(rootfs, repository_hardware, device_updater_release)
    audit_image_software(
        rootfs,
        repository_root,
        payload_root=payload_root,
        expected_payload_sha256=payload_sha256,
        expected_release_id=release_id,
        expected_version=version,
        expected_git_commit=git_commit,
    )
    return image_release


def _assert_same_file(installed: Path, source: Path, mode: int | None = None) -> None:
    try:
        details = installed.lstat()
    except OSError as exc:
        raise ImageSoftwareError(f"installed file is absent: {installed}") from exc
    if not stat.S_ISREG(details.st_mode) or installed.read_bytes() != source.read_bytes():
        raise ImageSoftwareError(f"installed file differs from controlled source: {installed}")
    if os.name == "posix" and mode is not None and stat.S_IMODE(details.st_mode) != mode:
        raise ImageSoftwareError(f"installed file mode is invalid: {installed}")
    if os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0):
        raise ImageSoftwareError(f"installed file is not owned by root: {installed}")


def _assert_root_owned_directory(installed: Path, mode: int = 0o755) -> None:
    try:
        details = installed.lstat()
    except OSError as exc:
        raise ImageSoftwareError(f"installed directory is absent: {installed}") from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or (os.name == "posix" and stat.S_IMODE(details.st_mode) != mode)
        or (os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0))
    ):
        raise ImageSoftwareError(
            f"installed directory is not root-owned {mode:04o}: {installed}"
        )


def _assert_root_owned_directory_chain(
    rootfs: Path, installed: Path, mode: int = 0o755
) -> None:
    try:
        relative = installed.relative_to(rootfs)
    except ValueError as exc:
        raise ImageSoftwareError("installed directory escapes the image root") from exc
    current = rootfs
    for part in relative.parts:
        current /= part
        _assert_root_owned_directory(current, mode)


def _audit_public_runtime_trust_store(rootfs: Path, installed: Path) -> None:
    # Validate every path component with lstat before reading the trust store.
    # Otherwise a symlink at /usr, /usr/share, /usr/share/ecobin, or the store
    # root can redirect an otherwise byte-identical key set outside the image's
    # controlled read-only trust boundary.
    _assert_root_owned_directory_chain(rootfs, installed)
    _validate_public_key_store(
        installed,
        name_pattern=RUNTIME_PUBLIC_KEY_NAME,
        require_root_ownership=True,
    )


def _assert_tree_matches(installed: Path, source: Path) -> None:
    installed_scan = _scan_tree(installed)
    source_scan = _scan_tree(source)
    if installed_scan != source_scan:
        raise ImageSoftwareError(f"installed controlled tree differs: {installed}")


def _locked_subtree(lock: dict[str, Any], prefix: str) -> dict[str, dict[str, Any]]:
    normalized = prefix.rstrip("/")
    result: dict[str, dict[str, Any]] = {}
    entries = lock.get("entries")
    if not isinstance(entries, list):
        raise ImageSoftwareError("installed payload inventory is malformed")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ImageSoftwareError("installed payload inventory is malformed")
        path = entry["path"]
        if path == normalized:
            continue
        if path.startswith(normalized + "/"):
            relative = path[len(normalized) + 1 :]
            result[relative] = {
                key: value for key, value in entry.items() if key != "path"
            }
    if not result:
        raise ImageSoftwareError(f"installed payload inventory subtree is empty: {prefix}")
    return result


def _assert_tree_matches_lock(installed: Path, lock: dict[str, Any], prefix: str) -> None:
    if _scan_tree(installed) != _locked_subtree(lock, prefix):
        raise ImageSoftwareError(f"installed tree differs from payload lock: {installed}")


def _assert_file_matches_lock(installed: Path, lock: dict[str, Any], path: str) -> None:
    entries = lock.get("entries")
    expected = None
    if isinstance(entries, list):
        expected = next(
            (entry for entry in entries if isinstance(entry, dict) and entry.get("path") == path),
            None,
        )
    if not isinstance(expected, dict) or expected.get("type") != "file":
        raise ImageSoftwareError(f"payload lock has no required file: {path}")
    details = installed.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or (
            os.name == "posix"
            and stat.S_IMODE(details.st_mode) != int(expected["mode"], 8)
        )
        or details.st_size != expected["size"]
        or _sha256(installed) != expected["sha256"]
    ):
        raise ImageSoftwareError(f"installed file differs from payload lock: {installed}")


def _assert_repository_tree(installed: Path, source: Path) -> None:
    expected = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in _source_files(source)
    }
    actual: dict[str, bytes] = {}
    for path in _source_files(installed):
        actual[path.relative_to(installed).as_posix()] = path.read_bytes()
    if actual != expected:
        raise ImageSoftwareError(f"installed repository tree differs: {installed}")


def _audit_factory_app(app: Path, repository_hardware: Path) -> None:
    _assert_root_owned_directory(app)
    for directory in (path for path in app.rglob("*") if path.is_dir()):
        _assert_root_owned_directory(directory)
    for package in FACTORY_APP_PACKAGES:
        _assert_repository_tree(app / package, repository_hardware / package)
    for name in FACTORY_APP_RUNTIME_FILES:
        _assert_same_file(app / name, repository_hardware / name, 0o644)

    expected_files = set(FACTORY_APP_RUNTIME_FILES)
    for package in FACTORY_APP_PACKAGES:
        expected_files.update(
            f"{package}/{source.relative_to(repository_hardware / package).as_posix()}"
            for source in _source_files(repository_hardware / package)
        )
    actual_files = {
        source.relative_to(app).as_posix()
        for source in _source_files(app)
    }
    if actual_files != expected_files:
        raise ImageSoftwareError("factory application source allowlist is not exact")

    expected_top_level = set(FACTORY_APP_PACKAGES)
    expected_top_level.update(
        PurePosixPath(name).parts[0] for name in FACTORY_APP_RUNTIME_FILES
    )
    actual_top_level = {entry.name for entry in app.iterdir()}
    if actual_top_level != expected_top_level:
        raise ImageSoftwareError("factory application top-level allowlist is not exact")


def _audit_units(
    rootfs: Path,
    repository_hardware: Path,
    *,
    permanent_layer: bool,
) -> None:
    systemd = rootfs / "etc/systemd/system"
    for name in MAIN_UNITS if permanent_layer else LEGACY_MAIN_UNITS:
        _assert_same_file(systemd / name, repository_hardware / name, 0o644)
    for source_directory in (
        repository_hardware / "factory/systemd",
        repository_hardware / "first_boot/systemd",
    ):
        for source in _source_files(source_directory):
            _assert_same_file(systemd / source.relative_to(source_directory), source, 0o644)
    if permanent_layer:
        helper_unit_source = (
            repository_hardware / "device_management/helpers/systemd"
        )
        for name in PRIVILEGED_HELPER_UNITS:
            _assert_same_file(
                systemd / name,
                helper_unit_source / name,
                0o644,
            )
        for source_relative, target_relative in DEVICE_MANAGEMENT_CONFIG_FILES:
            _assert_same_file(
                rootfs / target_relative,
                repository_hardware / source_relative,
                0o644,
            )
        for source_relative, target_relative, mode in DEVICE_MANAGEMENT_SUPPORT_FILES:
            _assert_same_file(
                rootfs / target_relative,
                repository_hardware / source_relative,
                mode,
            )
    for name in CONFLICTING_UNITS:
        mask = systemd / name
        if not mask.is_symlink() or os.readlink(mask) != "/dev/null":
            raise ImageSoftwareError(f"conflicting service is not masked: {name}")
    for relative, target in ENABLED_LINKS.items():
        link = systemd / relative
        if not link.is_symlink() or os.readlink(link) != target:
            raise ImageSoftwareError(f"required service is not enabled exactly: {relative}")
    for directory in systemd.rglob("*"):
        if not directory.parent.name.endswith((".wants", ".requires", ".upholds")):
            continue
        name = directory.name
        target = os.readlink(directory).rsplit("/", 1)[-1] if directory.is_symlink() else name
        if _is_static_unit_name(name) or _is_static_unit_name(target):
            raise ImageSoftwareError(f"stage unit is independently enabled: {name}")


def audit_image_software(
    rootfs: Path,
    repository_root: Path,
    *,
    payload_root: Path | None = None,
    expected_payload_sha256: str | None = None,
    expected_release_id: str | None = None,
    expected_version: str | None = None,
    expected_git_commit: str | None = None,
) -> dict[str, Any]:
    rootfs = _require_root(rootfs)
    repository_root = repository_root.resolve(strict=True)
    repository_hardware = repository_root / "hardware"
    for shared_code_root in (
        rootfs / "opt/ecobin",
        rootfs / "opt/ecobin/factory-test",
        rootfs / "opt/ecobin/remote-support",
    ):
        _assert_root_owned_directory(shared_code_root)
    private_image_release = rootfs / "etc/ecobin/image-release.json"
    public_image_release = rootfs / "usr/share/ecobin/image-release.json"
    _assert_root_owned_directory(public_image_release.parent)
    _assert_same_file(public_image_release, private_image_release, 0o644)
    image_release = _load_json(private_image_release)
    legacy_required = {
        "schemaVersion",
        "releaseId",
        "version",
        "gitCommit",
        "softwarePayloadId",
        "softwarePayloadLockSha256",
        "components",
        "contracts",
    }
    current_required = legacy_required | {"softwarePayloadSchemaVersion"}
    if (
        (set(image_release) == legacy_required and image_release["schemaVersion"] == 1)
        or (
            set(image_release) == current_required
            and image_release["schemaVersion"] == 1
        )
    ):
        pass
    else:
        raise ImageSoftwareError("installed image release metadata is malformed")
    if image_release["contracts"] != _factory_contract_facts(repository_root):
        raise ImageSoftwareError("installed factory-seal contract facts differ")
    if expected_release_id is not None and image_release["releaseId"] != expected_release_id:
        raise ImageSoftwareError("installed image release ID differs from build")
    if expected_version is not None and image_release["version"] != expected_version:
        raise ImageSoftwareError("installed image version differs from build")
    if expected_git_commit is not None and image_release["gitCommit"] != expected_git_commit:
        raise ImageSoftwareError("installed image Git commit differs from build")
    payload_sha = expected_payload_sha256 or image_release["softwarePayloadLockSha256"]
    if not isinstance(payload_sha, str) or not HEX_64.fullmatch(payload_sha):
        raise ImageSoftwareError("installed payload identity is malformed")
    installed_lock = rootfs / "usr/share/ecobin" / LOCK_NAME
    if _sha256(installed_lock) != payload_sha:
        raise ImageSoftwareError("installed software payload lock digest differs")
    if payload_root is not None:
        lock = load_and_validate_payload(
            payload_root.resolve(strict=True),
            expected_sha256=payload_sha,
            expected_git_commit=image_release["gitCommit"],
        )
        _assert_same_file(installed_lock, payload_root / LOCK_NAME, 0o644)
    else:
        lock = _load_json(installed_lock)
        if set(lock) != {
            "schemaVersion",
            "lockState",
            "payloadId",
            "sourceGitCommit",
            "components",
            "entries",
        }:
            raise ImageSoftwareError("installed software payload lock fields are not exact")
        _component_names_for_lock_schema(lock.get("schemaVersion"))
        if lock.get("lockState") != "LOCKED":
            raise ImageSoftwareError("installed software payload lock is not locked")
        _require_release_id(lock.get("payloadId"), field="payloadId")
        if not isinstance(lock.get("sourceGitCommit"), str) or not GIT_COMMIT.fullmatch(
            lock["sourceGitCommit"]
        ):
            raise ImageSoftwareError("installed software payload Git identity is malformed")
    lock_schema_version = lock["schemaVersion"]
    component_names = _component_names_for_lock_schema(lock_schema_version)
    if (
        image_release["softwarePayloadId"] != lock.get("payloadId")
        or image_release["gitCommit"] != lock.get("sourceGitCommit")
    ):
        raise ImageSoftwareError(
            "installed image identity differs from its software payload lock"
        )
    if (
        image_release["schemaVersion"] != 1
        or image_release.get("softwarePayloadSchemaVersion", 1)
        != lock_schema_version
    ):
        raise ImageSoftwareError(
            "installed image and software payload schema versions differ"
        )
    permanent_layer = lock_schema_version == LOCK_SCHEMA_VERSION
    if permanent_layer:
        for shared_code_root in (
            rootfs / "opt/ecobin/communication",
            rootfs / "opt/ecobin/updater",
        ):
            _assert_root_owned_directory(shared_code_root)
    components = lock.get("components")
    if not isinstance(components, dict) or set(components) != set(component_names):
        raise ImageSoftwareError("installed component identity set is incomplete")
    for name in component_names:
        component = components[name]
        expected_fields = (
            {"releaseId", "root"}
            if name in {"hardwareRuntime", *PERMANENT_COMPONENT_NAMES}
            else {"releaseId", "venv"}
            if name in {"enrollment", "remoteSupport", "factoryTest"}
            else {"releaseId"}
        )
        if not isinstance(component, dict) or set(component) != expected_fields:
            raise ImageSoftwareError("installed component identity is malformed")
        _require_release_id(component.get("releaseId"), field=f"{name}.releaseId")
        if (
            permanent_layer
            and name in PERMANENT_COMPONENT_NAMES
            and len(component["releaseId"]) > 32
        ):
            raise ImageSoftwareError(
                f"{name}.releaseId exceeds the device fact limit"
            )
    expected_components = {
        name: {"releaseId": components[name]["releaseId"]}
        for name in component_names
    }
    if image_release["components"] != expected_components:
        raise ImageSoftwareError("installed component release IDs are inconsistent")

    hardware_release = rootfs / "opt/ecobin/hardware/releases" / components["hardwareRuntime"]["releaseId"]
    hardware_current = rootfs / "opt/ecobin/hardware/current"
    if not hardware_current.is_symlink() or os.readlink(hardware_current) != f"releases/{components['hardwareRuntime']['releaseId']}":
        raise ImageSoftwareError("hardware runtime current link is invalid")
    runtime_app_files = (
        RUNTIME_APP_FILES if permanent_layer else LEGACY_RUNTIME_APP_FILES
    )
    for name in runtime_app_files:
        _assert_same_file(hardware_release / "app" / name, repository_hardware / name)
    actual_runtime_files = {
        path.relative_to(hardware_release / "app").as_posix()
        for path in (hardware_release / "app").rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_runtime_files != set(runtime_app_files):
        raise ImageSoftwareError("hardware runtime app allowlist is not exact")
    if payload_root is not None:
        _assert_tree_matches(hardware_release, payload_root / components["hardwareRuntime"]["root"])
    _assert_tree_matches_lock(
        hardware_release,
        lock,
        components["hardwareRuntime"]["root"],
    )

    factory_release = rootfs / "opt/ecobin/factory-test/releases" / components["factoryTest"]["releaseId"]
    _assert_root_owned_directory(factory_release)
    _audit_factory_app(factory_release / "app", repository_hardware)
    if payload_root is not None:
        _assert_tree_matches(factory_release / ".venv", payload_root / components["factoryTest"]["venv"])
    _assert_tree_matches_lock(
        factory_release / ".venv", lock, components["factoryTest"]["venv"]
    )
    enrollment = rootfs / "opt/ecobin/enrollment"
    for name in ENROLLMENT_FILES:
        _assert_same_file(enrollment / name, repository_hardware / name, 0o644)
    if payload_root is not None:
        _assert_tree_matches(enrollment / ".venv", payload_root / components["enrollment"]["venv"])
    _assert_tree_matches_lock(
        enrollment / ".venv", lock, components["enrollment"]["venv"]
    )
    remote_release = rootfs / "opt/ecobin/remote-support/releases" / components["remoteSupport"]["releaseId"]
    remote_current = rootfs / "opt/ecobin/remote-support/current"
    if (
        not remote_current.is_symlink()
        or os.readlink(remote_current)
        != f"releases/{components['remoteSupport']['releaseId']}"
    ):
        raise ImageSoftwareError("remote support current link is invalid")
    _assert_root_owned_directory(remote_release)
    _assert_root_owned_directory(remote_release / "app")
    for name in REMOTE_SUPPORT_FILES:
        _assert_same_file(remote_release / "app" / name, repository_hardware / name, 0o644)
    if payload_root is not None:
        _assert_tree_matches(remote_release / ".venv", payload_root / components["remoteSupport"]["venv"])
    _assert_tree_matches_lock(
        remote_release / ".venv", lock, components["remoteSupport"]["venv"]
    )

    if permanent_layer:
        for component_name, install_name, expected_files in (
            ("communicationAgent", "communication", COMMUNICATION_AGENT_FILES),
            ("deviceUpdater", "updater", DEVICE_UPDATER_FILES),
        ):
            release_id = components[component_name]["releaseId"]
            component_release = (
                rootfs / f"opt/ecobin/{install_name}/releases" / release_id
            )
            component_current = rootfs / f"opt/ecobin/{install_name}/current"
            if (
                not component_current.is_symlink()
                or os.readlink(component_current) != f"releases/{release_id}"
            ):
                raise ImageSoftwareError(
                    f"permanent component current link is invalid: {component_name}"
                )
            _assert_root_owned_directory(component_release)
            _assert_root_owned_directory(component_release / "app")
            for name in expected_files:
                _assert_same_file(
                    component_release / "app" / name,
                    repository_hardware / name,
                    0o644,
                )
            if payload_root is not None:
                _assert_tree_matches(
                    component_release,
                    payload_root / components[component_name]["root"],
                )
            _assert_tree_matches_lock(
                component_release,
                lock,
                components[component_name]["root"],
            )

        helper_library = rootfs / "usr/lib/ecobin/device-management"
        _assert_root_owned_directory(helper_library)
        _assert_root_owned_directory(helper_library / "helpers")
        updater_release = (
            rootfs
            / "opt/ecobin/updater/releases"
            / components["deviceUpdater"]["releaseId"]
        )
        _assert_same_file(
            helper_library / "local_control.py",
            repository_hardware / "local_control.py",
            0o644,
        )
        _assert_same_file(
            helper_library / "local_control.py",
            updater_release / "app/local_control.py",
            0o644,
        )
        actual_helper_files = {
            path.relative_to(helper_library / "helpers").as_posix()
            for path in _source_files(helper_library / "helpers")
        }
        if actual_helper_files != set(DEVICE_UPDATER_HELPER_FILES):
            raise ImageSoftwareError(
                "installed privileged helper allowlist is not exact"
            )
        for name in DEVICE_UPDATER_HELPER_FILES:
            _assert_same_file(
                helper_library / "helpers" / name,
                repository_hardware / "device_management/helpers" / name,
                0o644,
            )
            _assert_same_file(
                helper_library / "helpers" / name,
                updater_release / "helpers" / name,
                0o644,
            )

    _assert_same_file(rootfs / "etc/ecobin/hardware.env", repository_hardware / "install/hardware.env.example", 0o640)
    if payload_root is not None:
        _assert_same_file(rootfs / "etc/ecobin/enrollment.env", payload_root / "config/enrollment.env", 0o600)
        _assert_same_file(rootfs / "etc/ecobin/cellular.env", payload_root / "config/cellular.env", 0o600)
        for trust_name in ("mcu-release-keys", "runtime-release-keys"):
            _assert_tree_matches(rootfs / "etc/ecobin" / trust_name, payload_root / "trust" / trust_name)
    _assert_file_matches_lock(rootfs / "etc/ecobin/enrollment.env", lock, "config/enrollment.env")
    _assert_file_matches_lock(rootfs / "etc/ecobin/cellular.env", lock, "config/cellular.env")
    for trust_name in ("mcu-release-keys", "runtime-release-keys"):
        _assert_tree_matches_lock(
            rootfs / "etc/ecobin" / trust_name,
            lock,
            f"trust/{trust_name}",
        )
    if permanent_layer:
        public_runtime_trust = rootfs / "usr/share/ecobin/runtime-release-keys"
        _audit_public_runtime_trust_store(rootfs, public_runtime_trust)
        if payload_root is not None:
            _assert_tree_matches(
                public_runtime_trust,
                payload_root / "trust/runtime-release-keys",
            )
        _assert_tree_matches_lock(
            public_runtime_trust,
            lock,
            "trust/runtime-release-keys",
        )
        release_environment = (
            rootfs / "usr/share/ecobin/device-management-release.env"
        )
        try:
            release_environment_details = release_environment.lstat()
        except OSError as exc:
            raise ImageSoftwareError(
                "installed device-management release environment is absent"
            ) from exc
        if (
            not stat.S_ISREG(release_environment_details.st_mode)
            or (
                os.name == "posix"
                and (
                    stat.S_IMODE(release_environment_details.st_mode) != 0o644
                    or release_environment_details.st_uid != 0
                    or release_environment_details.st_gid != 0
                )
            )
        ):
            raise ImageSoftwareError(
                "installed device-management release environment metadata differs"
            )
        if (
            release_environment.read_bytes()
            != _device_management_release_environment(components)
        ):
            raise ImageSoftwareError(
                "installed device-management release environment differs"
            )
    _validate_enrollment_config(rootfs / "etc/ecobin/enrollment.env")
    _validate_cellular_config(rootfs / "etc/ecobin/cellular.env")
    validate_trust_directories(
        rootfs / "etc/ecobin/mcu-release-keys",
        rootfs / "etc/ecobin/runtime-release-keys",
    )
    _audit_units(
        rootfs,
        repository_hardware,
        permanent_layer=permanent_layer,
    )
    _assert_target_programs(rootfs)
    return image_release


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("validate-payload", "validate-trust", "install", "audit"),
    )
    parser.add_argument("--rootfs", type=Path)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--payload-sha256")
    parser.add_argument("--release-id")
    parser.add_argument("--version")
    parser.add_argument("--git-commit")
    parser.add_argument("--mcu-trust", type=Path)
    parser.add_argument("--runtime-trust", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "validate-payload":
            if not all((args.payload, args.payload_sha256, args.git_commit)):
                raise ImageSoftwareError(
                    "validate-payload requires payload, payload SHA-256 and Git commit"
                )
            load_and_validate_payload(
                args.payload,
                expected_sha256=args.payload_sha256,
                expected_git_commit=args.git_commit,
            )
        elif args.command == "validate-trust":
            if not args.mcu_trust or not args.runtime_trust:
                raise ImageSoftwareError(
                    "validate-trust requires MCU and runtime trust directories"
                )
            validate_trust_directories(args.mcu_trust, args.runtime_trust)
        elif args.command == "install":
            if not all(
                (
                    args.rootfs,
                    args.repository_root,
                    args.payload,
                    args.payload_sha256,
                    args.release_id,
                    args.version,
                    args.git_commit,
                )
            ):
                raise ImageSoftwareError("install requires payload and all build identities")
            install_image_software(
                args.rootfs,
                args.repository_root,
                args.payload,
                payload_sha256=args.payload_sha256,
                release_id=args.release_id,
                version=args.version,
                git_commit=args.git_commit,
            )
        else:
            if not args.rootfs or not args.repository_root:
                raise ImageSoftwareError("audit requires rootfs and repository root")
            audit_image_software(
                args.rootfs,
                args.repository_root,
                payload_root=args.payload,
                expected_payload_sha256=args.payload_sha256,
                expected_release_id=args.release_id,
                expected_version=args.version,
                expected_git_commit=args.git_commit,
            )
    except (ImageSoftwareError, OSError, ValueError) as exc:
        print(f"image-software-{args.command}=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"image-software-{args.command}=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
