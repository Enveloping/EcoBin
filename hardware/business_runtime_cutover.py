#!/usr/bin/env python3
"""Root-only, one-way migration from the image runtime to the managed runtime.

The live command exposes no path or service arguments.  Library callers can
inject fixed test paths and a service controller, while the production CLI
always operates on the image-owned EcoBin layout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

try:  # Imported on Windows for unit tests; the live CLI is Linux-only.
    import fcntl
except ImportError:  # pragma: no cover - production always provides fcntl
    fcntl = None  # type: ignore[assignment]

try:  # The live command resolves the immutable service account.
    import pwd
except ImportError:  # pragma: no cover - production always provides pwd
    pwd = None  # type: ignore[assignment]

from business_runtime_cutover_state import (
    CUTOVER_ACTIVE_MARKER,
    CUTOVER_PENDING_MARKER,
    CUTOVER_STATE_ROOT,
    BusinessRuntimeCutoverMode,
    inspect_business_runtime_cutover,
    read_cutover_marker,
    validate_cutover_marker,
)
from install.runtime_payload_manifest import EDGE_SCHEMA_VERSION

SYSTEMCTL = "/usr/bin/systemctl"
LOCK_PATH = Path("/run/ecobin/privileged/business-runtime-cutover.lock")
PRIVILEGED_MUTATION_LOCK_PATH = Path("/run/ecobin/privileged/mutation.lock")
MAINTENANCE_INSTALLER_LOCK_PATH = Path(
    "/run/ecobin-device-management-maintenance/installer.lock"
)
LEGACY_DATA_ROOT = Path("/var/lib/ecobin/hardware")
BUSINESS_DATA_ROOT = Path("/var/lib/ecobin/business")
CURRENT_BUSINESS_RELEASE = Path("/opt/ecobin/business/current")
MAX_CONFIGURATION_BYTES = 1024 * 1024
MAX_BOOT_ID_BYTES = 64
MAX_PHOTO_REFERENCES = 20_000
SYSTEMCTL_TIMEOUT_SECONDS = 45

STOP_UNITS = (
    "ecobin-business-runtime.target",
    "ecobin-business-updatable-candidate.service",
    "ecobin-business.service",
    "ecobin-communication-proxy.service",
    "ecobin-updater-candidate.service",
    "ecobin-business-activation-candidate-helper.socket",
    "ecobin-business-release-activation-candidate-helper.socket",
    "ecobin-mcu-flash-candidate-helper.socket",
    "ecobin-hardware.service",
    "ecobin-communication.service",
    "ecobin-updater.service",
    "ecobin-device-management-preflight.service",
    "ecobin-business-activation-helper.socket",
    "ecobin-mcu-flash-helper.socket",
)


class BusinessRuntimeCutoverError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CutoverPaths:
    state_root: Path
    pending_marker: Path
    active_marker: Path
    lock_path: Path
    legacy_data_root: Path
    business_data_root: Path
    current_business_release: Path

    @classmethod
    def live(cls) -> CutoverPaths:
        return cls(
            state_root=CUTOVER_STATE_ROOT,
            pending_marker=CUTOVER_PENDING_MARKER,
            active_marker=CUTOVER_ACTIVE_MARKER,
            lock_path=LOCK_PATH,
            legacy_data_root=LEGACY_DATA_ROOT,
            business_data_root=BUSINESS_DATA_ROOT,
            current_business_release=CURRENT_BUSINESS_RELEASE,
        )

    @property
    def legacy_database(self) -> Path:
        return self.legacy_data_root / "edge.db"

    @property
    def business_database(self) -> Path:
        return self.business_data_root / "edge.db"

    @property
    def legacy_boot_id(self) -> Path:
        return self.legacy_data_root / "edge-boot-id"

    @property
    def business_boot_id(self) -> Path:
        return self.business_data_root / "edge-boot-id"

    @property
    def legacy_configuration(self) -> Path:
        return self.legacy_data_root / "device-config.json"

    @property
    def business_configuration(self) -> Path:
        return self.business_data_root / "device-config.json"

    @property
    def legacy_photos(self) -> Path:
        return self.legacy_data_root / "photos"

    @property
    def business_photos(self) -> Path:
        return self.business_data_root / "photos"


class ServiceController(Protocol):
    def stop(self, units: tuple[str, ...]) -> None: ...

    def require_inactive(self, units: tuple[str, ...]) -> None: ...


class SystemdServiceController:
    def __init__(self, *, runner: Any = subprocess.run) -> None:
        self._runner = runner

    def stop(self, units: tuple[str, ...]) -> None:
        try:
            result = self._runner(
                (SYSTEMCTL, "stop", *units),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=SYSTEMCTL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_STOP_FAILED",
                "the fixed runtime services could not be stopped",
            ) from error
        if result.returncode != 0:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_STOP_FAILED",
                "the fixed runtime services did not stop cleanly",
            )

    def require_inactive(self, units: tuple[str, ...]) -> None:
        for unit in units:
            try:
                result = self._runner(
                    (SYSTEMCTL, "is-active", "--quiet", unit),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_STATE_UNKNOWN",
                    "a fixed runtime service state could not be confirmed",
                ) from error
            if result.returncode != 3:
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_STILL_ACTIVE",
                    f"the fixed runtime service is not inactive: {unit}",
                )


class BusinessRuntimeCutover:
    def __init__(
        self,
        *,
        paths: CutoverPaths,
        business_uid: int,
        business_gid: int,
        services: ServiceController | None = None,
        utc_now: Callable[[], datetime] | None = None,
        mutation_guard: Callable[[], AbstractContextManager[Any]] | None = None,
        privileged_uid: int = 0,
        privileged_gid: int = 0,
    ) -> None:
        for value, label in (
            (business_uid, "business UID"),
            (business_gid, "business GID"),
            (privileged_uid, "privileged UID"),
            (privileged_gid, "privileged GID"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} is invalid")
        self.paths = paths
        self.business_uid = business_uid
        self.business_gid = business_gid
        self.privileged_uid = privileged_uid
        self.privileged_gid = privileged_gid
        self.services = services or SystemdServiceController()
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._mutation_guard = mutation_guard or (
            lambda: _exclusive_lock(
                self.paths.lock_path,
                uid=self.privileged_uid,
                gid=self.privileged_gid,
            )
        )

    def status(self) -> dict[str, Any]:
        inspection = inspect_business_runtime_cutover(
            pending_path=self.paths.pending_marker,
            active_path=self.paths.active_marker,
            expected_uid=self.privileged_uid,
            expected_gid=self.privileged_gid,
        )
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "cutoverMode": inspection.mode.value,
            "errorCode": inspection.error_code,
        }
        if inspection.marker is not None:
            result["operationUid"] = inspection.marker["operationUid"]
            result["phase"] = inspection.marker["phase"]
        if inspection.mode is BusinessRuntimeCutoverMode.ACTIVE:
            try:
                self.verify_active()
            except BusinessRuntimeCutoverError as error:
                result["cutoverMode"] = BusinessRuntimeCutoverMode.INVALID.value
                result["errorCode"] = error.code
            else:
                result["runtimeDataVerified"] = True
        return result

    def prepare(self, operation_uid: str, evidence_sha256: str) -> dict[str, Any]:
        operation_uid = _require_uuid4(operation_uid, "operation UID")
        evidence_sha256 = _require_sha256(
            evidence_sha256,
            "cutover evidence digest",
        )
        with self._mutation_guard():
            state, pending, active = self._state_for_prepare()
            if state == "ACTIVE":
                _require_matching_identity(active, operation_uid, evidence_sha256)
                verified = self._verify_runtime_data(active)
                return {
                    **verified,
                    "disposition": "ALREADY_ACTIVE",
                }
            if state == "FINALIZING":
                _require_matching_identity(pending, operation_uid, evidence_sha256)
                _require_matching_identity(active, operation_uid, evidence_sha256)
                verified = self._verify_runtime_data(active)
                self._remove_marker(
                    self.paths.pending_marker,
                    expected_phase="PREPARING",
                    expected_identity=(operation_uid, evidence_sha256),
                )
                return {
                    **verified,
                    "disposition": "FINALIZED_AFTER_INTERRUPTION",
                }
            if state == "LEGACY":
                self._preflight_new_cutover()
                pending = {
                    "schemaVersion": 1,
                    "phase": "PREPARING",
                    "operationUid": operation_uid,
                    "evidenceSha256": evidence_sha256,
                    "startedAt": self._timestamp(),
                }
                self._write_marker(
                    self.paths.pending_marker,
                    pending,
                    expected_phase="PREPARING",
                )
            else:
                _require_matching_identity(pending, operation_uid, evidence_sha256)
                self._require_resume_layout()

            self.services.stop(STOP_UNITS)
            self.services.require_inactive(STOP_UNITS)
            facts = self._migrate_runtime_data(operation_uid)
            active = {
                "schemaVersion": 1,
                "phase": "ACTIVE",
                "operationUid": operation_uid,
                "evidenceSha256": evidence_sha256,
                **facts,
                "completedAt": self._timestamp(),
            }
            self._write_marker(
                self.paths.active_marker,
                active,
                expected_phase="ACTIVE",
            )
            verified = self._verify_runtime_data(active)
            self._remove_marker(
                self.paths.pending_marker,
                expected_phase="PREPARING",
                expected_identity=(operation_uid, evidence_sha256),
            )
            return {**verified, "disposition": "ACTIVATED"}

    def verify_active(self) -> dict[str, Any]:
        inspection = inspect_business_runtime_cutover(
            pending_path=self.paths.pending_marker,
            active_path=self.paths.active_marker,
            expected_uid=self.privileged_uid,
            expected_gid=self.privileged_gid,
        )
        if inspection.mode is not BusinessRuntimeCutoverMode.ACTIVE:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_NOT_ACTIVE",
                "the managed business runtime cutover is not active",
            )
        return self._verify_runtime_data(inspection.marker)

    def _state_for_prepare(
        self,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        pending_exists = os.path.lexists(self.paths.pending_marker)
        active_exists = os.path.lexists(self.paths.active_marker)
        try:
            pending = (
                read_cutover_marker(
                    self.paths.pending_marker,
                    expected_phase="PREPARING",
                    expected_uid=self.privileged_uid,
                    expected_gid=self.privileged_gid,
                )
                if pending_exists
                else None
            )
            active = (
                read_cutover_marker(
                    self.paths.active_marker,
                    expected_phase="ACTIVE",
                    expected_uid=self.privileged_uid,
                    expected_gid=self.privileged_gid,
                )
                if active_exists
                else None
            )
        except (OSError, ValueError) as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_MARKER_INVALID",
                "the persistent business runtime cutover marker is invalid",
            ) from error
        if pending is not None and active is not None:
            if (
                pending["operationUid"] != active["operationUid"]
                or pending["evidenceSha256"] != active["evidenceSha256"]
            ):
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_CUTOVER_CONFLICT",
                    "the pending and active cutover identities differ",
                )
            return "FINALIZING", pending, active
        if active is not None:
            return "ACTIVE", None, active
        if pending is not None:
            return "PREPARING", pending, None
        return "LEGACY", None, None

    def _preflight_new_cutover(self) -> None:
        _require_directory(
            self.paths.state_root,
            mode=0o700,
            uid=self.privileged_uid,
            gid=self.privileged_gid,
            label="cutover state directory",
        )
        _require_directory(
            self.paths.legacy_data_root,
            label="legacy business data directory",
        )
        _require_directory(
            self.paths.business_data_root,
            mode=0o700,
            uid=self.business_uid,
            gid=self.business_gid,
            label="managed business data directory",
        )
        _require_directory(
            self.paths.business_photos,
            mode=0o700,
            uid=self.business_uid,
            gid=self.business_gid,
            label="managed business photo directory",
        )
        _require_regular(self.paths.legacy_database, "legacy business database")
        _read_boot_id(self.paths.legacy_boot_id)
        for path, label in (
            (self.paths.current_business_release, "managed current release"),
            (self.paths.business_database, "managed business database"),
            (self.paths.business_boot_id, "managed edge boot ID"),
            (self.paths.business_configuration, "managed device configuration"),
        ):
            if os.path.lexists(path):
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_CUTOVER_TARGET_CONFLICT",
                    f"{label} already exists before the one-way cutover",
                )
        _verify_sqlite(self.paths.legacy_database)
        _require_expected_schema_and_idle(self.paths.legacy_database)
        try:
            if next(self.paths.business_photos.iterdir(), None) is not None:
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_CUTOVER_TARGET_CONFLICT",
                    "managed business photo directory is not empty",
                )
        except OSError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_TARGET_UNAVAILABLE",
                "managed business photo directory cannot be inspected",
            ) from error

    def _require_resume_layout(self) -> None:
        _require_directory(
            self.paths.state_root,
            mode=0o700,
            uid=self.privileged_uid,
            gid=self.privileged_gid,
            label="cutover state directory",
        )
        _require_directory(
            self.paths.legacy_data_root,
            label="legacy business data directory",
        )
        _require_directory(
            self.paths.business_data_root,
            mode=0o700,
            uid=self.business_uid,
            gid=self.business_gid,
            label="managed business data directory",
        )
        _require_directory(
            self.paths.business_photos,
            mode=0o700,
            uid=self.business_uid,
            gid=self.business_gid,
            label="managed business photo directory",
        )
        _require_regular(self.paths.legacy_database, "legacy business database")
        _read_boot_id(self.paths.legacy_boot_id)
        if os.path.lexists(self.paths.current_business_release):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_TARGET_CONFLICT",
                "a managed business release appeared during cutover",
            )

    def _migrate_runtime_data(self, operation_uid: str) -> dict[str, Any]:
        _verify_sqlite(self.paths.legacy_database)
        self._reset_partial_outputs()
        temporary_database = self.paths.business_data_root / (
            f".edge.db.cutover-{operation_uid}.tmp"
        )
        _unlink_regular_if_present(
            temporary_database,
            "cutover database temporary file",
        )
        try:
            _sqlite_backup(self.paths.legacy_database, temporary_database)
            source_snapshot_sha256 = _sha256_file(temporary_database)
            _require_expected_schema_and_idle(temporary_database)
            photo_facts = self._migrate_photo_references(temporary_database)
            _verify_sqlite(temporary_database)
            _set_file_identity(
                temporary_database,
                mode=0o600,
                uid=self.business_uid,
                gid=self.business_gid,
            )
            initial_database_sha256 = _sha256_file(temporary_database)
            database_size = temporary_database.stat().st_size
            os.replace(temporary_database, self.paths.business_database)
            _fsync_directory(self.paths.business_data_root)

            edge_boot_id = _read_boot_id(self.paths.legacy_boot_id)
            _atomic_copy_regular(
                self.paths.legacy_boot_id,
                self.paths.business_boot_id,
                max_bytes=MAX_BOOT_ID_BYTES,
                mode=0o600,
                uid=self.business_uid,
                gid=self.business_gid,
            )
            configuration_migrated = False
            configuration_sha256: str | None = None
            if os.path.lexists(self.paths.legacy_configuration):
                configuration_bytes = _read_json_object_file(
                    self.paths.legacy_configuration,
                    max_bytes=MAX_CONFIGURATION_BYTES,
                    label="legacy device configuration",
                )
                _atomic_write_bytes(
                    self.paths.business_configuration,
                    configuration_bytes,
                    mode=0o600,
                    uid=self.business_uid,
                    gid=self.business_gid,
                )
                configuration_migrated = True
                configuration_sha256 = hashlib.sha256(
                    configuration_bytes
                ).hexdigest()
            facts = {
                "sourceSnapshotSha256": source_snapshot_sha256,
                "initialBusinessDatabaseSha256": initial_database_sha256,
                "businessDatabaseSize": database_size,
                **photo_facts,
                "edgeBootId": edge_boot_id,
                "configurationMigrated": configuration_migrated,
                "configurationSha256": configuration_sha256,
            }
            self._verify_runtime_data_files(facts)
            return facts
        except Exception:
            _unlink_regular_if_present(
                temporary_database,
                "cutover database temporary file",
            )
            raise

    def _migrate_photo_references(self, database: Path) -> dict[str, int]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(database), timeout=10.0)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT photo_uid, local_path
                   FROM photo_outbox
                   WHERE tombstoned=0
                   ORDER BY photo_uid"""
            ).fetchall()
        except sqlite3.Error as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_DATABASE_SCHEMA_INVALID",
                "active photo references could not be read from the snapshot",
            ) from error
        finally:
            if connection is not None:
                connection.close()
        if len(rows) > MAX_PHOTO_REFERENCES:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_REFERENCE_LIMIT_EXCEEDED",
                "the business database contains too many active photo references",
            )

        replacements: list[tuple[str, str]] = []
        copied_destinations: set[Path] = set()
        copied_count = 0
        for row in rows:
            source, destination = self._photo_paths(row["local_path"])
            if destination in copied_destinations:
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_PHOTO_PATH_CONFLICT",
                    "multiple active photo references resolve to one local file",
                )
            copied_destinations.add(destination)
            if os.path.lexists(source):
                _require_real_ancestry(self.paths.legacy_photos, source.parent)
                _ensure_private_directory_tree(
                    self.paths.business_photos,
                    destination.parent,
                    uid=self.business_uid,
                    gid=self.business_gid,
                )
                _atomic_copy_regular(
                    source,
                    destination,
                    max_bytes=None,
                    mode=0o600,
                    uid=self.business_uid,
                    gid=self.business_gid,
                )
                copied_count += 1
            replacements.append((str(destination), row["photo_uid"]))

        connection = None
        try:
            connection = sqlite3.connect(str(database), timeout=10.0)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "UPDATE photo_outbox SET local_path=? WHERE photo_uid=?",
                replacements,
            )
            connection.commit()
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            raise BusinessRuntimeCutoverError(
                "BUSINESS_DATABASE_MIGRATION_FAILED",
                "active photo paths could not be migrated",
            ) from error
        finally:
            if connection is not None:
                connection.close()
        return {
            "migratedPhotoFileCount": copied_count,
            "migratedPhotoReferenceCount": len(rows),
        }

    def _photo_paths(self, value: object) -> tuple[Path, Path]:
        if not isinstance(value, str) or not value:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "an active photo reference has no valid local path",
            )
        source = Path(value)
        if not source.is_absolute():
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "an active photo reference is not an absolute legacy path",
            )
        try:
            relative = source.relative_to(self.paths.legacy_photos)
        except ValueError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "an active photo reference escapes the legacy photo directory",
            ) from error
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "an active photo reference contains an unsafe path component",
            )
        return source, self.paths.business_photos / relative

    def _reset_partial_outputs(self) -> None:
        for path in (
            self.paths.business_database,
            Path(f"{self.paths.business_database}-wal"),
            Path(f"{self.paths.business_database}-shm"),
            Path(f"{self.paths.business_database}-journal"),
            self.paths.business_boot_id,
            self.paths.business_configuration,
        ):
            _unlink_regular_if_present(path, "partial cutover output")
        _clear_private_directory(
            self.paths.business_photos,
            uid=self.business_uid,
            gid=self.business_gid,
        )

    def _verify_runtime_data(
        self,
        marker: Any,
    ) -> dict[str, Any]:
        if not isinstance(marker, dict):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_MARKER_INVALID",
                "the active cutover marker is unavailable",
            )
        try:
            validate_cutover_marker(marker, expected_phase="ACTIVE")
        except ValueError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_MARKER_INVALID",
                "the active cutover marker content is invalid",
            ) from error
        self._verify_runtime_data_files(marker)
        return {
            "schemaVersion": 1,
            "cutoverMode": "ACTIVE",
            "operationUid": marker["operationUid"],
            "businessDatabaseSize": self.paths.business_database.stat().st_size,
            "migratedPhotoFileCount": marker["migratedPhotoFileCount"],
            "migratedPhotoReferenceCount": marker[
                "migratedPhotoReferenceCount"
            ],
            "configurationMigrated": marker["configurationMigrated"],
            "runtimeDataVerified": True,
        }

    def _verify_runtime_data_files(self, facts: Any) -> None:
        _require_regular(
            self.paths.business_database,
            "managed business database",
            mode=0o600,
            uid=self.business_uid,
            gid=self.business_gid,
        )
        _verify_sqlite(self.paths.business_database)
        _require_cutover_baseline_schema(self.paths.business_database)
        _require_directory(
            self.paths.business_photos,
            mode=0o700,
            uid=self.business_uid,
            gid=self.business_gid,
            label="managed business photo directory",
        )
        _require_business_photo_paths(
            self.paths.business_database,
            self.paths.business_photos,
        )
        _require_regular(
            self.paths.business_boot_id,
            "managed edge boot ID",
            mode=0o600,
            uid=self.business_uid,
            gid=self.business_gid,
        )
        if _read_boot_id(self.paths.business_boot_id) != facts["edgeBootId"]:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_EDGE_BOOT_ID_MISMATCH",
                "the managed edge boot ID differs from the cutover fact",
            )
        if os.path.lexists(self.paths.business_configuration):
            _require_regular(
                self.paths.business_configuration,
                "managed device configuration",
                mode=0o600,
                uid=self.business_uid,
                gid=self.business_gid,
            )
            _read_json_object_file(
                self.paths.business_configuration,
                max_bytes=MAX_CONFIGURATION_BYTES,
                label="managed device configuration",
            )
        elif facts["configurationMigrated"]:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_CONFIGURATION_MISSING",
                "the configuration copied during cutover is missing",
            )

    def _write_marker(
        self,
        path: Path,
        document: dict[str, Any],
        *,
        expected_phase: str,
    ) -> None:
        try:
            validate_cutover_marker(document, expected_phase=expected_phase)
        except ValueError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_MARKER_INVALID",
                "the generated cutover marker is invalid",
            ) from error
        if os.path.lexists(path):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_CONFLICT",
                "a cutover marker appeared unexpectedly",
            )
        raw = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        _atomic_write_bytes(
            path,
            raw,
            mode=0o600,
            uid=self.privileged_uid,
            gid=self.privileged_gid,
        )

    def _remove_marker(
        self,
        path: Path,
        *,
        expected_phase: str,
        expected_identity: tuple[str, str],
    ) -> None:
        try:
            marker = read_cutover_marker(
                path,
                expected_phase=expected_phase,
                expected_uid=self.privileged_uid,
                expected_gid=self.privileged_gid,
            )
        except (OSError, ValueError) as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_MARKER_CHANGED",
                "the pending cutover marker changed before finalization",
            ) from error
        _require_matching_identity(marker, *expected_identity)
        path.unlink()
        _fsync_directory(path.parent)

    def _timestamp(self) -> str:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise BusinessRuntimeCutoverError(
                "TRUSTED_TIME_UNAVAILABLE",
                "the cutover clock did not provide an aware UTC time",
            )
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_matching_identity(
    marker: Any,
    operation_uid: str,
    evidence_sha256: str,
) -> None:
    if not isinstance(marker, dict) or (
        marker.get("operationUid") != operation_uid
        or marker.get("evidenceSha256") != evidence_sha256
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_CONFLICT",
            "the requested cutover identity differs from persistent state",
        )


