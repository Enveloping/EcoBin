"""Load and validate the post-enrollment device credential bundle.

The bundle is the only production source for OneNet and remote-support
secrets.  Environment variables remain supported for the three legacy
OneNet values so an already deployed device can be adopted without changing
all of its configuration in one step.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


DEFAULT_CREDENTIALS_PATH = "/etc/ecobin/device-credentials.json"
DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH = (
    "/etc/ecobin/remote-support-credentials.json"
)
_SSH_PUBLIC_KEY = re.compile(
    r"^ssh-ed25519 [A-Za-z0-9+/]+={0,2}(?: [^\r\n]{1,128})?$"
)
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class OneNetCredentials:
    product_id: str
    device_name: str
    device_id: str
    device_key: str
    mqtt_host: str
    mqtt_port: int


@dataclass(frozen=True)
class RemoteSupportCredentials:
    server_host: str
    server_port: int
    server_user: str
    server_host_public_key: str
    identity_private_key: str
    jump_user: str
    maintenance_principal: str
    maintenance_ca_public_key: str
    lease_guard_command: str = "ecobin-lease-guard"

    def known_hosts_name(self) -> str:
        if self.server_port == 22:
            return self.server_host
        return f"[{self.server_host}]:{self.server_port}"

    def known_hosts_line(self) -> str:
        key_parts = self.server_host_public_key.split()
        return f"{self.known_hosts_name()} {key_parts[0]} {key_parts[1]}\n"


@dataclass(frozen=True)
class DeviceCredentials:
    schema_version: int
    asset_uid: str
    hardware_sn: str
    model_code: str
    expected_port_count: int
    ssh_host_public_key: str
    one_net: OneNetCredentials
    remote_support: RemoteSupportCredentials
    device_entry_url: str | None
    raw: dict[str, Any]


def credentials_path_from_environment() -> str:
    return os.getenv(
        "ECOBIN_DEVICE_CREDENTIALS_PATH",
        DEFAULT_CREDENTIALS_PATH,
    ).strip()


def load_device_credentials(
    path: str | os.PathLike[str] | None = None,
    *,
    required: bool = False,
) -> DeviceCredentials | None:
    """Read a mode-0600 JSON bundle and validate every security boundary."""

    resolved = Path(path or credentials_path_from_environment())
    try:
        raw_bytes = resolved.read_bytes()
    except FileNotFoundError:
        if required:
            raise ValueError(f"device credentials do not exist: {resolved}")
        return None
    if os.name != "nt":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise ValueError("device credentials must not be group/world accessible")
    if not raw_bytes or len(raw_bytes) > 64 * 1024:
        raise ValueError("device credentials must contain 1..65536 bytes")
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("device credentials are not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ValueError("device credentials root must be an object")
    return validate_device_credentials(document)


def load_remote_support_credentials(
    path: str | os.PathLike[str] = DEFAULT_REMOTE_SUPPORT_CREDENTIALS_PATH,
    *,
    required: bool = False,
) -> RemoteSupportCredentials | None:
    """Read the tunnel-only credential document used by the agent."""

    resolved = Path(path)
    try:
        raw_bytes = resolved.read_bytes()
    except FileNotFoundError:
        if required:
            raise ValueError(
                f"remote support credentials do not exist: {resolved}"
            )
        return None
    if os.name != "nt":
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise ValueError(
                "remote support credentials must not be group/world accessible"
            )
    if not raw_bytes or len(raw_bytes) > 16 * 1024:
        raise ValueError(
            "remote support credentials must contain 1..16384 bytes"
        )
    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "remote support credentials are not valid UTF-8 JSON"
        ) from error
    if not isinstance(document, dict):
        raise ValueError("remote support credentials root must be an object")
    return validate_remote_support_credentials(document)


def validate_remote_support_credentials(
    document: dict[str, Any],
) -> RemoteSupportCredentials:
    if set(document) != {"schemaVersion", "hardwareSn", "remoteSupport"}:
        raise ValueError("remote support credential fields are invalid")
    if document.get("schemaVersion") != 1:
        raise ValueError(
            "unsupported remote support credentials schemaVersion"
        )
    hardware_sn = _required_text(document, "hardwareSn", maximum=64)
    return _validate_remote_support_section(
        _required_object(document, "remoteSupport"),
        hardware_sn,
    )


def validate_device_credentials(document: dict[str, Any]) -> DeviceCredentials:
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported device credentials schemaVersion")
    asset_uid = _required_text(document, "assetUid", maximum=36)
    _require_uuid4(asset_uid, "assetUid")
    hardware_sn = _required_text(document, "hardwareSn", maximum=64)
    if document.get("modelCode") != "EC-M0":
        raise ValueError("device credentials modelCode must be EC-M0")
    if document.get("expectedPortCount") != 1:
        raise ValueError("device credentials expectedPortCount must be 1")
    ssh_host_public_key = _required_text(
        document,
        "sshHostPublicKey",
        maximum=1024,
    )
    if not _SSH_PUBLIC_KEY.fullmatch(ssh_host_public_key):
        raise ValueError("sshHostPublicKey must be ssh-ed25519")
    _validate_ed25519_blob(ssh_host_public_key, "sshHostPublicKey")
    one_net_raw = _required_object(document, "oneNet")
    mqtt_port = one_net_raw.get("mqttPort")
    if (
        isinstance(mqtt_port, bool)
        or not isinstance(mqtt_port, int)
        or not 1 <= mqtt_port <= 65535
    ):
        raise ValueError("OneNet mqttPort is out of range")
    one_net = OneNetCredentials(
        product_id=_required_text(one_net_raw, "productId", maximum=64),
        device_name=_required_text(one_net_raw, "deviceName", maximum=64),
        device_id=_required_text(one_net_raw, "deviceId", maximum=64),
        device_key=_required_text(one_net_raw, "deviceKey", maximum=512),
        mqtt_host=_required_text(one_net_raw, "mqttHost", maximum=253),
        mqtt_port=mqtt_port,
    )
    if one_net.device_name != hardware_sn:
        raise ValueError("OneNet deviceName must equal hardwareSn")

    remote_support = _validate_remote_support_section(
        _required_object(document, "remoteSupport"),
        hardware_sn,
    )

    entry_url = document.get("deviceEntryUrl")
    if entry_url is not None:
        if (
            not isinstance(entry_url, str)
            or not entry_url.startswith("https://")
            or not 1 <= len(entry_url) <= 192
        ):
            raise ValueError("deviceEntryUrl must be an HTTPS URL within 192 characters")

    return DeviceCredentials(
        schema_version=1,
        asset_uid=asset_uid,
        hardware_sn=hardware_sn,
        model_code="EC-M0",
        expected_port_count=1,
        ssh_host_public_key=ssh_host_public_key,
        one_net=one_net,
        remote_support=remote_support,
        device_entry_url=entry_url,
        raw=dict(document),
    )


def _validate_remote_support_section(
    remote_raw: dict[str, Any],
    hardware_sn: str,
) -> RemoteSupportCredentials:
    host = _required_text(remote_raw, "tunnelHost", maximum=253)
    if not _HOST.fullmatch(host) or ".." in host:
        raise ValueError("remote support serverHost is invalid")
    port = remote_raw.get("tunnelSshPort")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("remote support serverPort is out of range")
    user = _required_text(remote_raw, "tunnelUser", maximum=64)
    if not _USER.fullmatch(user):
        raise ValueError("remote support serverUser is invalid")
    host_key = _required_text(
        remote_raw,
        "tunnelServerHostPublicKey",
        maximum=1024,
    )
    if not _SSH_PUBLIC_KEY.fullmatch(host_key):
        raise ValueError("remote support server host key must be ssh-ed25519")
    _validate_ed25519_blob(host_key, "remote support server host key")
    identity = _required_text(
        remote_raw,
        "tunnelIdentityPrivateKey",
        maximum=8192,
        strip=False,
    )
    if not (
        identity.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n")
        and identity.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")
    ):
        raise ValueError("remote support identity must be an OpenSSH private key")
    try:
        tunnel_private = serialization.load_ssh_private_key(
            identity.encode("ascii"),
            password=None,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("remote support identity private key is invalid") from error
    if not isinstance(tunnel_private, ed25519.Ed25519PrivateKey):
        raise ValueError("remote support identity private key must be Ed25519")
    jump_user = _required_text(remote_raw, "jumpUser", maximum=64)
    if not _USER.fullmatch(jump_user):
        raise ValueError("remote support jumpUser is invalid")
    maintenance_principal = _required_text(
        remote_raw,
        "maintenancePrincipal",
        maximum=128,
    )
    if maintenance_principal != f"ecobin-device-{hardware_sn}":
        raise ValueError("remote support maintenancePrincipal is invalid")
    maintenance_ca = _required_text(
        remote_raw,
        "maintenanceCaPublicKey",
        maximum=1024,
    )
    if not _SSH_PUBLIC_KEY.fullmatch(maintenance_ca):
        raise ValueError("maintenance CA public key must be ssh-ed25519")
    _validate_ed25519_blob(maintenance_ca, "maintenance CA public key")

    return RemoteSupportCredentials(
        server_host=host,
        server_port=port,
        server_user=user,
        server_host_public_key=host_key,
        identity_private_key=identity,
        jump_user=jump_user,
        maintenance_principal=maintenance_principal,
        maintenance_ca_public_key=maintenance_ca,
    )


def effective_onenet_credentials(
    bundle: DeviceCredentials | None,
    environment: dict[str, str] | None = None,
) -> OneNetCredentials:
    """Resolve legacy environment values first, then the enrolled bundle.

    Requiring all three values from the same source prevents a partially
    migrated environment from pairing a legacy key with a newly enrolled
    device name.
    """

    source = environment if environment is not None else os.environ
    legacy = tuple(
        (source.get(name) or "").strip()
        for name in (
            "ECOBIN_PRODUCT_ID",
            "ECOBIN_DEVICE_NAME",
            "ECOBIN_DEVICE_KEY",
        )
    )
    if all(legacy):
        return OneNetCredentials(
            product_id=legacy[0],
            device_name=legacy[1],
            device_id="",
            device_key=legacy[2],
            mqtt_host="studio-mqtt.heclouds.com",
            mqtt_port=1883,
        )
    if any(legacy):
        raise ValueError("legacy OneNet environment credentials are incomplete")
    if bundle is not None:
        return bundle.one_net
    return OneNetCredentials(
        product_id="",
        device_name="",
        device_id="",
        device_key="",
        mqtt_host="studio-mqtt.heclouds.com",
        mqtt_port=1883,
    )


def _required_object(parent: dict[str, Any], field: str) -> dict[str, Any]:
    value = parent.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _required_text(
    parent: dict[str, Any],
    field: str,
    *,
    maximum: int,
    strip: bool = True,
) -> str:
    value = parent.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip() if strip else value
    if not 1 <= len(normalized) <= maximum or "\x00" in normalized:
        raise ValueError(f"{field} length is invalid")
    return normalized


def _validate_ed25519_blob(public_key: str, field: str) -> None:
    try:
        blob = base64.b64decode(public_key.split()[1], validate=True)
    except (IndexError, binascii.Error) as error:
        raise ValueError(f"{field} encoding is invalid") from error
    prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(blob) != len(prefix) + 32 or not blob.startswith(prefix):
        raise ValueError(f"{field} encoding is invalid")


def _require_uuid4(value: str, field: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
