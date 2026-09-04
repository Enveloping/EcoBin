"""Strict persistent state for the one-way business-runtime cutover.

The first-boot coordinator and the root-only cutover tool intentionally share
this dependency-free validator.  A malformed, ambiguous, or unexpectedly
owned marker is never interpreted as permission to start either runtime.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

CUTOVER_STATE_ROOT = Path(
    "/var/lib/ecobin/privileged/business-runtime-cutover"
)
CUTOVER_PENDING_MARKER = CUTOVER_STATE_ROOT / "pending.json"
CUTOVER_ACTIVE_MARKER = CUTOVER_STATE_ROOT / "active.json"
MARKER_SCHEMA_VERSION = 1
MAX_MARKER_BYTES = 8192

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PENDING_FIELDS = frozenset(
    {
        "schemaVersion",
        "phase",
        "operationUid",
        "evidenceSha256",
        "startedAt",
    }
)
_ACTIVE_FIELDS = frozenset(
    {
        "schemaVersion",
        "phase",
        "operationUid",
        "evidenceSha256",
        "sourceSnapshotSha256",
        "initialBusinessDatabaseSha256",
        "businessDatabaseSize",
        "migratedPhotoFileCount",
        "migratedPhotoReferenceCount",
        "edgeBootId",
        "configurationMigrated",
        "configurationSha256",
        "completedAt",
    }
)


class BusinessRuntimeCutoverMode(str, Enum):
    LEGACY = "LEGACY"
    PREPARING = "PREPARING"
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class BusinessRuntimeCutoverInspection:
    mode: BusinessRuntimeCutoverMode
    marker: Mapping[str, Any] | None = None
    error_code: str | None = None


def inspect_business_runtime_cutover(
    *,
    pending_path: str | os.PathLike[str] = CUTOVER_PENDING_MARKER,
    active_path: str | os.PathLike[str] = CUTOVER_ACTIVE_MARKER,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> BusinessRuntimeCutoverInspection:
    """Classify the persistent selector without ever raising to first boot."""

    pending = Path(pending_path)
    active = Path(active_path)
    pending_exists = os.path.lexists(pending)
    active_exists = os.path.lexists(active)
    if not pending_exists and not active_exists:
        return BusinessRuntimeCutoverInspection(
            BusinessRuntimeCutoverMode.LEGACY
        )
    if pending_exists and active_exists:
        return BusinessRuntimeCutoverInspection(
            BusinessRuntimeCutoverMode.INVALID,
            error_code="BUSINESS_RUNTIME_CUTOVER_MARKERS_AMBIGUOUS",
        )
    try:
        if pending_exists:
            marker = read_cutover_marker(
                pending,
                expected_phase="PREPARING",
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            return BusinessRuntimeCutoverInspection(
                BusinessRuntimeCutoverMode.PREPARING,
                marker=marker,
            )
        marker = read_cutover_marker(
            active,
            expected_phase="ACTIVE",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        return BusinessRuntimeCutoverInspection(
            BusinessRuntimeCutoverMode.ACTIVE,
            marker=marker,
        )
    except (OSError, ValueError):
        return BusinessRuntimeCutoverInspection(
            BusinessRuntimeCutoverMode.INVALID,
            error_code="BUSINESS_RUNTIME_CUTOVER_MARKER_INVALID",
        )


def read_cutover_marker(
    path: str | os.PathLike[str],
    *,
    expected_phase: str,
    expected_uid: int = 0,
    expected_gid: int = 0,
) -> dict[str, Any]:
    marker_path = Path(path)
    before = marker_path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (
            os.name == "posix"
            and (
                stat.S_IMODE(before.st_mode) != 0o600
                or (before.st_uid, before.st_gid)
                != (expected_uid, expected_gid)
            )
        )
        or not 1 <= before.st_size <= MAX_MARKER_BYTES
    ):
        raise ValueError("business runtime cutover marker is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(marker_path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise ValueError("business runtime cutover marker changed")
        raw = os.read(descriptor, MAX_MARKER_BYTES + 1)
    finally:
        os.close(descriptor)
    if not 1 <= len(raw) <= MAX_MARKER_BYTES:
        raise ValueError("business runtime cutover marker size is invalid")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("business runtime cutover marker is invalid JSON") from error
    validate_cutover_marker(document, expected_phase=expected_phase)
    return document


def validate_cutover_marker(
    document: object,
    *,
    expected_phase: str,
) -> None:
    if expected_phase not in {"PREPARING", "ACTIVE"}:
        raise ValueError("business runtime cutover phase is unsupported")
    expected_fields = (
        _PENDING_FIELDS if expected_phase == "PREPARING" else _ACTIVE_FIELDS
    )
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ValueError("business runtime cutover marker fields are invalid")
    if (
        type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != MARKER_SCHEMA_VERSION
        or document["phase"] != expected_phase
    ):
        raise ValueError("business runtime cutover marker version is invalid")
    _require_uuid4(document["operationUid"])
    _require_sha256(document["evidenceSha256"])
    timestamp_field = "startedAt" if expected_phase == "PREPARING" else "completedAt"
    _require_utc_timestamp(document[timestamp_field])
    if expected_phase == "PREPARING":
        return

    _require_sha256(document["sourceSnapshotSha256"])
    _require_sha256(document["initialBusinessDatabaseSha256"])
    for field in (
        "businessDatabaseSize",
        "migratedPhotoFileCount",
        "migratedPhotoReferenceCount",
        "edgeBootId",
    ):
        value = document[field]
        if type(value) is not int or value < (1 if field in {"businessDatabaseSize", "edgeBootId"} else 0):
            raise ValueError(f"business runtime cutover {field} is invalid")
    if type(document["configurationMigrated"]) is not bool:
        raise ValueError("business runtime cutover configuration fact is invalid")
    configuration_digest = document["configurationSha256"]
    if document["configurationMigrated"]:
        _require_sha256(configuration_digest)
    elif configuration_digest is not None:
        raise ValueError("absent configuration must not carry a digest")


def _require_uuid4(value: object) -> str:
    if not isinstance(value, str):
        # Marker validation deliberately exposes one ValueError contract for
        # malformed persisted content, including fields of the wrong type.
        raise ValueError(  # noqa: TRY004
            "business runtime cutover operation must be a UUIDv4"
        )
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(
            "business runtime cutover operation must be a UUIDv4"
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("business runtime cutover operation must be a UUIDv4")
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("business runtime cutover digest is invalid")
    return value


def _require_utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("business runtime cutover timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("business runtime cutover timestamp is invalid") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("business runtime cutover timestamp is not UTC")
    return value