def _require_uuid4(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise BusinessRuntimeCutoverError(
            "REQUEST_INVALID", f"{label} must be a lowercase UUIDv4"
        )
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise BusinessRuntimeCutoverError(
            "REQUEST_INVALID", f"{label} must be a lowercase UUIDv4"
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise BusinessRuntimeCutoverError(
            "REQUEST_INVALID", f"{label} must be a lowercase UUIDv4"
        )
    return value


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BusinessRuntimeCutoverError(
            "REQUEST_INVALID", f"{label} must be lowercase SHA-256"
        )
    return value


def _require_directory(
    path: Path,
    *,
    label: str,
    mode: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNAVAILABLE",
            f"{label} is unavailable",
        ) from error
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink():
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} is not a real directory",
        )
    if (
        os.name == "posix"
        and mode is not None
        and stat.S_IMODE(details.st_mode) != mode
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} has an unsafe mode",
        )
    if (
        os.name == "posix"
        and uid is not None
        and gid is not None
        and (details.st_uid, details.st_gid) != (uid, gid)
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} has an unsafe owner",
        )
    return details


def _require_regular(
    path: Path,
    label: str,
    *,
    mode: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNAVAILABLE",
            f"{label} is unavailable",
        ) from error
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} is not a single regular file",
        )
    if (
        os.name == "posix"
        and mode is not None
        and stat.S_IMODE(details.st_mode) != mode
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} has an unsafe mode",
        )
    if (
        os.name == "posix"
        and uid is not None
        and gid is not None
        and (details.st_uid, details.st_gid) != (uid, gid)
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} has an unsafe owner",
        )
    return details


