"""Strict non-secret device identity owned by the business runtime."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BUSINESS_IDENTITY_PATH = (
    "/var/lib/ecobin/business/device-identity.json"
)
MAX_IDENTITY_BYTES = 4096
_DEVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class BusinessDeviceIdentity:
    asset_uid: str
    device_name: str
    model_code: str
    expected_port_count: int
    device_entry_url: str | None


def validate_business_identity_document(
    document: Any,
) -> BusinessDeviceIdentity:
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "assetUid",
        "deviceName",
        "modelCode",
        "expectedPortCount",
        "deviceEntryUrl",
    }:
        raise ValueError("business device identity fields are invalid")
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise ValueError("business device identity schema is unsupported")
    asset_uid = document["assetUid"]
    try:
        parsed_uid = uuid.UUID(asset_uid, version=4)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("business assetUid is invalid") from error
    if str(parsed_uid) != asset_uid or parsed_uid.version != 4:
        raise ValueError("business assetUid is invalid")
    device_name = document["deviceName"]
    if (
        not isinstance(device_name, str)
        or _DEVICE_NAME.fullmatch(device_name) is None
    ):
        raise ValueError("business deviceName is invalid")
    if document["modelCode"] != "EC-M0":
        raise ValueError("business modelCode is invalid")
    if (
        type(document["expectedPortCount"]) is not int
        or document["expectedPortCount"] != 1
    ):
        raise ValueError("business expectedPortCount is invalid")
    entry_url = document["deviceEntryUrl"]
    if entry_url is not None and (
        not isinstance(entry_url, str)
        or not entry_url.startswith("https://")
        or not 1 <= len(entry_url) <= 192
        or any(character in entry_url for character in "\r\n\x00")
    ):
        raise ValueError("business deviceEntryUrl is invalid")
    return BusinessDeviceIdentity(
        asset_uid=asset_uid,
        device_name=device_name,
        model_code="EC-M0",
        expected_port_count=1,
        device_entry_url=entry_url,
    )


def load_business_identity(
    path: str | os.PathLike[str] = DEFAULT_BUSINESS_IDENTITY_PATH,
) -> BusinessDeviceIdentity:
    source = Path(path)
    try:
        details = source.lstat()
    except OSError as error:
        raise ValueError("business device identity is unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("business device identity must be a regular file")
    if details.st_nlink != 1:
        raise ValueError("business device identity must not be hard-linked")
    if os.name == "posix":
        if details.st_uid != os.geteuid():
            raise ValueError("business device identity has an unexpected owner")
        if stat.S_IMODE(details.st_mode) != 0o600:
            raise ValueError("business device identity must use mode 0600")
    try:
        raw = source.read_bytes()
    except OSError as error:
        raise ValueError("business device identity cannot be read") from error
    if not 1 <= len(raw) <= MAX_IDENTITY_BYTES:
        raise ValueError("business device identity size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("business device identity is not valid JSON") from error
    return validate_business_identity_document(document)


__all__ = [
    "BusinessDeviceIdentity",
    "DEFAULT_BUSINESS_IDENTITY_PATH",
    "load_business_identity",
    "validate_business_identity_document",
]
