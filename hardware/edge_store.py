"""edge_store.py —— 香橙派边缘 SQLite 存储。

SQLite 是香橙派的唯一持久化真相源。
"""

from __future__ import annotations

import json as _json
import hashlib
import logging
import os
import sqlite3
import threading
import time
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from onenet_wire import (
    build_business_confirmation_receipt,
    build_configuration_progress_event,
    build_event_envelope,
    canonical_payload_sha256,
    validate_factory_seal_envelope_at_acceptance,
)
from factory_seal.errors import FactorySealError
from factory_seal.validation import authorization_binding_sha256
from trusted_clock import local_deadline_reference, sample_clock

logger = logging.getLogger("edge-store")

CURRENT_SCHEMA_VERSION = 18
WORK_TYPE_NONE = "NONE"
WORK_TYPE_DELIVERY = "DELIVERY"
WORK_TYPE_CLEAN = "CLEAN"
WORK_TYPE_FULLNESS = "FULLNESS"
WORK_TYPE_BASELINE = "BASELINE"
EVENT_PENDING = "PENDING"
EVENT_SENDING = "SENDING"
EVENT_CONFIRMED = "CONFIRMED"
EVENT_DEAD = "DEAD"
PHOTO_CAPTURE_PENDING = "CAPTURE_PENDING"
PHOTO_PENDING = "PENDING"
PHOTO_UPLOADING = "UPLOADING"
PHOTO_UPLOADED = "UPLOADED"
PHOTO_DEAD = "DEAD"
FAULT_OBSERVED = "OBSERVED"
FAULT_RECOVERED = "RECOVERED"
REMOTE_SUPPORT_TERMINAL_STATES = frozenset({
    "CLOSED",
    "FAILED",
    "EXPIRED",
})
REMOTE_SUPPORT_FAILURE_CODES = frozenset({
    "CREDENTIALS_INVALID",
    "SSH_NOT_AVAILABLE",
    "SSH_START_FAILED",
    "SSH_EXITED",
    "PROCESS_SUPERVISION_FAILED",
})

# A factory-seal authorization has no physical side effect until the local
# authorization row, command completion and ACCEPTED observation commit in one
# SQLite transaction.  These local prerequisites may be repaired safely and
# retried only after OneNet redelivers the same immutable command.  Keep this
# list beside the persistence transition so a caller cannot requeue an
# arbitrary FAILED command by mistake.
FACTORY_SEAL_RETRYABLE_ERROR_CODES = frozenset({
    "EDGE_RESTARTED",
    "FACTORY_SEAL_NOT_AVAILABLE",
    "IMAGE_RELEASE_INVALID",
    "FACTORY_REPORT_INVALID",
    "DEVICE_CREDENTIALS_INVALID",
    "FACTORY_SEAL_RETRYABLE_FAILURE",
})

# These failures prove that the frozen cloud authority cannot be reconciled
# with this EdgeStore.  Retrying the same command bytes cannot change that
# fact, so the command processor may emit the terminal REJECTED observation.
FACTORY_SEAL_TERMINAL_ERROR_CODES = frozenset({
    "FACTORY_ALREADY_SEALED",
    "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID",
    "FACTORY_SEAL_COMMAND_CONFLICT",
    "FACTORY_SEAL_COMMAND_STATE_INVALID",
    "ACCEPTANCE_EVIDENCE_NOT_FOUND",
    "ACCEPTANCE_EVIDENCE_NOT_LATEST",
    "ACCEPTANCE_EVIDENCE_INVALID",
    "ACCEPTANCE_EVIDENCE_MISMATCH",
    "FACTORY_SEAL_GENERATION_STALE",
    "FACTORY_SEAL_GENERATION_CONFLICT",
    "FACTORY_SEAL_OBSERVATION_CONFLICT",
})
FACTORY_SEAL_ACCEPTANCE_EVIDENCE_SCHEMA_VERSIONS = frozenset({3, 4})
MCU_UPDATE_ACTIVE_STATES = frozenset({
    "QUEUED",
    "PACKAGE_FETCH_FAILED",
    "PREFLIGHT",
    "PREPARED",
    "FLASHING_TARGET",
    "VERIFYING_TARGET",
    "ROLLING_BACK",
    "VERIFYING_ROLLBACK",
    "FAILED_LOCKED",
})
MCU_UPDATE_TERMINAL_STATES = frozenset({
    "SUCCEEDED",
    "ROLLED_BACK",
    "REJECTED",
})


def _canonical_mcu_prepare_identity(identity: dict) -> str:
    """Validate and canonically encode the application identity before F2."""

    if not isinstance(identity, dict):
        raise ValueError("MCU prepare identity must be an object")
    snapshot = {
        field: identity.get(field)
        for field in (
            "protocolRevision",
            "firmwareVersionCode",
            "firmwareVersion",
            "firmwareIdentityHex",
        )
    }
    if (
        not isinstance(snapshot["protocolRevision"], int)
        or isinstance(snapshot["protocolRevision"], bool)
        or snapshot["protocolRevision"] != 2
        or not isinstance(snapshot["firmwareVersionCode"], int)
        or isinstance(snapshot["firmwareVersionCode"], bool)
        or snapshot["firmwareVersionCode"] <= 0
        or not isinstance(snapshot["firmwareVersion"], str)
        or not snapshot["firmwareVersion"]
        or not isinstance(snapshot["firmwareIdentityHex"], str)
        or len(snapshot["firmwareIdentityHex"]) != 16
        or any(
            character not in "0123456789abcdef"
            for character in snapshot["firmwareIdentityHex"]
        )
    ):
        raise ValueError("MCU prepare identity is invalid")
    encoded = _json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return encoded