def _verify_sqlite(path: Path) -> None:
    _require_regular(path, "business SQLite database")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _sqlite_read_only_uri(path),
            uri=True,
            timeout=10.0,
        )
        rows = connection.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_INTEGRITY_FAILED",
            "the business SQLite database could not be verified",
        ) from error
    finally:
        if connection is not None:
            connection.close()
    if rows != [("ok",)]:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_INTEGRITY_FAILED",
            "the business SQLite integrity check did not pass",
        )


def _require_expected_schema_and_idle(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _sqlite_read_only_uri(path),
            uri=True,
            timeout=10.0,
        )
        schema = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        slot = connection.execute(
            """SELECT work_type, work_uid, work_state
               FROM work_slot WHERE slot_id=1"""
        ).fetchone()
        maintenance = connection.execute(
            "SELECT COUNT(*) FROM maintenance_lock"
        ).fetchone()
    except sqlite3.Error as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_SCHEMA_INVALID",
            "the legacy business database schema could not be verified",
        ) from error
    finally:
        if connection is not None:
            connection.close()
    if schema != (int(EDGE_SCHEMA_VERSION),):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_SCHEMA_UNSUPPORTED",
            "the legacy business database schema is not the image schema",
        )
    if slot != ("NONE", None, None):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_NOT_IDLE",
            "a physical business operation is still active",
        )
    if maintenance != (0,):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_MAINTENANCE_ACTIVE",
            "an MCU maintenance operation is still active",
        )


