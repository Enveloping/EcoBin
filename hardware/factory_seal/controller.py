from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import threading
import uuid

from first_boot.atomic_json import AtomicJsonFile
from onenet_wire import build_event_envelope

from .errors import FactorySealError
from .validation import (
    FactorySealPaths,
    collect_local_factory_facts,
    completion_event_matches_authorization,
    factory_seal_completion_payload,
    inspect_sealed_authorization,
    sealed_document_matches_authorization,
    valid_device_capabilities,
)
from trusted_clock import raw_utc_now, sample_clock


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
FaultHook = Callable[[str], None]


class FactorySealController:
    """Root-only status, confirmation, and reboot reconciliation service."""

    def __init__(
        self,
        paths: FactorySealPaths = FactorySealPaths(),
        *,
        runtime_healthy: Callable[[], bool] | None = None,
        stop_factory: Callable[[], bool] | None = None,
        apply_production_firewall: Callable[[], bool] | None = None,
        apply_emergency_firewall: Callable[[], bool] | None = None,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self.paths = paths
        self._runtime_healthy = runtime_healthy or _runtime_healthy
        self._stop_factory = stop_factory or _stop_factory
        self._apply_production_firewall = (
            apply_production_firewall or _apply_production_firewall
        )
        self._apply_emergency_firewall = (
            apply_emergency_firewall or _apply_emergency_firewall
        )
        self._fault_hook = fault_hook
        self._lock = threading.RLock()
        self._sealed_file = AtomicJsonFile(
            paths.sealed,
            mode=0o600,
            directory_mode=0o700,
            maximum_bytes=8192,
        )

    def status(self) -> dict[str, object]:
        with self._lock:
            sealed = self._read_sealed()
            if sealed is not None:
                row = self._authorization_by_command(
                    sealed.get("authorizationCommandUid")
                )
                marker_valid = self._sealed_matches_authorization(sealed, row)
                completion = inspect_sealed_authorization(self.paths)
                return _public_status(
                    authorized=marker_valid,
                    confirm_allowed=False,
                    status_code=(
                        completion.status_code
                        if marker_valid
                        else "SEALED_FACT_INVALID"
                    ),
                    generation=(
                        row["acceptance_generation"] if row is not None else None
                    ),
                    binding=(
                        row["authorization_binding_sha256"]
                        if row is not None
                        else None
                    ),
                )
            seal_fact = inspect_sealed_authorization(self.paths)
            if seal_fact.exists:
                # A SEALING/SEALED database row cannot replace the
                # independently fsynced marker.  Surface the precise
                # fail-closed fact instead of making the portal claim that a
                # marker-less device is sealed.
                return _public_status(
                    authorized=False,
                    confirm_allowed=False,
                    status_code=seal_fact.status_code,
                    generation=seal_fact.acceptance_generation,
                    binding=seal_fact.authorization_binding_sha256,
                )
            row = self._current_authorization()
            if row is None:
                return _public_status(
                    authorized=False,
                    confirm_allowed=False,
                    status_code="CLOUD_ACCEPTANCE_REQUIRED",
                )
            if row["state"] != "AUTHORIZED":
                return _public_status(
                    authorized=row["state"] in {"SEALING", "SEALED"},
                    confirm_allowed=False,
                    status_code=(
                        "SEALED_CLEANUP_PENDING"
                        if row["state"] == "SEALING"
                        else "SEALED"
                        if row["state"] == "SEALED"
                        else "CLOUD_ACCEPTANCE_REQUIRED"
                    ),
                    generation=row["acceptance_generation"],
                    binding=row["authorization_binding_sha256"],
                )
            code = self._eligibility_code(row)
            return _public_status(
                authorized=True,
                confirm_allowed=code == "SEAL_READY",
                status_code=code,
                generation=row["acceptance_generation"],
                binding=row["authorization_binding_sha256"],
            )

    def confirm(self, operator_confirmation_uid: str) -> dict[str, object]:
        confirmation_uid = _uuid4(
            operator_confirmation_uid,
            "FACTORY_SEAL_CONFIRMATION_INVALID",
        )
        with self._lock:
            existing_seal = self._read_sealed()
            if existing_seal is not None:
                if existing_seal.get("operatorConfirmationUid") != confirmation_uid:
                    raise FactorySealError("FACTORY_SEAL_CONFIRMATION_CONFLICT")
                return self.status()
            row = self._current_authorization()
            if row is None or row["state"] != "AUTHORIZED":
                raise FactorySealError("FACTORY_SEAL_NOT_AUTHORIZED")
            code = self._eligibility_code(row)
            if code != "SEAL_READY":
                raise FactorySealError(code)
            sealed_clock = sample_clock()
            now = sealed_clock.raw_observed_at or raw_utc_now()
            sealed = {
                "schemaVersion": 2,
                "status": "SEALED",
                "imageReleaseId": row["image_release_id"],
                "hardwareIdentitySha256": _sha256_text(row["hardware_sn"]),
                "factoryReportSha256": row["factory_report_sha256"],
                "authorizationCommandUid": row["command_uid"],
                "acceptanceGeneration": row["acceptance_generation"],
                "authorizationBindingSha256": row[
                    "authorization_binding_sha256"
                ],
                "operatorConfirmationUid": confirmation_uid,
                "sealedClockQuality": sealed_clock.quality,
                "sealedAt": now,
            }
            self._write_seal_and_mark_sealing(row, sealed)
            self._fault("after_database_sealing")
            return _public_status(
                authorized=True,
                confirm_allowed=False,
                status_code="SEALED_CLEANUP_PENDING",
                generation=row["acceptance_generation"],
                binding=row["authorization_binding_sha256"],
            )

    def reconcile_cleanup(self) -> str:
        """Resume only in the sealing direction after the marker exists."""

        with self._lock:
            sealed = self._read_sealed()
            if sealed is None:
                fact = inspect_sealed_authorization(self.paths)
                if not fact.exists:
                    return "NO_SEAL"
                # A missing marker is not proof that sealing never started:
                # the database may already have durably entered SEALING or
                # SEALED, or it may be unreadable.  In either case reopening
                # the factory AP/cellular path would reverse an irreversible
                # security transition.  Stop factory services and keep only
                # the emergency network posture until an operator repairs the
                # sealed fact.
                self._stop_factory()
                self._apply_emergency_firewall()
                return fact.status_code
            row = self._authorization_by_command(
                sealed.get("authorizationCommandUid")
            )
            if not self._sealed_matches_authorization(sealed, row):
                # A malformed marker is still a permanent AP deny fact.
                self._stop_factory()
                self._apply_emergency_firewall()
                return "SEALED_FACT_INVALID"
            sealed = self._upgrade_legacy_sealed_marker(sealed, row)
            self._mark_sealing(sealed)
            if not self._stop_factory():
                return "FACTORY_SERVICES_STOP_FAILED"
            self._fault("after_factory_services_stopped")
            _unlink_and_fsync(self.paths.setup_ap_key)
            self._fault("after_setup_key_cleanup")
            for path in (
                self.paths.enrollment_key,
                self.paths.enrollment_state,
                self.paths.enrollment_implementation,
            ):
                _unlink_and_fsync(path)
            self._fault("after_enrollment_artifact_cleanup")
            _clear_flat_directory(self.paths.factory_photos)
            _unlink_and_fsync(self.paths.factory_state)
            self._fault("after_factory_artifact_cleanup")
            if not self._apply_production_firewall():
                return "PRODUCTION_FIREWALL_FAILED"
            self._fault("after_production_firewall")
            self._mark_sealed_and_enqueue_completion(sealed)
            self._fault("after_database_sealed")
            return "SEALED"

    def _eligibility_code(self, row: sqlite3.Row) -> str:
        try:
            facts = collect_local_factory_facts(self.paths)
        except FactorySealError as error:
            return error.code
        if (
            facts.image_release_id != row["image_release_id"]
            or facts.image_release_sha256 != row["image_release_sha256"]
            or facts.factory_report_sha256 != row["factory_report_sha256"]
        ):
            return "FACTORY_SEAL_LOCAL_FACT_CHANGED"
        for path in (
            self.paths.enrollment_key,
            self.paths.enrollment_state,
            self.paths.enrollment_implementation,
        ):
            if _path_entry_exists(path):
                return "ENROLLMENT_CLEANUP_REQUIRED"
        credentials = _read_json(self.paths.credentials)
        if (
            credentials is None
            or credentials.get("schemaVersion") != 1
            or credentials.get("hardwareSn") != row["hardware_sn"]
        ):
            return "DEVICE_CREDENTIALS_INVALID"
        handoff = _read_json(self.paths.handoff_fact)
        if (
            handoff is None
            or handoff.get("schemaVersion") != 1
            or handoff.get("status") != "HANDOFF_SAFE"
            or handoff.get("imageReleaseId") != row["image_release_id"]
        ):
            return "HANDOFF_SAFE_REQUIRED"
        report = _read_json(self.paths.factory_report)
        capabilities = _read_json(self.paths.device_capabilities)
        if not isinstance(report, dict) or not valid_device_capabilities(
            capabilities,
            report,
        ):
            return "DEVICE_CAPABILITIES_INVALID"
        if self._maintenance_lock_exists():
            return "MAINTENANCE_BUSY"
        if not self._runtime_healthy():
            return "RUNTIME_NOT_HEALTHY"
        return "SEAL_READY"

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.paths.edge_store,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _current_authorization(self) -> sqlite3.Row | None:
        try:
            with self._connection() as connection:
                return connection.execute(
                    """SELECT * FROM factory_seal_authorization
                       WHERE state IN ('AUTHORIZED', 'SEALING', 'SEALED')
                       ORDER BY acceptance_generation DESC LIMIT 1"""
                ).fetchone()
        except sqlite3.Error:
            return None

    def _authorization_by_command(self, command_uid: object) -> sqlite3.Row | None:
        if not isinstance(command_uid, str):
            return None
        try:
            with self._connection() as connection:
                return connection.execute(
                    """SELECT * FROM factory_seal_authorization
                       WHERE command_uid=?""",
                    (command_uid,),
                ).fetchone()
        except sqlite3.Error:
            return None

    def _maintenance_lock_exists(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute(
                    "SELECT 1 FROM maintenance_lock LIMIT 1"
                ).fetchone() is not None
        except sqlite3.Error:
            return True

    def _mark_sealing(self, sealed: dict[str, object]) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._mark_sealing_in_connection(connection, sealed)
            connection.commit()

    def _write_seal_and_mark_sealing(
        self,
        expected: sqlite3.Row,
        sealed: dict[str, object],
    ) -> None:
        """Serialize an in-flight authorization against operator sealing."""

        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT * FROM factory_seal_authorization
                   WHERE state IN ('AUTHORIZED', 'SEALING', 'SEALED')
                   ORDER BY acceptance_generation DESC LIMIT 1"""
            ).fetchone()
            if (
                current is None
                or current["state"] != "AUTHORIZED"
                or current["command_uid"] != expected["command_uid"]
                or current["authorization_binding_sha256"]
                != expected["authorization_binding_sha256"]
                or self._eligibility_code(current) != "SEAL_READY"
            ):
                raise FactorySealError("FACTORY_SEAL_FACT_CHANGED")
            # This atomic file remains the first irreversible action.  The
            # SQLite write lock only serializes newer cloud authorization;
            # no local state or network policy changes before file+dir fsync.
            self._sealed_file.write_object(sealed)
            self._fault("after_sealed_directory_fsync")
            self._mark_sealing_in_connection(connection, sealed)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _mark_sealing_in_connection(
        connection: sqlite3.Connection,
        sealed: dict[str, object],
    ) -> None:
        updated = connection.execute(
            """UPDATE factory_seal_authorization
               SET state=CASE WHEN state='SEALED' THEN state ELSE 'SEALING' END,
                   operator_confirmation_uid=COALESCE(
                       operator_confirmation_uid, ?
                   ),
                   confirmed_at=COALESCE(confirmed_at, ?),
                   last_error=NULL
               WHERE command_uid=?
                 AND authorization_binding_sha256=?
                 AND (
                     operator_confirmation_uid IS NULL
                     OR operator_confirmation_uid=?
                 )
                 AND (confirmed_at IS NULL OR confirmed_at=?)
                 AND state IN ('AUTHORIZED', 'SEALING', 'SEALED')""",
            (
                sealed["operatorConfirmationUid"],
                sealed["sealedAt"],
                sealed["authorizationCommandUid"],
                sealed["authorizationBindingSha256"],
                sealed["operatorConfirmationUid"],
                sealed["sealedAt"],
            ),
        )
        if updated.rowcount != 1:
            raise FactorySealError("SEALED_FACT_INVALID")

    def _mark_sealed_and_enqueue_completion(
        self,
        sealed: dict[str, object],
    ) -> str:
        """Commit local completion and its reliable fact as one unit."""

        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM factory_seal_authorization
                   WHERE command_uid=?""",
                (sealed["authorizationCommandUid"],),
            ).fetchone()
            if (
                not self._sealed_matches_authorization(sealed, row)
                or row is None
                or row["state"] not in {"SEALING", "SEALED"}
                or row["operator_confirmation_uid"]
                != sealed["operatorConfirmationUid"]
                or row["confirmed_at"] != sealed["sealedAt"]
            ):
                raise FactorySealError("SEALED_FACT_INVALID")

            cleanup_completed_at = row["cleanup_completed_at"]
            completion_event_uid = row["completion_event_uid"]
            if (
                cleanup_completed_at is not None
                or completion_event_uid is not None
            ):
                if (
                    cleanup_completed_at is None
                    or completion_event_uid is None
                ):
                    raise FactorySealError(
                        "SEALED_COMPLETION_FACT_INVALID"
                    )
                event_row = connection.execute(
                    """SELECT event_uid, edge_event_sequence, event_type,
                              payload_json, work_uid
                       FROM event_outbox WHERE event_uid=?""",
                    (completion_event_uid,),
                ).fetchone()
                if not completion_event_matches_authorization(
                    sealed,
                    row,
                    event_row,
                ):
                    raise FactorySealError(
                        "SEALED_COMPLETION_FACT_INVALID"
                    )
                connection.commit()
                return completion_event_uid

            cleanup_clock = sample_clock()
            cleanup_completed_at = (
                cleanup_clock.raw_observed_at or raw_utc_now()
            )
            completion_clock_quality = _combined_clock_quality(
                sealed.get("sealedClockQuality", "SYNCED"),
                cleanup_clock.quality,
            )
            completion_event_uid = str(uuid.uuid4())
            edge_event_sequence = self._next_event_sequence(
                connection,
                cleanup_completed_at,
            )
            payload = factory_seal_completion_payload(
                sealed,
                row,
                cleanup_completed_at,
                completion_clock_quality,
            )
            event = build_event_envelope(
                event_uid=completion_event_uid,
                device_name=row["hardware_sn"],
                edge_event_sequence=edge_event_sequence,
                event_type="FACTORY_SEAL_COMPLETED",
                target_type="DEVICE_ASSET",
                target_uid=row["hardware_sn"],
                command_uid=row["command_uid"],
                payload=payload,
                delivery_class="RELIABLE_FACT",
            )
            # The platform treats cleanupCompletedAt as the authoritative
            # occurrence instant.  The generic builder samples its own clock,
            # so pin the envelope to the already-persisted transaction fact
            # even when the two calls cross a millisecond boundary.
            event["occurredAt"] = cleanup_completed_at
            event["clockQuality"] = completion_clock_quality
            if completion_clock_quality != "SYNCED":
                event["occurredAt"] = None
            updated = connection.execute(
                """UPDATE factory_seal_authorization
                   SET state='SEALED', completed_at=COALESCE(completed_at, ?),
                       cleanup_completed_at=?, completion_event_uid=?,
                       completion_clock_quality=?,
                       last_error=NULL
                   WHERE command_uid=?
                     AND authorization_binding_sha256=?
                     AND state IN ('SEALING', 'SEALED')
                     AND cleanup_completed_at IS NULL
                     AND completion_event_uid IS NULL""",
                (
                    cleanup_completed_at,
                    cleanup_completed_at,
                    completion_event_uid,
                    completion_clock_quality,
                    sealed["authorizationCommandUid"],
                    sealed["authorizationBindingSha256"],
                ),
            )
            if updated.rowcount != 1:
                raise FactorySealError("SEALED_COMPLETION_FACT_INVALID")
            self._fault("after_completion_state_written")
            connection.execute(
                """INSERT INTO event_outbox
                   (event_uid, edge_event_sequence, event_type,
                    payload_json, work_uid)
                   VALUES (?, ?, 'FACTORY_SEAL_COMPLETED', ?, ?)""",
                (
                    completion_event_uid,
                    edge_event_sequence,
                    json.dumps(event, ensure_ascii=False),
                    row["hardware_sn"],
                ),
            )
            self._fault("after_completion_event_written")
            connection.commit()
            return completion_event_uid
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _next_event_sequence(
        connection: sqlite3.Connection,
        updated_at: str,
    ) -> int:
        row = connection.execute(
            """SELECT state_value FROM device_state
               WHERE state_key='edge_event_sequence'"""
        ).fetchone()
        if row is None:
            raise FactorySealError("EDGE_EVENT_SEQUENCE_INVALID")
        try:
            sequence = int(row["state_value"]) + 1
        except (TypeError, ValueError):
            raise FactorySealError("EDGE_EVENT_SEQUENCE_INVALID") from None
        if not 1 <= sequence <= 9_999_999_999_999:
            raise FactorySealError("EDGE_EVENT_SEQUENCE_INVALID")
        updated = connection.execute(
            """UPDATE device_state SET state_value=?, updated_at=?
               WHERE state_key='edge_event_sequence'""",
            (str(sequence), updated_at),
        )
        if updated.rowcount != 1:
            raise FactorySealError("EDGE_EVENT_SEQUENCE_INVALID")
        return sequence

    def _read_sealed(self) -> dict[str, object] | None:
        if not _path_entry_exists(self.paths.sealed):
            return None
        try:
            return self._sealed_file.read_object()
        except (OSError, ValueError):
            return {"invalid": True}

    def _upgrade_legacy_sealed_marker(
        self,
        sealed: dict[str, object],
        row: sqlite3.Row,
    ) -> dict[str, object]:
        """Upgrade only a v1 marker whose completion is not yet durable."""

        if sealed.get("schemaVersion") != 1:
            return sealed
        if (
            row["cleanup_completed_at"] is not None
            or row["completion_event_uid"] is not None
        ):
            # A completed v1 outbox row is an immutable reliable fact.  Its
            # canonical payload intentionally lacks the v2 clock-quality
            # field, so rewriting only the marker would make restart
            # validation compare two different schema versions.
            return sealed
        upgraded = dict(sealed)
        upgraded["schemaVersion"] = 2
        # Schema v1 defined both persisted device instants as trusted.  Keep
        # that historical meaning explicit; the later cleanup sample can then
        # lower the combined completion quality without producing a v1 event
        # that illegally carries an absent occurrence time.
        upgraded["sealedClockQuality"] = "SYNCED"
        self._sealed_file.write_object(upgraded)
        self._fault("after_legacy_seal_v2_upgrade")
        return upgraded

    @staticmethod
    def _sealed_matches_authorization(
        sealed: dict[str, object],
        row: sqlite3.Row | None,
    ) -> bool:
        return sealed_document_matches_authorization(sealed, row)

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)