def _parse_utc_instant(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an RFC3339 instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an RFC3339 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _require_uuid4_local(value: str, field: str) -> str:
    try:
        parsed = _uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(f"{field} must be a lowercase UUIDv4")
    return value


def _validate_device_entry_url(url: str, sha256: str) -> None:
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 192
        or not url.startswith("https://")
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in url)
    ):
        raise ValueError(
            "device entry URL must be printable ASCII HTTPS within 192 bytes"
        )
    if (
        not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ValueError("device entry URL SHA-256 is invalid")
    if hashlib.sha256(url.encode("ascii")).hexdigest() != sha256:
        raise ValueError("device entry URL SHA-256 mismatch")


class EdgeStore:
    """香橙派边缘 SQLite 存储。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self._open_connection()
        self._migrate()
        recovered_events = self.recover_sending_events()
        if recovered_events:
            logger.info(
                "Recovered %d in-flight events for retransmission",
                recovered_events,
            )
        logger.info("EdgeStore 初始化: %s (v%d)", self.db_path, CURRENT_SCHEMA_VERSION)

    def prepare_schema(self) -> None:
        """Migrate and verify the store without starting runtime recovery.

        The first-boot coordinator calls this through an early, networkless
        oneshot before it reads the factory-seal table.  This breaks the boot
        dependency cycle for an empty database, a v14 database, or a database
        whose previous migration transaction was interrupted by power loss.
        """

        if self._conn is not None:
            raise RuntimeError("EdgeStore schema preparation requires a closed store")
        try:
            self._open_connection()
            self._migrate()
            version = self._conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            table = self._conn.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='factory_seal_authorization'"""
            ).fetchone()
            quick_check = self._conn.execute("PRAGMA quick_check").fetchone()[0]
            if (
                version != CURRENT_SCHEMA_VERSION
                or table is None
                or quick_check != "ok"
            ):
                raise RuntimeError("EdgeStore schema preparation verification failed")
        finally:
            self.close()

    def _open_connection(self) -> None:
        if self._conn is not None:
            return
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        self._conn = conn

    def _migrate(self) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._apply_migrations(conn)
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Apply every pending schema step inside one explicit transaction."""

        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        if current == CURRENT_SCHEMA_VERSION:
            # Re-check the latest durable shapes even when their version rows
            # are present.  A copied or historically partially-applied
            # database must fail closed instead of silently weakening either
            # clock evidence or command-observation identity.
            self._migrate_v17()
            self._migrate_v18()
            return
        if current not in {0, 9, 10, 11, 12, 13, 14, 15, 16, 17}:
            raise RuntimeError(
                "EdgeStore 数据库时代不兼容；永久资产 v9 不读取旧设备数据库"
            )
        logger.info("EdgeStore 创建永久资产数据库 v%d", CURRENT_SCHEMA_VERSION)
        if current < 1:
            self._create_tables()
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            current = 1
        if current < 2:
            self._migrate_v2()
            conn.execute("INSERT INTO schema_version (version) VALUES (2)")
            current = 2
        if current < 3:
            self._migrate_v3()
            conn.execute("INSERT INTO schema_version (version) VALUES (3)")
            current = 3
        if current < 4:
            self._migrate_v4()
            conn.execute("INSERT INTO schema_version (version) VALUES (4)")
            current = 4
        if current < 5:
            self._migrate_v5()
            conn.execute("INSERT INTO schema_version (version) VALUES (5)")
            current = 5
        if current < 6:
            self._migrate_v6()
            conn.execute("INSERT INTO schema_version (version) VALUES (6)")
            current = 6
        if current < 7:
            self._migrate_v7()
            conn.execute("INSERT INTO schema_version (version) VALUES (7)")
            current = 7
        if current < 8:
            self._migrate_v8()
            conn.execute("INSERT INTO schema_version (version) VALUES (8)")
            current = 8
        if current < 9:
            conn.execute("INSERT INTO schema_version (version) VALUES (9)")
            current = 9
        if current < 10:
            self._migrate_v10()
            conn.execute("INSERT INTO schema_version (version) VALUES (10)")
            current = 10
        if current < 11:
            self._migrate_v11()
            conn.execute("INSERT INTO schema_version (version) VALUES (11)")
            current = 11
        if current < 12:
            self._migrate_v12()
            conn.execute("INSERT INTO schema_version (version) VALUES (12)")
            current = 12
        if current < 13:
            self._migrate_v13()
            conn.execute("INSERT INTO schema_version (version) VALUES (13)")
            current = 13
        if current < 14:
            self._migrate_v14()
            conn.execute("INSERT INTO schema_version (version) VALUES (14)")
            current = 14
        if current < 15:
            self._migrate_v15()
            conn.execute("INSERT INTO schema_version (version) VALUES (15)")
            current = 15
        if current < 16:
            self._migrate_v16()
            conn.execute("INSERT INTO schema_version (version) VALUES (16)")
            current = 16
        if current < 17:
            self._migrate_v17()
            conn.execute("INSERT INTO schema_version (version) VALUES (17)")
            current = 17
        if current < 18:
            self._migrate_v18()
            conn.execute("INSERT INTO schema_version (version) VALUES (18)")

    def _migrate_v10(self) -> None:
        """Add the independent, reboot-safe remote-support control slot."""

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS remote_support_session (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                session_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                device_name TEXT NOT NULL,
                remote_port INTEGER NOT NULL
                    CHECK (remote_port BETWEEN 22011 AND 22014),
                expires_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'CONNECTING', 'OPEN', 'CLOSING', 'CLOSED',
                    'FAILED', 'EXPIRED'
                )),
                failure_code TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )

    def _migrate_v11(self) -> None:
        """Add an exclusive, reboot-safe STM32 firmware update journal."""

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS maintenance_lock (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                lock_type TEXT NOT NULL CHECK (
                    lock_type = 'MCU_FIRMWARE_UPDATE'
                ),
                owner_uid TEXT NOT NULL,
                detail_json TEXT,
                acquired_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_update (
                update_uid TEXT PRIMARY KEY,
                deployment_uid TEXT NOT NULL UNIQUE,
                command_uid TEXT UNIQUE,
                source TEXT NOT NULL CHECK (source IN ('CLOUD', 'LOCAL')),
                package_path TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'QUEUED', 'PREFLIGHT', 'PREPARED',
                    'FLASHING_TARGET', 'VERIFYING_TARGET',
                    'ROLLING_BACK', 'VERIFYING_ROLLBACK',
                    'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED',
                    'REJECTED'
                )),
                legacy_preflight INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_preflight IN (0, 1)),
                allow_downgrade INTEGER NOT NULL DEFAULT 0
                    CHECK (allow_downgrade IN (0, 1)),
                requested_reason TEXT,
                target_attempt_count INTEGER NOT NULL DEFAULT 0,
                rollback_attempt_count INTEGER NOT NULL DEFAULT 0,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_mcu_update_state
               ON mcu_firmware_update(state, requested_at)"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_progress (
                update_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                target_attempt_count INTEGER NOT NULL,
                rollback_attempt_count INTEGER NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (
                    update_uid, stage,
                    target_attempt_count, rollback_attempt_count
                )
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_firmware_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                current_package_path TEXT,
                current_package_sha256 TEXT,
                current_manifest_json TEXT,
                current_installed_at TEXT,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                previous_installed_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        now = self._now()
        self._conn.execute(
            """INSERT OR IGNORE INTO mcu_firmware_state
               (singleton_id, updated_at) VALUES (1, ?)""",
            (now,),
        )

    def _migrate_v12(self) -> None:
        """Reserve cloud updates before package I/O and widen the journal state."""

        self._conn.execute(
            "ALTER TABLE mcu_firmware_update RENAME TO mcu_firmware_update_v11"
        )
        self._conn.execute(
            """CREATE TABLE mcu_firmware_update (
                update_uid TEXT PRIMARY KEY,
                deployment_uid TEXT NOT NULL UNIQUE,
                command_uid TEXT UNIQUE,
                source TEXT NOT NULL CHECK (source IN ('CLOUD', 'LOCAL')),
                package_path TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                package_ready INTEGER NOT NULL DEFAULT 1
                    CHECK (package_ready IN (0, 1)),
                state TEXT NOT NULL CHECK (state IN (
                    'QUEUED', 'PACKAGE_FETCH_FAILED',
                    'PREFLIGHT', 'PREPARED',
                    'FLASHING_TARGET', 'VERIFYING_TARGET',
                    'ROLLING_BACK', 'VERIFYING_ROLLBACK',
                    'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED',
                    'REJECTED'
                )),
                legacy_preflight INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_preflight IN (0, 1)),
                allow_downgrade INTEGER NOT NULL DEFAULT 0
                    CHECK (allow_downgrade IN (0, 1)),
                requested_reason TEXT,
                target_attempt_count INTEGER NOT NULL DEFAULT 0,
                rollback_attempt_count INTEGER NOT NULL DEFAULT 0,
                previous_package_path TEXT,
                previous_package_sha256 TEXT,
                previous_manifest_json TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )
        self._conn.execute(
            """INSERT INTO mcu_firmware_update (
                 update_uid, deployment_uid, command_uid, source,
                 package_path, package_sha256, manifest_json,
                 package_ready, state, legacy_preflight, allow_downgrade,
                 requested_reason, target_attempt_count,
                 rollback_attempt_count, previous_package_path,
                 previous_package_sha256, previous_manifest_json,
                 last_error_code, last_error_message, requested_at,
                 started_at, updated_at, completed_at
               )
               SELECT update_uid, deployment_uid, command_uid, source,
                      package_path, package_sha256, manifest_json,
                      1, state, legacy_preflight, allow_downgrade,
                      requested_reason, target_attempt_count,
                      rollback_attempt_count, previous_package_path,
                      previous_package_sha256, previous_manifest_json,
                      last_error_code, last_error_message, requested_at,
                      started_at, updated_at, completed_at
               FROM mcu_firmware_update_v11"""
        )
        self._conn.execute("DROP TABLE mcu_firmware_update_v11")
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_mcu_update_state
               ON mcu_firmware_update(state, requested_at)"""
        )

    def _migrate_v13(self) -> None:
        """Bound cloud package acquisition across task wake generations."""

        self._conn.execute(
            """ALTER TABLE mcu_firmware_update
               ADD COLUMN package_acquisition_attempt_count INTEGER
                   NOT NULL DEFAULT 0
                   CHECK (package_acquisition_attempt_count >= 0)"""
        )

    def _migrate_v14(self) -> None:
        """Persist the point after which an F2 stop command may have run."""

        # V14 was first released before migrations used an explicit
        # transaction. A power loss may therefore leave either column on a
        # database whose recorded version is still 13. Resume from the actual
        # table shape instead of replaying already-persisted DDL.
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('mcu_firmware_update')"
            ).fetchall()
        }
        if "prepare_recovery_required" not in columns:
            self._conn.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_recovery_required INTEGER
                       NOT NULL DEFAULT 0
                       CHECK (prepare_recovery_required IN (0, 1))"""
            )
        if "prepare_identity_json" not in columns:
            self._conn.execute(
                """ALTER TABLE mcu_firmware_update
                   ADD COLUMN prepare_identity_json TEXT"""
            )
        uncertain_updates = self._conn.execute(
            """SELECT update_uid, previous_manifest_json
               FROM mcu_firmware_update
               WHERE legacy_preflight=0
                 AND state IN ('PREFLIGHT', 'PREPARED')
                 AND target_attempt_count=0
                 AND rollback_attempt_count=0"""
        ).fetchall()
        for update in uncertain_updates:
            identity_json = None
            try:
                manifest = _json.loads(update["previous_manifest_json"])
                identity_json = _canonical_mcu_prepare_identity(
                    {
                        "protocolRevision": manifest.get(
                            "fixedFrameRevision"
                        ),
                        "firmwareVersionCode": manifest.get(
                            "firmwareVersionCode"
                        ),
                        "firmwareVersion": manifest.get("firmwareVersion"),
                        "firmwareIdentityHex": manifest.get(
                            "firmwareIdentityHex"
                        ),
                    }
                )

            except (AttributeError, TypeError, ValueError):
                # v13 did not persist the exact pre-F2 F3 snapshot.  Missing
                # or corrupt stable identity must migrate fail-closed.
                pass
            self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET prepare_recovery_required=1,
                       prepare_identity_json=?
                   WHERE update_uid=?""",
                (identity_json, update["update_uid"]),
            )

    def _migrate_v15(self) -> None:
        """Add the monotonic, reboot-safe factory-seal authorization journal.

        Every DDL statement is shape-aware and idempotent.  This is required
        even though current migrations run in one explicit transaction:
        deployed v14 databases may be left with durable DDL but no recorded
        v15 version by older SQLite wrappers or a process kill at the boundary.
        """

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS factory_seal_authorization (
                command_uid TEXT PRIMARY KEY,
                command_sha256 TEXT NOT NULL,
                hardware_sn TEXT NOT NULL,
                acceptance_generation INTEGER NOT NULL
                    CHECK (acceptance_generation > 0),
                evidence_event_uid TEXT NOT NULL,
                acceptance_challenge_uid TEXT NOT NULL,
                acceptance_evidence_sha256 TEXT NOT NULL,
                factory_bag_revision INTEGER NOT NULL
                    CHECK (factory_bag_revision >= 0),
                factory_bag_set_sha256 TEXT NOT NULL,
                image_release_id TEXT NOT NULL,
                image_release_sha256 TEXT NOT NULL,
                factory_report_sha256 TEXT NOT NULL,
                authorization_binding_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'AUTHORIZED', 'SUPERSEDED', 'SEALING', 'SEALED'
                )),
                operator_confirmation_uid TEXT,
                authorized_at TEXT NOT NULL,
                confirmed_at TEXT,
                completed_at TEXT,
                last_error TEXT
            )"""
        )
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        required = {
            "command_uid",
            "command_sha256",
            "hardware_sn",
            "acceptance_generation",
            "evidence_event_uid",
            "acceptance_challenge_uid",
            "acceptance_evidence_sha256",
            "factory_bag_revision",
            "factory_bag_set_sha256",
            "image_release_id",
            "image_release_sha256",
            "factory_report_sha256",
            "authorization_binding_sha256",
            "state",
            "operator_confirmation_uid",
            "authorized_at",
            "confirmed_at",
            "completed_at",
            "last_error",
        }
        forward_columns = {
            "cleanup_completed_at",
            "completion_event_uid",
            "completion_clock_quality",
        }
        if (
            not required.issubset(columns)
            or not columns.issubset(required | forward_columns)
        ):
            raise RuntimeError(
                "factory seal authorization table shape is incompatible"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_acceptance_generation
               ON factory_seal_authorization(acceptance_generation)"""
        )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_operator_confirmation
               ON factory_seal_authorization(operator_confirmation_uid)
               WHERE operator_confirmation_uid IS NOT NULL"""
        )
        self._conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_factory_seal_state
               ON factory_seal_authorization(state, acceptance_generation)"""
        )

    def _migrate_v16(self) -> None:
        """Persist the atomic factory-seal completion fact and outbox link.

        The first release of this migration may be interrupted after either
        ``ALTER TABLE`` has reached durable storage but before schema version
        16 is recorded.  Inspecting the real table shape makes every restart
        safe: an existing column is retained, a missing column is added, and
        the complete final shape is verified before the version advances.
        """

        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        if "cleanup_completed_at" not in columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN cleanup_completed_at TEXT"""
            )
        if "completion_event_uid" not in columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN completion_event_uid TEXT"""
            )

        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        required = {
            "command_uid",
            "command_sha256",
            "hardware_sn",
            "acceptance_generation",
            "evidence_event_uid",
            "acceptance_challenge_uid",
            "acceptance_evidence_sha256",
            "factory_bag_revision",
            "factory_bag_set_sha256",
            "image_release_id",
            "image_release_sha256",
            "factory_report_sha256",
            "authorization_binding_sha256",
            "state",
            "operator_confirmation_uid",
            "authorized_at",
            "confirmed_at",
            "completed_at",
            "last_error",
            "cleanup_completed_at",
            "completion_event_uid",
        }
        if not required.issubset(columns) or not columns.issubset(
            required | {"completion_clock_quality"}
        ):
            raise RuntimeError(
                "factory seal authorization v16 table shape is incompatible"
            )
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS
                   idx_factory_seal_completion_event
               ON factory_seal_authorization(completion_event_uid)
               WHERE completion_event_uid IS NOT NULL"""
        )
        index = next(
            (
                row
                for row in self._conn.execute(
                    "PRAGMA index_list('factory_seal_authorization')"
                ).fetchall()
                if row["name"] == "idx_factory_seal_completion_event"
            ),
            None,
        )
        indexed_columns = [
            row["name"]
            for row in self._conn.execute(
                "PRAGMA index_info('idx_factory_seal_completion_event')"
            ).fetchall()
        ]
        if (
            index is None
            or index["unique"] != 1
            or index["partial"] != 1
            or indexed_columns != ["completion_event_uid"]
        ):
            raise RuntimeError(
                "factory seal completion index shape is incompatible"
            )

    def _migrate_v17(self) -> None:
        """Persist clock trust beside raw device wall-clock observations."""

        command_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('command_inbox')"
            ).fetchall()
        }
        if "received_clock_quality" not in command_columns:
            self._conn.execute(
                """ALTER TABLE command_inbox
                   ADD COLUMN received_clock_quality TEXT NOT NULL
                       DEFAULT 'ESTIMATED'
                       CHECK (received_clock_quality IN (
                           'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                       ))"""
            )

        photo_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('photo_outbox')"
            ).fetchall()
        }
        if "captured_clock_quality" not in photo_columns:
            self._conn.execute(
                """ALTER TABLE photo_outbox
                   ADD COLUMN captured_clock_quality TEXT
                       CHECK (captured_clock_quality IS NULL OR
                              captured_clock_quality IN (
                                  'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                              ))"""
            )
        self._conn.execute(
            """UPDATE photo_outbox
               SET captured_clock_quality='ESTIMATED'
               WHERE captured_at IS NOT NULL
                 AND captured_clock_quality IS NULL"""
        )

        seal_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info('factory_seal_authorization')"
            ).fetchall()
        }
        if "completion_clock_quality" not in seal_columns:
            self._conn.execute(
                """ALTER TABLE factory_seal_authorization
                   ADD COLUMN completion_clock_quality TEXT
                       CHECK (completion_clock_quality IS NULL OR
                              completion_clock_quality IN (
                                  'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
                              ))"""
            )
        self._conn.execute(
            """UPDATE factory_seal_authorization
               SET completion_clock_quality='SYNCED'
               WHERE state='SEALED'
                 AND completion_clock_quality IS NULL"""
        )

        required_shapes = {
            "command_inbox": "received_clock_quality",
            "photo_outbox": "captured_clock_quality",
            "factory_seal_authorization": "completion_clock_quality",
        }
        for table, column in required_shapes.items():
            columns = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info('{table}')"
                ).fetchall()
            }
            if column not in columns:
                raise RuntimeError(
                    f"EdgeStore v17 {table} shape is incompatible"
                )

    def _migrate_v18(self) -> None:
        """Qualify one command stage by its stable error identity.

        A clean operation can first time out and later be interrupted by an
        edge restart.  Both are reliable facts with stage ``FAILED``; the
        second fact must not overwrite the first.  Empty ``error_code`` is
        the durable identity for stages whose wire ``errorCode`` is null.
        """

        table_info = self._conn.execute(
            "PRAGMA table_info('command_observation')"
        ).fetchall()
        columns = {row["name"] for row in table_info}
        primary_key = [
            row["name"]
            for row in sorted(
                (row for row in table_info if row["pk"]),
                key=lambda row: row["pk"],
            )
        ]
        v18_columns = {
            "command_uid",
            "stage",
            "error_code",
            "event_uid",
            "canonical_sha256",
            "created_at",
        }
        if columns == v18_columns:
            if primary_key != ["command_uid", "stage", "error_code"]:
                raise RuntimeError(
                    "EdgeStore v18 command observation key is incompatible"
                )
            event_uid_unique = any(
                index["unique"] == 1
                and [
                    row["name"]
                    for row in self._conn.execute(
                        f"PRAGMA index_info('{index['name']}')"
                    ).fetchall()
                ] == ["event_uid"]
                for index in self._conn.execute(
                    "PRAGMA index_list('command_observation')"
                ).fetchall()
            )
            if not event_uid_unique:
                raise RuntimeError(
                    "EdgeStore v18 command event identity is not unique"
                )
            return

        v17_columns = v18_columns - {"error_code"}
        if (
            columns != v17_columns
            or primary_key != ["command_uid", "stage"]
        ):
            raise RuntimeError(
                "EdgeStore v17 command observation shape is incompatible"
            )
        temporary = self._conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name IN (
                   'command_observation_v17',
                   'command_observation_v18'
               )"""
        ).fetchall()
        if temporary:
            raise RuntimeError(
                "EdgeStore v18 command observation migration is incomplete"
            )

        rows = self._conn.execute(
            """SELECT observation.command_uid, observation.stage,
                      observation.event_uid,
                      observation.canonical_sha256,
                      observation.created_at,
                      event_row.payload_json
               FROM command_observation observation
               LEFT JOIN event_outbox event_row
                 ON event_row.event_uid = observation.event_uid
               ORDER BY observation.created_at, observation.event_uid"""
        ).fetchall()
        migrated_rows: list[tuple[str, str, str, str, str, str]] = []
        for row in rows:
            try:
                event = _json.loads(row["payload_json"])
                payload = event["payload"]
                error_code = payload.get("errorCode")
            except (KeyError, TypeError, _json.JSONDecodeError) as error:
                raise RuntimeError(
                    "EdgeStore v18 command observation event is invalid"
                ) from error
            if (
                event.get("eventType") != "DEVICE_COMMAND_OBSERVED"
                or event.get("commandUid") != row["command_uid"]
                or payload.get("stage") != row["stage"]
                or (
                    error_code is not None
                    and (
                        not isinstance(error_code, str)
                        or not error_code
                    )
                )
                or canonical_payload_sha256(payload)
                != row["canonical_sha256"]
            ):
                raise RuntimeError(
                    "EdgeStore v18 command observation event conflicts "
                    "with its identity"
                )
            migrated_rows.append((
                row["command_uid"],
                row["stage"],
                error_code if error_code is not None else "",
                row["event_uid"],
                row["canonical_sha256"],
                row["created_at"],
            ))

        self._conn.execute(
            """CREATE TABLE command_observation_v18 (
                command_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                error_code TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                canonical_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (command_uid, stage, error_code)
            )"""
        )
        self._conn.executemany(
            """INSERT INTO command_observation_v18
               (command_uid, stage, error_code, event_uid,
                canonical_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            migrated_rows,
        )
        self._conn.execute(
            "ALTER TABLE command_observation RENAME TO command_observation_v17"
        )
        self._conn.execute(
            "ALTER TABLE command_observation_v18 RENAME TO command_observation"
        )
        self._conn.execute("DROP TABLE command_observation_v17")

    def _create_tables(self) -> None:
        conn = self._conn
        conn.execute("""CREATE TABLE IF NOT EXISTS command_inbox (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            command_uid TEXT NOT NULL UNIQUE,
            command_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cmd_inbox_state ON command_inbox(state)")
        conn.execute("""CREATE TABLE IF NOT EXISTS work_slot (
            slot_id INTEGER PRIMARY KEY CHECK (slot_id = 1),
            work_type TEXT NOT NULL DEFAULT 'NONE',
            work_uid TEXT,
            work_state TEXT,
            port_no INTEGER,
            context_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT OR IGNORE INTO work_slot (slot_id) VALUES (1)")
        conn.execute("""CREATE TABLE IF NOT EXISTS event_outbox (
            event_uid TEXT NOT NULL PRIMARY KEY,
            edge_event_sequence INTEGER NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            mqtt_msg_id INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT,
            last_platform_code INTEGER,
            platform_accepted_at TEXT,
            last_platform_reply_at TEXT,
            work_uid TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_state ON event_outbox(state, tombstoned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_work ON event_outbox(work_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evt_retry ON event_outbox(state, next_retry_at) WHERE state='PENDING' AND tombstoned=0")
        conn.execute("""CREATE TABLE IF NOT EXISTS photo_outbox (
            photo_uid TEXT NOT NULL PRIMARY KEY,
            slot_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            cos_key TEXT,
            url TEXT,
            state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            uploaded_at TEXT,
            work_uid TEXT,
            work_type TEXT,
            device_name TEXT,
            content_sha256 TEXT,
            size_bytes INTEGER,
            captured_at TEXT,
            grant_request_event_uid TEXT,
            grant_generation INTEGER NOT NULL DEFAULT 0,
            status_event_uid TEXT,
            last_error TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_state ON photo_outbox(state, tombstoned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_work ON photo_outbox(work_uid)")
        conn.execute("""CREATE TABLE IF NOT EXISTS confirmation_inbox (
            confirmation_uid TEXT NOT NULL PRIMARY KEY,
            event_uid TEXT NOT NULL,
            outcome TEXT NOT NULL,
            payload_json TEXT,
            received_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conf_event ON confirmation_inbox(event_uid)")
        conn.execute("""CREATE TABLE IF NOT EXISTS device_state (
            state_key TEXT NOT NULL PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.executemany(
            "INSERT OR IGNORE INTO device_state (state_key, state_value) VALUES (?, ?)",
            [
                ("edge_boot_id", ""),
                ("edge_event_sequence", "0"),
                ("mqtt_session_present", "false"),
                ("mqtt_last_rc", "0"),
            ],
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS faults (
            fault_uid TEXT NOT NULL PRIMARY KEY,
            component TEXT NOT NULL,
            fault_code INTEGER NOT NULL,
            severity TEXT NOT NULL,
            lifecycle TEXT NOT NULL DEFAULT 'OBSERVED',
            detail_json TEXT,
            observed_at TEXT NOT NULL DEFAULT (datetime('now')),
            recovered_at TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faults_lifecycle ON faults(lifecycle)")
        conn.execute("""CREATE TABLE IF NOT EXISTS tombstones (
            tombstone_uid TEXT NOT NULL PRIMARY KEY,
            original_type TEXT NOT NULL,
            original_uid TEXT NOT NULL,
            cleared_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")

    def _migrate_v2(self) -> None:
        conn = self._conn
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(command_inbox)").fetchall()
        }
        additions = {
            "canonical_sha256": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "processing_started_at": "TEXT",
            "last_error": "TEXT",
            "result_json": "TEXT",
            "mcu_command_uid": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE command_inbox ADD COLUMN {name} {declaration}"
                )
        rows = conn.execute(
            "SELECT rowid, payload_json FROM command_inbox WHERE canonical_sha256 IS NULL"
        ).fetchall()
        for row in rows:
            payload = _json.loads(row["payload_json"])
            stable = dict(payload) if isinstance(payload, dict) else payload
            if isinstance(stable, dict):
                stable.pop("cosGrant", None)
            conn.execute(
                "UPDATE command_inbox SET canonical_sha256=? WHERE rowid=?",
                (canonical_payload_sha256(stable), row["rowid"]),
            )

        conn.execute("""CREATE TABLE IF NOT EXISTS configuration_state (
            application_uid TEXT NOT NULL PRIMARY KEY,
            command_uid TEXT NOT NULL UNIQUE,
            device_name TEXT NOT NULL,
            config_version INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            mcu_payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            part_command_uids_json TEXT NOT NULL,
            state TEXT NOT NULL,
            commit_mcu_command_uid TEXT,
            error_code TEXT,
            edge_saved_at TEXT NOT NULL,
            applied_at TEXT
        )""")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_config_version "
            "ON configuration_state(config_version)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS mcu_event_inbox (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            mcu_boot_id INTEGER NOT NULL,
            mcu_event_sequence INTEGER NOT NULL,
            message_name TEXT NOT NULL,
            message_type INTEGER NOT NULL,
            source_tx_sequence INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT,
            last_error TEXT,
            UNIQUE(mcu_boot_id, mcu_event_sequence)
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcu_event_state "
            "ON mcu_event_inbox(state, rowid)"
        )

    def _migrate_v3(self) -> None:
        """Add an Edge-owned receive generation for fixed MCU boot IDs."""
        conn = self._conn
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(mcu_event_inbox)"
            ).fetchall()
        }
        if "mcu_receive_generation" not in columns:
            conn.execute(
                "ALTER TABLE mcu_event_inbox RENAME TO mcu_event_inbox_v2"
            )
            conn.execute("DROP INDEX IF EXISTS idx_mcu_event_state")
            conn.execute("""CREATE TABLE mcu_event_inbox (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                mcu_receive_generation INTEGER NOT NULL,
                mcu_boot_id INTEGER NOT NULL,
                mcu_event_sequence INTEGER NOT NULL,
                message_name TEXT NOT NULL,
                message_type INTEGER NOT NULL,
                source_tx_sequence INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING',
                received_at TEXT NOT NULL DEFAULT (datetime('now')),
                processed_at TEXT,
                last_error TEXT,
                UNIQUE(
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence
                )
            )""")
            conn.execute(
                """INSERT INTO mcu_event_inbox
                   (rowid, mcu_receive_generation, mcu_boot_id,
                    mcu_event_sequence, message_name, message_type,
                    source_tx_sequence, content_sha256, payload_json,
                    state, received_at, processed_at, last_error)
                   SELECT rowid, 0, mcu_boot_id, mcu_event_sequence,
                          message_name, message_type, source_tx_sequence,
                          content_sha256, payload_json, state, received_at,
                          processed_at, last_error
                   FROM mcu_event_inbox_v2"""
            )
            conn.execute("DROP TABLE mcu_event_inbox_v2")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mcu_event_state "
            "ON mcu_event_inbox(state, rowid)"
        )
        conn.executemany(
            """INSERT OR IGNORE INTO device_state
               (state_key, state_value) VALUES (?, ?)""",
            [
                ("mcu_receive_generation", "0"),
                ("active_mcu_boot_id", ""),
            ],
        )

    def _migrate_v4(self) -> None:
        """Add persistent photo metadata without persisting temporary grants."""
        conn = self._conn
        conn.execute("""CREATE TABLE IF NOT EXISTS photo_outbox (
            photo_uid TEXT NOT NULL PRIMARY KEY,
            slot_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            cos_key TEXT,
            url TEXT,
            state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            uploaded_at TEXT,
            work_uid TEXT,
            work_type TEXT,
            device_name TEXT,
            content_sha256 TEXT,
            size_bytes INTEGER,
            captured_at TEXT,
            grant_request_event_uid TEXT,
            grant_generation INTEGER NOT NULL DEFAULT 0,
            status_event_uid TEXT,
            last_error TEXT,
            tombstoned INTEGER NOT NULL DEFAULT 0
        )""")
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(photo_outbox)"
            ).fetchall()
        }
        additions = {
            "url": "TEXT",
            "work_type": "TEXT",
            "device_name": "TEXT",
            "content_sha256": "TEXT",
            "size_bytes": "INTEGER",
            "captured_at": "TEXT",
            "grant_request_event_uid": "TEXT",
            "grant_generation": "INTEGER NOT NULL DEFAULT 0",
            "status_event_uid": "TEXT",
            "last_error": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE photo_outbox ADD COLUMN {name} {declaration}"
                )

        # Temporary COS credentials are execution-only data. Scrub any command
        # rows created by earlier versions that persisted the transport grant.
        command_table = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='command_inbox'"""
        ).fetchone()
        if command_table:
            for row in conn.execute(
                "SELECT rowid, payload_json FROM command_inbox"
            ).fetchall():
                payload = _json.loads(row["payload_json"])
                if (
                    isinstance(payload, dict)
                    and payload.get("cosGrant") is not None
                ):
                    payload["cosGrant"] = None
                    conn.execute(
                        """UPDATE command_inbox SET payload_json=?
                           WHERE rowid=?""",
                        (
                            _json.dumps(payload, ensure_ascii=False),
                            row["rowid"],
                        ),
                    )

        self._backfill_photo_metadata(conn)
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_photo_state
               ON photo_outbox(state, tombstoned)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_photo_work
               ON photo_outbox(work_uid)"""
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_photo_work_slot
               ON photo_outbox(work_type, work_uid, slot_name)
               WHERE tombstoned=0 AND work_type IS NOT NULL"""
        )

    def _migrate_v5(self) -> None:
        """Add fixed-frame compatibility results and reliable control identity."""
        conn = self._conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS confirmation_inbox (
                confirmation_uid TEXT NOT NULL PRIMARY KEY,
                event_uid TEXT NOT NULL,
                outcome TEXT NOT NULL,
                payload_json TEXT,
                received_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_conf_event
               ON confirmation_inbox(event_uid)"""
        )
        confirmation_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(confirmation_inbox)"
            ).fetchall()
        }
        for name, declaration in {
            "command_uid": "TEXT",
            "canonical_sha256": "TEXT",
            "receipt_event_uid": "TEXT",
        }.items():
            if name not in confirmation_columns:
                conn.execute(
                    "ALTER TABLE confirmation_inbox "
                    f"ADD COLUMN {name} {declaration}"
                )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_conf_receipt_event
               ON confirmation_inbox(receipt_event_uid)
               WHERE receipt_event_uid IS NOT NULL"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS command_observation (
                command_uid TEXT NOT NULL,
                stage TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                canonical_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (command_uid, stage)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS fixed_frame_local_result (
                result_type TEXT NOT NULL,
                result_key TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (result_type, result_key)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS bag_baseline (
                bag_uid TEXT NOT NULL PRIMARY KEY,
                weight_grams INTEGER NOT NULL,
                source_kind TEXT NOT NULL,
                source_work_type TEXT,
                source_work_uid TEXT,
                source_mcu_boot_id INTEGER,
                source_mcu_event_sequence INTEGER,
                source_observed_at TEXT,
                measurement_uid TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS edge_fault_state (
                fault_uid TEXT NOT NULL PRIMARY KEY,
                scope_key TEXT NOT NULL,
                port_no INTEGER,
                component TEXT NOT NULL,
                fault_code TEXT NOT NULL,
                severity TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                discovery_count INTEGER NOT NULL DEFAULT 1,
                first_detected_at TEXT NOT NULL,
                last_detected_at TEXT NOT NULL,
                recovered_at TEXT,
                detail_json TEXT,
                recovery_evidence TEXT
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_edge_fault_active
               ON edge_fault_state(
                   lifecycle, scope_key, component, fault_code
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mcu_derived_event (
                mcu_receive_generation INTEGER NOT NULL,
                mcu_boot_id INTEGER NOT NULL,
                mcu_event_sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                PRIMARY KEY (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    event_type
                )
            )"""
        )

    def _migrate_v6(self) -> None:
        """Keep URLs as evidence of successful COS upload only."""
        self._conn.execute(
            """UPDATE photo_outbox
               SET url=NULL
               WHERE state<>'UPLOADED' AND url IS NOT NULL"""
        )

    def _migrate_v7(self) -> None:
        """Persist sanitized OneNet event-post reply evidence."""
        columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(event_outbox)"
            ).fetchall()
        }
        if not columns:
            return
        for name, declaration in {
            "last_platform_code": "INTEGER",
            "platform_accepted_at": "TEXT",
            "last_platform_reply_at": "TEXT",
        }.items():
            if name not in columns:
                self._conn.execute(
                    "ALTER TABLE event_outbox "
                    f"ADD COLUMN {name} {declaration}"
                )

    def _migrate_v8(self) -> None:
        """Persist the current-bag fullness state owned by the edge."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS port_fullness_state (
                port_no INTEGER NOT NULL PRIMARY KEY,
                bag_uid TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('FULL', 'NOT_FULL')),
                last_state_change_uid TEXT,
                last_event_uid TEXT,
                updated_at TEXT NOT NULL
            )"""
        )

    def _backfill_photo_metadata(self, conn) -> None:
        delivery_slots = {
            "OPEN_INNER": "BEFORE_INNER",
            "OPEN_OUTSIDE": "BEFORE_OUTER",
            "CLOSE_INNER": "AFTER_INNER",
            "CLOSE_OUTSIDE": "AFTER_OUTER",
        }
        clean_slots = {
            "OPEN_INNER": "FIRST_OPEN_INNER",
            "OPEN_OUTSIDE": "FIRST_OPEN_OUTER",
            "CLOSE_INNER": "FINAL_CLOSE_INNER",
            "CLOSE_OUTSIDE": "FINAL_CLOSE_OUTER",
        }
        rows = conn.execute(
            """SELECT * FROM photo_outbox
               WHERE work_type IS NULL OR content_sha256 IS NULL
                  OR size_bytes IS NULL OR captured_at IS NULL"""
        ).fetchall()
        for row in rows:
            work_type = row["work_type"]
            device_name = row["device_name"]
            slot_name = row["slot_name"]
            event = conn.execute(
                """SELECT event_type, payload_json FROM event_outbox
                   WHERE work_uid=? AND event_type IN (
                       'DELIVERY_COMPLETE', 'CLEAN_COMPLETE'
                   )
                   ORDER BY edge_event_sequence LIMIT 1""",
                (row["work_uid"],),
            ).fetchone()
            if event:
                envelope = _json.loads(event["payload_json"])
                device_name = (
                    device_name or envelope.get("targetDeviceName")
                )
                if event["event_type"] == "DELIVERY_COMPLETE":
                    work_type = "DELIVERY_SESSION"
                    slot_name = delivery_slots.get(slot_name, slot_name)
                else:
                    work_type = "CLEAN_OPERATION"
                    slot_name = clean_slots.get(slot_name, slot_name)

            local_path = row["local_path"]
            content_sha256 = row["content_sha256"]
            size_bytes = row["size_bytes"]
            captured_at = row["captured_at"]
            if os.path.isfile(local_path):
                if content_sha256 is None:
                    digest = hashlib.sha256()
                    with open(local_path, "rb") as source:
                        for chunk in iter(lambda: source.read(64 * 1024), b""):
                            digest.update(chunk)
                    content_sha256 = digest.hexdigest()
                stat = os.stat(local_path)
                size_bytes = size_bytes or stat.st_size
                captured_at = captured_at or time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z",
                    time.gmtime(stat.st_mtime),
                )
            conn.execute(
                """UPDATE photo_outbox
                   SET slot_name=?, work_type=?, device_name=?,
                       content_sha256=?, size_bytes=?, captured_at=?
                   WHERE photo_uid=?""",
                (
                    slot_name,
                    work_type,
                    device_name,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    row["photo_uid"],
                ),
            )

    # ── 事务辅助 ──

    @contextmanager
    def transaction(self, *, immediate: bool = False):
        with self._lock:
            try:
                if immediate:
                    self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    @staticmethod
    def _command_received_at_now() -> tuple[str, datetime, str]:
        sampled = sample_clock()
        encoded = sampled.raw_observed_at
        if encoded is None:
            received_at = datetime.now(timezone.utc)
            encoded = received_at.isoformat(timespec="milliseconds").replace(
                "+00:00",
                "Z",
            )
        else:
            received_at = datetime.fromisoformat(
                encoded.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        return encoded, received_at, sampled.quality

    @staticmethod
    def _parse_command_received_at(value: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError("command received_at is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("command received_at is invalid") from error
        # Rows created before the factory-seal path stored SQLite's UTC
        # CURRENT_TIMESTAMP form without an explicit offset.  That field is
        # database-owned, so interpreting that legacy shape as UTC is safe.
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _new_uid(self) -> str:
        return str(_uuid.uuid4())

    def _next_seq(self, conn) -> int:
        row = conn.execute(
            "SELECT state_value FROM device_state WHERE state_key='edge_event_sequence'"
        ).fetchone()
        seq = int(row["state_value"] or 0) + 1
        conn.execute(
            "UPDATE device_state SET state_value=?, updated_at=? WHERE state_key='edge_event_sequence'",
            (str(seq), self._now()),
        )
        return seq

    # ── 原子事务 1: 接收命令 ──

    def receive_command(self, command_uid: str, command_type: str, payload: dict) -> str:
        stable = dict(payload)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(payload)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        received_at, _received_datetime, clock_quality = (
            self._command_received_at_now()
        )
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT canonical_sha256 FROM command_inbox WHERE command_uid=?",
                (command_uid,),
            ).fetchone()
            if existing:
                if existing["canonical_sha256"] != canonical_sha256:
                    logger.error("命令幂等冲突: %s", command_uid)
                    return "CONFLICT"
                logger.info("命令去重: %s", command_uid)
                return "DUPLICATE"
            conn.execute(
                """INSERT INTO command_inbox
                   (command_uid, command_type, payload_json,
                    canonical_sha256, received_at,
                    received_clock_quality)
                   VALUES (?,?,?,?,?,?)""",
                (
                    command_uid,
                    command_type,
                    _json.dumps(stored, ensure_ascii=False),
                    canonical_sha256,
                    received_at,
                    clock_quality,
                ),
            )
            return "ACCEPTED"

    def receive_factory_seal_command(self, command: dict) -> str:
        """Validate and durably accept a seal command at one trusted instant.

        A first delivery must still be valid when this transaction accepts
        it.  An exact duplicate may be validated against the original
        database-owned receipt time, allowing queue/restart recovery after
        ``expiresAt`` without accepting a newly expired command.
        """

        command_uid = command.get("commandUid")
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(command)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        with self.transaction(immediate=True):
            existing = self._conn.execute(
                """SELECT command_type, canonical_sha256, received_at,
                          received_clock_quality
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["command_type"] != "AUTHORIZE_FACTORY_SEAL"
                    or existing["canonical_sha256"] != canonical_sha256
                ):
                    logger.error("命令幂等冲突: %s", command_uid)
                    return "CONFLICT"
                acceptance_time = self._parse_command_received_at(
                    existing["received_at"]
                )
                validate_factory_seal_envelope_at_acceptance(
                    command,
                    acceptance_time,
                    acceptance_clock_quality=existing[
                        "received_clock_quality"
                    ],
                )
                logger.info("命令去重: %s", command_uid)
                return "DUPLICATE"

            received_at, acceptance_time, clock_quality = (
                self._command_received_at_now()
            )
            validate_factory_seal_envelope_at_acceptance(
                command,
                acceptance_time,
                acceptance_clock_quality=clock_quality,
            )
            self._conn.execute(
                """INSERT INTO command_inbox
                   (command_uid, command_type, payload_json,
                    canonical_sha256, received_at,
                    received_clock_quality)
                   VALUES (?, 'AUTHORIZE_FACTORY_SEAL', ?, ?, ?, ?)""",
                (
                    command_uid,
                    _json.dumps(stored, ensure_ascii=False),
                    canonical_sha256,
                    received_at,
                    clock_quality,
                ),
            )
            return "ACCEPTED"

    def validate_claimed_factory_seal_command(self, command: dict) -> None:
        """Revalidate a claimed seal using only its durable receipt fact."""

        command_uid = command.get("commandUid")
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        with self._lock:
            row = self._conn.execute(
                """SELECT command_type, canonical_sha256, state, received_at,
                          received_clock_quality
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
        if row is None or row["command_type"] != "AUTHORIZE_FACTORY_SEAL":
            raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")
        if row["canonical_sha256"] != canonical_sha256:
            raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")
        if row["state"] != "PROCESSING":
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        try:
            acceptance_time = self._parse_command_received_at(
                row["received_at"]
            )
            validate_factory_seal_envelope_at_acceptance(
                command,
                acceptance_time,
                acceptance_clock_quality=row[
                    "received_clock_quality"
                ],
            )
        except (TypeError, ValueError) as error:
            raise FactorySealError(
                "FACTORY_SEAL_ACCEPTANCE_FACT_INVALID"
            ) from error

    def receive_rejected_command(
        self,
        command: dict,
        error_code: str,
    ) -> str:
        """Persist a valid, capability-rejected command without queueing it."""
        command_uid = command["commandUid"]
        command_type = command["commandType"]
        stable = dict(command)
        stable.pop("cosGrant", None)
        canonical_sha256 = canonical_payload_sha256(stable)
        stored = dict(command)
        if "cosGrant" in stored:
            stored["cosGrant"] = None
        received_at, _received_datetime, clock_quality = (
            self._command_received_at_now()
        )
        with self.transaction():
            existing = self._conn.execute(
                """SELECT command_type, canonical_sha256, state, last_error
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["command_type"] != command_type
                    or existing["canonical_sha256"] != canonical_sha256
                ):
                    return "CONFLICT"
                if existing["state"] == "PENDING":
                    changed = self._conn.execute(
                        """UPDATE command_inbox
                           SET state='REJECTED', processed_at=?,
                               processing_started_at=NULL, last_error=?
                           WHERE command_uid=? AND state='PENDING'""",
                        (self._now(), error_code, command_uid),
                    )
                    if changed.rowcount != 1:
                        return "CONFLICT"
                elif (
                    existing["state"] != "REJECTED"
                    or existing["last_error"] != error_code
                ):
                    return "CONFLICT"
            else:
                self._conn.execute(
                    """INSERT INTO command_inbox
                       (command_uid, command_type, payload_json,
                        canonical_sha256, state, received_at,
                        received_clock_quality, processed_at, last_error)
                       VALUES (?, ?, ?, ?, 'REJECTED', ?, ?, ?, ?)""",
                    (
                        command_uid,
                        command_type,
                        _json.dumps(stored, ensure_ascii=False),
                        canonical_sha256,
                        received_at,
                        clock_quality,
                        self._now(),
                        error_code,
                    ),
                )
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                return "CONFLICT"
            return "REJECTED"

    def accept_factory_seal_authorization(
        self,
        command: dict,
        local_facts: dict[str, str],
    ) -> dict[str, Any]:
        """Atomically authorize, complete, and emit the reliable ACCEPTED fact."""

        command_uid = command["commandUid"]
        payload = command["payload"]
        generation = payload["acceptanceGeneration"]
        command_sha256 = canonical_payload_sha256(
            {key: value for key, value in command.items() if key != "cosGrant"}
        )
        # BEGIN IMMEDIATE freezes the latest local acceptance evidence before
        # the first read and keeps another SQLite writer from inserting a
        # newer challenge between that check and authorization commit.
        with self.transaction(immediate=True):
            inbox = self._conn.execute(
                """SELECT command_type, canonical_sha256, state
                   FROM command_inbox WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if (
                inbox is None
                or inbox["command_type"] != "AUTHORIZE_FACTORY_SEAL"
                or inbox["canonical_sha256"] != command_sha256
            ):
                raise FactorySealError("FACTORY_SEAL_COMMAND_CONFLICT")

            existing = self._conn.execute(
                """SELECT authorization_binding_sha256, state
                   FROM factory_seal_authorization WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if existing is not None:
                self._complete_factory_seal_command_in_tx(command)
                return {
                    "disposition": "DUPLICATE_AUTHORIZED",
                    "acceptanceGeneration": generation,
                    "authorizationBindingSha256": existing[
                        "authorization_binding_sha256"
                    ],
                    "state": existing["state"],
                }
            if inbox["state"] != "PROCESSING":
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")

            evidence_row = self._conn.execute(
                """SELECT event_uid, payload_json
                   FROM event_outbox
                   WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                     AND work_uid=?
                   ORDER BY edge_event_sequence DESC
                   LIMIT 1""",
                (payload["hardwareSn"],),
            ).fetchone()
            if evidence_row is None:
                raise FactorySealError("ACCEPTANCE_EVIDENCE_NOT_FOUND")
            if evidence_row["event_uid"] != payload["acceptanceEvidenceUid"]:
                raise FactorySealError("ACCEPTANCE_EVIDENCE_NOT_LATEST")
            try:
                evidence_event = _json.loads(evidence_row["payload_json"])
                evidence = evidence_event["payload"]
            except (KeyError, TypeError, ValueError):
                raise FactorySealError("ACCEPTANCE_EVIDENCE_INVALID") from None
            if (
                evidence_event.get("eventUid")
                != payload["acceptanceEvidenceUid"]
                or evidence_event.get("eventType")
                != "DEVICE_ACCEPTANCE_EVIDENCE"
                or evidence_event.get("payloadSha256")
                != payload["acceptanceEvidenceSha256"]
                or canonical_payload_sha256(evidence)
                != payload["acceptanceEvidenceSha256"]
                or evidence_event.get("target")
                != {"type": "DEVICE_ASSET", "uid": payload["hardwareSn"]}
                or evidence.get("evidenceSchemaVersion")
                not in FACTORY_SEAL_ACCEPTANCE_EVIDENCE_SCHEMA_VERSIONS
                or evidence.get("challengeUid")
                != payload["acceptanceChallengeUid"]
                or evidence.get("factoryBagRevision")
                != payload["factoryBagRevision"]
                or evidence.get("factoryBagSetSha256")
                != payload["factoryBagSetSha256"]
            ):
                raise FactorySealError("ACCEPTANCE_EVIDENCE_MISMATCH")

            highest = self._conn.execute(
                """SELECT command_uid, acceptance_generation
                   FROM factory_seal_authorization
                   ORDER BY acceptance_generation DESC LIMIT 1"""
            ).fetchone()
            if highest is not None and generation < highest["acceptance_generation"]:
                raise FactorySealError("FACTORY_SEAL_GENERATION_STALE")
            if highest is not None and generation == highest["acceptance_generation"]:
                raise FactorySealError("FACTORY_SEAL_GENERATION_CONFLICT")

            binding_values = {
                "commandUid": command_uid,
                "hardwareSn": payload["hardwareSn"],
                "acceptanceGeneration": generation,
                "acceptanceEvidenceUid": payload["acceptanceEvidenceUid"],
                "acceptanceChallengeUid": payload["acceptanceChallengeUid"],
                "acceptanceEvidenceSha256": payload[
                    "acceptanceEvidenceSha256"
                ],
                "factoryBagRevision": payload["factoryBagRevision"],
                "factoryBagSetSha256": payload["factoryBagSetSha256"],
                **local_facts,
            }
            binding = authorization_binding_sha256(binding_values)
            now = self._now()
            self._conn.execute(
                """UPDATE factory_seal_authorization
                   SET state='SUPERSEDED', last_error='NEWER_GENERATION'
                   WHERE state='AUTHORIZED'"""
            )
            self._conn.execute(
                """INSERT INTO factory_seal_authorization (
                       command_uid, command_sha256, hardware_sn,
                       acceptance_generation, evidence_event_uid,
                       acceptance_challenge_uid,
                       acceptance_evidence_sha256,
                       factory_bag_revision, factory_bag_set_sha256,
                       image_release_id, image_release_sha256,
                       factory_report_sha256,
                       authorization_binding_sha256, state, authorized_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                             'AUTHORIZED', ?)""",
                (
                    command_uid,
                    command_sha256,
                    payload["hardwareSn"],
                    generation,
                    payload["acceptanceEvidenceUid"],
                    payload["acceptanceChallengeUid"],
                    payload["acceptanceEvidenceSha256"],
                    payload["factoryBagRevision"],
                    payload["factoryBagSetSha256"],
                    local_facts["imageReleaseId"],
                    local_facts["imageReleaseSha256"],
                    local_facts["factoryReportSha256"],
                    binding,
                    now,
                ),
            )
            self._complete_factory_seal_command_in_tx(command)
            return {
                "disposition": "AUTHORIZED",
                "acceptanceGeneration": generation,
                "authorizationBindingSha256": binding,
                "state": "AUTHORIZED",
            }

    def reject_factory_seal_command(
        self,
        command: dict,
        error_code: str,
    ) -> bool:
        """Atomically reject the command and retain its reliable observation."""

        if error_code not in FACTORY_SEAL_TERMINAL_ERROR_CODES:
            raise ValueError(
                "factory seal terminal error is not allow-listed"
            )
        with self.transaction():
            row = self._conn.execute(
                """SELECT command_type, state FROM command_inbox
                   WHERE command_uid=?""",
                (command["commandUid"],),
            ).fetchone()
            if row is None or row["command_type"] != "AUTHORIZE_FACTORY_SEAL":
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            if row["state"] not in {"PROCESSING", "REJECTED"}:
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            if row["state"] == "PROCESSING":
                updated = self._conn.execute(
                    """UPDATE command_inbox
                       SET state='REJECTED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (self._now(), error_code, command["commandUid"]),
                )
                if updated.rowcount != 1:
                    raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise FactorySealError("FACTORY_SEAL_OBSERVATION_CONFLICT")
            return row["state"] == "PROCESSING"

    def _complete_factory_seal_command_in_tx(self, command: dict) -> None:
        row = self._conn.execute(
            "SELECT state FROM command_inbox WHERE command_uid=?",
            (command["commandUid"],),
        ).fetchone()
        if row is None:
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        if row["state"] == "PROCESSING":
            result = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    _json.dumps(
                        {
                            "acceptanceGeneration": command["payload"][
                                "acceptanceGeneration"
                            ],
                            "disposition": "FACTORY_SEAL_AUTHORIZED",
                        },
                        ensure_ascii=False,
                    ),
                    command["commandUid"],
                ),
            )
            if result.rowcount != 1:
                raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        elif row["state"] != "COMPLETED":
            raise FactorySealError("FACTORY_SEAL_COMMAND_STATE_INVALID")
        observation = self._record_command_observation_in_tx(
            self._conn,
            command,
            "ACCEPTED",
        )
        if observation == "CONFLICT":
            raise FactorySealError("FACTORY_SEAL_OBSERVATION_CONFLICT")

    def get_command(self, command_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM command_inbox WHERE command_uid=?", (command_uid,)
            ).fetchone()
        return self._decode_command_row(row)

    def claim_next_command(self) -> Optional[dict]:
        with self.transaction():
            maintenance = self._conn.execute(
                """SELECT lock_type, owner_uid
                   FROM maintenance_lock WHERE singleton_id=1"""
            ).fetchone()
            if maintenance:
                # A firmware package fetch deliberately keeps maintenance
                # locked. Only the command owning that exact journal may be
                # reclaimed with fresh execution-only COS credentials; every
                # unrelated physical command remains blocked.
                if maintenance["lock_type"] != "MCU_FIRMWARE_UPDATE":
                    return None
                row = self._conn.execute(
                    """SELECT command_row.*
                       FROM command_inbox command_row
                       JOIN mcu_firmware_update firmware_update
                         ON firmware_update.command_uid=command_row.command_uid
                       WHERE firmware_update.update_uid=?
                         AND firmware_update.package_ready=0
                         AND firmware_update.state IN (
                           'QUEUED', 'PACKAGE_FETCH_FAILED'
                         )
                         AND command_row.command_type=
                           'START_MCU_FIRMWARE_UPDATE'
                         AND command_row.state='PENDING'
                       LIMIT 1""",
                    (maintenance["owner_uid"],),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """SELECT * FROM command_inbox
                       WHERE state='PENDING' ORDER BY rowid LIMIT 1"""
                ).fetchone()
            if not row:
                return None
            now = self._now()
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PROCESSING', processing_started_at=?,
                       attempt_count=attempt_count+1, last_error=NULL
                   WHERE rowid=? AND state='PENDING'""",
                (now, row["rowid"]),
            )
            if updated.rowcount != 1:
                return None
            claimed = self._conn.execute(
                "SELECT * FROM command_inbox WHERE rowid=?", (row["rowid"],)
            ).fetchone()
            return self._decode_command_row(claimed)

    def mark_command_waiting_mcu(
        self,
        command_uid: str,
        mcu_command_uid: Optional[str],
        result: Optional[dict] = None,
    ) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='WAITING_MCU_RESULT', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    mcu_command_uid,
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def mark_command_recovery_required(
        self,
        command_uid: str,
        error_code: str,
        mcu_command_uid: Optional[str] = None,
        result: Optional[dict] = None,
    ) -> bool:
        """Keep an indeterminate physical command locked for reconciliation."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', mcu_command_uid=?,
                       result_json=?, processing_started_at=NULL,
                       processed_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    mcu_command_uid,
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    error_code,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def complete_command(self, command_uid: str, result: Optional[dict] = None) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?, processing_started_at=NULL,
                       result_json=?, last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(result, ensure_ascii=False) if result else None,
                    command_uid,
                ),
            )
            return cur.rowcount == 1

    def complete_device_acceptance(
        self,
        command: dict,
        evidence_payload: dict,
    ) -> dict:
        """Atomically retain acceptance evidence and complete its command.

        The camera upload/readback happens before this transaction because it
        talks to COS.  Once those external checks finish, the reliable event
        and command terminal state must commit together so a process crash can
        never leave a completed command without its evidence event.
        """
        command_uid = command["commandUid"]
        with self.transaction():
            row = self._conn.execute(
                """SELECT state, command_type FROM command_inbox
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is None:
                raise ValueError("acceptance command is not persisted")
            if row["command_type"] != "REQUEST_DEVICE_ACCEPTANCE":
                raise ValueError("command is not a device acceptance request")
            if row["state"] == "COMPLETED":
                existing = self._conn.execute(
                    """SELECT payload_json FROM event_outbox
                       WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'
                         AND json_extract(payload_json, '$.commandUid')=?
                       ORDER BY edge_event_sequence DESC LIMIT 1""",
                    (command_uid,),
                ).fetchone()
                if existing is None:
                    raise ValueError(
                        "completed acceptance command has no evidence"
                    )
                return _json.loads(existing["payload_json"])
            if row["state"] != "PROCESSING":
                raise ValueError("acceptance command is not processing")

            event_uid = self._new_uid()
            sequence = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=command["targetDeviceName"],
                edge_event_sequence=sequence,
                event_type="DEVICE_ACCEPTANCE_EVIDENCE",
                target_type="DEVICE_ASSET",
                target_uid=command["targetDeviceName"],
                command_uid=command_uid,
                payload=evidence_payload,
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_ACCEPTANCE_EVIDENCE",
            )
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    _json.dumps(
                        {
                            "challengeUid": evidence_payload[
                                "challengeUid"
                            ],
                            "evidenceEventUid": event_uid,
                            "disposition": "EVIDENCE_RECORDED",
                        },
                        ensure_ascii=False,
                    ),
                    command_uid,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("acceptance command state changed")
            return event

    def fail_command(
        self,
        command_uid: str,
        error_code: str,
        *,
        retryable: bool = False,
    ) -> bool:
        state = "PENDING" if retryable else "FAILED"
        processed_at = None if retryable else self._now()
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state=?, processed_at=?, processing_started_at=NULL,
                       last_error=?
                   WHERE command_uid=?""",
                (state, processed_at, error_code, command_uid),
            )
            return cur.rowcount == 1

    def fail_command_and_observe(
        self,
        command: dict,
        error_code: str,
        *,
        stage: str,
        mcu_command_uid: Optional[str] = None,
    ) -> bool:
        """Atomically fail a command and create its stable observation."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    stage,
                    mcu_command_uid=mcu_command_uid,
                    error_code=error_code,
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            return cur.rowcount == 1

    def reject_claimed_command_and_observe(
        self,
        command: dict,
        error_code: str,
    ) -> bool:
        """Atomically reject a claimed command before any physical action."""

        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='REJECTED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=? AND state='PROCESSING'""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if cur.rowcount != 1:
                return False
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "REJECTED",
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise ValueError("command observation conflict")
            return True

    def fail_factory_seal_command_for_retry(
        self,
        command_uid: str,
        error_code: str,
    ) -> bool:
        """Park a locally repairable seal command until cloud redelivery.

        No terminal command observation is created here.  The backend keeps
        the immutable reliable task pending and redelivers the same command;
        only that duplicate delivery may move this allow-listed failure back
        to PENDING.  Restricting both command type and error code prevents this
        recovery seam from replaying physical or deterministically rejected
        commands.
        """

        if error_code not in FACTORY_SEAL_RETRYABLE_ERROR_CODES:
            raise ValueError(
                "factory seal retry error is not allow-listed"
            )
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?
                     AND command_type='AUTHORIZE_FACTORY_SEAL'
                     AND state='PROCESSING'""",
                (self._now(), error_code, command_uid),
            )
            return cur.rowcount == 1

    def requeue_failed_command(
        self,
        command_uid: str,
        expected_error: str,
    ) -> bool:
        """Requeue a stable command when fresh execution-only data arrives."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='FAILED'
                     AND last_error=?""",
                (command_uid, expected_error),
            )
            return cur.rowcount == 1

    def requeue_failed_factory_seal_command(
        self,
        command_uid: str,
    ) -> bool:
        """Requeue only a repairable seal failure on duplicate delivery."""

        placeholders = ",".join(
            "?" for _ in FACTORY_SEAL_RETRYABLE_ERROR_CODES
        )
        retryable_errors = tuple(
            sorted(FACTORY_SEAL_RETRYABLE_ERROR_CODES)
        )
        with self.transaction():
            cur = self._conn.execute(
                f"""UPDATE command_inbox
                    SET state='PENDING', processed_at=NULL,
                        processing_started_at=NULL, last_error=NULL
                    WHERE command_uid=? AND state='FAILED'
                      AND command_type='AUTHORIZE_FACTORY_SEAL'
                      AND last_error IN ({placeholders})""",
                (command_uid, *retryable_errors),
            )
            return cur.rowcount == 1

    def requeue_completed_photo_grant_command(
        self,
        command_uid: str,
    ) -> bool:
        """Allow fresh execution-only STS credentials for one stable request."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='COMPLETED'
                     AND command_type='PROVIDE_PHOTO_UPLOAD_GRANT'""",
                (command_uid,),
            )
            return cur.rowcount == 1

    def requeue_failed_mcu_firmware_command(
        self,
        command_uid: str,
    ) -> bool:
        """Retry package acquisition only when a fresh COS grant can help."""
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL, last_error=NULL
                   WHERE command_uid=? AND state='FAILED'
                     AND command_type='START_MCU_FIRMWARE_UPDATE'
                     AND (
                       last_error='FIRMWARE_GRANT_NOT_AVAILABLE'
                       OR EXISTS (
                         SELECT 1 FROM mcu_firmware_update firmware_update
                         WHERE firmware_update.command_uid=command_inbox.command_uid
                           AND firmware_update.package_ready=0
                           AND firmware_update.state IN (
                             'QUEUED', 'PACKAGE_FETCH_FAILED'
                           )
                       )
                     )""",
                (command_uid,),
            )
            return cur.rowcount == 1

    def recover_interrupted_commands(
        self,
        *,
        physical_recovery_required: bool = True,
    ) -> dict[str, int]:
        """Requeue idempotent controls; never resume physical commands."""
        with self.transaction():
            config = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='APPLY_CONFIGURATION'"""
            ).rowcount
            remote_support = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type IN (
                       'OPEN_REMOTE_SUPPORT_TUNNEL',
                       'CLOSE_REMOTE_SUPPORT_TUNNEL'
                     )"""
            ).rowcount
            acceptance = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL,
                       last_error='ACCEPTANCE_GRANT_NOT_AVAILABLE'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='REQUEST_DEVICE_ACCEPTANCE'""",
                (self._now(),),
            ).rowcount
            firmware = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL,
                       last_error='FIRMWARE_GRANT_NOT_AVAILABLE'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='START_MCU_FIRMWARE_UPDATE'""",
                (self._now(),),
            ).rowcount
            factory_seal = self._conn.execute(
                """UPDATE command_inbox
                   SET state='PENDING', processed_at=NULL,
                       processing_started_at=NULL,
                       last_error='PROCESS_RESTARTED'
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type='AUTHORIZE_FACTORY_SEAL'"""
            ).rowcount
            rows = self._conn.execute(
                """SELECT command_uid, payload_json
                   FROM command_inbox
                   WHERE state IN (
                       'PROCESSING', 'WAITING_MCU_RESULT',
                       'RECOVERY_REQUIRED'
                   )
                     AND command_type NOT IN (
                         'APPLY_CONFIGURATION',
                         'REQUEST_DEVICE_ACCEPTANCE',
                          'START_MCU_FIRMWARE_UPDATE',
                          'OPEN_REMOTE_SUPPORT_TUNNEL',
                          'CLOSE_REMOTE_SUPPORT_TUNNEL',
                          'AUTHORIZE_FACTORY_SEAL'
                      )"""
            ).fetchall()
            physical_failed = 0
            for row in rows:
                command = _json.loads(row["payload_json"])
                if command.get("targetDeviceName"):
                    observation = self._record_command_observation_in_tx(
                        self._conn,
                        command,
                        "FAILED",
                        error_code="EDGE_RESTARTED",
                    )
                    if observation == "CONFLICT":
                        raise ValueError("command observation conflict")
                physical_failed += self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL,
                           last_error='EDGE_RESTARTED'
                       WHERE command_uid=?
                         AND state IN (
                             'PROCESSING', 'WAITING_MCU_RESULT',
                             'RECOVERY_REQUIRED'
                         )""",
                    (self._now(), row["command_uid"]),
                ).rowcount
            return {
                "configuration_requeued": config,
                "remote_support_requeued": remote_support,
                "acceptance_grant_lost": acceptance,
                "firmware_grant_lost": firmware,
                "factory_seal_requeued": factory_seal,
                "physical_locked": 0,
                "physical_failed": physical_failed,
            }

    @staticmethod
    def _decode_command_row(row) -> Optional[dict]:
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        if result.get("result_json"):
            result["result"] = _json.loads(result["result_json"])
        else:
            result["result"] = None
        return result

    # ── 配置应用与进度 ──

    def save_configuration_edge(
        self,
        command: dict,
        part_command_uids: list[str],
    ) -> str:
        payload = command["payload"]
        config = payload["config"]
        application_uid = payload["applicationUid"]
        version = config["version"]
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if existing:
                if (
                    existing["config_version"] == version
                    and existing["content_sha256"] == config["contentSha256"]
                    and existing["mcu_payload_sha256"] == config["mcuPayloadSha256"]
                ):
                    return "DUPLICATE"
                return "CONFLICT"
            highest = self._conn.execute(
                """SELECT config_version, content_sha256, mcu_payload_sha256
                   FROM configuration_state
                   ORDER BY config_version DESC LIMIT 1"""
            ).fetchone()
            if highest is not None:
                if version < highest["config_version"]:
                    return "OUTDATED"
                if version == highest["config_version"]:
                    return "CONFLICT"

            now = self._now()
            self._conn.execute(
                """INSERT INTO configuration_state
                   (application_uid, command_uid, device_name, config_version,
                    content_sha256, mcu_payload_sha256, payload_json,
                    part_command_uids_json, state, edge_saved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    application_uid,
                    command["commandUid"],
                    command["targetDeviceName"],
                    version,
                    config["contentSha256"],
                    config["mcuPayloadSha256"],
                    _json.dumps(payload, ensure_ascii=False),
                    _json.dumps(part_command_uids),
                    "EDGE_SAVED",
                    now,
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=command["targetDeviceName"],
                command_uid=command["commandUid"],
                application_uid=application_uid,
                stage="EDGE_SAVED",
                version=version,
                content_sha256=config["contentSha256"],
                mcu_payload_sha256=config["mcuPayloadSha256"],
                edge_event_sequence=seq,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            self._upsert_state(self._conn, "saved_config_version", str(version), now)
            self._upsert_state(
                self._conn, "saved_config_content_sha256", config["contentSha256"], now
            )
            return "ACCEPTED"

    def get_configuration(self, application_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        result["part_command_uids"] = _json.loads(
            result.pop("part_command_uids_json")
        )
        return result

    def get_latest_applied_configuration(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM configuration_state
                   WHERE state='APPLIED'
                   ORDER BY config_version DESC LIMIT 1"""
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json.loads(result.pop("payload_json"))
        result["part_command_uids"] = _json.loads(
            result.pop("part_command_uids_json")
        )
        return result

    def apply_configuration_result(self, payload: dict) -> str:
        application_uid = payload.get("applicationUid")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row:
                return "UNKNOWN"
            if (
                row["config_version"] != payload.get("configVersion")
                or row["content_sha256"] != payload.get("contentSha256")
                or row["mcu_payload_sha256"] != payload.get("mcuPayloadSha256")
            ):
                return "CONFLICT"
            part_command_uids = _json.loads(row["part_command_uids_json"])
            if payload.get("mcuCommandUid") != part_command_uids[-1]:
                return "CONFLICT"
            desired_state = "APPLIED" if payload.get("status") == "APPLIED" else "FAILED"
            if row["state"] == desired_state:
                return "DUPLICATE"
            if row["state"] not in (
                "EDGE_SAVED",
                "WAITING_MCU_RESULT",
                "RECOVERY_REQUIRED",
            ):
                return "CONFLICT"

            error_code = None
            if desired_state == "FAILED":
                error_code = payload.get("faultCode") or "MCU_INTERNAL"
                if error_code == "NONE":
                    error_code = "MCU_INTERNAL"
            now = self._now()
            self._conn.execute(
                """UPDATE configuration_state
                   SET state=?, commit_mcu_command_uid=?, error_code=?, applied_at=?
                   WHERE application_uid=?""",
                (
                    desired_state,
                    payload.get("mcuCommandUid"),
                    error_code,
                    now,
                    application_uid,
                ),
            )
            command_state = "COMPLETED" if desired_state == "APPLIED" else "FAILED"
            self._conn.execute(
                """UPDATE command_inbox
                   SET state=?, processed_at=?, processing_started_at=NULL,
                       mcu_command_uid=?, last_error=?
                   WHERE command_uid=?""",
                (
                    command_state,
                    now,
                    payload.get("mcuCommandUid"),
                    error_code,
                    row["command_uid"],
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=row["device_name"],
                command_uid=row["command_uid"],
                application_uid=application_uid,
                stage=desired_state,
                version=row["config_version"],
                content_sha256=row["content_sha256"],
                mcu_payload_sha256=row["mcu_payload_sha256"],
                edge_event_sequence=seq,
                mcu_command_uid=payload.get("mcuCommandUid"),
                error_code=error_code,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            if desired_state == "APPLIED":
                self._upsert_state(
                    self._conn, "applied_config_version", str(row["config_version"]), now
                )
                self._upsert_state(
                    self._conn,
                    "applied_config_content_sha256",
                    row["content_sha256"],
                    now,
                )
                self._upsert_state(
                    self._conn,
                    "applied_config_mcu_payload_sha256",
                    row["mcu_payload_sha256"],
                    now,
                )
            return "ACCEPTED"

    def mark_configuration_waiting_mcu(
        self,
        application_uid: str,
        commit_mcu_command_uid: str,
    ) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE configuration_state
                   SET state='WAITING_MCU_RESULT', commit_mcu_command_uid=?
                   WHERE application_uid=? AND state='EDGE_SAVED'""",
                (commit_mcu_command_uid, application_uid),
            )
            return cur.rowcount == 1

    def mark_configuration_recovery_required(
        self,
        application_uid: str,
        error_code: str,
    ) -> bool:
        with self.transaction():
            row = self._conn.execute(
                "SELECT command_uid FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                """UPDATE configuration_state
                   SET state='RECOVERY_REQUIRED', error_code=?
                   WHERE application_uid=?""",
                (error_code, application_uid),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='RECOVERY_REQUIRED', processing_started_at=NULL,
                       last_error=?
                   WHERE command_uid=?""",
                (error_code, row["command_uid"]),
            )
            return True

    def fail_configuration_edge(
        self,
        application_uid: str,
        error_code: str,
        mcu_command_uid: Optional[str] = None,
    ) -> bool:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM configuration_state WHERE application_uid=?",
                (application_uid,),
            ).fetchone()
            if not row or row["state"] == "FAILED":
                return False
            now = self._now()
            self._conn.execute(
                """UPDATE configuration_state
                   SET state='FAILED', error_code=?, commit_mcu_command_uid=?,
                       applied_at=?
                   WHERE application_uid=?""",
                (error_code, mcu_command_uid, now, application_uid),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?, processing_started_at=NULL,
                       mcu_command_uid=?, last_error=?
                   WHERE command_uid=?""",
                (
                    now,
                    mcu_command_uid,
                    error_code,
                    row["command_uid"],
                ),
            )
            seq = self._next_seq(self._conn)
            event = build_configuration_progress_event(
                device_name=row["device_name"],
                command_uid=row["command_uid"],
                application_uid=application_uid,
                stage="FAILED",
                version=row["config_version"],
                content_sha256=row["content_sha256"],
                mcu_payload_sha256=row["mcu_payload_sha256"],
                edge_event_sequence=seq,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
            )
            self._insert_event(self._conn, event, "CONFIGURATION_PROGRESS")
            return True

    # ── MCU 关键事件可靠收件 ──

    def begin_mcu_receive_generation(self, mcu_boot_id: int) -> int:
        """Start a new Edge-local UART receive generation after HELLO."""
        if not isinstance(mcu_boot_id, int) or mcu_boot_id <= 0:
            raise ValueError("mcu_boot_id must be a positive integer")
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='mcu_receive_generation'"""
            ).fetchone()
            generation = int(row["state_value"] if row else 0) + 1
            now = self._now()
            self._upsert_state(
                self._conn,
                "mcu_receive_generation",
                str(generation),
                now,
            )
            self._upsert_state(
                self._conn,
                "active_mcu_boot_id",
                str(mcu_boot_id),
                now,
            )
            return generation

    def get_mcu_receive_generation(self) -> int:
        value = self.get_state("mcu_receive_generation", "0")
        return int(value or 0)

    def receive_mcu_frame(self, frame: dict) -> str:
        payload = frame.get("payload") or {}
        mcu_boot_id = payload.get("mcuBootId")
        event_sequence = payload.get("mcuEventSequence")
        if not isinstance(mcu_boot_id, int) or not isinstance(event_sequence, int):
            return "REJECTED"
        stable = {
            "messageName": frame.get("message_name"),
            "payload": payload,
        }
        content_sha256 = canonical_payload_sha256(stable)
        with self.transaction():
            generation_row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='mcu_receive_generation'"""
            ).fetchone()
            generation = int(
                generation_row["state_value"] if generation_row else 0
            )
            active_boot_row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='active_mcu_boot_id'"""
            ).fetchone()
            active_boot_id = (
                active_boot_row["state_value"] if active_boot_row else ""
            )
            if active_boot_id and int(active_boot_id) != mcu_boot_id:
                return "REJECTED"
            existing = self._conn.execute(
                """SELECT content_sha256 FROM mcu_event_inbox
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (generation, mcu_boot_id, event_sequence),
            ).fetchone()
            if existing:
                return (
                    "DUPLICATE"
                    if existing["content_sha256"] == content_sha256
                    else "CONFLICT"
                )
            historical_duplicate = self._conn.execute(
                """SELECT 1 FROM mcu_event_inbox
                   WHERE mcu_boot_id=? AND mcu_event_sequence=?
                     AND content_sha256=?
                   LIMIT 1""",
                (mcu_boot_id, event_sequence, content_sha256),
            ).fetchone()
            if historical_duplicate:
                return "DUPLICATE"
            self._conn.execute(
                """INSERT INTO mcu_event_inbox
                   (mcu_receive_generation, mcu_boot_id, mcu_event_sequence,
                    message_name, message_type, source_tx_sequence,
                    content_sha256, payload_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    generation,
                    mcu_boot_id,
                    event_sequence,
                    frame["message_name"],
                    frame["message_type"],
                    frame["tx_sequence"],
                    content_sha256,
                    _json.dumps(payload, ensure_ascii=False),
                ),
            )
            return "ACCEPTED"

    def list_pending_mcu_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM mcu_event_inbox
                   WHERE state='PENDING' ORDER BY rowid LIMIT ?""",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def mark_mcu_event_processed(
        self,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        mcu_receive_generation: Optional[int] = None,
    ) -> bool:
        with self.transaction():
            if mcu_receive_generation is None:
                mcu_receive_generation = self.get_mcu_receive_generation()
            cur = self._conn.execute(
                """UPDATE mcu_event_inbox
                   SET state='PROCESSED', processed_at=?, last_error=NULL
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (
                    self._now(),
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return cur.rowcount == 1

    def mark_mcu_event_failed(
        self,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        error: str,
        mcu_receive_generation: Optional[int] = None,
    ) -> bool:
        with self.transaction():
            if mcu_receive_generation is None:
                mcu_receive_generation = self.get_mcu_receive_generation()
            cur = self._conn.execute(
                """UPDATE mcu_event_inbox SET state='FAILED', last_error=?
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?""",
                (
                    error,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return cur.rowcount == 1

    @staticmethod
    def _insert_event(conn, event: dict, event_type: str) -> None:
        conn.execute(
            """INSERT INTO event_outbox
               (event_uid, edge_event_sequence, event_type, payload_json, work_uid)
               VALUES (?,?,?,?,?)""",
            (
                event["eventUid"],
                event["edgeEventSequence"],
                event_type,
                _json.dumps(event, ensure_ascii=False),
                event["target"]["uid"],
            ),
        )

    def _record_command_observation_in_tx(
        self,
        conn,
        command: dict,
        stage: str,
        *,
        mcu_command_uid: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> str:
        payload = {
            "observedCommandType": command["commandType"],
            "stage": stage,
            "mcuCommandUid": mcu_command_uid,
            "errorCode": error_code,
        }
        canonical_sha256 = canonical_payload_sha256(payload)
        error_identity = error_code if error_code is not None else ""
        existing = conn.execute(
            """SELECT event_uid, canonical_sha256
               FROM command_observation
               WHERE command_uid=? AND stage=? AND error_code=?""",
            (command["commandUid"], stage, error_identity),
        ).fetchone()
        if existing:
            return (
                "DUPLICATE"
                if existing["canonical_sha256"] == canonical_sha256
                else "CONFLICT"
            )
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        event = build_event_envelope(
            event_uid=event_uid,
            device_name=command["targetDeviceName"],
            edge_event_sequence=sequence,
            event_type="DEVICE_COMMAND_OBSERVED",
            target_type="DEVICE_COMMAND",
            target_uid=command["commandUid"],
            command_uid=command["commandUid"],
            payload=payload,
        )
        self._insert_event(
            conn,
            event,
            "DEVICE_COMMAND_OBSERVED",
        )
        conn.execute(
            """INSERT INTO command_observation
               (command_uid, stage, error_code, event_uid,
                canonical_sha256)
               VALUES (?, ?, ?, ?, ?)""",
            (
                command["commandUid"],
                stage,
                error_identity,
                event_uid,
                canonical_sha256,
            ),
        )
        return "ACCEPTED"

    def record_command_observation(
        self,
        command: dict,
        stage: str,
        *,
        mcu_command_uid: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> str:
        with self.transaction():
            return self._record_command_observation_in_tx(
                self._conn,
                command,
                stage,
                mcu_command_uid=mcu_command_uid,
                error_code=error_code,
            )

    @staticmethod
    def _upsert_state(conn, key: str, value: str, now: str) -> None:
        conn.execute(
            """INSERT INTO device_state (state_key, state_value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(state_key) DO UPDATE
               SET state_value=excluded.state_value, updated_at=excluded.updated_at""",
            (key, value, now),
        )

    # ── 工单槽操作 ──

    def begin_mcu_firmware_update(
        self,
        *,
        update_uid: str,
        deployment_uid: str,
        source: str,
        package_path: str,
        package_sha256: str,
        manifest: dict,
        command_uid: Optional[str] = None,
        legacy_preflight: bool = False,
        allow_downgrade: bool = False,
        requested_reason: Optional[str] = None,
        package_ready: bool = True,
        device_name: Optional[str] = None,
    ) -> str:
        """Journal an update and atomically exclude physical business work."""
        _require_uuid4_local(update_uid, "update_uid")
        _require_uuid4_local(deployment_uid, "deployment_uid")
        if command_uid is not None:
            _require_uuid4_local(command_uid, "command_uid")
        if source not in {"CLOUD", "LOCAL"}:
            raise ValueError("MCU update source must be CLOUD or LOCAL")
        if not os.path.isabs(package_path):
            raise ValueError("MCU package path must be absolute")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        if not isinstance(legacy_preflight, bool):
            raise ValueError("legacy_preflight must be boolean")
        if not isinstance(allow_downgrade, bool):
            raise ValueError("allow_downgrade must be boolean")
        if not isinstance(package_ready, bool):
            raise ValueError("package_ready must be boolean")

        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = self._now()
        with self.transaction():
            existing = self._conn.execute(
                """SELECT update_uid, package_sha256, manifest_json,
                          legacy_preflight, allow_downgrade, package_ready
                   FROM mcu_firmware_update WHERE deployment_uid=?""",
                (deployment_uid,),
            ).fetchone()
            if existing:
                same = (
                    existing["package_sha256"] == package_sha256
                    and existing["manifest_json"] == manifest_json
                    and bool(existing["legacy_preflight"])
                    == legacy_preflight
                    and bool(existing["allow_downgrade"])
                    == allow_downgrade
                    and bool(existing["package_ready"]) == package_ready
                )
                return "DUPLICATE" if same else "CONFLICT"

            maintenance = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if maintenance:
                return "MAINTENANCE_BUSY"
            slot = self._conn.execute(
                "SELECT work_type FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if slot and slot["work_type"] != WORK_TYPE_NONE:
                return "WORK_BUSY"
            if command_uid is None:
                blocking_command = self._conn.execute(
                    """SELECT command_uid FROM command_inbox
                       WHERE state IN (
                         'PROCESSING', 'WAITING_MCU_RESULT',
                         'RECOVERY_REQUIRED'
                       ) LIMIT 1"""
                ).fetchone()
            else:
                blocking_command = self._conn.execute(
                    """SELECT command_uid FROM command_inbox
                       WHERE state IN (
                         'PROCESSING', 'WAITING_MCU_RESULT',
                         'RECOVERY_REQUIRED'
                       ) AND command_uid<>? LIMIT 1""",
                    (command_uid,),
                ).fetchone()
            if blocking_command:
                return "COMMAND_BUSY"

            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """INSERT INTO mcu_firmware_update (
                     update_uid, deployment_uid, command_uid, source,
                     package_path, package_sha256, manifest_json,
                     package_ready, state,
                     legacy_preflight, allow_downgrade, requested_reason,
                     previous_package_path, previous_package_sha256,
                     previous_manifest_json, requested_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update_uid,
                    deployment_uid,
                    command_uid,
                    source,
                    package_path,
                    package_sha256,
                    manifest_json,
                    int(package_ready),
                    int(legacy_preflight),
                    int(allow_downgrade),
                    requested_reason,
                    stable["current_package_path"] if stable else None,
                    stable["current_package_sha256"] if stable else None,
                    stable["current_manifest_json"] if stable else None,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """INSERT INTO maintenance_lock (
                     singleton_id, lock_type, owner_uid, detail_json,
                     acquired_at, updated_at
                   ) VALUES (1, 'MCU_FIRMWARE_UPDATE', ?, ?, ?, ?)""",
                (
                    update_uid,
                    _json.dumps(
                        {"deploymentUid": deployment_uid},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="QUEUED",
                )
            return "ACCEPTED"

    @staticmethod
    def _decode_mcu_update_row(row) -> Optional[dict]:
        if row is None:
            return None
        result = dict(row)
        result["manifest"] = _json.loads(result.pop("manifest_json"))
        previous = result.pop("previous_manifest_json")
        result["previous_manifest"] = (
            _json.loads(previous) if previous else None
        )
        prepare_identity = result.pop("prepare_identity_json")
        result["prepare_identity"] = (
            _json.loads(prepare_identity) if prepare_identity else None
        )
        result["prepare_recovery_required"] = bool(
            result["prepare_recovery_required"]
        )
        result["legacy_preflight"] = bool(result["legacy_preflight"])
        result["allow_downgrade"] = bool(result["allow_downgrade"])
        result["package_ready"] = bool(result["package_ready"])
        return result

    def get_mcu_firmware_update(self, update_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_mcu_firmware_update_by_deployment(
        self,
        deployment_uid: str,
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE deployment_uid=?",
                (deployment_uid,),
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_active_mcu_firmware_update(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT u.* FROM maintenance_lock l
                   JOIN mcu_firmware_update u ON u.update_uid=l.owner_uid
                   WHERE l.singleton_id=1
                     AND l.lock_type='MCU_FIRMWARE_UPDATE'"""
            ).fetchone()
        return self._decode_mcu_update_row(row)

    def get_maintenance_lock(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        detail_json = result.pop("detail_json")
        result["detail"] = _json.loads(detail_json) if detail_json else None
        return result

    def get_mcu_firmware_state(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
        result = dict(row) if row else {"singleton_id": 1}
        for prefix in ("current", "previous"):
            raw = result.pop(f"{prefix}_manifest_json", None)
            result[f"{prefix}_manifest"] = _json.loads(raw) if raw else None
        return result

    def record_mcu_firmware_package_acquisition_attempt(
        self,
        update_uid: str,
        *,
        attempt_limit: int,
    ) -> Optional[int]:
        """Start one package attempt, or return ``None`` at the total limit."""
        if (
            not isinstance(attempt_limit, int)
            or isinstance(attempt_limit, bool)
            or attempt_limit <= 0
        ):
            raise ValueError("package acquisition attempt limit is invalid")
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                """SELECT package_ready, state,
                          package_acquisition_attempt_count
                   FROM mcu_firmware_update WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or bool(update["package_ready"])
                or update["state"] not in {
                    "QUEUED",
                    "PACKAGE_FETCH_FAILED",
                }
            ):
                raise ValueError("MCU package acquisition is not active")
            previous = int(
                update["package_acquisition_attempt_count"]
            )
            if previous >= attempt_limit:
                return None
            current = previous + 1
            changed = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET package_acquisition_attempt_count=?, state='QUEUED',
                       last_error_code=NULL, last_error_message=NULL,
                       updated_at=?
                   WHERE update_uid=?""",
                (current, now, update_uid),
            )
            if changed.rowcount != 1:
                raise RuntimeError("MCU package attempt journal update lost")
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return current

    def reject_mcu_firmware_update_before_start(
        self,
        *,
        update_uid: str,
        deployment_uid: str,
        command_uid: str,
        package_sha256: str,
        manifest: dict,
        error_code: str,
        error_message: str,
        device_name: str,
        requested_reason: Optional[str] = None,
    ) -> str:
        """Persist a reliable terminal refusal without taking maintenance."""
        _require_uuid4_local(update_uid, "update_uid")
        _require_uuid4_local(deployment_uid, "deployment_uid")
        _require_uuid4_local(command_uid, "command_uid")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        if not device_name:
            raise ValueError("device name is required for MCU rejection")
        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        package_path = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.abspath(self.db_path)),
                "mcu-firmware-rejected",
                f"{package_sha256}.efw",
            )
        )
        now = self._now()
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE deployment_uid=?",
                (deployment_uid,),
            ).fetchone()
            if existing:
                same = (
                    existing["command_uid"] == command_uid
                    and existing["package_sha256"] == package_sha256
                    and existing["manifest_json"] == manifest_json
                    and existing["state"] == "REJECTED"
                    and existing["last_error_code"] == error_code
                )
                return "DUPLICATE" if same else "CONFLICT"
            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """INSERT INTO mcu_firmware_update (
                     update_uid, deployment_uid, command_uid, source,
                     package_path, package_sha256, manifest_json,
                     package_ready, state, legacy_preflight,
                     allow_downgrade, requested_reason,
                     previous_package_path, previous_package_sha256,
                     previous_manifest_json, last_error_code,
                     last_error_message, requested_at, updated_at,
                     completed_at
                   ) VALUES (?, ?, ?, 'CLOUD', ?, ?, ?, 0, 'REJECTED',
                             0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    update_uid,
                    deployment_uid,
                    command_uid,
                    package_path,
                    package_sha256,
                    manifest_json,
                    requested_reason,
                    stable["current_package_path"] if stable else None,
                    stable["current_package_sha256"] if stable else None,
                    stable["current_manifest_json"] if stable else None,
                    error_code,
                    error_message,
                    now,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=? AND state='PROCESSING'""",
                (now, error_code, command_uid),
            )
            self._create_mcu_firmware_progress_event_in_tx(
                self._conn,
                device_name=device_name,
                update_uid=update_uid,
                stage="REJECTED",
                error_code=error_code,
            )
            return "ACCEPTED"

    def attach_mcu_firmware_package(
        self,
        update_uid: str,
        *,
        package_path: str,
        package_sha256: str,
        manifest: dict,
        device_name: Optional[str] = None,
    ) -> str:
        """Atomically attach a verified package to its pre-I/O reservation."""
        if not os.path.isabs(package_path):
            raise ValueError("MCU package path must be absolute")
        if (
            not isinstance(package_sha256, str)
            or len(package_sha256) != 64
            or any(c not in "0123456789abcdef" for c in package_sha256)
        ):
            raise ValueError("MCU package SHA-256 is invalid")
        if not isinstance(manifest, dict):
            raise ValueError("MCU package manifest must be an object")
        manifest_json = _json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity_fields = (
            "releaseUid",
            "firmwareVersion",
            "firmwareVersionCode",
            "firmwareIdentityHex",
            "hardwareCompatibility",
            "fixedFrameRevision",
        )
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid or not update:
                return "NOT_ACTIVE"
            if update["source"] != "CLOUD":
                return "CONFLICT"
            reserved = _json.loads(update["manifest_json"])
            if (
                update["package_sha256"] != package_sha256
                or any(
                    reserved.get(field) != manifest.get(field)
                    for field in identity_fields
                )
            ):
                return "CONFLICT"
            if bool(update["package_ready"]):
                return "DUPLICATE" if update["manifest_json"] == manifest_json else "CONFLICT"
            if update["state"] not in {
                "QUEUED",
                "PACKAGE_FETCH_FAILED",
            }:
                return "CONFLICT"
            self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET package_path=?, package_sha256=?, manifest_json=?,
                       package_ready=1, state='QUEUED',
                       last_error_code=NULL, last_error_message=NULL,
                       updated_at=?
                   WHERE update_uid=?""",
                (
                    package_path,
                    package_sha256,
                    manifest_json,
                    now,
                    update_uid,
                ),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="QUEUED",
                )
            return "ACCEPTED"

    def fail_mcu_firmware_package_acquisition(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Keep the maintenance lock and expose a retryable package failure."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                """SELECT command_uid, package_ready
                   FROM mcu_firmware_update WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or bool(update["package_ready"])
            ):
                return False
            changed = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET state='PACKAGE_FETCH_FAILED', updated_at=?,
                       last_error_code=?, last_error_message=?
                   WHERE update_uid=?""",
                (now, error_code, error_message, update_uid),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if update["command_uid"]:
                # Publish the retry fact only after the owning inbox command
                # is durably retryable. This closes the race where a fresh
                # grant arrives before CommandProcessor handles the exception.
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (now, error_code, update["command_uid"]),
                )
            if device_name is not None:
                # Each fresh credential attempt must produce a fresh reliable
                # failure fact so the platform can wake the same command task
                # again. The immutable outbox event remains retained.
                self._conn.execute(
                    """DELETE FROM mcu_firmware_progress
                       WHERE update_uid=?
                         AND stage='PACKAGE_FETCH_FAILED'
                         AND target_attempt_count=0
                         AND rollback_attempt_count=0""",
                    (update_uid,),
                )
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="PACKAGE_FETCH_FAILED",
                    error_code=error_code,
                )
            return changed.rowcount == 1

    def transition_mcu_firmware_update(
        self,
        update_uid: str,
        state: str,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> bool:
        allowed = MCU_UPDATE_ACTIVE_STATES | MCU_UPDATE_TERMINAL_STATES
        if state not in allowed:
            raise ValueError("invalid MCU firmware update state")
        now = self._now()
        completed_at = now if state in MCU_UPDATE_TERMINAL_STATES else None
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET state=?, started_at=COALESCE(started_at, ?),
                       updated_at=?, completed_at=?, last_error_code=?,
                       last_error_message=?
                   WHERE update_uid=?""",
                (
                    state,
                    now,
                    now,
                    completed_at,
                    error_code,
                    error_message,
                    update_uid,
                ),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage=state,
                    error_code=error_code,
                )
            return updated.rowcount == 1

    def arm_mcu_firmware_prepare_recovery(
        self,
        update_uid: str,
        identity: dict,
    ) -> bool:
        """Persist the current application identity before any F2 execution."""

        identity_json = _canonical_mcu_prepare_identity(identity)
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update
                   SET prepare_recovery_required=1, prepare_identity_json=?,
                        updated_at=?
                   WHERE update_uid=?
                     AND state IN (
                       'PREFLIGHT', 'PREPARED', 'FLASHING_TARGET',
                       'VERIFYING_TARGET', 'ROLLING_BACK',
                       'VERIFYING_ROLLBACK'
                     )""",
                (identity_json, now, update_uid),
            )
            if updated.rowcount != 1:
                return False
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return True

    def record_mcu_firmware_attempt(
        self,
        update_uid: str,
        *,
        rollback: bool,
        device_name: Optional[str] = None,
    ) -> int:
        state = "ROLLING_BACK" if rollback else "FLASHING_TARGET"
        column = (
            "rollback_attempt_count" if rollback else "target_attempt_count"
        )
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                raise ValueError("MCU update does not own the maintenance lock")
            self._conn.execute(
                f"""UPDATE mcu_firmware_update
                    SET {column}={column}+1, state=?,
                        started_at=COALESCE(started_at, ?), updated_at=?,
                        last_error_code=NULL, last_error_message=NULL
                    WHERE update_uid=?""",
                (state, now, now, update_uid),
            )
            row = self._conn.execute(
                f"SELECT {column} AS count FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage=state,
                )
            return int(row["count"])

    def complete_mcu_firmware_update(
        self,
        update_uid: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Promote the verified target, retain one prior stable package, unlock."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid or not update:
                return False
            stable = self._conn.execute(
                "SELECT * FROM mcu_firmware_state WHERE singleton_id=1"
            ).fetchone()
            self._conn.execute(
                """UPDATE mcu_firmware_state SET
                     previous_package_path=?, previous_package_sha256=?,
                     previous_manifest_json=?, previous_installed_at=?,
                     current_package_path=?, current_package_sha256=?,
                     current_manifest_json=?, current_installed_at=?,
                     updated_at=? WHERE singleton_id=1""",
                (
                    stable["current_package_path"],
                    stable["current_package_sha256"],
                    stable["current_manifest_json"],
                    stable["current_installed_at"],
                    update["package_path"],
                    update["package_sha256"],
                    update["manifest_json"],
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """UPDATE mcu_firmware_update SET state='SUCCEEDED',
                     updated_at=?, completed_at=?, last_error_code=NULL,
                     last_error_message=NULL WHERE update_uid=?""",
                (now, now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="SUCCEEDED",
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return True

    def complete_mcu_firmware_rollback(
        self,
        update_uid: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Record successful restoration of the unchanged prior stable image."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update SET state='ROLLED_BACK',
                     updated_at=?, completed_at=? WHERE update_uid=?""",
                (now, now, update_uid),
            )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="ROLLED_BACK",
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return updated.rowcount == 1

    def fail_mcu_firmware_update_locked(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Keep maintenance locked when neither target nor rollback is safe."""
        return self.transition_mcu_firmware_update(
            update_uid,
            "FAILED_LOCKED",
            error_code=error_code,
            error_message=error_message,
            device_name=device_name,
        )

    def reject_mcu_firmware_update(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
        *,
        device_name: Optional[str] = None,
    ) -> bool:
        """Reject before touching flash and release the maintenance lock."""
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if not owner or owner["owner_uid"] != update_uid:
                return False
            updated = self._conn.execute(
                """UPDATE mcu_firmware_update SET state='REJECTED',
                     updated_at=?, completed_at=?, last_error_code=?,
                     last_error_message=? WHERE update_uid=?""",
                (now, now, error_code, error_message, update_uid),
            )
            command = self._conn.execute(
                """SELECT command_uid FROM mcu_firmware_update
                   WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if command and command["command_uid"]:
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL, last_error=?
                       WHERE command_uid=? AND state='PROCESSING'""",
                    (now, error_code, command["command_uid"]),
                )
            if device_name is not None:
                self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=update_uid,
                    stage="REJECTED",
                    error_code=error_code,
                )
            self._conn.execute(
                "DELETE FROM maintenance_lock WHERE owner_uid=?",
                (update_uid,),
            )
            return updated.rowcount == 1

    def retry_failed_mcu_firmware_update(
        self,
        update_uid: str,
        *,
        rollback_only: bool,
        reason: str,
    ) -> bool:
        """Re-arm a failed locked journal through an explicit local action."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("manual MCU update retry reason is required")
        now = self._now()
        with self.transaction():
            owner = self._conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            update = self._conn.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            if (
                not owner
                or owner["owner_uid"] != update_uid
                or not update
                or update["state"] != "FAILED_LOCKED"
            ):
                return False
            if rollback_only and (
                not update["previous_package_path"]
                or not update["previous_package_sha256"]
                or not update["previous_manifest_json"]
            ):
                raise ValueError("no stable rollback package is available")
            state = "ROLLING_BACK" if rollback_only else "QUEUED"
            self._conn.execute(
                """UPDATE mcu_firmware_update SET state=?,
                     target_attempt_count=0, rollback_attempt_count=0,
                     requested_reason=?, last_error_code=NULL,
                     last_error_message=NULL, completed_at=NULL,
                     updated_at=? WHERE update_uid=?""",
                (state, reason.strip()[:512], now, update_uid),
            )
            self._conn.execute(
                "UPDATE maintenance_lock SET updated_at=? WHERE owner_uid=?",
                (now, update_uid),
            )
            return True

    def create_mcu_firmware_progress_event(
        self,
        *,
        device_name: str,
        update_uid: str,
        stage: str,
        error_code: Optional[str] = None,
    ) -> str:
        """Persist one idempotent reliable progress fact for OneNet upload."""
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        with self.transaction():
            return self._create_mcu_firmware_progress_event_in_tx(
                self._conn,
                device_name=device_name,
                update_uid=update_uid,
                stage=stage,
                error_code=error_code,
            )

    def _create_mcu_firmware_progress_event_in_tx(
        self,
        conn,
        *,
        device_name: str,
        update_uid: str,
        stage: str,
        error_code: Optional[str] = None,
    ) -> str:
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        update = conn.execute(
            "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
            (update_uid,),
        ).fetchone()
        if update is None or update["state"] != stage:
            raise ValueError("MCU progress stage differs from journal")
        key = (
            update_uid,
            stage,
            update["target_attempt_count"],
            update["rollback_attempt_count"],
        )
        existing = conn.execute(
            """SELECT event_uid FROM mcu_firmware_progress
               WHERE update_uid=? AND stage=?
                 AND target_attempt_count=?
                 AND rollback_attempt_count=?""",
            key,
        ).fetchone()
        if existing:
            return "DUPLICATE"

        manifest = _json.loads(update["manifest_json"])
        installed_manifest = None
        if stage == "SUCCEEDED":
            installed_manifest = manifest
        elif stage == "ROLLED_BACK" and update["previous_manifest_json"]:
            installed_manifest = _json.loads(
                update["previous_manifest_json"]
            )
        elif stage in {
            "QUEUED",
            "PACKAGE_FETCH_FAILED",
            "PREFLIGHT",
            "PREPARED",
            "REJECTED",
        }:
            stable = conn.execute(
                """SELECT current_manifest_json
                   FROM mcu_firmware_state WHERE singleton_id=1"""
            ).fetchone()
            if stable and stable["current_manifest_json"]:
                installed_manifest = _json.loads(
                    stable["current_manifest_json"]
                )
        if stage in {
            "PACKAGE_FETCH_FAILED",
            "FAILED_LOCKED",
            "REJECTED",
        }:
            stable_error = error_code or update["last_error_code"]
            if not stable_error:
                raise ValueError("failed MCU progress requires an error code")
            progress_error = stable_error
        else:
            progress_error = None
        payload = {
            "deploymentUid": update["deployment_uid"],
            "updateUid": update_uid,
            "releaseUid": manifest["releaseUid"],
            "source": update["source"],
            "stage": stage,
            "firmwareVersion": manifest["firmwareVersion"],
            "firmwareVersionCode": manifest["firmwareVersionCode"],
            "firmwareIdentityHex": manifest["firmwareIdentityHex"],
            "fixedFrameRevision": manifest["fixedFrameRevision"],
            "targetAttemptCount": update["target_attempt_count"],
            "rollbackAttemptCount": update["rollback_attempt_count"],
            "legacyPreflight": bool(update["legacy_preflight"]),
            "downgradeAuthorized": bool(update["allow_downgrade"]),
            "installedFirmwareVersion": (
                installed_manifest["firmwareVersion"]
                if installed_manifest else None
            ),
            "installedFirmwareVersionCode": (
                installed_manifest["firmwareVersionCode"]
                if installed_manifest else None
            ),
            "installedFirmwareIdentityHex": (
                installed_manifest["firmwareIdentityHex"]
                if installed_manifest else None
            ),
            "errorCode": progress_error,
        }
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        event = build_event_envelope(
            event_uid=event_uid,
            device_name=device_name,
            edge_event_sequence=sequence,
            event_type="MCU_FIRMWARE_UPDATE_PROGRESS",
            target_type="MCU_FIRMWARE_DEPLOYMENT",
            target_uid=update["deployment_uid"],
            command_uid=update["command_uid"],
            delivery_class="RELIABLE_FACT",
            payload=payload,
        )
        self._insert_event(
            conn,
            event,
            "MCU_FIRMWARE_UPDATE_PROGRESS",
        )
        conn.execute(
            """INSERT INTO mcu_firmware_progress
               (update_uid, stage, target_attempt_count,
                rollback_attempt_count, event_uid, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (*key, event_uid, self._now()),
        )
        return "ACCEPTED"

    def reconcile_mcu_firmware_progress_events(
        self,
        device_name: str,
    ) -> int:
        """Backfill the current journal state after upgrading from schema v11."""
        if not device_name:
            raise ValueError("device name is required for MCU progress")
        with self.transaction():
            rows = self._conn.execute(
                "SELECT update_uid, state FROM mcu_firmware_update"
            ).fetchall()
            created = 0
            for row in rows:
                disposition = self._create_mcu_firmware_progress_event_in_tx(
                    self._conn,
                    device_name=device_name,
                    update_uid=row["update_uid"],
                    stage=row["state"],
                )
                if disposition == "ACCEPTED":
                    created += 1
            return created

    def acquire_work_slot(
        self,
        work_type: str,
        work_uid: str,
        port_no: int,
        context: dict,
        *,
        observed_command: Optional[dict] = None,
    ) -> bool:
        with self.transaction():
            conn = self._conn
            maintenance = conn.execute(
                "SELECT owner_uid FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if maintenance:
                return False
            slot = conn.execute("SELECT work_type FROM work_slot WHERE slot_id=1").fetchone()
            if not slot or slot["work_type"] != WORK_TYPE_NONE:
                return False
            conn.execute(
                "UPDATE work_slot SET work_type=?, work_uid=?, work_state='ACTIVE', port_no=?, context_json=?, updated_at=? WHERE slot_id=1",
                (work_type, work_uid, port_no, _json.dumps(context, ensure_ascii=False), self._now()),
            )
            if observed_command is not None:
                observation = self._record_command_observation_in_tx(
                    conn,
                    observed_command,
                    "ACCEPTED",
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            return True

    # ── 事件发件箱操作 ──

    def list_pending_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM event_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (
                       event_type='BUSINESS_CONFIRMATION_RECEIPT'
                       OR confirmed_at IS NULL
                     )
                     AND (
                       next_retry_at IS NULL
                       OR datetime(next_retry_at) <= datetime('now')
                     )
                   ORDER BY edge_event_sequence LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recover_sending_events(self) -> int:
        """A process restart loses MQTT packet identities; resend by event UID."""
        with self.transaction():
            exists = self._conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='event_outbox'"""
            ).fetchone()
            if not exists:
                return 0
            repaired = self._conn.execute(
                """UPDATE event_outbox
                   SET state='CONFIRMED', mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE state<>'CONFIRMED'
                     AND confirmed_at IS NOT NULL
                     AND event_type<>'BUSINESS_CONFIRMATION_RECEIPT'"""
            ).rowcount
            if repaired:
                logger.warning(
                    "Repaired %d reliable event states from durable "
                    "business confirmations",
                    repaired,
                )
            return self._conn.execute(
                """UPDATE event_outbox
                   SET state='PENDING', mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE state='SENDING' AND tombstoned=0"""
            ).rowcount

    def count_pending_reliable_events(self) -> int:
        """Count unconfirmed RELIABLE_FACT rows, including in-flight rows."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT payload_json FROM event_outbox
                   WHERE state IN ('PENDING', 'SENDING')
                     AND tombstoned=0
                     AND confirmed_at IS NULL"""
            ).fetchall()
        count = 0
        for row in rows:
            try:
                envelope = _json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if envelope.get("deliveryClass") == "RELIABLE_FACT":
                count += 1
        return count

    def get_event(self, event_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
        return dict(row) if row else None

    def get_event_by_sequence(
        self,
        edge_event_sequence: int,
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM event_outbox "
                "WHERE edge_event_sequence=?",
                (edge_event_sequence,),
            ).fetchone()
        return dict(row) if row else None

    # ── 设备状态操作 ──

    def get_state(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_value FROM device_state WHERE state_key=?", (key,)
            ).fetchone()
        return row["state_value"] if row else default

    def get_state_record(self, key: str) -> Optional[dict]:
        """Return a state value together with its trusted SQLite write time."""
        with self._lock:
            row = self._conn.execute(
                """SELECT state_value, updated_at FROM device_state
                   WHERE state_key=?""",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction():
            self._conn.execute(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=?, updated_at=?""",
                (key, value, self._now(), value, self._now()),
            )

    def save_device_entry_url(
        self,
        url: str,
        sha256: str,
        issued_at: str,
    ) -> dict:
        """Atomically retain the newest platform-issued device entry URL."""
        _validate_device_entry_url(url, sha256)
        incoming_time = _parse_utc_instant(issued_at, "issued_at")
        incoming = {
            "deviceEntryUrl": url,
            "deviceEntryUrlSha256": sha256,
            "issuedAt": issued_at,
        }
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='device_entry_url'"""
            ).fetchone()
            if row:
                try:
                    current = _json.loads(row["state_value"])
                    _validate_device_entry_url(
                        current["deviceEntryUrl"],
                        current["deviceEntryUrlSha256"],
                    )
                    current_time = _parse_utc_instant(
                        current["issuedAt"],
                        "stored issuedAt",
                    )
                except (KeyError, TypeError, ValueError, _json.JSONDecodeError) as error:
                    raise RuntimeError(
                        "stored device entry URL is corrupt"
                    ) from error
                if incoming_time < current_time:
                    return {**current, "disposition": "STALE_IGNORED"}
                if incoming_time == current_time:
                    if current["deviceEntryUrlSha256"] != sha256:
                        raise ValueError(
                            "device entry URL conflicts at the same issuedAt"
                        )
                    return {**current, "disposition": "UNCHANGED"}

            serialized = _json.dumps(
                incoming,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            self._upsert_state(
                self._conn,
                "device_entry_url",
                serialized,
                self._now(),
            )
            return {**incoming, "disposition": "SAVED"}

    def get_device_entry_url(self) -> Optional[dict]:
        """Return the locally proven entry URL, or fail on corrupt state."""
        raw = self.get_state("device_entry_url")
        if not raw:
            return None
        try:
            record = _json.loads(raw)
            _validate_device_entry_url(
                record["deviceEntryUrl"],
                record["deviceEntryUrlSha256"],
            )
            _parse_utc_instant(record["issuedAt"], "stored issuedAt")
        except (KeyError, TypeError, ValueError, _json.JSONDecodeError) as error:
            raise RuntimeError("stored device entry URL is corrupt") from error
        return record

    def save_fixed_frame_self_test(self, result: dict) -> bool:
        """Retain F0/F1 and report whether its smoke projection changed."""
        if not isinstance(result, dict):
            raise ValueError("fixed-frame self-test result must be an object")
        smoke_state = result.get("smokeState")
        smoke_health = result.get("smokeSensorHealth")
        fault_code = result.get("faultCode")
        legal = (
            smoke_state in {"NORMAL", "ALARM"}
            and smoke_health == "OK"
            and fault_code is None
        ) or (
            smoke_state == "UNKNOWN"
            and smoke_health in {
                "TIMEOUT",
                "SENSOR_FAULT",
                "PROTOCOL_ERROR",
            }
            and fault_code == "SMOKE_SENSOR"
        )
        if not legal:
            raise ValueError("invalid fixed-frame smoke projection")
        serialized = _json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transaction():
            now = self._now()
            smoke_values = {
                "smoke_state": smoke_state,
                "smoke_sensor_health": smoke_health,
                "smoke_fault_code": fault_code or "NONE",
                "port_1_smoke_state": smoke_state,
                "port_1_smoke_sensor_health": smoke_health,
                "port_1_smoke_fault_code": fault_code or "NONE",
            }
            placeholders = ",".join("?" for _ in smoke_values)
            rows = self._conn.execute(
                f"""SELECT state_key, state_value FROM device_state
                     WHERE state_key IN ({placeholders})""",
                tuple(smoke_values),
            ).fetchall()
            previous = {
                row["state_key"]: row["state_value"] for row in rows
            }
            projection_changed = any(
                previous.get(key) != value
                for key, value in smoke_values.items()
            )
            values = {
                "fixed_frame_latest_self_test_json": serialized,
                **smoke_values,
            }
            for key, value in values.items():
                self._upsert_state(self._conn, key, value, now)
            return projection_changed

    def get_or_create_edge_store_instance_uid(self) -> str:
        """Return the permanent identity of this freshly-created edge DB."""
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='edge_store_instance_uid'"""
            ).fetchone()
            if row:
                try:
                    parsed = _uuid.UUID(row["state_value"])
                except (ValueError, TypeError, AttributeError) as error:
                    raise RuntimeError(
                        "edge store instance identity is corrupt"
                    ) from error
                if parsed.version != 4 or str(parsed) != row["state_value"]:
                    raise RuntimeError(
                        "edge store instance identity is not UUIDv4"
                    )
                return str(parsed)
            instance_uid = str(_uuid.uuid4())
            self._upsert_state(
                self._conn,
                "edge_store_instance_uid",
                instance_uid,
                self._now(),
            )
            return instance_uid

    def get_edge_boot_id(self) -> str:
        return self.get_state("edge_boot_id")

    def set_edge_boot_id(self, boot_id: str) -> None:
        self.set_state("edge_boot_id", boot_id)

    def get_edge_event_sequence(self) -> int:
        return int(self.get_state("edge_event_sequence", "0"))

    def reserve_edge_event_sequence(self) -> int:
        """Atomically reserve one sequence for a direct telemetry event."""
        with self.transaction():
            return self._next_seq(self._conn)

    def save_mqtt_persistent_state(self, session_present: bool, reason_code: int) -> None:
        with self.transaction():
            now = self._now()
            self._conn.executemany(
                """INSERT INTO device_state (state_key, state_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=?, updated_at=?""",
                [
                    ("mqtt_session_present", "true" if session_present else "false", now,
                     "true" if session_present else "false", now),
                    ("mqtt_last_rc", str(reason_code), now, str(reason_code), now),
                ],
            )

    def record_safety_state_and_event(
        self,
        *,
        device_name: str,
        mcu_receive_generation: int,
        payload: dict,
    ) -> str:
        """Atomically project and publish one real UART safety change."""
        smoke_state = payload.get("smokeState")
        smoke_health = payload.get("smokeSensorHealth")
        fault_code = payload.get("faultCode")
        legal = (
            (
                smoke_state in {"NORMAL", "ALARM"}
                and smoke_health == "OK"
                and fault_code in (None, "NONE")
            )
            or (
                smoke_state == "UNKNOWN"
                and smoke_health != "OK"
                and fault_code == "SMOKE_SENSOR"
            )
        )
        if not legal:
            return "REJECTED"
        mcu_boot_id = payload.get("mcuBootId")
        mcu_event_sequence = payload.get("mcuEventSequence")
        if (
            not isinstance(mcu_boot_id, int)
            or mcu_boot_id <= 0
            or not isinstance(mcu_event_sequence, int)
            or mcu_event_sequence <= 0
        ):
            return "REJECTED"
        port_no = payload.get("portNo")
        if not isinstance(port_no, int) or port_no <= 0:
            port_no = None
        fixed_frame = payload.get("compatibilityMode") is True
        with self.transaction():
            existing = self._conn.execute(
                """SELECT event_uid FROM mcu_derived_event
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=? AND mcu_event_sequence=?
                     AND event_type='SAFETY_SENSOR_STATE_CHANGED'""",
                (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE mcu_event_inbox
                       SET state='PROCESSED', processed_at=?,
                           last_error=NULL
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?""",
                    (
                        self._now(),
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                )
                return "DUPLICATE"
            now = self._now()
            scope = (
                f"port_{port_no}" if port_no is not None else "device"
            )
            unchanged = False
            if fixed_frame:
                previous_row = self._conn.execute(
                    """SELECT state_value FROM device_state
                       WHERE state_key='fixed_frame_latest_smoke_json'"""
                ).fetchone()
                previous = None
                if previous_row:
                    try:
                        candidate = _json.loads(previous_row["state_value"])
                        previous = candidate if isinstance(candidate, dict) else None
                    except (TypeError, ValueError):
                        previous = None
                unchanged = bool(
                    previous
                    and previous.get("smokeState") == smoke_state
                    and previous.get("smokeSensorHealth") == smoke_health
                    and previous.get("faultCode")
                    == (None if fault_code in (None, "NONE") else fault_code)
                )
                self._upsert_state(
                    self._conn,
                    "fixed_frame_latest_smoke_json",
                    _json.dumps(
                        {
                            "smokeState": smoke_state,
                            "smokeSensorHealth": smoke_health,
                            "faultCode": (
                                None
                                if fault_code in (None, "NONE")
                                else fault_code
                            ),
                            "mcuBootId": mcu_boot_id,
                            "mcuEventSequence": mcu_event_sequence,
                            "rawFrameHex": payload.get("rawFrameHex"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_state",
                smoke_state,
                now,
            )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_sensor_health",
                smoke_health,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_state",
                smoke_state,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_sensor_health",
                smoke_health,
                now,
            )
            self._upsert_state(
                self._conn,
                f"{scope}_smoke_fault_code",
                "NONE" if fault_code in (None, "NONE") else fault_code,
                now,
            )
            self._upsert_state(
                self._conn,
                "smoke_fault_code",
                "NONE" if fault_code in (None, "NONE") else fault_code,
                now,
            )
            if unchanged:
                self._conn.execute(
                    """UPDATE mcu_event_inbox
                       SET state='PROCESSED', processed_at=?,
                           last_error=NULL
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?""",
                    (
                        now,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                )
                return "UNCHANGED"
            event_uid = self._new_uid()
            sequence = self._next_seq(self._conn)
            work_type = payload.get("workType", "NONE")
            work_uid = (
                payload.get("workUid")
                if work_type != "NONE"
                else None
            )
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="SAFETY_SENSOR_STATE_CHANGED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "portNo": port_no,
                    "smokeState": smoke_state,
                    "smokeSensorHealth": smoke_health,
                    "faultCode": (
                        None
                        if fault_code in (None, "NONE")
                        else fault_code
                    ),
                    "workType": work_type,
                    "workUid": work_uid,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "SAFETY_SENSOR_STATE_CHANGED",
            )
            self._conn.execute(
                """INSERT INTO mcu_derived_event
                   (mcu_receive_generation, mcu_boot_id,
                    mcu_event_sequence, event_type, event_uid)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    "SAFETY_SENSOR_STATE_CHANGED",
                    event_uid,
                ),
            )
            self._conn.execute(
                """UPDATE mcu_event_inbox
                   SET state='PROCESSED', processed_at=?,
                       last_error=NULL
                   WHERE mcu_receive_generation=?
                     AND mcu_boot_id=?
                     AND mcu_event_sequence=?""",
                (
                    now,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                ),
            )
            return "ACCEPTED"

    # ── 故障操作 ──

    def record_fault(self, component: str, fault_code: int, severity: str,
                     detail: Optional[dict] = None, *,
                     fault_uid: Optional[str] = None,
                     lifecycle: str = FAULT_OBSERVED) -> str:
        fault_uid = fault_uid or self._new_uid()
        recovered_at = self._now() if lifecycle == FAULT_RECOVERED else None
        with self.transaction():
            self._conn.execute(
                """INSERT INTO faults
                   (fault_uid, component, fault_code, severity, lifecycle,
                    detail_json, recovered_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(fault_uid) DO UPDATE SET
                     component=excluded.component,
                     fault_code=excluded.fault_code,
                     severity=excluded.severity,
                     lifecycle=excluded.lifecycle,
                     detail_json=excluded.detail_json,
                     recovered_at=excluded.recovered_at""",
                (
                    fault_uid,
                    component,
                    fault_code,
                    severity,
                    lifecycle,
                    _json.dumps(detail, ensure_ascii=False)
                    if detail
                    else None,
                    recovered_at,
                ),
            )
        return fault_uid

    def mark_fault_recovered(self, fault_uid: str) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                "UPDATE faults SET lifecycle='RECOVERED', recovered_at=? WHERE fault_uid=?",
                (self._now(), fault_uid),
            )
            return cur.rowcount > 0

    def list_active_faults(self) -> list[dict]:
        with self._lock:
            legacy_rows = self._conn.execute(
                "SELECT * FROM faults WHERE lifecycle='OBSERVED' ORDER BY observed_at"
            ).fetchall()
            edge_rows = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED'
                   ORDER BY first_detected_at"""
            ).fetchall()
        return [
            *(dict(row) for row in legacy_rows),
            *(dict(row) for row in edge_rows),
        ]

    def get_active_edge_fault(
        self,
        component: str,
        fault_code: str,
        port_no: Optional[int] = None,
    ) -> Optional[dict]:
        scope_key = (
            f"PORT:{port_no}" if port_no is not None else "DEVICE"
        )
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED' AND scope_key=?
                     AND component=? AND fault_code=?
                   LIMIT 1""",
                (scope_key, component, fault_code),
            ).fetchone()
        return dict(row) if row else None

    def observe_fault_and_create_event(
        self,
        *,
        device_name: str,
        component: str,
        fault_code: str,
        severity: str,
        port_no: Optional[int] = None,
        fault_uid: Optional[str] = None,
        mcu_boot_id: Optional[int] = None,
        mcu_event_sequence: Optional[int] = None,
        mcu_receive_generation: Optional[int] = None,
        detail: Optional[dict] = None,
    ) -> str:
        """Create or monotonically upgrade one active fault atomically."""
        if not device_name or device_name == "UNKNOWN_DEVICE":
            return "REJECTED"
        scope_key = (
            f"PORT:{port_no}" if port_no is not None else "DEVICE"
        )
        severity_rank = {
            "WARNING": 1,
            "BLOCK_PORT": 2,
            "BLOCK_DEVICE": 3,
        }
        if severity not in severity_rank:
            return "REJECTED"
        now = self._now()
        with self.transaction():
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                derived = self._conn.execute(
                    """SELECT event_uid FROM mcu_derived_event
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?
                         AND event_type='DEVICE_FAULT_OBSERVED'""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                ).fetchone()
                if derived:
                    self._mark_mcu_event_processed_in_tx(
                        self._conn,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        now,
                    )
                    return "DUPLICATE"
            active = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE lifecycle='OBSERVED' AND scope_key=?
                     AND component=? AND fault_code=?
                   LIMIT 1""",
                (scope_key, component, fault_code),
            ).fetchone()
            if active:
                current_rank = severity_rank.get(
                    active["severity"],
                    0,
                )
                self._conn.execute(
                    """UPDATE edge_fault_state
                       SET discovery_count=discovery_count+1,
                           last_detected_at=?
                       WHERE fault_uid=?""",
                    (now, active["fault_uid"]),
                )
                if severity_rank[severity] <= current_rank:
                    return "DUPLICATE"
                fault_uid = active["fault_uid"]
                self._conn.execute(
                    """UPDATE edge_fault_state SET severity=?
                       WHERE fault_uid=?""",
                    (severity, fault_uid),
                )
            else:
                fault_uid = fault_uid or self._new_uid()
                self._conn.execute(
                    """INSERT INTO edge_fault_state
                       (fault_uid, scope_key, port_no, component,
                        fault_code, severity, lifecycle,
                        first_detected_at, last_detected_at,
                        detail_json)
                       VALUES (?, ?, ?, ?, ?, ?, 'OBSERVED',
                               ?, ?, ?)""",
                    (
                        fault_uid,
                        scope_key,
                        port_no,
                        component,
                        fault_code,
                        severity,
                        now,
                        now,
                        (
                            _json.dumps(detail, ensure_ascii=False)
                            if detail
                            else None
                        ),
                    ),
                )
            sequence = self._next_seq(self._conn)
            event_uid = self._new_uid()
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DEVICE_FAULT_OBSERVED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "faultUid": fault_uid,
                    "portNo": port_no,
                    "component": component,
                    "severity": severity,
                    "faultCode": fault_code,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_FAULT_OBSERVED",
            )
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                self._conn.execute(
                    """INSERT INTO mcu_derived_event
                       (mcu_receive_generation, mcu_boot_id,
                        mcu_event_sequence, event_type, event_uid)
                       VALUES (?, ?, ?, 'DEVICE_FAULT_OBSERVED', ?)""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_uid,
                    ),
                )
                self._mark_mcu_event_processed_in_tx(
                    self._conn,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    now,
                )
            return "ACCEPTED"

    def recover_fault_and_create_event(
        self,
        *,
        device_name: str,
        fault_uid: str,
        component: str,
        fault_code: str,
        port_no: Optional[int],
        recovery_evidence: str,
        mcu_boot_id: Optional[int] = None,
        mcu_event_sequence: Optional[int] = None,
        mcu_receive_generation: Optional[int] = None,
    ) -> str:
        """Close exactly one active fault and create its recovery fact."""
        if not device_name or device_name == "UNKNOWN_DEVICE":
            return "REJECTED"
        with self.transaction():
            now = self._now()
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                derived = self._conn.execute(
                    """SELECT event_uid FROM mcu_derived_event
                       WHERE mcu_receive_generation=?
                         AND mcu_boot_id=?
                         AND mcu_event_sequence=?
                         AND event_type='DEVICE_FAULT_RECOVERED'""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                    ),
                ).fetchone()
                if derived:
                    self._mark_mcu_event_processed_in_tx(
                        self._conn,
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        now,
                    )
                    return "DUPLICATE"
            fault = self._conn.execute(
                """SELECT * FROM edge_fault_state
                   WHERE fault_uid=?""",
                (fault_uid,),
            ).fetchone()
            if not fault:
                return "UNKNOWN"
            if fault["lifecycle"] == "RECOVERED":
                return "DUPLICATE"
            if (
                fault["component"] != component
                or fault["fault_code"] != fault_code
                or fault["port_no"] != port_no
            ):
                return "CONFLICT"
            self._conn.execute(
                """UPDATE edge_fault_state
                   SET lifecycle='RECOVERED', recovered_at=?,
                       recovery_evidence=?
                   WHERE fault_uid=?""",
                (now, recovery_evidence, fault_uid),
            )
            sequence = self._next_seq(self._conn)
            event_uid = self._new_uid()
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="DEVICE_FAULT_RECOVERED",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                payload={
                    "faultUid": fault_uid,
                    "portNo": port_no,
                    "component": component,
                    "severity": fault["severity"],
                    "faultCode": fault_code,
                    "mcuBootId": mcu_boot_id,
                    "mcuEventSequence": mcu_event_sequence,
                },
            )
            self._insert_event(
                self._conn,
                event,
                "DEVICE_FAULT_RECOVERED",
            )
            if (
                mcu_receive_generation is not None
                and mcu_boot_id is not None
                and mcu_event_sequence is not None
            ):
                self._conn.execute(
                    """INSERT INTO mcu_derived_event
                       (mcu_receive_generation, mcu_boot_id,
                        mcu_event_sequence, event_type, event_uid)
                       VALUES (?, ?, ?, 'DEVICE_FAULT_RECOVERED', ?)""",
                    (
                        mcu_receive_generation,
                        mcu_boot_id,
                        mcu_event_sequence,
                        event_uid,
                    ),
                )
                self._mark_mcu_event_processed_in_tx(
                    self._conn,
                    mcu_receive_generation,
                    mcu_boot_id,
                    mcu_event_sequence,
                    now,
                )
            return "ACCEPTED"

    @staticmethod
    def _mark_mcu_event_processed_in_tx(
        conn,
        mcu_receive_generation: int,
        mcu_boot_id: int,
        mcu_event_sequence: int,
        processed_at: str,
    ) -> None:
        conn.execute(
            """UPDATE mcu_event_inbox
               SET state='PROCESSED', processed_at=?,
                   last_error=NULL
               WHERE mcu_receive_generation=?
                 AND mcu_boot_id=? AND mcu_event_sequence=?""",
            (
                processed_at,
                mcu_receive_generation,
                mcu_boot_id,
                mcu_event_sequence,
            ),
        )

    # ── 完整性校验与维护 ──

    def integrity_check(self) -> bool:
        try:
            with self._lock:
                row = self._conn.execute("PRAGMA integrity_check").fetchone()
            ok = row[0] == "ok"
            if not ok:
                logger.error("SQLite 完整性检查失败: %s", row[0])
            return ok
        except Exception as e:
            logger.error("SQLite 完整性检查异常: %s", e)
            return False

    def tombstone_confirmed_events(self, older_than_hours: int = 72) -> int:
        with self.transaction():
            conn = self._conn
            rows = conn.execute(
                """SELECT event_uid, event_type FROM event_outbox
                   WHERE state = 'CONFIRMED' AND tombstoned = 0
                     AND datetime(confirmed_at) <= datetime('now', ?)""",
                (f"-{older_than_hours} hours",),
            ).fetchall()
            count = 0
            for r in rows:
                conn.execute(
                    "INSERT INTO tombstones (tombstone_uid, original_type, original_uid) VALUES (?, ?, ?)",
                    (self._new_uid(), r["event_type"], r["event_uid"]),
                )
                conn.execute("UPDATE event_outbox SET tombstoned=1 WHERE event_uid=?", (r["event_uid"],))
                count += 1
            if count:
                logger.info("墓碑化 %d 个已确认事件", count)
            return count

    def cleanup_dead_photos(self, older_than_hours: int = 72) -> int:
        with self.transaction():
            conn = self._conn
            rows = conn.execute(
                """SELECT photo_uid, local_path FROM photo_outbox
                   WHERE state = 'DEAD' AND tombstoned = 0
                     AND created_at <= datetime('now', ?)""",
                (f"-{older_than_hours} hours",),
            ).fetchall()
            count = 0
            for r in rows:
                try:
                    if os.path.exists(r["local_path"]):
                        os.remove(r["local_path"])
                except OSError:
                    pass
                conn.execute("UPDATE photo_outbox SET tombstoned=1 WHERE photo_uid=?", (r["photo_uid"],))
                count += 1
            return count

    # ── 独立远程维护隧道槽 ──

    def import_remote_support_status_event(
        self,
        fact: dict[str, Any],
    ) -> str:
        """Reliably adopt one fact from the independent tunnel agent.

        The agent keeps the fact until this EdgeStore transaction commits and
        the local RPC acknowledgement succeeds.  Replaying the same event UID
        is therefore normal after either process crashes.
        """

        required = {
            "eventUid",
            "sessionUid",
            "commandUid",
            "deviceName",
            "remotePort",
            "state",
            "failureCode",
            "occurredAt",
        }
        if (
            not isinstance(fact, dict)
            or frozenset(fact) not in {
                frozenset(required),
                frozenset(required | {"clockQuality"}),
            }
        ):
            raise ValueError("remote support status fact fields are invalid")
        event_uid = _require_uuid4_local(fact["eventUid"], "eventUid")
        session_uid = _require_uuid4_local(
            fact["sessionUid"],
            "sessionUid",
        )
        command_uid = _require_uuid4_local(
            fact["commandUid"],
            "commandUid",
        )
        device_name = fact["deviceName"]
        if (
            not isinstance(device_name, str)
            or not 1 <= len(device_name) <= 64
            or any(character in device_name for character in "\x00\r\n")
        ):
            raise ValueError("remote support status deviceName is invalid")
        remote_port = fact["remotePort"]
        if (
            isinstance(remote_port, bool)
            or not isinstance(remote_port, int)
            or remote_port not in range(22011, 22015)
        ):
            raise ValueError("remote support status remotePort is invalid")
        state = fact["state"]
        if state not in {
            "CONNECTING",
            "OPEN",
            "CLOSED",
            "FAILED",
            "EXPIRED",
        }:
            raise ValueError("remote support status state is invalid")
        failure_code = fact["failureCode"]
        if (
            failure_code is not None
            and failure_code not in REMOTE_SUPPORT_FAILURE_CODES
        ):
            raise ValueError("remote support status failureCode is invalid")
        if state == "FAILED" and failure_code is None:
            raise ValueError("FAILED remote support status requires failureCode")
        occurred_at = fact["occurredAt"]
        clock_quality = fact.get("clockQuality", "SYNCED")
        if clock_quality not in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}:
            raise ValueError("remote support clockQuality is invalid")
        if clock_quality == "SYNCED":
            _parse_utc_instant(occurred_at, "occurredAt")
        elif occurred_at is not None:
            raise ValueError("unsynced remote support fact carries occurredAt")
        payload = {
            "sessionUid": session_uid,
            "state": state,
            "remotePort": remote_port,
            "failureCode": failure_code,
        }

        with self.transaction():
            existing = self._conn.execute(
                "SELECT payload_json FROM event_outbox WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if existing is not None:
                envelope = _json.loads(existing["payload_json"])
                same = (
                    envelope.get("eventType")
                    == "REMOTE_SUPPORT_TUNNEL_STATUS"
                    and envelope.get("commandUid") == command_uid
                    and envelope.get("target")
                    == {"type": "DEVICE_ASSET", "uid": device_name}
                    and envelope.get("occurredAt") == occurred_at
                    and envelope.get("clockQuality") == clock_quality
                    and envelope.get("payload") == payload
                )
                return "DUPLICATE" if same else "CONFLICT"
            sequence = self._next_seq(self._conn)
            envelope = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type="REMOTE_SUPPORT_TUNNEL_STATUS",
                target_type="DEVICE_ASSET",
                target_uid=device_name,
                command_uid=command_uid,
                delivery_class="RELIABLE_FACT",
                payload=payload,
            )
            envelope["occurredAt"] = occurred_at
            envelope["clockQuality"] = clock_quality
            self._insert_event(
                self._conn,
                envelope,
                "REMOTE_SUPPORT_TUNNEL_STATUS",
            )
            return "ACCEPTED"

    def request_remote_support_open(
        self,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        expires_at: str,
    ) -> str:
        """Persist desired OPEN and CONNECTING evidence in one transaction."""

        _require_uuid4_local(session_uid, "session_uid")
        _require_uuid4_local(command_uid, "command_uid")
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("device_name is required")
        if (
            isinstance(remote_port, bool)
            or not isinstance(remote_port, int)
            or remote_port not in range(22011, 22015)
        ):
            raise ValueError("remote_port must be one of 22011..22014")
        deadline_reference = local_deadline_reference()
        if (
            deadline_reference is not None
            and _parse_utc_instant(expires_at, "expires_at")
            <= deadline_reference
        ):
            raise ValueError("remote support session is already expired")
        with self.transaction():
            existing = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if existing:
                same_session = existing["session_uid"] == session_uid
                same_request = (
                    same_session
                    and existing["remote_port"] == remote_port
                    and existing["expires_at"] == expires_at
                    and existing["device_name"] == device_name
                )
                if same_session:
                    # A session UID is a permanent idempotency identity.  A
                    # delayed/replayed open must never resurrect a CLOSED,
                    # FAILED or EXPIRED lease; a genuinely new lease uses a
                    # new session UID.
                    return "DUPLICATE" if same_request else "CONFLICT"
                if existing["state"] not in REMOTE_SUPPORT_TERMINAL_STATES:
                    return "CONFLICT"
            now = self._now()
            self._conn.execute(
                """INSERT INTO remote_support_session
                   (singleton_id, session_uid, command_uid, device_name,
                    remote_port, expires_at, state, failure_code,
                    attempt_count, next_attempt_at, created_at, updated_at)
                   VALUES (1,?,?,?,?,?,'CONNECTING',NULL,0,NULL,?,?)
                   ON CONFLICT(singleton_id) DO UPDATE SET
                     session_uid=excluded.session_uid,
                     command_uid=excluded.command_uid,
                     device_name=excluded.device_name,
                     remote_port=excluded.remote_port,
                     expires_at=excluded.expires_at,
                     state='CONNECTING', failure_code=NULL,
                     attempt_count=0, next_attempt_at=NULL,
                     created_at=excluded.created_at,
                     updated_at=excluded.updated_at""",
                (
                    session_uid,
                    command_uid,
                    device_name,
                    remote_port,
                    expires_at,
                    now,
                    now,
                ),
            )
            self._create_remote_support_status_event_in_tx(
                self._conn,
                session_uid=session_uid,
                command_uid=command_uid,
                device_name=device_name,
                remote_port=remote_port,
                state="CONNECTING",
                failure_code=None,
            )
            return "ACCEPTED"

    def request_remote_support_close(
        self,
        session_uid: str,
        command_uid: str,
    ) -> str:
        _require_uuid4_local(session_uid, "session_uid")
        _require_uuid4_local(command_uid, "command_uid")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "NOT_FOUND"
            if row["state"] in REMOTE_SUPPORT_TERMINAL_STATES:
                return "ALREADY_TERMINAL"
            if row["state"] == "CLOSING":
                return "DUPLICATE"
            self._conn.execute(
                """UPDATE remote_support_session
                   SET command_uid=?, state='CLOSING', failure_code=NULL,
                       next_attempt_at=NULL, updated_at=?
                   WHERE singleton_id=1""",
                (command_uid, self._now()),
            )
            return "ACCEPTED"

    def get_remote_support_session(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
        return dict(row) if row else None

    def transition_remote_support_session(
        self,
        session_uid: str,
        state: str,
        *,
        failure_code: Optional[str] = None,
    ) -> str:
        if state not in {
            "CONNECTING",
            "OPEN",
            "CLOSED",
            "FAILED",
            "EXPIRED",
        }:
            raise ValueError("invalid remote support status state")
        if failure_code is not None and failure_code not in REMOTE_SUPPORT_FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        if state == "FAILED" and failure_code is None:
            raise ValueError("FAILED remote support state requires failure_code")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if row is None or row["session_uid"] != session_uid:
                return "STALE"
            if row["state"] in REMOTE_SUPPORT_TERMINAL_STATES:
                return "TERMINAL"
            if row["state"] == state and row["failure_code"] == failure_code:
                return "DUPLICATE"
            now = self._now()
            self._conn.execute(
                """UPDATE remote_support_session
                   SET state=?, failure_code=?, next_attempt_at=NULL,
                       attempt_count=CASE WHEN ?='OPEN' THEN 0 ELSE attempt_count END,
                       updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (state, failure_code, state, now, session_uid),
            )
            self._create_remote_support_status_event_in_tx(
                self._conn,
                session_uid=session_uid,
                command_uid=row["command_uid"],
                device_name=row["device_name"],
                remote_port=row["remote_port"],
                state=state,
                failure_code=failure_code,
            )
            return "ACCEPTED"

    def record_remote_support_retry(
        self,
        session_uid: str,
        *,
        failure_code: str,
        max_attempts: int,
    ) -> str:
        if failure_code not in REMOTE_SUPPORT_FAILURE_CODES:
            raise ValueError("invalid remote support failure code")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM remote_support_session WHERE singleton_id=1"
            ).fetchone()
            if (
                row is None
                or row["session_uid"] != session_uid
                or row["state"] in REMOTE_SUPPORT_TERMINAL_STATES
                or row["state"] == "CLOSING"
            ):
                return "STALE"
            attempts = int(row["attempt_count"]) + 1
            next_state = (
                "FAILED" if attempts >= max_attempts else "CONNECTING"
            )
            self._conn.execute(
                """UPDATE remote_support_session
                   SET state=?, failure_code=?, attempt_count=?,
                        next_attempt_at=NULL, updated_at=?
                   WHERE singleton_id=1 AND session_uid=?""",
                (
                    next_state,
                    failure_code,
                    attempts,
                    self._now(),
                    session_uid,
                ),
            )
            if row["state"] != next_state or row["failure_code"] != failure_code:
                self._create_remote_support_status_event_in_tx(
                    self._conn,
                    session_uid=session_uid,
                    command_uid=row["command_uid"],
                    device_name=row["device_name"],
                    remote_port=row["remote_port"],
                    state=next_state,
                    failure_code=failure_code,
                )
            return next_state

    def _create_remote_support_status_event_in_tx(
        self,
        conn,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        state: str,
        failure_code: Optional[str],
    ) -> None:
        event_uid = self._new_uid()
        sequence = self._next_seq(conn)
        payload = {
            "sessionUid": session_uid,
            "state": state,
            "remotePort": remote_port,
            "failureCode": failure_code,
        }
        envelope = build_event_envelope(
            event_uid=event_uid,
            device_name=device_name,
            edge_event_sequence=sequence,
            event_type="REMOTE_SUPPORT_TUNNEL_STATUS",
            target_type="DEVICE_ASSET",
            target_uid=device_name,
            command_uid=command_uid,
            delivery_class="RELIABLE_FACT",
            payload=payload,
        )
        self._insert_event(conn, envelope, "REMOTE_SUPPORT_TUNNEL_STATUS")

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("EdgeStore 已关闭")

    # ── 照片发件箱操作 ──

    def reserve_photo_captures(
        self,
        captures: list[dict],
    ) -> list[dict]:
        """Persist stable photo identities before touching a camera."""
        reserved = []
        with self.transaction():
            for capture in captures:
                existing = self._conn.execute(
                    """SELECT * FROM photo_outbox
                       WHERE work_type=? AND work_uid=? AND slot_name=?
                       ORDER BY tombstoned, created_at
                       LIMIT 1""",
                    (
                        capture["work_type"],
                        capture["work_uid"],
                        capture["slot_name"],
                    ),
                ).fetchone()
                if existing:
                    reserved.append(dict(existing))
                    continue
                self._conn.execute(
                    """INSERT INTO photo_outbox
                       (photo_uid, slot_name, local_path, cos_key,
                        state, work_uid, work_type, device_name)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        capture["photo_uid"],
                        capture["slot_name"],
                        capture["local_path"],
                        capture.get("cos_key"),
                        PHOTO_CAPTURE_PENDING,
                        capture["work_uid"],
                        capture["work_type"],
                        capture["device_name"],
                    ),
                )
                row = self._conn.execute(
                    """SELECT * FROM photo_outbox
                       WHERE photo_uid=?""",
                    (capture["photo_uid"],),
                ).fetchone()
                reserved.append(dict(row))
        return reserved

    def list_capture_pending_photos(
        self,
        limit: int = 100,
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE state=? AND tombstoned=0
                   ORDER BY created_at, slot_name
                   LIMIT ?""",
                (PHOTO_CAPTURE_PENDING, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_photo(self, photo_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE photo_uid=?""",
                (photo_uid,),
            ).fetchone()
        return dict(row) if row else None

    def mark_photo_captured(
        self,
        photo_uid: str,
        *,
        content_sha256: str,
        size_bytes: int,
        captured_at: str,
        captured_clock_quality: str,
    ) -> bool:
        if captured_clock_quality not in {
            "SYNCED", "ESTIMATED", "UNAVAILABLE"
        }:
            raise ValueError("captured clock quality is invalid")
        with self.transaction():
            updated = self._conn.execute(
                """UPDATE photo_outbox
                   SET state=?, content_sha256=?, size_bytes=?,
                       captured_at=?, captured_clock_quality=?,
                       last_error=NULL
                   WHERE photo_uid=? AND state=?
                     AND tombstoned=0""",
                (
                    PHOTO_PENDING,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    captured_clock_quality,
                    photo_uid,
                    PHOTO_CAPTURE_PENDING,
                ),
            )
            return updated.rowcount == 1

    def list_pending_photos(self, limit: int = 5) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE state = 'PENDING' AND tombstoned = 0
                     AND (
                       next_retry_at IS NULL
                       OR datetime(next_retry_at) <= datetime('now')
                     )
                   ORDER BY created_at LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recover_photo_upload_queue(self) -> int:
        """Drop in-memory grant assumptions after an Edge process restart."""
        with self.transaction():
            rows = self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', grant_request_event_uid=NULL,
                        grant_generation=grant_generation+1,
                        next_retry_at=NULL, url=NULL,
                        last_error='EDGE_RESTARTED'
                   WHERE state IN ('PENDING', 'UPLOADING')
                     AND tombstoned=0 AND work_type IS NOT NULL"""
            ).rowcount
            return rows

    def assign_photo_grant_request(
        self,
        photo_uids: list[str],
        event_uid: str,
    ) -> None:
        if not photo_uids:
            return
        placeholders = ",".join("?" for _ in photo_uids)
        with self.transaction():
            self._conn.execute(
                f"""UPDATE photo_outbox
                    SET grant_request_event_uid=?
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING'""",
                (event_uid, *photo_uids),
            )

    def ensure_photo_grant_request(
        self,
        photo_uids: list[str],
        event_uid: str,
        payload: dict,
        *,
        device_name: str,
        work_type: str,
        work_uid: str,
    ) -> str:
        """Atomically create/reuse one request event and assign its photos."""
        if not photo_uids:
            raise ValueError("photo_uids must not be empty")
        placeholders = ",".join("?" for _ in photo_uids)
        with self.transaction():
            rows = self._conn.execute(
                f"""SELECT photo_uid, grant_request_event_uid
                    FROM photo_outbox
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING' AND tombstoned=0""",
                tuple(photo_uids),
            ).fetchall()
            if len(rows) != len(set(photo_uids)):
                raise ValueError("photo grant request contains unavailable photo")
            existing = next(
                (
                    row["grant_request_event_uid"]
                    for row in rows
                    if row["grant_request_event_uid"]
                ),
                None,
            )
            selected_event_uid = existing or event_uid
            if existing is None:
                seq = self._next_seq(self._conn)
                event = build_event_envelope(
                    event_uid=event_uid,
                    device_name=device_name,
                    edge_event_sequence=seq,
                    event_type="PHOTO_UPLOAD_GRANT_REQUESTED",
                    target_type=work_type,
                    target_uid=work_uid,
                    payload=payload,
                )
                self._insert_event(
                    self._conn,
                    event,
                    "PHOTO_UPLOAD_GRANT_REQUESTED",
                )
            self._conn.execute(
                f"""UPDATE photo_outbox
                    SET grant_request_event_uid=?
                    WHERE photo_uid IN ({placeholders})
                      AND state='PENDING'""",
                (selected_event_uid, *photo_uids),
            )
            return selected_event_uid

    def invalidate_photo_grant(
        self,
        work_type: str,
        work_uid: str,
        error_code: str,
    ) -> int:
        with self.transaction():
            return self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', grant_request_event_uid=NULL,
                        grant_generation=grant_generation+1,
                        next_retry_at=NULL, url=NULL, last_error=?
                   WHERE work_type=? AND work_uid=?
                     AND state IN ('PENDING', 'UPLOADING')
                     AND tombstoned=0""",
                (error_code, work_type, work_uid),
            ).rowcount

    def mark_photo_uploading(self, photo_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='UPLOADING', url=NULL
                   WHERE photo_uid=?""",
                (photo_uid,),
            )

    def mark_photo_uploaded(
        self,
        photo_uid: str,
        cos_key: str,
        url: Optional[str] = None,
        status_event_uid: Optional[str] = None,
    ) -> None:
        if not url:
            raise ValueError("uploaded photo requires url")
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='UPLOADED', cos_key=?, url=?, uploaded_at=?,
                       status_event_uid=?, last_error=NULL,
                       next_retry_at=NULL
                   WHERE photo_uid=?""",
                (
                    cos_key,
                    url,
                    self._now(),
                    status_event_uid,
                    photo_uid,
                ),
            )

    def mark_photo_pending_retry(
        self,
        photo_uid: str,
        error_code: str = "PHOTO_UPLOAD_FAILED",
    ) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(120, 5 * (1 + retries))
        next_retry = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now_s + backoff))
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='PENDING', retry_count=retry_count+1,
                        next_retry_at=?, last_error=?, url=NULL
                   WHERE photo_uid=?""",
                (next_retry, error_code, photo_uid),
            )

    def mark_photo_dead(
        self,
        photo_uid: str,
        error_code: str = "PHOTO_UPLOAD_EXPIRED",
        status_event_uid: Optional[str] = None,
    ) -> None:
        with self.transaction():
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='DEAD', url=NULL, last_error=?,
                       status_event_uid=?
                   WHERE photo_uid=?""",
                (error_code, status_event_uid, photo_uid),
            )

    def record_photo_status(
        self,
        photo_uid: str,
        event_uid: str,
        payload: dict,
        *,
        state: str,
        cos_key: Optional[str] = None,
        url: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> str:
        """Atomically persist a terminal photo state and its reliable fact."""
        if state not in (PHOTO_UPLOADED, PHOTO_DEAD):
            raise ValueError("photo status state is invalid")
        if state == PHOTO_UPLOADED and not url:
            raise ValueError("uploaded photo status requires url")
        if state == PHOTO_DEAD and url is not None:
            raise ValueError("missing photo status must not contain url")
        with self.transaction():
            photo = self._conn.execute(
                """SELECT * FROM photo_outbox
                   WHERE photo_uid=? AND tombstoned=0""",
                (photo_uid,),
            ).fetchone()
            if not photo:
                return "UNKNOWN"
            if photo["status_event_uid"]:
                return "DUPLICATE"
            seq = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=photo["device_name"],
                edge_event_sequence=seq,
                event_type="PHOTO_STATUS_REPORTED",
                target_type=photo["work_type"],
                target_uid=photo["work_uid"],
                payload=payload,
            )
            self._insert_event(
                self._conn,
                event,
                "PHOTO_STATUS_REPORTED",
            )
            uploaded_at = self._now() if state == PHOTO_UPLOADED else None
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state=?, cos_key=COALESCE(?, cos_key),
                       url=?, uploaded_at=?,
                       status_event_uid=?, last_error=?,
                       next_retry_at=NULL
                   WHERE photo_uid=?""",
                (
                    state,
                    cos_key,
                    url,
                    uploaded_at,
                    event_uid,
                    error_code,
                    photo_uid,
                ),
            )
            return "ACCEPTED"

    def get_photos_by_work(self, work_uid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM photo_outbox WHERE work_uid=? AND tombstoned=0 ORDER BY slot_name",
                (work_uid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_confirmed_uploaded_photos(
        self,
        limit: int = 20,
    ) -> list[dict]:
        """List uploaded files whose status fact was confirmed by backend."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT p.*
                   FROM photo_outbox p
                   JOIN event_outbox e
                     ON e.event_uid = p.status_event_uid
                   WHERE p.state='UPLOADED'
                     AND p.tombstoned=0
                     AND e.state='CONFIRMED'
                     AND EXISTS (
                       SELECT 1
                       FROM confirmation_inbox c
                       WHERE c.event_uid=e.event_uid
                         AND c.outcome='BUSINESS_APPLIED'
                     )
                     AND EXISTS (
                       SELECT 1
                       FROM event_outbox completed
                       WHERE completed.work_uid=p.work_uid
                         AND completed.event_type=CASE p.work_type
                           WHEN 'DELIVERY_SESSION'
                             THEN 'DELIVERY_COMPLETE'
                           WHEN 'CLEAN_OPERATION'
                             THEN 'CLEAN_COMPLETE'
                         END
                         AND completed.state='CONFIRMED'
                     )
                   ORDER BY p.uploaded_at
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def tombstone_photo(self, photo_uid: str) -> bool:
        with self.transaction():
            row = self._conn.execute(
                """SELECT photo_uid FROM photo_outbox
                   WHERE photo_uid=? AND tombstoned=0""",
                (photo_uid,),
            ).fetchone()
            if not row:
                return False
            self._conn.execute(
                """INSERT INTO tombstones
                   (tombstone_uid, original_type, original_uid)
                   VALUES (?, 'PHOTO', ?)""",
                (self._new_uid(), photo_uid),
            )
            self._conn.execute(
                """UPDATE photo_outbox SET tombstoned=1
                   WHERE photo_uid=?""",
                (photo_uid,),
            )
            return True

    def mark_event_sending(self, event_uid: str, mqtt_msg_id: int) -> None:
        with self.transaction():
            self._conn.execute(
                """UPDATE event_outbox
                   SET state='SENDING', mqtt_msg_id=?
                   WHERE event_uid=? AND state='PENDING'
                     AND (
                       event_type='BUSINESS_CONFIRMATION_RECEIPT'
                       OR confirmed_at IS NULL
                     )""",
                (mqtt_msg_id, event_uid),
            )

    def mark_event_pending_retry(self, event_uid: str) -> None:
        now_s = int(time.time())
        row = self._conn.execute(
            "SELECT retry_count FROM event_outbox WHERE event_uid=?", (event_uid,)
        ).fetchone()
        retries = row["retry_count"] if row else 0
        backoff = min(60, 2 ** min(6, retries))
        next_retry = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(now_s + backoff),
        )
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='PENDING', "
                "retry_count=retry_count+1, next_retry_at=? "
                "WHERE event_uid=? AND state IN ('PENDING', 'SENDING') "
                "AND (event_type='BUSINESS_CONFIRMATION_RECEIPT' "
                "OR confirmed_at IS NULL)",
                (next_retry, event_uid),
            )

    def record_event_platform_reply(
        self,
        edge_event_sequence: int,
        code: int,
    ) -> Optional[str]:
        event = self.get_event_by_sequence(edge_event_sequence)
        if not event:
            return None
        event_uid = event["event_uid"]
        now = self._now()
        with self.transaction():
            if code in (0, 200):
                self._conn.execute(
                    """UPDATE event_outbox
                       SET last_platform_code=?,
                           platform_accepted_at=COALESCE(
                               platform_accepted_at, ?
                           ),
                           last_platform_reply_at=?
                       WHERE event_uid=?""",
                    (code, now, now, event_uid),
                )
            elif 2400 <= code <= 2499:
                self._conn.execute(
                    """UPDATE event_outbox
                       SET state='DEAD',
                           last_platform_code=?,
                           last_platform_reply_at=?,
                           next_retry_at=NULL
                       WHERE event_uid=?
                         AND state<>'CONFIRMED'
                         AND (
                           event_type='BUSINESS_CONFIRMATION_RECEIPT'
                           OR confirmed_at IS NULL
                         )""",
                    (code, now, event_uid),
                )
            else:
                self._conn.execute(
                    """UPDATE event_outbox
                       SET last_platform_code=?,
                           last_platform_reply_at=?
                       WHERE event_uid=?""",
                    (code, now, event_uid),
                )
        if code not in (0, 200) and not 2400 <= code <= 2499:
            self.mark_event_pending_retry(event_uid)
        return event_uid

    def mark_event_dead(self, event_uid: str) -> None:
        with self.transaction():
            self._conn.execute(
                "UPDATE event_outbox SET state='DEAD' WHERE event_uid=?", (event_uid,)
            )

    def mark_control_receipt_published(self, event_uid: str) -> bool:
        with self.transaction():
            cur = self._conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=? AND event_type=
                     'BUSINESS_CONFIRMATION_RECEIPT'""",
                (EVENT_CONFIRMED, self._now(), event_uid),
            )
            return cur.rowcount > 0

    def list_unconfirmed_events(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event_outbox WHERE state='SENDING' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def release_work_slot(self, work_uid: str) -> bool:
        with self.transaction():
            conn = self._conn
            slot = conn.execute("SELECT work_uid FROM work_slot WHERE slot_id=1").fetchone()
            if not slot or slot["work_uid"] != work_uid:
                return False
            conn.execute(
                "UPDATE work_slot SET work_type='NONE', work_uid=NULL, work_state=NULL, port_no=NULL, context_json=NULL, updated_at=? WHERE slot_id=1",
                (self._now(),),
            )
            return True

    def get_work_slot(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
        if row and row["work_type"] != WORK_TYPE_NONE:
            result = dict(row)
            result["context"] = _json.loads(result["context_json"]) if result["context_json"] else {}
            return result
        return None

    def update_work_context(self, work_uid: str, context: dict) -> bool:
        with self.transaction():
            conn = self._conn
            row = conn.execute(
                "SELECT work_uid FROM work_slot WHERE slot_id=1 AND work_uid=?", (work_uid,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE work_slot SET context_json=?, updated_at=? WHERE slot_id=1",
                (_json.dumps(context, ensure_ascii=False), self._now()),
            )
            return True

    def complete_fixed_frame_work(
        self,
        *,
        work_type: str,
        work_uid: str,
        command_uid: str,
        command_result: dict,
        context: dict,
        observation: dict,
        event_uid: str,
        event_type: str,
        event_payload: dict,
        device_name: str,
        target_type: str,
        bag_baseline: Optional[dict] = None,
        fullness_transition: Optional[dict] = None,
    ) -> str:
        """Atomically finish a DD/EF work item and release the single slot."""
        with self.transaction():
            slot = self._conn.execute(
                """SELECT work_type, work_uid FROM work_slot
                   WHERE slot_id=1"""
            ).fetchone()
            if (
                not slot
                or slot["work_type"] != work_type
                or slot["work_uid"] != work_uid
            ):
                return "UNKNOWN"
            existing = self._conn.execute(
                "SELECT event_uid FROM event_outbox WHERE event_uid=?",
                (event_uid,),
            ).fetchone()
            if existing:
                return "DUPLICATE"
            sequence = self._next_seq(self._conn)
            event = build_event_envelope(
                event_uid=event_uid,
                device_name=device_name,
                edge_event_sequence=sequence,
                event_type=event_type,
                target_type=target_type,
                target_uid=work_uid,
                command_uid=command_uid,
                payload=event_payload,
            )
            self._insert_event(self._conn, event, event_type)
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL, result_json=?,
                       last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(command_result, ensure_ascii=False),
                    command_uid,
                ),
            )
            now = self._now()
            self._upsert_state(
                self._conn,
                "fixed_frame_latest_observation_json",
                _json.dumps(observation, ensure_ascii=False),
                now,
            )
            self._upsert_state(
                self._conn,
                (
                    f"port_{context['port_no']}_"
                    f"{work_type.lower()}_runtime_context_json"
                ),
                _json.dumps(context, ensure_ascii=False),
                now,
            )
            if bag_baseline is not None:
                self._upsert_bag_baseline_in_tx(
                    self._conn,
                    bag_baseline,
                )
            if fullness_transition is not None:
                self._apply_fullness_transition_in_tx(
                    self._conn,
                    fullness_transition,
                )
            if work_type == WORK_TYPE_CLEAN:
                self._set_clean_restart_interlock_in_tx(
                    self._conn,
                    int(context["port_no"]),
                    False,
                )
            self._conn.execute(
                """UPDATE work_slot
                   SET work_type='NONE', work_uid=NULL,
                       work_state=NULL, port_no=NULL,
                       context_json=NULL, updated_at=?
                   WHERE slot_id=1""",
                (now,),
            )
            return "ACCEPTED"

    def fail_fixed_frame_work(
        self,
        *,
        work_uid: str,
        command: dict,
        error_code: str,
        mcu_command_uid: Optional[str],
        stage: str = "FAILED",
    ) -> bool:
        """Atomically fail a fixed-frame work item without replaying it."""
        with self.transaction():
            slot = self._conn.execute(
                "SELECT work_uid FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if not slot or slot["work_uid"] != work_uid:
                return False
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (
                    self._now(),
                    error_code,
                    command["commandUid"],
                ),
            )
            if command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    stage,
                    mcu_command_uid=mcu_command_uid,
                    error_code=error_code,
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
            self._conn.execute(
                """UPDATE work_slot
                   SET work_type='NONE', work_uid=NULL,
                       work_state=NULL, port_no=NULL,
                       context_json=NULL, updated_at=?
                   WHERE slot_id=1""",
                (self._now(),),
            )
            return True

    def mark_clean_window_expired_for_recovery(
        self,
        work_uid: str,
    ) -> str:
        """Atomically retain an expired clean for the original recovery flow."""

        error_code = "COMMAND_EXPIRED"
        with self.transaction():
            slot = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if (
                slot is None
                or slot["work_type"] != WORK_TYPE_CLEAN
                or slot["work_uid"] != work_uid
            ):
                return "STALE"
            context = (
                _json.loads(slot["context_json"])
                if slot["context_json"]
                else {}
            )
            command_uid = context.get("start_command_uid")
            if not command_uid:
                raise ValueError(
                    "active clean has no persisted start command"
                )
            row = self._conn.execute(
                """SELECT payload_json FROM command_inbox
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    "active clean start command is not persisted"
                )
            command = _json.loads(row["payload_json"])
            if (
                command.get("commandUid") != command_uid
                or command.get("commandType") != "START_CLEAN_OPERATION"
            ):
                raise ValueError(
                    "active clean start command identity is invalid"
                )

            context["phase"] = "CLEAN_RECOVERY_REQUIRED"
            context["recovery_error_code"] = error_code
            now = self._now()
            self._conn.execute(
                """UPDATE work_slot
                   SET work_state='RECOVERY_REQUIRED', context_json=?,
                       updated_at=?
                   WHERE slot_id=1 AND work_type=? AND work_uid=?""",
                (
                    _json.dumps(context, ensure_ascii=False),
                    now,
                    WORK_TYPE_CLEAN,
                    work_uid,
                ),
            )
            updated = self._conn.execute(
                """UPDATE command_inbox
                   SET state='FAILED', processed_at=?,
                       processing_started_at=NULL, last_error=?
                   WHERE command_uid=?""",
                (now, error_code, command_uid),
            )
            if updated.rowcount != 1:
                raise ValueError(
                    "active clean start command state could not be updated"
                )
            observation = self._record_command_observation_in_tx(
                self._conn,
                command,
                "FAILED",
                mcu_command_uid=context.get("start_mcu_command_uid"),
                error_code=error_code,
            )
            if observation == "CONFLICT":
                raise ValueError("command observation conflict")
            return "RECOVERY_REQUIRED"

    def abort_interrupted_work(self) -> dict[str, Any]:
        """Cancel one non-durable physical work slot after edge restart.

        A completion already present in the reliable outbox is authoritative:
        its event and photos remain replayable and no FAILED observation is
        generated. Otherwise the start command is failed with EDGE_RESTARTED,
        incomplete photo uploads are stopped, and an interrupted clean sets a
        persistent port interlock that only a later completed clean can clear.
        """
        with self.transaction():
            slot = self._conn.execute(
                "SELECT * FROM work_slot WHERE slot_id=1"
            ).fetchone()
            if not slot or slot["work_type"] == WORK_TYPE_NONE:
                return {"outcome": "NO_ACTIVE_WORK"}
            work_type = slot["work_type"]
            work_uid = slot["work_uid"]
            port_no = slot["port_no"]
            context = (
                _json.loads(slot["context_json"])
                if slot["context_json"]
                else {}
            )
            completion_type = {
                WORK_TYPE_DELIVERY: "DELIVERY_COMPLETE",
                WORK_TYPE_CLEAN: "CLEAN_COMPLETE",
                WORK_TYPE_FULLNESS: "FULLNESS_SAMPLE_COMPLETE",
                WORK_TYPE_BASELINE: "BASELINE_MEASUREMENT_COMPLETE",
            }.get(work_type)
            durable_completion = None
            if completion_type:
                durable_completion = self._conn.execute(
                    """SELECT event_uid FROM event_outbox
                       WHERE work_uid=? AND event_type=?
                         AND tombstoned=0
                       LIMIT 1""",
                    (work_uid, completion_type),
                ).fetchone()
            if durable_completion:
                if work_type == WORK_TYPE_CLEAN and port_no is not None:
                    self._set_clean_restart_interlock_in_tx(
                        self._conn, int(port_no), False
                    )
                self._clear_work_slot_in_tx(self._conn)
                return {
                    "outcome": "DURABLE_COMPLETION_PRESERVED",
                    "work_type": work_type,
                    "work_uid": work_uid,
                }

            command_uid = (
                context.get("start_command_uid")
                or context.get("command_uid")
            )
            command = None
            if command_uid:
                row = self._conn.execute(
                    "SELECT payload_json FROM command_inbox WHERE command_uid=?",
                    (command_uid,),
                ).fetchone()
                command = _json.loads(row["payload_json"]) if row else None
            command_observed = False
            if command and command.get("targetDeviceName"):
                observation = self._record_command_observation_in_tx(
                    self._conn,
                    command,
                    "FAILED",
                    mcu_command_uid=context.get("start_mcu_command_uid"),
                    error_code="EDGE_RESTARTED",
                )
                if observation == "CONFLICT":
                    raise ValueError("command observation conflict")
                command_observed = True
            if command:
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='FAILED', processed_at=?,
                           processing_started_at=NULL,
                           last_error='EDGE_RESTARTED'
                       WHERE command_uid=?""",
                    (self._now(), command_uid),
                )
            self._conn.execute(
                """UPDATE photo_outbox
                   SET state='DEAD', next_retry_at=NULL,
                       last_error='EDGE_RESTARTED'
                   WHERE work_uid=?
                     AND tombstoned=0
                     AND state NOT IN ('UPLOADED', 'DEAD')""",
                (work_uid,),
            )
            if work_type == WORK_TYPE_CLEAN and port_no is not None:
                self._set_clean_restart_interlock_in_tx(
                    self._conn, int(port_no), True
                )
            self._clear_work_slot_in_tx(self._conn)
            return {
                "outcome": "ABORTED",
                "work_type": work_type,
                "work_uid": work_uid,
                "command_observed": command_observed,
            }

    @staticmethod
    def _clear_work_slot_in_tx(conn) -> None:
        conn.execute(
            """UPDATE work_slot
               SET work_type='NONE', work_uid=NULL,
                   work_state=NULL, port_no=NULL,
                   context_json=NULL, updated_at=datetime('now')
               WHERE slot_id=1"""
        )

    @staticmethod
    def _clean_restart_interlock_key(port_no: int) -> str:
        return f"port_{port_no}_clean_restart_interlock"

    def _set_clean_restart_interlock_in_tx(
        self,
        conn,
        port_no: int,
        active: bool,
    ) -> None:
        self._upsert_state(
            conn,
            self._clean_restart_interlock_key(port_no),
            "true" if active else "false",
            self._now(),
        )

    def clean_restart_interlock_active(self, port_no: int) -> bool:
        return self.get_state(
            self._clean_restart_interlock_key(port_no),
            "false",
        ) == "true"

    def clear_clean_restart_interlock(self, port_no: int) -> None:
        with self.transaction():
            self._set_clean_restart_interlock_in_tx(
                self._conn, port_no, False
            )

    def get_bag_baseline(self, bag_uid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bag_baseline WHERE bag_uid=?",
                (bag_uid,),
            ).fetchone()
        return dict(row) if row else None

    def get_port_fullness_state(
        self,
        port_no: int,
        bag_uid: str,
    ) -> str:
        """Absence of a FULL fact is the deliberate NOT_FULL default."""
        with self._lock:
            row = self._conn.execute(
                """SELECT bag_uid, state
                   FROM port_fullness_state
                   WHERE port_no=?""",
                (port_no,),
            ).fetchone()
        if row is None or row["bag_uid"] != bag_uid:
            return "NOT_FULL"
        return row["state"]

    def _apply_fullness_transition_in_tx(
        self,
        conn,
        transition: dict,
    ) -> bool:
        port_no = transition["port_no"]
        bag_uid = transition["bag_uid"]
        desired_state = transition["state"]
        current = conn.execute(
            """SELECT bag_uid, state
               FROM port_fullness_state
               WHERE port_no=?""",
            (port_no,),
        ).fetchone()
        current_state = (
            current["state"]
            if current is not None and current["bag_uid"] == bag_uid
            else "NOT_FULL"
        )
        now = self._now()
        if current_state == desired_state:
            conn.execute(
                """INSERT INTO port_fullness_state
                   (port_no, bag_uid, state, last_state_change_uid,
                    last_event_uid, updated_at)
                   VALUES (?, ?, ?, NULL, NULL, ?)
                   ON CONFLICT(port_no) DO UPDATE SET
                     bag_uid=excluded.bag_uid,
                     state=excluded.state,
                     last_state_change_uid=CASE
                       WHEN port_fullness_state.bag_uid=excluded.bag_uid
                       THEN port_fullness_state.last_state_change_uid
                       ELSE NULL END,
                     last_event_uid=CASE
                       WHEN port_fullness_state.bag_uid=excluded.bag_uid
                       THEN port_fullness_state.last_event_uid
                       ELSE NULL END,
                     updated_at=excluded.updated_at""",
                (port_no, bag_uid, desired_state, now),
            )
            return False

        sequence = self._next_seq(conn)
        envelope = build_event_envelope(
            event_uid=transition["event_uid"],
            device_name=transition["device_name"],
            edge_event_sequence=sequence,
            event_type="FULLNESS_STATE_CHANGED",
            target_type="PORT_FULLNESS_STATE",
            target_uid=transition["state_change_uid"],
            command_uid=None,
            payload=transition["payload"],
        )
        self._insert_event(
            conn,
            envelope,
            "FULLNESS_STATE_CHANGED",
        )
        conn.execute(
            """INSERT INTO port_fullness_state
               (port_no, bag_uid, state, last_state_change_uid,
                last_event_uid, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(port_no) DO UPDATE SET
                 bag_uid=excluded.bag_uid,
                 state=excluded.state,
                 last_state_change_uid=excluded.last_state_change_uid,
                 last_event_uid=excluded.last_event_uid,
                 updated_at=excluded.updated_at""",
            (
                port_no,
                bag_uid,
                desired_state,
                transition["state_change_uid"],
                transition["event_uid"],
                now,
            ),
        )
        return True

    @staticmethod
    def _upsert_bag_baseline_in_tx(conn, baseline: dict) -> None:
        conn.execute(
            """INSERT INTO bag_baseline
               (bag_uid, weight_grams, source_kind,
                source_work_type, source_work_uid,
                source_mcu_boot_id, source_mcu_event_sequence,
                source_observed_at, measurement_uid, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(bag_uid) DO UPDATE SET
                 weight_grams=excluded.weight_grams,
                 source_kind=excluded.source_kind,
                 source_work_type=excluded.source_work_type,
                 source_work_uid=excluded.source_work_uid,
                 source_mcu_boot_id=excluded.source_mcu_boot_id,
                 source_mcu_event_sequence=
                   excluded.source_mcu_event_sequence,
                 source_observed_at=excluded.source_observed_at,
                 measurement_uid=excluded.measurement_uid,
                 updated_at=excluded.updated_at""",
            (
                baseline["bag_uid"],
                baseline["weight_grams"],
                baseline["source_kind"],
                baseline.get("source_work_type"),
                baseline.get("source_work_uid"),
                baseline.get("source_mcu_boot_id"),
                baseline.get("source_mcu_event_sequence"),
                baseline.get("source_observed_at"),
                baseline["measurement_uid"],
                baseline["updated_at"],
            ),
        )

    def complete_fixed_frame_local_result(
        self,
        *,
        result_type: str,
        result_key: str,
        command: dict,
        event_uid: str,
        event_type: str,
        target_type: str,
        target_uid: str,
        event_payload: dict,
        result: dict,
        bag_baseline: Optional[dict] = None,
    ) -> str:
        """Freeze a local fixed-frame result and its reliable event."""
        with self.transaction():
            existing = self._conn.execute(
                """SELECT event_uid, result_json
                   FROM fixed_frame_local_result
                   WHERE result_type=? AND result_key=?""",
                (result_type, result_key),
            ).fetchone()
            if existing:
                frozen_result = (
                    _json.loads(existing["result_json"])
                    if existing["result_json"]
                    else {}
                )
                self._conn.execute(
                    """UPDATE command_inbox
                       SET state='COMPLETED', processed_at=?,
                           processing_started_at=NULL,
                           result_json=?, last_error=NULL
                       WHERE command_uid=?""",
                    (
                        self._now(),
                        _json.dumps(
                            frozen_result,
                            ensure_ascii=False,
                        ),
                        command["commandUid"],
                    ),
                )
                return "DUPLICATE"
            sequence = self._next_seq(self._conn)
            envelope = build_event_envelope(
                event_uid=event_uid,
                device_name=command["targetDeviceName"],
                edge_event_sequence=sequence,
                event_type=event_type,
                target_type=target_type,
                target_uid=target_uid,
                command_uid=command["commandUid"],
                payload=event_payload,
            )
            self._insert_event(self._conn, envelope, event_type)
            if bag_baseline is not None:
                self._upsert_bag_baseline_in_tx(
                    self._conn,
                    bag_baseline,
                )
            self._conn.execute(
                """INSERT INTO fixed_frame_local_result
                   (result_type, result_key, command_uid, event_uid,
                    payload_json, result_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    result_type,
                    result_key,
                    command["commandUid"],
                    event_uid,
                    _json.dumps(event_payload, ensure_ascii=False),
                    _json.dumps(result, ensure_ascii=False),
                ),
            )
            self._conn.execute(
                """UPDATE command_inbox
                   SET state='COMPLETED', processed_at=?,
                       processing_started_at=NULL,
                       result_json=?, last_error=NULL
                   WHERE command_uid=?""",
                (
                    self._now(),
                    _json.dumps(result, ensure_ascii=False),
                    command["commandUid"],
                ),
            )
            return "ACCEPTED"

    def reserve_compat_mcu_event_sequence(self) -> int:
        with self.transaction():
            row = self._conn.execute(
                """SELECT state_value FROM device_state
                   WHERE state_key='fixed_frame_compat_event_sequence'"""
            ).fetchone()
            sequence = int(row["state_value"] if row else 0) + 1
            self._upsert_state(
                self._conn,
                "fixed_frame_compat_event_sequence",
                str(sequence),
                self._now(),
            )
            return sequence

    # ── 原子事务 4: 接收业务确认 ──

    def receive_business_confirmation(self, confirmation_uid: str, event_uid: str,
                                      outcome: str, payload: Optional[dict] = None) -> str:
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT confirmation_uid FROM confirmation_inbox WHERE confirmation_uid=?",
                (confirmation_uid,),
            ).fetchone()
            if existing:
                return "DUPLICATE"
            conn.execute(
                "INSERT INTO confirmation_inbox (confirmation_uid, event_uid, outcome, payload_json) VALUES (?,?,?,?)",
                (confirmation_uid, event_uid, outcome,
                 _json.dumps(payload, ensure_ascii=False) if payload else None),
            )
            conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=?""",
                (EVENT_CONFIRMED, self._now(), event_uid),
            )
            return "ACCEPTED"

    def receive_business_confirmation_and_create_receipt(
        self,
        command_uid: Optional[str] = None,
        confirmation_payload: Optional[dict] = None,
        device_name: str = "",
        *,
        command: Optional[dict] = None,
    ) -> str:
        """Persist I-045 confirmation and create the required receipt event.

        Returns ACCEPTED, DUPLICATE or REJECTED. A rejected confirmation is not
        persisted because it does not match a local reliable event.
        """

        if command is not None:
            command_uid = command["commandUid"]
            confirmation_payload = command["payload"]
            stable_command = dict(command)
            stable_command.pop("cosGrant", None)
        else:
            stable_command = {
                "commandUid": command_uid,
                "payload": confirmation_payload,
            }
        if not isinstance(confirmation_payload, dict) or not command_uid:
            return "REJECTED"
        confirmation_uid = confirmation_payload.get("confirmationUid")
        original_event_uid = confirmation_payload.get("originalEventUid")
        original_payload_sha256 = confirmation_payload.get("originalPayloadSha256")
        outcome = confirmation_payload.get("outcome")
        if not confirmation_uid or not original_event_uid or not original_payload_sha256 or not outcome:
            return "REJECTED"
        confirmation_sha256 = canonical_payload_sha256(stable_command)

        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                """SELECT command_uid, canonical_sha256,
                          payload_json, receipt_event_uid
                   FROM confirmation_inbox
                   WHERE confirmation_uid=?""",
                (confirmation_uid,),
            ).fetchone()
            if existing:
                stored_payload = (
                    _json.loads(existing["payload_json"])
                    if existing["payload_json"]
                    else None
                )
                same = (
                    existing["command_uid"] in (None, command_uid)
                    and stored_payload == confirmation_payload
                    and existing["canonical_sha256"] in (
                        None,
                        confirmation_sha256,
                    )
                )
                if not same:
                    return "CONFLICT"
                if existing["receipt_event_uid"]:
                    conn.execute(
                        """UPDATE event_outbox
                           SET state='PENDING', mqtt_msg_id=NULL,
                               next_retry_at=NULL, confirmed_at=NULL,
                               tombstoned=0
                           WHERE event_uid=? AND event_type=
                             'BUSINESS_CONFIRMATION_RECEIPT'""",
                        (existing["receipt_event_uid"],),
                    )
                return "DUPLICATE"

            event_confirmation = conn.execute(
                """SELECT confirmation_uid FROM confirmation_inbox
                   WHERE event_uid=?""",
                (original_event_uid,),
            ).fetchone()
            if event_confirmation:
                return "CONFLICT"
            event = conn.execute(
                "SELECT payload_json FROM event_outbox WHERE event_uid=?",
                (original_event_uid,),
            ).fetchone()
            if not event:
                return "REJECTED"
            event_json = _json.loads(event["payload_json"])
            if (
                event_json.get("deliveryClass") is not None
                and event_json.get("deliveryClass")
                != "RELIABLE_FACT"
            ):
                return "REJECTED"
            local_sha = event_json.get("payloadSha256")
            if local_sha != original_payload_sha256:
                return "REJECTED"

            seq = self._next_seq(conn)
            receipt = build_business_confirmation_receipt(
                device_name=device_name,
                command_uid=command_uid,
                confirmation_uid=confirmation_uid,
                original_event_uid=original_event_uid,
                original_payload_sha256=original_payload_sha256,
                outcome=outcome,
                edge_event_sequence=seq,
            )
            conn.execute(
                """INSERT INTO event_outbox
                   (event_uid, edge_event_sequence, event_type,
                    payload_json, work_uid)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    receipt["eventUid"],
                    seq,
                    "BUSINESS_CONFIRMATION_RECEIPT",
                    _json.dumps(receipt, ensure_ascii=False),
                    original_event_uid,
                ),
            )
            conn.execute(
                """INSERT INTO confirmation_inbox
                   (confirmation_uid, event_uid, outcome, payload_json,
                    command_uid, canonical_sha256, receipt_event_uid)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    confirmation_uid,
                    original_event_uid,
                    outcome,
                    _json.dumps(
                        confirmation_payload,
                        ensure_ascii=False,
                    ),
                    command_uid,
                    confirmation_sha256,
                    receipt["eventUid"],
                ),
            )
            conn.execute(
                """UPDATE event_outbox
                   SET state=?, confirmed_at=?, mqtt_msg_id=NULL,
                       next_retry_at=NULL
                   WHERE event_uid=?""",
                (
                    EVENT_CONFIRMED,
                    self._now(),
                    original_event_uid,
                ),
            )
            if outcome == "EVENT_QUARANTINED":
                self._upsert_state(
                    conn,
                    f"quarantined_event:{original_event_uid}",
                    _json.dumps(
                        {
                            "confirmationUid": confirmation_uid,
                            "errorCode": confirmation_payload.get(
                                "errorCode"
                            ),
                            "quarantineUid": confirmation_payload.get(
                                "quarantineUid"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    self._now(),
                )
            return "ACCEPTED"

    # ── 原子事务 5: 登记照片 ──

    def register_photo(
        self,
        photo_uid: str,
        slot_name: str,
        local_path: str,
        cos_key: Optional[str] = None,
        work_uid: Optional[str] = None,
        *,
        work_type: Optional[str] = None,
        device_name: Optional[str] = None,
        content_sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        captured_at: Optional[str] = None,
        captured_clock_quality: Optional[str] = None,
    ) -> str:
        if captured_at is None:
            if captured_clock_quality is not None:
                raise ValueError(
                    "captured clock quality requires captured_at"
                )
        else:
            # Calls predating EdgeStore v17 supplied a captured timestamp but
            # had no separate quality argument. Preserve their historical
            # semantics: an explicitly supplied capture time was trusted.
            captured_clock_quality = captured_clock_quality or "SYNCED"
            if captured_clock_quality not in {
                "SYNCED", "ESTIMATED", "UNAVAILABLE"
            }:
                raise ValueError("captured clock quality is invalid")
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM photo_outbox WHERE photo_uid=?", (photo_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            conn.execute(
                """INSERT INTO photo_outbox
                   (photo_uid, slot_name, local_path, cos_key, work_uid,
                    work_type, device_name, content_sha256, size_bytes,
                    captured_at, captured_clock_quality)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    photo_uid,
                    slot_name,
                    local_path,
                    cos_key,
                    work_uid,
                    work_type,
                    device_name,
                    content_sha256,
                    size_bytes,
                    captured_at,
                    captured_clock_quality,
                ),
            )
            return "ACCEPTED"

    # ── 原子事务 2: 接收 MCU 事件 ──

    def receive_mcu_event(self, event_uid: str, event_type: str, payload: dict,
                          work_uid: Optional[str] = None) -> str:
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            seq = self._next_seq(conn)
            conn.execute(
                "INSERT INTO event_outbox (event_uid, edge_event_sequence, event_type, payload_json, work_uid) VALUES (?,?,?,?,?)",
                (event_uid, seq, event_type, _json.dumps(payload, ensure_ascii=False), work_uid),
            )
            return "ACCEPTED"

    # ── 原子事务 3: 创建边缘事件 ──

    def create_edge_event(self, event_uid: str, event_type: str, payload: dict,
                          work_uid: Optional[str] = None,
                          work_state_update: Optional[dict] = None,
                          *,
                          device_name: Optional[str] = None,
                          target_type: Optional[str] = None,
                          target_uid: Optional[str] = None,
                          command_uid: Optional[str] = None,
                          delivery_class: str = "RELIABLE_FACT",
                          fullness_transition: Optional[dict] = None) -> str:
        with self.transaction():
            conn = self._conn
            existing = conn.execute(
                "SELECT state FROM event_outbox WHERE event_uid=?", (event_uid,)
            ).fetchone()
            if existing:
                return "DUPLICATE"
            seq = self._next_seq(conn)
            stored_payload = payload
            if device_name and target_type:
                stored_payload = build_event_envelope(
                    event_uid=event_uid,
                    device_name=device_name,
                    edge_event_sequence=seq,
                    event_type=event_type,
                    target_type=target_type,
                    target_uid=target_uid or work_uid or event_uid,
                    command_uid=command_uid,
                    delivery_class=delivery_class,
                    payload=payload,
                )
            conn.execute(
                "INSERT INTO event_outbox (event_uid, edge_event_sequence, event_type, payload_json, work_uid) VALUES (?,?,?,?,?)",
                (
                    event_uid,
                    seq,
                    event_type,
                    _json.dumps(stored_payload, ensure_ascii=False),
                    work_uid,
                ),
            )
            if event_type == "CLEAN_COMPLETE" and payload.get("portNo"):
                self._set_clean_restart_interlock_in_tx(
                    conn,
                    int(payload["portNo"]),
                    False,
                )
            if fullness_transition is not None:
                self._apply_fullness_transition_in_tx(
                    conn,
                    fullness_transition,
                )
            if work_state_update and work_uid:
                ctx = work_state_update.get("context", {})
                conn.execute(
                    "UPDATE work_slot SET work_state=?, context_json=?, updated_at=? WHERE work_uid=?",
                    (work_state_update.get("state"), _json.dumps(ctx, ensure_ascii=False),
                     self._now(), work_uid),
                )
            return "ACCEPTED"