def _require_cutover_baseline_schema(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _sqlite_read_only_uri(path),
            uri=True,
            timeout=10.0,
        )
        row = connection.execute(
            """SELECT MAX(version),
                      SUM(CASE WHEN version=? THEN 1 ELSE 0 END)
               FROM schema_version""",
            (int(EDGE_SCHEMA_VERSION),),
        ).fetchone()
    except sqlite3.Error as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_SCHEMA_INVALID",
            "the managed business database schema could not be verified",
        ) from error
    finally:
        if connection is not None:
            connection.close()
    if (
        row is None
        or type(row[0]) is not int
        or row[0] < int(EDGE_SCHEMA_VERSION)
        or row[1] != 1
    ):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_SCHEMA_UNSUPPORTED",
            "the managed database no longer contains the cutover baseline schema",
        )


def _require_business_photo_paths(database: Path, photo_root: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            _sqlite_read_only_uri(database), uri=True, timeout=10.0
        )
        rows = connection.execute(
            "SELECT local_path FROM photo_outbox WHERE tombstoned=0"
        ).fetchall()
    except sqlite3.Error as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_SCHEMA_INVALID",
            "managed photo references could not be verified",
        ) from error
    finally:
        if connection is not None:
            connection.close()
    if len(rows) > MAX_PHOTO_REFERENCES:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_PHOTO_REFERENCE_LIMIT_EXCEEDED",
            "the managed database contains too many photo references",
        )
    for (value,) in rows:
        if not isinstance(value, str):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "a managed photo reference has an invalid path",
            )
        try:
            relative = Path(value).relative_to(photo_root)
        except ValueError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "a managed photo reference escapes its private directory",
            ) from error
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "a managed photo reference contains an unsafe component",
            )


