"""Strict minimal OneNet credential owned by the communication account."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_COMMUNICATION_CREDENTIALS_PATH = (
    "/var/lib/ecobin/communication/onenet-credentials.json"
)
MAX_CREDENTIAL_BYTES = 4096
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")


@dataclass(frozen=True, slots=True)
class CommunicationCredentials:
    product_id: str
    device_name: str
    device_key: str
    mqtt_host: str
    mqtt_port: int


def load_communication_credentials(
    path: str | os.PathLike[str] = DEFAULT_COMMUNICATION_CREDENTIALS_PATH,
) -> CommunicationCredentials:
    """Load a regular, single-link, mode-0600 credential document."""

    source = Path(path)
    try:
        details = source.lstat()
    except OSError as error:
        raise ValueError("communication credentials are unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("communication credentials must be a regular file")
    if details.st_nlink != 1:
        raise ValueError("communication credentials must not be hard-linked")
    if os.name == "posix":
        if details.st_uid != os.geteuid():
            raise ValueError("communication credentials have an unexpected owner")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise ValueError("communication credentials must use mode 0600")
    try:
        raw = source.read_bytes()
    except OSError as error:
        raise ValueError("communication credentials cannot be read") from error
    if not 1 <= len(raw) <= MAX_CREDENTIAL_BYTES:
        raise ValueError("communication credential size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("communication credentials are not valid JSON") from error
    return validate_communication_credentials_document(document)


def validate_communication_credentials_document(
    document: Any,
) -> CommunicationCredentials:
    """Validate the projection content without opening its owner-only file."""

    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "productId",
        "deviceName",
        "deviceKey",
        "mqttHost",
        "mqttPort",
    }:
        raise ValueError("communication credential fields are invalid")
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise ValueError("communication credential schema is unsupported")
    product_id = _require_identifier(document["productId"], "productId")
    device_name = _require_identifier(document["deviceName"], "deviceName")
    device_key = document["deviceKey"]
    if (
        not isinstance(device_key, str)
        or not 8 <= len(device_key) <= 512
        or device_key != device_key.strip()
    ):
        raise ValueError("deviceKey is invalid")
    try:
        decoded_key = base64.b64decode(device_key, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("deviceKey is not canonical Base64") from error
    if not 8 <= len(decoded_key) <= 384:
        raise ValueError("deviceKey decoded size is invalid")
    mqtt_host = document["mqttHost"]
    if (
        not isinstance(mqtt_host, str)
        or _HOST.fullmatch(mqtt_host) is None
        or ".." in mqtt_host
    ):
        raise ValueError("mqttHost is invalid")
    mqtt_port = document["mqttPort"]
    if (
        isinstance(mqtt_port, bool)
        or not isinstance(mqtt_port, int)
        or not 1 <= mqtt_port <= 65535
    ):
        raise ValueError("mqttPort is invalid")
    return CommunicationCredentials(
        product_id=product_id,
        device_name=device_name,
        device_key=device_key,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
    )


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


__all__ = [
    "CommunicationCredentials",
    "DEFAULT_COMMUNICATION_CREDENTIALS_PATH",
    "load_communication_credentials",
    "validate_communication_credentials_document",
]