def _public_status(
    *,
    authorized: bool,
    confirm_allowed: bool,
    status_code: str,
    generation: int | None = None,
    binding: str | None = None,
) -> dict[str, object]:
    return {
        "authorized": authorized,
        "confirmAllowed": confirm_allowed,
        "statusCode": status_code,
        "acceptanceGeneration": generation,
        "authorizationBindingSha256": binding,
    }


def _uuid4(value: str, error_code: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise FactorySealError(error_code) from None
    if parsed.version != 4 or str(parsed) != value:
        raise FactorySealError(error_code)
    return value


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size < 2 or info.st_size > 64 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _unlink_and_fsync(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    _fsync_directory(path.parent)
    return True


def _clear_flat_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise FactorySealError("FACTORY_TEMP_ARTIFACT_INVALID")
    for entry in path.iterdir():
        details = entry.lstat()
        if stat.S_ISDIR(details.st_mode) and not stat.S_ISLNK(details.st_mode):
            raise FactorySealError("FACTORY_TEMP_ARTIFACT_INVALID")
        entry.unlink()
    _fsync_directory(path)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _runtime_healthy() -> bool:
    for unit in (
        "ecobin-hardware.service",
        "ecobin-cellular-uplink.service",
        "ecobin-remote-support.service",
    ):
        try:
            result = subprocess.run(
                ("/usr/bin/systemctl", "is-active", "--quiet", unit),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
    return True


def _stop_factory() -> bool:
    result = subprocess.run(
        ("/usr/bin/systemctl", "stop", "ecobin-factory.target"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def _apply_production_firewall() -> bool:
    try:
        from first_boot.cellular_config import load_cellular_config
        from first_boot.cellular_firewall import (
            apply_emergency_uplink_lock,
            apply_production_uplink_gate,
        )
        from first_boot.cellular_probe import (
            SysfsUsbNetworkInventory,
            select_rndis_device,
        )

        config = load_cellular_config()
        device, _error_code = select_rndis_device(
            config,
            SysfsUsbNetworkInventory().devices(),
        )
        if device is None:
            apply_emergency_uplink_lock()
            return False
        return apply_production_uplink_gate(device.interface)
    except Exception:
        _apply_emergency_firewall()
        return False


def _apply_emergency_firewall() -> bool:
    try:
        from first_boot.cellular_firewall import (
            apply_emergency_uplink_lock,
        )

        return apply_emergency_uplink_lock()
    except Exception:
        return False


def _sha256_text(value: str) -> str:
    return __import__("hashlib").sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _combined_clock_quality(first: str, second: str) -> str:
    qualities = {first, second}
    if qualities == {"SYNCED"}:
        return "SYNCED"
    if "UNAVAILABLE" in qualities:
        return "UNAVAILABLE"
    return "ESTIMATED"


__all__ = ["FactorySealController"]