def _sqlite_backup(source: Path, destination: Path) -> None:
    _require_regular(source, "legacy business database")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    os.close(descriptor)
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(
            _sqlite_read_only_uri(source), uri=True, timeout=10.0
        )
        destination_connection = sqlite3.connect(str(destination), timeout=10.0)
        destination_connection.execute("PRAGMA journal_mode=DELETE")
        source_connection.backup(destination_connection)
        destination_connection.commit()
    except sqlite3.Error as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_DATABASE_COPY_FAILED",
            "the legacy business database could not be copied consistently",
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
    _fsync_file(destination)


def _sqlite_read_only_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _read_boot_id(path: Path) -> int:
    raw = _read_regular_bytes(path, max_bytes=MAX_BOOT_ID_BYTES, label="edge boot ID")
    try:
        text = raw.decode("ascii").strip()
        value = int(text)
    except (UnicodeDecodeError, ValueError) as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_EDGE_BOOT_ID_INVALID",
            "the legacy edge boot ID is invalid",
        ) from error
    if str(value) != text or not 1 <= value <= 9_007_199_254_740_991:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_EDGE_BOOT_ID_INVALID",
            "the legacy edge boot ID is outside the supported range",
        )
    return value


def _read_json_object_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    raw = _read_regular_bytes(path, max_bytes=max_bytes, label=label)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_CONFIGURATION_INVALID",
            f"{label} is not valid JSON",
        ) from error
    if not isinstance(document, dict):
        raise BusinessRuntimeCutoverError(
            "BUSINESS_CONFIGURATION_INVALID",
            f"{label} is not a JSON object",
        )
    return raw


def _read_regular_bytes(path: Path, *, max_bytes: int, label: str) -> bytes:
    details = _require_regular(path, label)
    if not 1 <= details.st_size <= max_bytes:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_FILE_INVALID",
            f"{label} has an invalid size",
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
        ):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_PATH_CHANGED",
                f"{label} changed while it was opened",
            )
        raw = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if not 1 <= len(raw) <= max_bytes:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_FILE_INVALID",
            f"{label} has an invalid size",
        )
    return raw


def _atomic_copy_regular(
    source: Path,
    destination: Path,
    *,
    max_bytes: int | None,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    details = _require_regular(source, "cutover source file")
    if max_bytes is not None and not 1 <= details.st_size <= max_bytes:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_FILE_INVALID",
            "a cutover source file has an invalid size",
        )
    temporary = destination.with_name(f".{destination.name}.cutover.tmp")
    _unlink_regular_if_present(temporary, "cutover temporary file")
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    source_flags |= getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    destination_flags |= getattr(os, "O_CLOEXEC", 0)
    destination_flags |= getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    destination_fd: int | None = None
    try:
        opened = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
        ):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_PATH_CHANGED",
                "a cutover source file changed while it was opened",
            )
        destination_fd = os.open(temporary, destination_flags, mode)
        with os.fdopen(destination_fd, "wb", closefd=False) as output:
            with os.fdopen(os.dup(source_fd), "rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        _set_descriptor_identity(destination_fd, mode=mode, uid=uid, gid=gid)
        os.fsync(destination_fd)
        os.close(destination_fd)
        destination_fd = None
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)
        _unlink_regular_if_present(temporary, "cutover temporary file")


def _atomic_write_bytes(
    destination: Path,
    payload: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    temporary = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}-{uuid.uuid4()}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("cutover file write made no progress")
            offset += written
        _set_descriptor_identity(descriptor, mode=mode, uid=uid, gid=gid)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_regular_if_present(temporary, "cutover temporary file")


def _set_descriptor_identity(descriptor: int, *, mode: int, uid: int, gid: int) -> None:
    if os.name == "posix":
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, uid, gid)


def _set_file_identity(path: Path, *, mode: int, uid: int, gid: int) -> None:
    if os.name == "posix":
        os.chmod(path, mode, follow_symlinks=False)
        os.chown(path, uid, gid, follow_symlinks=False)
    else:
        os.chmod(path, mode)
    _fsync_file(path)


def _ensure_private_directory_tree(
    root: Path,
    destination_parent: Path,
    *,
    uid: int,
    gid: int,
) -> None:
    _require_directory(
        root,
        mode=0o700,
        uid=uid,
        gid=gid,
        label="managed business photo directory",
    )
    relative = destination_parent.relative_to(root)
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "a managed photo directory contains an unsafe component",
            )
        current = current / part
        try:
            os.mkdir(current, 0o700)
            if os.name == "posix":
                os.chown(current, uid, gid)
            _fsync_directory(current.parent)
        except FileExistsError:
            pass
        _require_directory(
            current,
            mode=0o700,
            uid=uid,
            gid=gid,
            label="managed business photo subdirectory",
        )


def _require_real_ancestry(root: Path, parent: Path) -> None:
    _require_directory(root, label="legacy business photo directory")
    try:
        relative = parent.relative_to(root)
    except ValueError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_PHOTO_PATH_INVALID",
            "a legacy photo parent escapes its fixed directory",
        ) from error
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_PHOTO_PATH_INVALID",
                "a legacy photo path contains an unsafe component",
            )
        current = current / part
        _require_directory(current, label="legacy business photo subdirectory")


def _clear_private_directory(path: Path, *, uid: int, gid: int) -> None:
    _require_directory(
        path,
        label="managed business photo directory",
        mode=0o700,
        uid=uid,
        gid=gid,
    )
    try:
        entries = tuple(path.iterdir())
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_TARGET_UNAVAILABLE",
            "partial managed photos could not be inspected",
        ) from error
    for entry in entries:
        details = entry.lstat()
        if stat.S_ISDIR(details.st_mode) and not entry.is_symlink():
            _clear_private_directory(entry, uid=uid, gid=gid)
            entry.rmdir()
        elif stat.S_ISREG(details.st_mode) and details.st_nlink == 1:
            if os.name == "posix" and (
                (details.st_uid, details.st_gid) != (uid, gid)
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise BusinessRuntimeCutoverError(
                    "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
                    "partial managed photos contain an unsafe file owner or mode",
                )
            entry.unlink()
        else:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
                "partial managed photos contain a link or special file",
            )
    _fsync_directory(path)


def _unlink_regular_if_present(path: Path, label: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
            f"{label} is not a single regular file",
        )
    path.unlink()


def _sha256_file(path: Path) -> str:
    details = _require_regular(path, "cutover digest source")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
        ):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_PATH_CHANGED",
                "the cutover digest source changed before reading",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_lock(path: Path, *, uid: int, gid: int) -> Iterable[None]:
    if fcntl is None:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PLATFORM_UNSUPPORTED",
            "the live cutover requires POSIX file locking",
        )
    _require_regular(
        path,
        "business runtime cutover lock",
        mode=0o600,
        uid=uid,
        gid=gid,
    )
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
            "the business runtime cutover lock could not be opened",
        ) from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_BUSY",
                "another business runtime cutover command is running",
            ) from error
        except OSError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
                "the business runtime cutover lock could not be acquired",
            ) from error
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_creatable_lock(
    path: Path,
    *,
    uid: int,
    gid: int,
) -> Iterable[None]:
    if fcntl is None:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_PLATFORM_UNSUPPORTED",
            "the live cutover requires POSIX file locking",
        )
    try:
        os.mkdir(path.parent, 0o700)
        if os.name == "posix":
            os.chown(path.parent, uid, gid)
        _fsync_directory(path.parent.parent)
    except FileExistsError:
        pass
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
            "the maintenance installer lock directory could not be created",
        ) from error
    _require_directory(
        path.parent,
        mode=0o700,
        uid=uid,
        gid=gid,
        label="maintenance installer lock directory",
    )

    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(
                path,
                os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
                "the maintenance installer lock could not be opened",
            ) from error
    except OSError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
            "the maintenance installer lock could not be created",
        ) from error
    try:
        details = os.fstat(descriptor)
        if created:
            _set_descriptor_identity(descriptor, mode=0o600, uid=uid, gid=gid)
            os.fsync(descriptor)
            _fsync_directory(path.parent)
            details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (
                os.name == "posix"
                and (
                    (details.st_uid, details.st_gid) != (uid, gid)
                    or stat.S_IMODE(details.st_mode) != 0o600
                )
            )
        ):
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_PATH_UNSAFE",
                "the maintenance installer lock file is unsafe",
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_BUSY",
                "device-management maintenance is already running",
            ) from error
        except OSError as error:
            raise BusinessRuntimeCutoverError(
                "BUSINESS_RUNTIME_CUTOVER_LOCK_UNAVAILABLE",
                "the maintenance installer lock could not be acquired",
            ) from error
        yield
    finally:
        os.close(descriptor)


@contextmanager
def _live_cutover_guard() -> Iterable[None]:
    with _exclusive_creatable_lock(
        MAINTENANCE_INSTALLER_LOCK_PATH,
        uid=0,
        gid=0,
    ), _exclusive_lock(
        PRIVILEGED_MUTATION_LOCK_PATH,
        uid=0,
        gid=0,
    ), _exclusive_lock(LOCK_PATH, uid=0, gid=0):
        yield


def _build_live_cutover() -> BusinessRuntimeCutover:
    if pwd is None:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_IDENTITY_UNAVAILABLE",
            "the business service account lookup is unavailable",
        )
    try:
        business = pwd.getpwnam("ecobin-business")
    except KeyError as error:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_IDENTITY_UNAVAILABLE",
            "the ecobin-business service account does not exist",
        ) from error
    if business.pw_uid <= 0 or business.pw_gid <= 0:
        raise BusinessRuntimeCutoverError(
            "BUSINESS_RUNTIME_IDENTITY_INVALID",
            "the ecobin-business service identity is invalid",
        )
    return BusinessRuntimeCutover(
        paths=CutoverPaths.live(),
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        mutation_guard=_live_cutover_guard,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="inspect the one-way cutover state")
    prepare = commands.add_parser(
        "prepare",
        help="stop the legacy runtime and prepare the managed runtime data",
    )
    prepare.add_argument("--operation-uid", required=True)
    prepare.add_argument("--evidence-digest", required=True)
    commands.add_parser(
        "verify-active",
        help="verify the active selector and managed runtime data",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(os, "geteuid", lambda: None)() != 0:
        print(
            json.dumps(
                {
                    "errorCode": "ROOT_REQUIRED",
                    "message": "business runtime cutover requires UID 0",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        cutover = _build_live_cutover()
        if args.command == "status":
            result = cutover.status()
        elif args.command == "prepare":
            result = cutover.prepare(args.operation_uid, args.evidence_digest)
        else:
            result = cutover.verify_active()
    except BusinessRuntimeCutoverError as error:
        print(
            json.dumps(
                {"errorCode": error.code, "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
