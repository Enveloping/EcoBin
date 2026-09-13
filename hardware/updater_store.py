"""Private durable safety state for the permanent device updater.

Schema version three may carry an application-level append-only extension for
the root-owned stage-four job-gate activation and safety-lock operations.  The
extension deliberately leaves the base schema version unchanged so the prior
default-off updater can still open the database during a software rollback.
The candidate remains fail-closed unless the process is started with its
explicit enable flag.  Software update execution and privileged-helper
mutations remain outside this module and disabled.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPDATER_SCHEMA_VERSION = 3
JOB_GATE_CONTROL_EXTENSION_VERSION = 1
UNKNOWN_EFFECT_RESOLUTION_EXTENSION_VERSION = 1
UPDATER_COMPONENT = "DEVICE_UPDATER"
MAX_RELEASE_VERSION_LENGTH = 32

JOB_GATE_STATES = frozenset({"OPEN", "DRAINING", "MAINTENANCE", "LOCKED"})
JOB_PERMIT_STATES = frozenset(
    {"GRANTED", "ACTIVE", "ABANDONED", "COMPLETED"}
)
PHYSICAL_ACTION_STATES = frozenset({"PREPARED", "ARMED", "CONFIRMED"})
JOB_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
JOB_WORK_TYPES = frozenset({"DELIVERY", "CLEAN", "FULLNESS", "BASELINE"})
PHYSICAL_ACTION_OUTCOMES = frozenset(
    {"EXECUTED", "NOT_EXECUTED", "FAILED_SAFE"}
)
LIVE_PHYSICAL_ACTION_OUTCOMES = frozenset({"EXECUTED", "FAILED_SAFE"})
PHYSICAL_ACTION_CONFIRMATION_BASES = frozenset(
    {
        "MCU_IDENTITY_BOUND_FACT",
        "LIVE_FIXED_FRAME_RESULT",
        "PREPARED_NOT_ARMED",
        "LIVE_DISPATCH_NOT_WRITTEN",
    }
)

_TOKEN_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_ACTION_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DISPATCH_ATTEMPT_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_AUTO_RESOLVABLE_LOCK_REASONS = frozenset(
    {
        "ACTIVE_JOB_RECONCILIATION",
        "ACTIVE_JOB_IN_PROGRESS",
        "PHYSICAL_ACTION_UNCONFIRMED",
        "DRAINING_ACTION_UNCONFIRMED",
        "STAGE4_CANDIDATE_DISABLED",
    }
)
_ACTIVE_JOB_LOCK_REASONS = frozenset(
    {
        "ACTIVE_JOB_RECONCILIATION",
        "ACTIVE_JOB_IN_PROGRESS",
        "PHYSICAL_ACTION_UNCONFIRMED",
        "DRAINING_ACTION_UNCONFIRMED",
    }
)

_PRISTINE_ROLLBACK_SCHEMA_TABLES = frozenset(
    {
        "schema_version",
        "updater_runtime_instance",
        "updater_management_state",
        "maintenance_lock",
        "job_permit",
        "physical_action_ledger",
        "physical_action_v2_evidence_quarantine",
        "job_gate_control_extension",
        "job_gate_control_operation",
        "operator_job_gate_lock",
        "physical_action_unknown_effect_extension",
        "physical_action_unknown_effect_resolution",
    }
)


class UpdaterStoreError(RuntimeError):
    """A stable failure that can safely cross the local-control boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PristineRollbackStateUsed(RuntimeError):
    """The database is valid, but permanent-layer safety history now exists."""


class UpdaterStore:
    """Single-owner SQLite store for updater and physical-safety facts."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        release_version: str,
        enable_stage4_candidate: bool = False,
        utc_now: Callable[[], datetime] | None = None,
        instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.path = Path(path)
        if self.path.name.casefold() == "edge.db":
            raise ValueError("updater store must not use the business edge.db")
        self.release_version = _require_release_version(release_version)
        if not isinstance(enable_stage4_candidate, bool):
            raise TypeError("stage-four candidate flag must be boolean")
        self.enable_stage4_candidate = enable_stage4_candidate
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._instance_uid_factory = instance_uid_factory
        self._connection: sqlite3.Connection | None = None
        self._runtime_instance_uid: str | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        """Open the private database, migrate atomically, and fail closed."""

        with self._lock:
            if self._connection is not None:
                return
            self._verify_private_parent()
            self._verify_existing_database_file()
            database_existed = self.path.exists()
            connection = sqlite3.connect(str(self.path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            try:
                self._verify_existing_database_file(
                    require_private_permissions=database_existed,
                )
                os.chmod(self.path, 0o600)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                version = self._read_schema_version(connection)
                if version is None:
                    self._create_v3_schema(connection)
                elif version == 1:
                    self._migrate_v1_to_v2(connection)
                    self._migrate_v2_to_v3(connection)
                elif version == 2:
                    self._migrate_v2_to_v3(connection)
                elif version != UPDATER_SCHEMA_VERSION:
                    raise RuntimeError("updater database schema is incompatible")
                self._verify_v3_schema(connection)
                self._ensure_job_gate_control_extension(connection)
                self._verify_job_gate_control_extension(connection)
                self._ensure_unknown_effect_resolution_extension(connection)
                self._verify_unknown_effect_resolution_extension(connection)
                self._verify_native_recovery_close_extension(connection)
                self._apply_runtime_candidate_posture(connection)
                self._verify_v3_invariants(connection)
                self._verify_job_gate_control_invariants(connection)

                instance_uid = _new_instance_uid(self._instance_uid_factory)
                started_at = _format_utc(self._utc_now())
                connection.execute(
                    """INSERT INTO updater_runtime_instance (
                           instance_uid, component, release_version, started_at
                       ) VALUES (?, ?, ?, ?)""",
                    (
                        instance_uid,
                        UPDATER_COMPONENT,
                        self.release_version,
                        started_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                connection.close()
                raise
            self._runtime_instance_uid = instance_uid
            self._connection = connection

    @staticmethod
    def _read_schema_version(connection: sqlite3.Connection) -> int | None:
        table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='schema_version'"""
        ).fetchone()
        if table is None:
            other_tables = connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
            if other_tables:
                raise RuntimeError("updater database schema is incompatible")
            return None
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version ORDER BY singleton_id"
        ).fetchall()
        if len(rows) != 1 or rows[0]["singleton_id"] != 1:
            raise RuntimeError("updater database schema is incompatible")
        version = rows[0]["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise RuntimeError("updater database schema is incompatible")
        return version

    def _create_v2_schema(self, connection: sqlite3.Connection) -> None:
        for statement in self._v2_schema_statements():
            connection.execute(statement)
        now = _format_utc(self._utc_now())
        connection.execute(
            "INSERT INTO schema_version(singleton_id, version) VALUES (1, 2)"
        )
        connection.execute(
            """INSERT INTO updater_management_state (
                   singleton_id, management_state_sequence,
                   stage4_candidate_enabled, updates_enabled,
                   job_gate_mode, job_gate_state, maintenance_state,
                   reconciliation_required, block_reason_code,
                   business_update_enabled, mcu_update_enabled,
                   created_at, updated_at
               ) VALUES (1, 1, 0, 0, 'DISABLED', 'LOCKED', 'LOCKED',
                         0, 'STAGE4_CANDIDATE_DISABLED', 0, 0, ?, ?)""",
            (now, now),
        )

    def _create_v3_schema(self, connection: sqlite3.Connection) -> None:
        """Create legacy schema three through the audited v2 migration path."""

        self._create_v2_schema(connection)
        self._migrate_v2_to_v3(connection)

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        """Replace the v1 fixed posture inside the caller's one transaction."""

        try:
            rows = connection.execute(
                """SELECT singleton_id, management_state_sequence,
                          updates_enabled, job_gate_mode, maintenance_state,
                          business_update_enabled, mcu_update_enabled,
                          created_at, updated_at
                   FROM updater_management_state"""
            ).fetchall()
            runtime_table = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='updater_runtime_instance'"""
            ).fetchone()
        except sqlite3.DatabaseError as error:
            raise RuntimeError("updater database schema is incompatible") from error
        if runtime_table is None or len(rows) != 1:
            raise RuntimeError("updater database schema is incompatible")
        row = rows[0]
        if tuple(row[key] for key in (
            "singleton_id",
            "updates_enabled",
            "job_gate_mode",
            "maintenance_state",
            "business_update_enabled",
            "mcu_update_enabled",
        )) != (1, 0, "NOT_ENFORCED_STAGE3", "IDLE", 0, 0):
            raise RuntimeError("updater database schema is incompatible")
        sequence = row["management_state_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise RuntimeError("updater database schema is incompatible")

        connection.execute("ALTER TABLE updater_management_state RENAME TO updater_management_state_v1")
        for statement in self._v2_schema_statements(include_existing_base=False):
            connection.execute(statement)
        now = _format_utc(self._utc_now())
        connection.execute(
            """INSERT INTO updater_management_state (
                   singleton_id, management_state_sequence,
                   stage4_candidate_enabled, updates_enabled,
                   job_gate_mode, job_gate_state, maintenance_state,
                   reconciliation_required, block_reason_code,
                   business_update_enabled, mcu_update_enabled,
                   created_at, updated_at
               ) VALUES (1, ?, 0, 0, 'DISABLED', 'LOCKED', 'LOCKED',
                         0, 'STAGE4_CANDIDATE_DISABLED', 0, 0, ?, ?)""",
            (sequence + 1, row["created_at"], now),
        )
        connection.execute("DROP TABLE updater_management_state_v1")
        connection.execute(
            "UPDATE schema_version SET version=2 WHERE singleton_id=1"
        )

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        """Add a non-authorizing PREPARED state and a runtime-bound ARM."""

        self._verify_v2_schema(connection)
        connection.execute(
            "ALTER TABLE physical_action_ledger RENAME TO physical_action_ledger_v2"
        )
        connection.execute(self._v3_physical_action_schema_statement())
        connection.execute(
            """INSERT INTO physical_action_ledger (
                   ledger_sequence, action_uid, permit_uid, work_uid,
                   command_uid, action_key, action_kind,
                   action_digest_sha256, state, dispatch_mode, arm_uid,
                   arm_runtime_instance_uid,
                   dispatch_attempt_token_sha256, receipt_uid,
                   confirmed_outcome, confirmation_basis,
                   evidence_digest_sha256, created_at, armed_at,
                   confirmed_at, updated_at
               )
               SELECT ledger_sequence, action_uid, permit_uid, work_uid,
                      command_uid, action_key, action_kind,
                      action_digest_sha256, 'ARMED',
                      'LEGACY_V2_UNCERTAIN', arm_uid, NULL, NULL,
                      NULL, NULL, NULL, NULL, created_at, armed_at,
                      NULL, updated_at
               FROM physical_action_ledger_v2"""
        )
        connection.execute(self._v3_legacy_evidence_schema_statement())
        quarantined_at = _format_utc(self._utc_now())
        connection.execute(
            """INSERT INTO physical_action_v2_evidence_quarantine (
                   action_uid, legacy_receipt_uid, legacy_outcome,
                   legacy_evidence_digest_sha256, legacy_confirmed_at,
                   quarantined_at
               )
               SELECT action_uid, receipt_uid, confirmed_outcome,
                      evidence_digest_sha256, confirmed_at, ?
               FROM physical_action_ledger_v2
               WHERE state='CONFIRMED'""",
            (quarantined_at,),
        )
        connection.execute("DROP TABLE physical_action_ledger_v2")
        connection.execute(
            "UPDATE schema_version SET version=3 WHERE singleton_id=1"
        )

    @staticmethod
    def _v2_schema_statements(*, include_existing_base: bool = True) -> tuple[str, ...]:
        base = (
            """CREATE TABLE schema_version (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                version INTEGER NOT NULL CHECK (version > 0)
            )""",
            """CREATE TABLE updater_runtime_instance (
                instance_uid TEXT PRIMARY KEY,
                component TEXT NOT NULL CHECK (component = 'DEVICE_UPDATER'),
                release_version TEXT NOT NULL
                    CHECK (length(release_version) BETWEEN 1 AND 32),
                started_at TEXT NOT NULL
            )""",
        ) if include_existing_base else ()
        return base + (
            """CREATE TABLE updater_management_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                management_state_sequence INTEGER NOT NULL
                    CHECK (management_state_sequence >= 1),
                stage4_candidate_enabled INTEGER NOT NULL
                    CHECK (stage4_candidate_enabled IN (0, 1)),
                updates_enabled INTEGER NOT NULL CHECK (updates_enabled = 0),
                job_gate_mode TEXT NOT NULL
                    CHECK (job_gate_mode IN ('DISABLED', 'ENFORCED')),
                job_gate_state TEXT NOT NULL
                    CHECK (job_gate_state IN
                           ('OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED')),
                maintenance_state TEXT NOT NULL
                    CHECK (maintenance_state IN
                           ('IDLE', 'DRAINING', 'MAINTENANCE', 'LOCKED')),
                reconciliation_required INTEGER NOT NULL
                    CHECK (reconciliation_required IN (0, 1)),
                block_reason_code TEXT,
                business_update_enabled INTEGER NOT NULL
                    CHECK (business_update_enabled = 0),
                mcu_update_enabled INTEGER NOT NULL
                    CHECK (mcu_update_enabled = 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK ((stage4_candidate_enabled = 0
                        AND job_gate_mode = 'DISABLED'
                        AND job_gate_state = 'LOCKED')
                       OR
                       (stage4_candidate_enabled = 1
                        AND job_gate_mode = 'ENFORCED')),
                CHECK ((job_gate_state = 'OPEN' AND maintenance_state = 'IDLE'
                        AND reconciliation_required = 0
                        AND block_reason_code IS NULL)
                       OR job_gate_state <> 'OPEN'),
                CHECK ((job_gate_state = 'DRAINING'
                        AND maintenance_state = 'DRAINING')
                       OR job_gate_state <> 'DRAINING'),
                CHECK ((job_gate_state = 'MAINTENANCE'
                        AND maintenance_state = 'MAINTENANCE')
                       OR job_gate_state <> 'MAINTENANCE'),
                CHECK ((job_gate_state = 'LOCKED'
                        AND maintenance_state = 'LOCKED')
                       OR job_gate_state <> 'LOCKED')
            )""",
            """CREATE TABLE maintenance_lock (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                owner_update_uid TEXT NOT NULL,
                maintenance_type TEXT NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN
                    ('DRAINING', 'MAINTENANCE', 'RECOVERY', 'LOCKED')),
                fence_token INTEGER NOT NULL CHECK (fence_token >= 1),
                acquired_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE job_permit (
                permit_uid TEXT PRIMARY KEY,
                work_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                work_type TEXT NOT NULL,
                request_digest_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN
                    ('GRANTED', 'ACTIVE', 'ABANDONED', 'COMPLETED')),
                grant_gate_sequence INTEGER NOT NULL CHECK (grant_gate_sequence >= 1),
                begin_uid TEXT UNIQUE,
                permit_digest_sha256 TEXT,
                disposition_uid TEXT UNIQUE,
                abandon_evidence_sha256 TEXT,
                completion_uid TEXT UNIQUE,
                completion_outcome TEXT,
                completion_digest_sha256 TEXT,
                created_at TEXT NOT NULL,
                begun_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                CHECK ((state = 'GRANTED' AND begin_uid IS NULL
                        AND disposition_uid IS NULL AND completion_uid IS NULL)
                       OR state <> 'GRANTED'),
                CHECK ((state = 'ACTIVE' AND begin_uid IS NOT NULL
                        AND disposition_uid IS NULL AND completion_uid IS NULL)
                       OR state <> 'ACTIVE'),
                CHECK ((state = 'ABANDONED' AND disposition_uid IS NOT NULL
                        AND abandon_evidence_sha256 IS NOT NULL)
                       OR state <> 'ABANDONED'),
                CHECK ((state = 'COMPLETED' AND completion_uid IS NOT NULL
                        AND completion_outcome IS NOT NULL
                        AND completion_digest_sha256 IS NOT NULL)
                       OR state <> 'COMPLETED')
            )""",
            """CREATE UNIQUE INDEX one_nonterminal_job_permit
               ON job_permit((1)) WHERE state IN ('GRANTED', 'ACTIVE')""",
            """CREATE TABLE physical_action_ledger (
                ledger_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                action_uid TEXT NOT NULL UNIQUE,
                permit_uid TEXT NOT NULL REFERENCES job_permit(permit_uid),
                work_uid TEXT NOT NULL,
                command_uid TEXT NOT NULL,
                action_key TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                action_digest_sha256 TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN
                    ('MAY_HAVE_EXECUTED', 'CONFIRMED')),
                arm_uid TEXT UNIQUE,
                receipt_uid TEXT UNIQUE,
                confirmed_outcome TEXT,
                evidence_digest_sha256 TEXT,
                created_at TEXT NOT NULL,
                armed_at TEXT,
                confirmed_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (work_uid, action_key),
                UNIQUE (command_uid, action_key),
                CHECK ((state = 'MAY_HAVE_EXECUTED' AND arm_uid IS NOT NULL
                        AND receipt_uid IS NULL)
                       OR state <> 'MAY_HAVE_EXECUTED'),
                CHECK ((state = 'CONFIRMED' AND arm_uid IS NOT NULL
                        AND receipt_uid IS NOT NULL
                        AND confirmed_outcome IS NOT NULL
                        AND evidence_digest_sha256 IS NOT NULL)
                       OR state <> 'CONFIRMED')
            )""",
        )

    @staticmethod
    def _v3_physical_action_schema_statement() -> str:
        return """CREATE TABLE physical_action_ledger (
            ledger_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            action_uid TEXT NOT NULL UNIQUE,
            permit_uid TEXT NOT NULL REFERENCES job_permit(permit_uid),
            work_uid TEXT NOT NULL,
            command_uid TEXT NOT NULL,
            action_key TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            action_digest_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN
                ('PREPARED', 'ARMED', 'CONFIRMED')),
            dispatch_mode TEXT NOT NULL CHECK (dispatch_mode IN
                ('PREPARED_ONLY', 'TWO_PHASE_V3',
                 'LEGACY_V2_UNCERTAIN')),
            arm_uid TEXT UNIQUE,
            arm_runtime_instance_uid TEXT REFERENCES
                updater_runtime_instance(instance_uid),
            dispatch_attempt_token_sha256 TEXT UNIQUE,
            receipt_uid TEXT UNIQUE,
            confirmed_outcome TEXT,
            confirmation_basis TEXT CHECK (confirmation_basis IS NULL OR
                confirmation_basis IN
                    ('MCU_IDENTITY_BOUND_FACT', 'LIVE_FIXED_FRAME_RESULT',
                     'PREPARED_NOT_ARMED',
                     'LIVE_DISPATCH_NOT_WRITTEN')),
            evidence_digest_sha256 TEXT,
            created_at TEXT NOT NULL,
            armed_at TEXT,
            confirmed_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE (work_uid, action_key),
            UNIQUE (command_uid, action_key),
            CHECK (
                (state = 'PREPARED'
                 AND dispatch_mode = 'PREPARED_ONLY'
                 AND arm_uid IS NULL
                 AND arm_runtime_instance_uid IS NULL
                 AND dispatch_attempt_token_sha256 IS NOT NULL
                 AND receipt_uid IS NULL
                 AND confirmed_outcome IS NULL
                 AND confirmation_basis IS NULL
                 AND evidence_digest_sha256 IS NULL
                 AND armed_at IS NULL
                 AND confirmed_at IS NULL)
                OR
                (state = 'ARMED'
                 AND receipt_uid IS NULL
                 AND confirmed_outcome IS NULL
                 AND confirmation_basis IS NULL
                 AND evidence_digest_sha256 IS NULL
                 AND confirmed_at IS NULL
                 AND (
                    (dispatch_mode = 'TWO_PHASE_V3'
                     AND arm_uid IS NULL
                     AND arm_runtime_instance_uid IS NOT NULL
                     AND dispatch_attempt_token_sha256 IS NOT NULL
                     AND armed_at IS NOT NULL)
                    OR
                    (dispatch_mode = 'LEGACY_V2_UNCERTAIN'
                     AND arm_uid IS NOT NULL
                     AND arm_runtime_instance_uid IS NULL
                     AND dispatch_attempt_token_sha256 IS NULL
                     AND armed_at IS NOT NULL)))
                OR
                (state = 'CONFIRMED'
                 AND receipt_uid IS NOT NULL
                 AND confirmed_outcome IS NOT NULL
                 AND confirmation_basis IS NOT NULL
                 AND evidence_digest_sha256 IS NOT NULL
                 AND confirmed_at IS NOT NULL
                 AND (
                    (dispatch_mode = 'PREPARED_ONLY'
                     AND arm_uid IS NULL
                     AND arm_runtime_instance_uid IS NULL
                     AND dispatch_attempt_token_sha256 IS NOT NULL
                     AND armed_at IS NULL
                     AND confirmation_basis = 'PREPARED_NOT_ARMED'
                     AND confirmed_outcome = 'NOT_EXECUTED')
                    OR
                    (dispatch_mode = 'TWO_PHASE_V3'
                     AND arm_uid IS NULL
                     AND arm_runtime_instance_uid IS NOT NULL
                     AND dispatch_attempt_token_sha256 IS NOT NULL
                     AND armed_at IS NOT NULL
                     AND ((confirmation_basis =
                               'LIVE_DISPATCH_NOT_WRITTEN'
                           AND confirmed_outcome = 'NOT_EXECUTED')
                          OR (confirmation_basis =
                                  'MCU_IDENTITY_BOUND_FACT'
                              AND confirmed_outcome IN
                                  ('EXECUTED', 'FAILED_SAFE'))
                          OR (confirmation_basis =
                                  'LIVE_FIXED_FRAME_RESULT'
                              AND confirmed_outcome IN
                                  ('EXECUTED', 'FAILED_SAFE'))))
                    OR
                    (dispatch_mode = 'LEGACY_V2_UNCERTAIN'
                     AND arm_uid IS NOT NULL
                     AND arm_runtime_instance_uid IS NULL
                     AND dispatch_attempt_token_sha256 IS NULL
                     AND armed_at IS NOT NULL
                     AND confirmation_basis =
                             'MCU_IDENTITY_BOUND_FACT'
                     AND confirmed_outcome IN
                             ('EXECUTED', 'FAILED_SAFE')))))
        )"""

    @staticmethod
    def _v3_legacy_evidence_schema_statement() -> str:
        return """CREATE TABLE physical_action_v2_evidence_quarantine (
            action_uid TEXT PRIMARY KEY REFERENCES
                physical_action_ledger(action_uid),
            legacy_receipt_uid TEXT NOT NULL UNIQUE,
            legacy_outcome TEXT NOT NULL CHECK (legacy_outcome IN
                ('EXECUTED', 'NOT_EXECUTED', 'FAILED_SAFE')),
            legacy_evidence_digest_sha256 TEXT NOT NULL,
            legacy_confirmed_at TEXT NOT NULL,
            quarantined_at TEXT NOT NULL
        )"""

    @staticmethod
    def _job_gate_control_metadata_schema_statement() -> str:
        return """CREATE TABLE job_gate_control_extension (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            extension_version INTEGER NOT NULL
                CHECK (extension_version = 1),
            candidate_activation_state TEXT NOT NULL CHECK (
                candidate_activation_state IN ('REQUIRED', 'ACTIVE')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""

    @staticmethod
    def _job_gate_control_operation_schema_statement() -> str:
        return """CREATE TABLE job_gate_control_operation (
            operation_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_uid TEXT NOT NULL UNIQUE
                CHECK (length(operation_uid) = 36
                       AND operation_uid = lower(operation_uid)
                       AND operation_uid NOT GLOB '*[^0-9a-f-]*'),
            operation_kind TEXT NOT NULL CHECK (operation_kind IN
                ('INITIAL_ACTIVATION', 'SAFETY_LOCK')),
            evidence_digest_sha256 TEXT NOT NULL
                CHECK (length(evidence_digest_sha256) = 64
                       AND evidence_digest_sha256 =
                           lower(evidence_digest_sha256)
                       AND evidence_digest_sha256 NOT GLOB
                           '*[^0-9a-f]*'),
            expected_management_state_sequence INTEGER NOT NULL
                CHECK (expected_management_state_sequence >= 1),
            previous_management_state_sequence INTEGER NOT NULL
                CHECK (previous_management_state_sequence >= 1),
            resulting_management_state_sequence INTEGER NOT NULL
                CHECK (resulting_management_state_sequence >= 1),
            previous_job_gate_state TEXT NOT NULL CHECK (
                previous_job_gate_state IN
                    ('OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED')),
            previous_block_reason_code TEXT,
            resulting_job_gate_state TEXT NOT NULL CHECK (
                resulting_job_gate_state IN ('OPEN', 'LOCKED')),
            resulting_block_reason_code TEXT,
            state_changed INTEGER NOT NULL CHECK (state_changed IN (0, 1)),
            created_at TEXT NOT NULL,
            CHECK (expected_management_state_sequence =
                   previous_management_state_sequence),
            CHECK (
                (operation_kind = 'INITIAL_ACTIVATION'
                 AND previous_job_gate_state = 'LOCKED'
                 AND previous_block_reason_code =
                     'STAGE4_ACTIVATION_REQUIRED'
                 AND resulting_job_gate_state = 'OPEN'
                 AND resulting_block_reason_code IS NULL
                 AND state_changed = 1
                 AND resulting_management_state_sequence =
                     previous_management_state_sequence + 1)
                OR
                (operation_kind = 'SAFETY_LOCK'
                 AND resulting_job_gate_state = 'LOCKED'
                 AND resulting_block_reason_code = 'MANUAL_SAFETY_LOCK'
                 AND ((state_changed = 0
                       AND previous_job_gate_state = 'LOCKED'
                       AND previous_block_reason_code =
                           'MANUAL_SAFETY_LOCK'
                       AND resulting_management_state_sequence =
                           previous_management_state_sequence)
                      OR
                      (state_changed = 1
                       AND resulting_management_state_sequence =
                           previous_management_state_sequence + 1))))
        )"""

    @staticmethod
    def _operator_job_gate_lock_schema_statement() -> str:
        return """CREATE TABLE operator_job_gate_lock (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            operation_uid TEXT NOT NULL UNIQUE REFERENCES
                job_gate_control_operation(operation_uid),
            acquired_management_state_sequence INTEGER NOT NULL
                CHECK (acquired_management_state_sequence >= 1),
            underlying_job_gate_state TEXT NOT NULL CHECK (
                underlying_job_gate_state IN
                    ('OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED')),
            underlying_maintenance_state TEXT NOT NULL CHECK (
                underlying_maintenance_state IN
                    ('IDLE', 'DRAINING', 'MAINTENANCE', 'LOCKED')),
            underlying_reconciliation_required INTEGER NOT NULL CHECK (
                underlying_reconciliation_required IN (0, 1)),
            underlying_block_reason_code TEXT,
            underlying_maintenance_phase TEXT CHECK (
                underlying_maintenance_phase IS NULL OR
                underlying_maintenance_phase IN
                    ('DRAINING', 'MAINTENANCE', 'RECOVERY', 'LOCKED')),
            acquired_at TEXT NOT NULL
        )"""

    @staticmethod
    def _unknown_effect_resolution_metadata_schema_statement() -> str:
        return """CREATE TABLE physical_action_unknown_effect_extension (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            extension_version INTEGER NOT NULL
                CHECK (extension_version = 1),
            created_at TEXT NOT NULL
        )"""

    @staticmethod
    def _unknown_effect_resolution_schema_statement() -> str:
        return """CREATE TABLE physical_action_unknown_effect_resolution (
            resolution_uid TEXT PRIMARY KEY
                CHECK (length(resolution_uid) = 36
                       AND resolution_uid = lower(resolution_uid)
                       AND resolution_uid NOT GLOB '*[^0-9a-f-]*'),
            action_uid TEXT NOT NULL UNIQUE REFERENCES
                physical_action_ledger(action_uid),
            permit_uid TEXT NOT NULL,
            work_uid TEXT NOT NULL,
            command_uid TEXT NOT NULL,
            action_key TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            action_digest_sha256 TEXT NOT NULL CHECK (
                length(action_digest_sha256) = 64
                AND action_digest_sha256 = lower(action_digest_sha256)
                AND action_digest_sha256 NOT GLOB '*[^0-9a-f]*'),
            expected_ledger_sequence INTEGER NOT NULL
                CHECK (expected_ledger_sequence >= 1),
            resolution_state TEXT NOT NULL CHECK (
                resolution_state = 'UNKNOWN_EFFECT_QUARANTINED'),
            evidence_digest_sha256 TEXT NOT NULL CHECK (
                length(evidence_digest_sha256) = 64
                AND evidence_digest_sha256 = lower(evidence_digest_sha256)
                AND evidence_digest_sha256 NOT GLOB '*[^0-9a-f]*'),
            resolved_at TEXT NOT NULL
        )"""

    @staticmethod
    def _verify_v2_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version ORDER BY singleton_id"
        ).fetchall()
        if [(row[0], row[1]) for row in rows] != [(1, 2)]:
            raise RuntimeError("updater database schema is incompatible")
        expected = {
            "schema_version",
            "updater_runtime_instance",
            "updater_management_state",
            "maintenance_lock",
            "job_permit",
            "physical_action_ledger",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not expected.issubset(actual):
            raise RuntimeError("updater database schema is incompatible")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise RuntimeError("updater database integrity check failed")

    @staticmethod
    def _verify_v3_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version ORDER BY singleton_id"
        ).fetchall()
        if [(row[0], row[1]) for row in rows] != [(1, 3)]:
            raise RuntimeError("updater database schema is incompatible")
        expected = {
            "schema_version",
            "updater_runtime_instance",
            "updater_management_state",
            "maintenance_lock",
            "job_permit",
            "physical_action_ledger",
            "physical_action_v2_evidence_quarantine",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not expected.issubset(actual):
            raise RuntimeError("updater database schema is incompatible")
        action_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(physical_action_ledger)"
            ).fetchall()
        }
        if not {
            "state",
            "dispatch_mode",
            "arm_runtime_instance_uid",
            "dispatch_attempt_token_sha256",
            "confirmation_basis",
        }.issubset(action_columns):
            raise RuntimeError("updater database schema is incompatible")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise RuntimeError("updater database integrity check failed")

    def _ensure_job_gate_control_extension(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        expected = {
            "job_gate_control_extension",
            "job_gate_control_operation",
            "operator_job_gate_lock",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        present = expected.intersection(actual)
        if present and present != expected:
            raise RuntimeError(
                "updater job-gate control extension is incomplete"
            )
        if present:
            return
        for statement in (
            self._job_gate_control_metadata_schema_statement(),
            self._job_gate_control_operation_schema_statement(),
            self._operator_job_gate_lock_schema_statement(),
        ):
            connection.execute(statement)
        now = _format_utc(self._utc_now())
        connection.execute(
            """INSERT INTO job_gate_control_extension (
                   singleton_id, extension_version,
                   candidate_activation_state, created_at, updated_at
               ) VALUES (1, ?, 'REQUIRED', ?, ?)""",
            (
                JOB_GATE_CONTROL_EXTENSION_VERSION,
                now,
                now,
            ),
        )

    @staticmethod
    def _verify_job_gate_control_extension(
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """SELECT singleton_id, extension_version,
                      candidate_activation_state
               FROM job_gate_control_extension ORDER BY singleton_id"""
        ).fetchall()
        if [(row[0], row[1], row[2]) for row in rows] not in [
            [(1, JOB_GATE_CONTROL_EXTENSION_VERSION, "REQUIRED")],
            [(1, JOB_GATE_CONTROL_EXTENSION_VERSION, "ACTIVE")],
        ]:
            raise RuntimeError(
                "updater job-gate control extension is incompatible"
            )
        expected_schemas = {
            "job_gate_control_extension": (
                UpdaterStore._job_gate_control_metadata_schema_statement()
            ),
            "job_gate_control_operation": (
                UpdaterStore._job_gate_control_operation_schema_statement()
            ),
            "operator_job_gate_lock": (
                UpdaterStore._operator_job_gate_lock_schema_statement()
            ),
        }
        for table, expected_schema in expected_schemas.items():
            row = connection.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type='table' AND name=?""",
                (table,),
            ).fetchone()
            if (
                row is None
                or _normalize_schema_sql(row[0])
                != _normalize_schema_sql(expected_schema)
            ):
                raise RuntimeError(
                    "updater job-gate control extension is incompatible"
                )
        foreign_key_check = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_check:
            raise RuntimeError("updater database integrity check failed")

    def _ensure_unknown_effect_resolution_extension(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        expected = {
            "physical_action_unknown_effect_extension",
            "physical_action_unknown_effect_resolution",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        present = expected.intersection(actual)
        if present and present != expected:
            raise RuntimeError(
                "updater unknown-effect resolution extension is incomplete"
            )
        if present:
            return
        connection.execute(
            self._unknown_effect_resolution_metadata_schema_statement()
        )
        connection.execute(self._unknown_effect_resolution_schema_statement())
        connection.execute(
            """INSERT INTO physical_action_unknown_effect_extension (
                   singleton_id, extension_version, created_at
               ) VALUES (1, ?, ?)""",
            (
                UNKNOWN_EFFECT_RESOLUTION_EXTENSION_VERSION,
                _format_utc(self._utc_now()),
            ),
        )

    @staticmethod
    def _verify_unknown_effect_resolution_extension(
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """SELECT singleton_id, extension_version
               FROM physical_action_unknown_effect_extension
               ORDER BY singleton_id"""
        ).fetchall()
        if [(row[0], row[1]) for row in rows] != [
            (1, UNKNOWN_EFFECT_RESOLUTION_EXTENSION_VERSION)
        ]:
            raise RuntimeError(
                "updater unknown-effect resolution extension is incompatible"
            )
        expected_schemas = {
            "physical_action_unknown_effect_extension": (
                UpdaterStore._unknown_effect_resolution_metadata_schema_statement()
            ),
            "physical_action_unknown_effect_resolution": (
                UpdaterStore._unknown_effect_resolution_schema_statement()
            ),
        }
        for table, expected_schema in expected_schemas.items():
            row = connection.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type='table' AND name=?""",
                (table,),
            ).fetchone()
            if (
                row is None
                or _normalize_schema_sql(row[0])
                != _normalize_schema_sql(expected_schema)
            ):
                raise RuntimeError(
                    "updater unknown-effect resolution extension is incompatible"
                )
        invalid = connection.execute(
            """SELECT 1
               FROM physical_action_unknown_effect_resolution resolution
               LEFT JOIN physical_action_ledger action
                 ON action.action_uid=resolution.action_uid
               LEFT JOIN job_permit permit
                 ON permit.permit_uid=resolution.permit_uid
               WHERE action.action_uid IS NULL
                  OR action.state<>'ARMED'
                  OR action.permit_uid<>resolution.permit_uid
                  OR action.work_uid<>resolution.work_uid
                  OR action.command_uid<>resolution.command_uid
                  OR action.action_key<>resolution.action_key
                  OR action.action_kind<>resolution.action_kind
                  OR action.action_digest_sha256<>
                         resolution.action_digest_sha256
                  OR action.ledger_sequence<>
                         resolution.expected_ledger_sequence
                  OR permit.permit_uid IS NULL
                  OR (permit.state='COMPLETED'
                      AND permit.completion_outcome<>'CANCELLED')
               LIMIT 1"""
        ).fetchone()
        if invalid is not None:
            raise RuntimeError(
                "updater unknown-effect resolution evidence is incompatible"
            )
        foreign_key_check = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_check:
            raise RuntimeError("updater database integrity check failed")

    def _apply_runtime_candidate_posture(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        state = self._management_row(connection)
        unresolved_actions = self._count_unresolved_actions(connection)
        active_permits = self._count_nonterminal_permits(connection)
        maintenance_lock = connection.execute(
            "SELECT 1 FROM maintenance_lock WHERE singleton_id=1"
        ).fetchone()
        operator_lock = connection.execute(
            "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
        ).fetchone()
        now = _format_utc(self._utc_now())
        activation_state = self._candidate_activation_state(connection)
        if (
            (
                not self.enable_stage4_candidate
                or not state["stage4_candidate_enabled"]
            )
            and activation_state != "REQUIRED"
        ):
            # A disabled runtime invalidates the prior enable cycle.  The base
            # state check is equally important: an older rollback binary knows
            # nothing about this extension, but it still persists DISABLED in
            # the v3 base table.  A later forward start must honour that fact.
            connection.execute(
                """UPDATE job_gate_control_extension
                   SET candidate_activation_state='REQUIRED', updated_at=?
                   WHERE singleton_id=1""",
                (now,),
            )
            activation_state = "REQUIRED"
        preserved_explicit_lock = (
            state["job_gate_state"] == "LOCKED"
            and state["block_reason_code"] is not None
            and state["block_reason_code"]
            not in _AUTO_RESOLVABLE_LOCK_REASONS
            and state["block_reason_code"] != "STAGE4_CANDIDATE_DISABLED"
        )

        if not self.enable_stage4_candidate:
            reconciliation = bool(
                unresolved_actions or active_permits or maintenance_lock
                or operator_lock or state["reconciliation_required"]
            )
            reason = (
                "MANUAL_SAFETY_LOCK"
                if operator_lock
                else (
                    state["block_reason_code"]
                    if reconciliation and state["block_reason_code"]
                    else "STAGE4_CANDIDATE_DISABLED"
                )
            )
            desired = (0, "DISABLED", "LOCKED", "LOCKED", int(reconciliation), reason)
        elif operator_lock:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "MANUAL_SAFETY_LOCK",
            )
        elif unresolved_actions:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "PHYSICAL_ACTION_UNCONFIRMED",
            )
        elif active_permits:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "ACTIVE_JOB_RECONCILIATION",
            )
        elif maintenance_lock:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "MAINTENANCE_RECOVERY_REQUIRED",
            )
        elif activation_state == "REQUIRED":
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "STAGE4_ACTIVATION_REQUIRED",
            )
        elif preserved_explicit_lock:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                state["block_reason_code"],
            )
        elif (
            not state["stage4_candidate_enabled"]
            or state["block_reason_code"] == "STAGE4_CANDIDATE_DISABLED"
        ):
            # Merely starting the candidate binary must not activate the
            # physical-job boundary.  Stage three's low-privilege hardware
            # evidence is a separate, controlled prerequisite.  A migration
            # operator must use the evidence-bearing activation operation
            # after that evidence has been reviewed.
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                "STAGE4_ACTIVATION_REQUIRED",
            )
        else:
            desired = (
                1,
                "ENFORCED",
                state["job_gate_state"],
                state["maintenance_state"],
                state["reconciliation_required"],
                state["block_reason_code"],
            )

        current = tuple(state[key] for key in (
            "stage4_candidate_enabled",
            "job_gate_mode",
            "job_gate_state",
            "maintenance_state",
            "reconciliation_required",
            "block_reason_code",
        ))
        if current != desired:
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       stage4_candidate_enabled=?, job_gate_mode=?,
                       job_gate_state=?, maintenance_state=?,
                       reconciliation_required=?, block_reason_code=?,
                       updated_at=?
                   WHERE singleton_id=1""",
                (*desired, now),
            )

    @staticmethod
    def _verify_v3_invariants(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT * FROM updater_management_state"
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("updater management state is incompatible")
        state = rows[0]
        armed = connection.execute(
            """SELECT COUNT(*)
               FROM physical_action_ledger action
               WHERE action.state='ARMED'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM physical_action_unknown_effect_resolution resolution
                     WHERE resolution.action_uid=action.action_uid)"""
        ).fetchone()[0]
        if armed and (
            state["job_gate_state"] != "LOCKED"
            or not state["reconciliation_required"]
        ):
            raise RuntimeError("updater safety ledger is inconsistent")
        locks = connection.execute("SELECT COUNT(*) FROM maintenance_lock").fetchone()[0]
        if state["job_gate_state"] in {"DRAINING", "MAINTENANCE"} and locks != 1:
            raise RuntimeError("updater maintenance lock is inconsistent")

    @staticmethod
    def _verify_job_gate_control_invariants(
        connection: sqlite3.Connection,
    ) -> None:
        state = UpdaterStore._management_row(connection)
        activation_state = UpdaterStore._candidate_activation_state(connection)
        operations = connection.execute(
            """SELECT * FROM job_gate_control_operation
               ORDER BY operation_sequence"""
        ).fetchall()
        for operation in operations:
            try:
                parsed_uid = uuid.UUID(operation["operation_uid"])
            except (ValueError, AttributeError) as error:
                raise RuntimeError(
                    "updater job-gate evidence is incompatible"
                ) from error
            if (
                parsed_uid.version != 4
                or str(parsed_uid) != operation["operation_uid"]
                or _SHA256_PATTERN.fullmatch(
                    operation["evidence_digest_sha256"]
                ) is None
                or operation["expected_management_state_sequence"]
                != operation["previous_management_state_sequence"]
                or operation["state_changed"] not in (0, 1)
            ):
                raise RuntimeError(
                    "updater job-gate evidence is incompatible"
                )
            previous_sequence = operation[
                "previous_management_state_sequence"
            ]
            resulting_sequence = operation[
                "resulting_management_state_sequence"
            ]
            changed = operation["state_changed"] == 1
            if operation["operation_kind"] == "INITIAL_ACTIVATION":
                valid_transition = (
                    changed
                    and operation["previous_job_gate_state"] == "LOCKED"
                    and operation["previous_block_reason_code"]
                    == "STAGE4_ACTIVATION_REQUIRED"
                    and operation["resulting_job_gate_state"] == "OPEN"
                    and operation["resulting_block_reason_code"] is None
                    and resulting_sequence == previous_sequence + 1
                )
            elif operation["operation_kind"] == "SAFETY_LOCK":
                valid_transition = (
                    operation["resulting_job_gate_state"] == "LOCKED"
                    and operation["resulting_block_reason_code"]
                    == "MANUAL_SAFETY_LOCK"
                    and (
                        (changed and resulting_sequence == previous_sequence + 1)
                        or (
                            not changed
                            and operation["previous_job_gate_state"]
                            == "LOCKED"
                            and operation["previous_block_reason_code"]
                            == "MANUAL_SAFETY_LOCK"
                            and resulting_sequence == previous_sequence
                        )
                    )
                )
            else:
                valid_transition = False
            if not valid_transition:
                raise RuntimeError(
                    "updater job-gate evidence is incompatible"
                )
        activation_count = sum(
            operation["operation_kind"] == "INITIAL_ACTIVATION"
            for operation in operations
        )
        if (
            (activation_state == "ACTIVE" and activation_count == 0)
            or (
                activation_state == "REQUIRED"
                and state["job_gate_state"] == "OPEN"
            )
            or (
                not state["stage4_candidate_enabled"]
                and activation_state != "REQUIRED"
            )
        ):
            raise RuntimeError(
                "updater candidate activation state is inconsistent"
            )
        operator_locks = connection.execute(
            "SELECT * FROM operator_job_gate_lock"
        ).fetchall()
        if len(operator_locks) > 1:
            raise RuntimeError("updater operator safety lock is incompatible")
        if not operator_locks:
            return
        operator_lock = operator_locks[0]
        operation = connection.execute(
            """SELECT * FROM job_gate_control_operation
               WHERE operation_uid=?""",
            (operator_lock["operation_uid"],),
        ).fetchone()
        if (
            operator_lock["singleton_id"] != 1
            or operation is None
            or operation["operation_kind"] != "SAFETY_LOCK"
            or operator_lock["acquired_management_state_sequence"]
            != operation["resulting_management_state_sequence"]
            or operator_lock["underlying_job_gate_state"]
            != operation["previous_job_gate_state"]
            or operator_lock["underlying_block_reason_code"]
            != operation["previous_block_reason_code"]
            or state["job_gate_state"] != "LOCKED"
            or state["maintenance_state"] != "LOCKED"
            or not state["reconciliation_required"]
            or state["block_reason_code"] != "MANUAL_SAFETY_LOCK"
        ):
            raise RuntimeError("updater operator safety lock is incompatible")

    def get_status(self) -> dict[str, Any]:
        """Return actual persisted capability and fail-closed gate facts."""

        with self._lock:
            connection = self._require_connection()
            instance_uid = self._require_runtime_instance_uid()
            instance = connection.execute(
                """SELECT component, release_version, started_at
                   FROM updater_runtime_instance WHERE instance_uid=?""",
                (instance_uid,),
            ).fetchone()
            state = self._management_row(connection)
            activation_state = self._candidate_activation_state(connection)
            maintenance = connection.execute(
                """SELECT owner_update_uid, maintenance_type, phase, fence_token
                   FROM maintenance_lock WHERE singleton_id=1"""
            ).fetchone()
            if instance is None:
                raise RuntimeError("updater durable state is unavailable")
            return {
                "component": instance["component"],
                "schemaVersion": UPDATER_SCHEMA_VERSION,
                "jobGateControlExtensionVersion": (
                    JOB_GATE_CONTROL_EXTENSION_VERSION
                ),
                "candidateActivationState": activation_state,
                "runtimeInstanceUid": instance_uid,
                "releaseVersion": instance["release_version"],
                "startedAt": instance["started_at"],
                "managementStateSequence": state["management_state_sequence"],
                "stage4CandidateEnabled": bool(state["stage4_candidate_enabled"]),
                "updatesEnabled": False,
                "jobGateMode": state["job_gate_mode"],
                "jobGateState": state["job_gate_state"],
                "jobPermitRpcEnabled": bool(state["stage4_candidate_enabled"]),
                "maintenanceState": state["maintenance_state"],
                "maintenanceOwnerUid": (
                    maintenance["owner_update_uid"] if maintenance else None
                ),
                "maintenanceType": (
                    maintenance["maintenance_type"] if maintenance else None
                ),
                "maintenancePhase": (
                    maintenance["phase"] if maintenance else None
                ),
                "maintenanceFenceToken": (
                    maintenance["fence_token"] if maintenance else None
                ),
                "reconciliationRequired": bool(state["reconciliation_required"]),
                "blockReasonCode": state["block_reason_code"],
                "activeJobPermitCount": self._count_nonterminal_permits(connection),
                "unreconciledPhysicalActionCount": self._count_unresolved_actions(connection),
                "businessUpdateEnabled": False,
                "mcuUpdateEnabled": False,
                "privilegedHelperMutationEnabled": False,
            }

    def get_job_gate_reconciliation_status(self) -> dict[str, Any]:
        """Return the root operator's persisted gate and evidence view."""

        with self._lock:
            connection = self._require_connection()
            return self._job_gate_reconciliation_result(connection)

    def activate_stage4_job_gate(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform the exact evidence-bearing activation for this cycle."""

        operation_uid, evidence_digest, expected_sequence = (
            self._job_gate_operation_request(payload)
        )
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            duplicate = self._existing_job_gate_operation(
                connection,
                operation_uid=operation_uid,
                operation_kind="INITIAL_ACTIVATION",
                evidence_digest=evidence_digest,
                expected_sequence=expected_sequence,
            )
            if duplicate is not None:
                unchanged_activation = (
                    self._candidate_activation_state(connection) == "ACTIVE"
                    and state["management_state_sequence"]
                    == duplicate["resulting_management_state_sequence"]
                    and state["job_gate_state"] == "OPEN"
                    and state["block_reason_code"] is None
                )
                if unchanged_activation:
                    return self._job_gate_operation_result(
                        duplicate,
                        disposition="DUPLICATE",
                    )
                raise UpdaterStoreError(
                    "STAGE4_ACTIVATION_NOT_ALLOWED",
                    "the recorded activation no longer represents the current candidate state",
                )

            self._require_management_sequence(state, expected_sequence)
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock prevents initial activation",
                )
            if (
                self._candidate_activation_state(connection) != "REQUIRED"
                or state["job_gate_mode"] != "ENFORCED"
                or state["job_gate_state"] != "LOCKED"
                or state["maintenance_state"] != "LOCKED"
                or not state["reconciliation_required"]
                or state["block_reason_code"]
                != "STAGE4_ACTIVATION_REQUIRED"
            ):
                raise UpdaterStoreError(
                    "STAGE4_ACTIVATION_NOT_ALLOWED",
                    "the job gate is not awaiting its initial activation",
                )
            if connection.execute(
                "SELECT 1 FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "MAINTENANCE_RECOVERY_REQUIRED",
                    "a maintenance lock prevents initial activation",
                )
            if self._count_nonterminal_permits(connection):
                raise UpdaterStoreError(
                    "JOB_ACTIVE",
                    "an unfinished job prevents initial activation",
                )
            if self._count_unresolved_actions(connection):
                raise UpdaterStoreError(
                    "RECONCILIATION_REQUIRED",
                    "an unconfirmed physical action prevents initial activation",
                )

            now = _format_utc(self._utc_now())
            resulting_sequence = expected_sequence + 1
            updated = connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=?, job_gate_state='OPEN',
                       maintenance_state='IDLE', reconciliation_required=0,
                       block_reason_code=NULL, updated_at=?
                   WHERE singleton_id=1
                     AND management_state_sequence=?
                     AND stage4_candidate_enabled=1
                     AND job_gate_mode='ENFORCED'
                     AND job_gate_state='LOCKED'
                     AND maintenance_state='LOCKED'
                     AND reconciliation_required=1
                     AND block_reason_code='STAGE4_ACTIVATION_REQUIRED'""",
                (resulting_sequence, now, expected_sequence),
            )
            if updated.rowcount != 1:
                raise UpdaterStoreError(
                    "MANAGEMENT_SEQUENCE_MISMATCH",
                    "the updater management state changed before activation",
                )
            operation = self._insert_job_gate_operation(
                connection,
                operation_uid=operation_uid,
                operation_kind="INITIAL_ACTIVATION",
                evidence_digest=evidence_digest,
                expected_sequence=expected_sequence,
                previous_sequence=expected_sequence,
                resulting_sequence=resulting_sequence,
                previous_gate_state="LOCKED",
                previous_block_reason_code="STAGE4_ACTIVATION_REQUIRED",
                resulting_gate_state="OPEN",
                resulting_block_reason_code=None,
                state_changed=True,
                created_at=now,
            )
            activated = connection.execute(
                """UPDATE job_gate_control_extension
                   SET candidate_activation_state='ACTIVE', updated_at=?
                   WHERE singleton_id=1
                     AND candidate_activation_state='REQUIRED'""",
                (now,),
            )
            if activated.rowcount != 1:
                raise UpdaterStoreError(
                    "STAGE4_ACTIVATION_NOT_ALLOWED",
                    "the candidate activation state changed before activation",
                )
            return self._job_gate_operation_result(
                operation,
                disposition="ACCEPTED",
            )

    def lock_stage4_job_gate(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Immediately fail closed without erasing outstanding work facts."""

        operation_uid, evidence_digest, expected_sequence = (
            self._job_gate_operation_request(payload)
        )
        with self._transaction() as connection:
            duplicate = self._existing_job_gate_operation(
                connection,
                operation_uid=operation_uid,
                operation_kind="SAFETY_LOCK",
                evidence_digest=evidence_digest,
                expected_sequence=expected_sequence,
            )
            if duplicate is not None:
                return self._job_gate_operation_result(
                    duplicate,
                    disposition="DUPLICATE",
                )

            state = self._require_candidate(connection)
            self._require_management_sequence(state, expected_sequence)
            previous_gate_state = state["job_gate_state"]
            previous_reason = state["block_reason_code"]
            maintenance = connection.execute(
                """SELECT phase FROM maintenance_lock
                   WHERE singleton_id=1"""
            ).fetchone()
            operator_lock = connection.execute(
                """SELECT 1 FROM operator_job_gate_lock
                   WHERE singleton_id=1"""
            ).fetchone()
            now = _format_utc(self._utc_now())
            resulting_reason = "MANUAL_SAFETY_LOCK"
            changed = not (
                previous_gate_state == "LOCKED"
                and state["maintenance_state"] == "LOCKED"
                and state["reconciliation_required"]
                and previous_reason == resulting_reason
            )

            if changed:
                resulting_sequence = expected_sequence + 1
                updated = connection.execute(
                    """UPDATE updater_management_state
                       SET management_state_sequence=?,
                           job_gate_state='LOCKED',
                           maintenance_state='LOCKED',
                           reconciliation_required=1,
                           block_reason_code=?, updated_at=?
                       WHERE singleton_id=1
                         AND management_state_sequence=?
                         AND stage4_candidate_enabled=1""",
                    (
                        resulting_sequence,
                        resulting_reason,
                        now,
                        expected_sequence,
                    ),
                )
                if updated.rowcount != 1:
                    raise UpdaterStoreError(
                        "MANAGEMENT_SEQUENCE_MISMATCH",
                        "the updater management state changed before locking",
                    )
            else:
                resulting_sequence = expected_sequence

            operation = self._insert_job_gate_operation(
                connection,
                operation_uid=operation_uid,
                operation_kind="SAFETY_LOCK",
                evidence_digest=evidence_digest,
                expected_sequence=expected_sequence,
                previous_sequence=expected_sequence,
                resulting_sequence=resulting_sequence,
                previous_gate_state=previous_gate_state,
                previous_block_reason_code=previous_reason,
                resulting_gate_state="LOCKED",
                resulting_block_reason_code=resulting_reason,
                state_changed=changed,
                created_at=now,
            )
            if operator_lock is None:
                connection.execute(
                    """INSERT INTO operator_job_gate_lock (
                           singleton_id, operation_uid,
                           acquired_management_state_sequence,
                           underlying_job_gate_state,
                           underlying_maintenance_state,
                           underlying_reconciliation_required,
                           underlying_block_reason_code,
                           underlying_maintenance_phase,
                           acquired_at
                       ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        operation_uid,
                        resulting_sequence,
                        previous_gate_state,
                        state["maintenance_state"],
                        state["reconciliation_required"],
                        previous_reason,
                        maintenance["phase"] if maintenance else None,
                        now,
                    ),
                )
            return self._job_gate_operation_result(
                operation,
                disposition="ACCEPTED",
            )

    def transition_job_gate(
        self,
        target_state: str,
        *,
        owner_update_uid: str | None = None,
        maintenance_type: str | None = None,
        block_reason_code: str | None = None,
    ) -> dict[str, Any]:
        """Move the candidate gate through its explicit maintenance states."""

        target = _require_enum(target_state, JOB_GATE_STATES, "job gate state")
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            current = state["job_gate_state"]
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock requires an exact release operation",
                )
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or state["block_reason_code"] == "STAGE4_ACTIVATION_REQUIRED"
            ):
                # While the current runtime posture awaits evidence-bearing
                # activation, *all* generic transitions are forbidden.  The
                # persisted marker survives reasons temporarily replaced by
                # active-job or physical-action reconciliation; the reason
                # check is an additional fail-closed consistency guard.
                raise UpdaterStoreError(
                    "JOB_GATE_RELEASE_NOT_ALLOWED",
                    "job-gate activation requires the exact evidence-bearing operation",
                )
            if (
                target == "OPEN"
                and current == "LOCKED"
                and state["block_reason_code"]
                in {
                    "STAGE4_ACTIVATION_REQUIRED",
                    "MANUAL_SAFETY_LOCK",
                }
            ):
                raise UpdaterStoreError(
                    "JOB_GATE_RELEASE_NOT_ALLOWED",
                    "this job-gate lock requires an exact release operation",
                )
            allowed = {
                "OPEN": {"DRAINING", "LOCKED"},
                "DRAINING": {"OPEN", "MAINTENANCE", "LOCKED"},
                "MAINTENANCE": {"OPEN", "LOCKED"},
                "LOCKED": {"OPEN", "LOCKED"},
            }
            if target != current and target not in allowed[current]:
                raise UpdaterStoreError(
                    "JOB_GATE_TRANSITION_INVALID",
                    "job gate transition is not allowed",
                )
            now = _format_utc(self._utc_now())
            if target == "DRAINING":
                owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
                kind = _require_token(maintenance_type, "maintenanceType")
                existing = connection.execute(
                    "SELECT owner_update_uid, maintenance_type FROM maintenance_lock"
                ).fetchone()
                if existing and (existing[0], existing[1]) != (owner, kind):
                    raise UpdaterStoreError(
                        "MAINTENANCE_BUSY",
                        "another maintenance operation owns the device",
                    )
                if existing is None:
                    fence = state["management_state_sequence"] + 1
                    connection.execute(
                        """INSERT INTO maintenance_lock (
                               singleton_id, owner_update_uid, maintenance_type,
                               phase, fence_token, acquired_at, updated_at
                           ) VALUES (1, ?, ?, 'DRAINING', ?, ?, ?)""",
                        (owner, kind, fence, now, now),
                    )
            elif target == "MAINTENANCE":
                lock = connection.execute(
                    "SELECT owner_update_uid, maintenance_type FROM maintenance_lock"
                ).fetchone()
                if lock is None:
                    raise UpdaterStoreError(
                        "MAINTENANCE_LOCK_REQUIRED",
                        "maintenance lock is required",
                    )
                if (
                    self._count_nonterminal_permits(connection)
                    or self._count_unresolved_actions(connection)
                ):
                    raise UpdaterStoreError(
                        "JOB_DRAIN_INCOMPLETE",
                        "device work has not drained before maintenance",
                    )
                connection.execute(
                    """UPDATE maintenance_lock
                       SET phase='MAINTENANCE', updated_at=? WHERE singleton_id=1""",
                    (now,),
                )
            elif target == "OPEN":
                if self._count_unresolved_actions(connection):
                    raise UpdaterStoreError(
                        "RECONCILIATION_REQUIRED",
                        "unconfirmed physical action keeps the job gate locked",
                    )
                if self._count_nonterminal_permits(connection):
                    raise UpdaterStoreError(
                        "JOB_ACTIVE",
                        "an unfinished job keeps the job gate closed",
                    )
                maintenance = connection.execute(
                    "SELECT 1 FROM maintenance_lock WHERE singleton_id=1"
                ).fetchone()
                if maintenance is not None:
                    # Stage four does not yet expose the update-owned,
                    # evidence-bearing release operation.  A generic gate
                    # transition must never erase a maintenance fence after
                    # cancellation, failure, or power loss.
                    raise UpdaterStoreError(
                        "MAINTENANCE_RECOVERY_REQUIRED",
                        "maintenance ownership requires explicit recovery",
                    )
            elif target == "LOCKED":
                reason = _require_optional_token(
                    block_reason_code,
                    "blockReasonCode",
                ) or "MANUAL_SAFETY_LOCK"
                connection.execute(
                    """UPDATE maintenance_lock SET phase='LOCKED', updated_at=?
                       WHERE singleton_id=1""",
                    (now,),
                )
                block_reason_code = reason

            maintenance_state = "IDLE" if target == "OPEN" else target
            reason = None if target == "OPEN" else (
                block_reason_code
                or ("MAINTENANCE_DRAINING" if target == "DRAINING" else "MAINTENANCE_ACTIVE")
            )
            reconciliation = int(target == "LOCKED")
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state=?, maintenance_state=?,
                       reconciliation_required=?, block_reason_code=?, updated_at=?
                   WHERE singleton_id=1""",
                (target, maintenance_state, reconciliation, reason, now),
            )
        return self.get_status()

    def require_update_maintenance(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        allow_recovery_lock: bool = True,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Prove one exact update still owns the drained maintenance fence."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        kind = _require_token(maintenance_type, "maintenanceType")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._lock:
            connection = self._require_connection()
            state = self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            accepted_states = {"MAINTENANCE"}
            if allow_recovery_lock:
                accepted_states.add("LOCKED")
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["fence_token"] != fence_token
                or state["job_gate_state"] not in accepted_states
                or state["maintenance_state"] not in accepted_states
                or self._count_nonterminal_permits(connection)
                or self._count_unresolved_actions(connection)
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_NOT_AUTHORIZED",
                    "the MCU update does not own a drained maintenance fence",
                )
        return self.get_status()

    def resume_update_maintenance(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Restore an interrupted, observed-safe update to MAINTENANCE."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        kind = _require_token(maintenance_type, "maintenanceType")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock prevents maintenance recovery",
                )
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["fence_token"] != fence_token
                or self._count_nonterminal_permits(connection)
                or self._count_unresolved_actions(connection)
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_NOT_AUTHORIZED",
                    "the interrupted MCU update maintenance fence is invalid",
                )
            if state["job_gate_state"] == "MAINTENANCE":
                return self.get_status()
            if (
                state["job_gate_state"] != "LOCKED"
                or state["block_reason_code"]
                != "MAINTENANCE_RECOVERY_REQUIRED"
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_RECOVERY_NOT_ALLOWED",
                    "the current safety lock cannot be resumed automatically",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE maintenance_lock
                   SET phase='MAINTENANCE', updated_at=? WHERE singleton_id=1""",
                (now,),
            )
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='MAINTENANCE',
                       maintenance_state='MAINTENANCE',
                       reconciliation_required=0,
                       block_reason_code='MAINTENANCE_ACTIVE', updated_at=?
                   WHERE singleton_id=1""",
                (now,),
            )
        return self.get_status()

    def resume_update_drain(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Restore an interrupted pre-hardware drain without opening work."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        kind = _require_token(maintenance_type, "maintenanceType")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock prevents drain recovery",
                )
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["phase"] != "DRAINING"
                or lock["fence_token"] != fence_token
                or self._count_nonterminal_permits(connection)
                or self._count_unresolved_actions(connection)
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_DRAIN_RECOVERY_NOT_AUTHORIZED",
                    "the interrupted MCU update drain cannot be resumed",
                )
            if state["job_gate_state"] == "DRAINING":
                return self.get_status()
            if (
                state["job_gate_state"] != "LOCKED"
                or state["maintenance_state"] != "LOCKED"
                or state["block_reason_code"] != "MAINTENANCE_RECOVERY_REQUIRED"
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_DRAIN_RECOVERY_NOT_ALLOWED",
                    "the current safety lock is not an interrupted update drain",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='DRAINING', maintenance_state='DRAINING',
                       reconciliation_required=0,
                       block_reason_code='MAINTENANCE_DRAINING', updated_at=?
                   WHERE singleton_id=1""",
                (now,),
            )
        return self.get_status()

    def lock_update_maintenance(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        reason_code: str,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Retain the exact MCU fence when neither image is proven safe."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        reason = _require_token(reason_code, "reasonCode")
        kind = _require_token(maintenance_type, "maintenanceType")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._transaction() as connection:
            self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if (
                lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["fence_token"] != fence_token
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_NOT_AUTHORIZED",
                    "the MCU update maintenance fence is invalid",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE maintenance_lock SET phase='LOCKED', updated_at=?
                   WHERE singleton_id=1""",
                (now,),
            )
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='LOCKED', maintenance_state='LOCKED',
                       reconciliation_required=1, block_reason_code=?, updated_at=?
                   WHERE singleton_id=1""",
                (reason, now),
            )
        return self.get_status()

    def release_update_maintenance(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        outcome: str,
        evidence_sha256: str,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Release only a verified target/rollback using its exact fence."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        kind = _require_token(maintenance_type, "maintenanceType")
        if outcome not in {"SUCCEEDED", "ROLLED_BACK"}:
            raise UpdaterStoreError(
                "MAINTENANCE_OUTCOME_INVALID",
                "MCU maintenance outcome is invalid",
            )
        _require_sha256(evidence_sha256, "evidenceSha256")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock prevents maintenance release",
                )
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["fence_token"] != fence_token
                or state["job_gate_state"] not in {"MAINTENANCE", "LOCKED"}
                or self._count_nonterminal_permits(connection)
                or self._count_unresolved_actions(connection)
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_RELEASE_NOT_ALLOWED",
                    "the verified MCU update cannot release this maintenance fence",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                "DELETE FROM maintenance_lock WHERE singleton_id=1"
            )
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='OPEN', maintenance_state='IDLE',
                       reconciliation_required=0, block_reason_code=NULL,
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )
        return self.get_status()

    def abort_update_drain(
        self,
        owner_update_uid: str,
        fence_token: int,
        *,
        evidence_sha256: str,
        maintenance_type: str = "MCU_FIRMWARE_UPDATE",
    ) -> dict[str, Any]:
        """Reopen work only while an MCU update is still waiting to drain."""

        owner = _require_uuid4(owner_update_uid, "ownerUpdateUid")
        kind = _require_token(maintenance_type, "maintenanceType")
        _require_sha256(evidence_sha256, "evidenceSha256")
        if (
            isinstance(fence_token, bool)
            or not isinstance(fence_token, int)
            or fence_token < 1
        ):
            raise UpdaterStoreError(
                "MAINTENANCE_FENCE_INVALID",
                "maintenance fence token is invalid",
            )
        with self._transaction() as connection:
            state = self._require_candidate(connection)
            lock = connection.execute(
                "SELECT * FROM maintenance_lock WHERE singleton_id=1"
            ).fetchone()
            if (
                lock is None
                and state["job_gate_state"] == "OPEN"
                and state["maintenance_state"] == "IDLE"
            ):
                return self.get_status()
            if connection.execute(
                "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
            ).fetchone() is not None:
                raise UpdaterStoreError(
                    "OPERATOR_SAFETY_LOCK_ACTIVE",
                    "the operator safety lock prevents drain cancellation",
                )
            if (
                self._candidate_activation_state(connection) != "ACTIVE"
                or lock is None
                or lock["owner_update_uid"] != owner
                or lock["maintenance_type"] != kind
                or lock["fence_token"] != fence_token
                or lock["phase"] != "DRAINING"
                or state["job_gate_state"] != "DRAINING"
                or state["maintenance_state"] != "DRAINING"
            ):
                raise UpdaterStoreError(
                    "MAINTENANCE_DRAIN_ABORT_NOT_ALLOWED",
                    "only the exact pre-hardware MCU drain may be cancelled",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                "DELETE FROM maintenance_lock WHERE singleton_id=1"
            )
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='OPEN', maintenance_state='IDLE',
                       reconciliation_required=0, block_reason_code=NULL,
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )
        return self.get_status()

    def request_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        work_uid = _require_uuid4(payload.get("workUid"), "workUid")
        command_uid = _require_uuid4(payload.get("commandUid"), "commandUid")
        work_type = _require_enum(
            payload.get("workType"),
            JOB_WORK_TYPES,
            "workType",
        )
        digest = _require_sha256(payload.get("requestDigestSha256"), "requestDigestSha256")
        identity = (work_uid, command_uid, work_type, digest)
        with self._transaction() as connection:
            self._require_candidate(connection)
            existing = self._permit_row(connection, permit_uid)
            if existing is not None:
                actual = tuple(existing[key] for key in (
                    "work_uid", "command_uid", "work_type", "request_digest_sha256"
                ))
                if actual != identity:
                    raise _conflict("JOB_PERMIT_CONFLICT", "job permit identity conflicts")
                return self._permit_result(
                    existing,
                    disposition="DUPLICATE",
                    may_start=self._permit_may_start(connection, existing),
                )
            state = self._management_row(connection)
            if state["job_gate_state"] != "OPEN":
                raise UpdaterStoreError("JOB_GATE_CLOSED", "job gate does not accept new work")
            if self._candidate_activation_state(connection) != "ACTIVE":
                raise UpdaterStoreError(
                    "JOB_GATE_CLOSED",
                    "job gate has no activation for the current candidate cycle",
                )
            if self._count_nonterminal_permits(connection):
                raise UpdaterStoreError("DEVICE_BUSY", "another device job is active")
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """INSERT INTO job_permit (
                           permit_uid, work_uid, command_uid, work_type,
                           request_digest_sha256, state, grant_gate_sequence,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'GRANTED', ?, ?, ?)""",
                    (
                        permit_uid, work_uid, command_uid, work_type, digest,
                        state["management_state_sequence"], now, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise UpdaterStoreError("DEVICE_BUSY", "another device job is active") from error
            row = self._permit_row(connection, permit_uid)
            assert row is not None
            return self._permit_result(
                row,
                disposition="ACCEPTED",
                may_start=self._permit_may_start(connection, row),
            )

    def begin_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        begin_uid = _require_uuid4(payload.get("beginUid"), "beginUid")
        digest = _require_sha256(payload.get("permitDigestSha256"), "permitDigestSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_permit(connection, permit_uid)
            if row["begin_uid"] is not None:
                if row["begin_uid"] == begin_uid and row["permit_digest_sha256"] == digest:
                    return self._permit_result(row, disposition="DUPLICATE")
                raise _conflict("JOB_BEGIN_CONFLICT", "job begin identity conflicts")
            if row["state"] != "GRANTED":
                raise UpdaterStoreError("JOB_PERMIT_STATE_CONFLICT", "job permit cannot be started")
            if digest != row["request_digest_sha256"]:
                raise _conflict(
                    "JOB_BEGIN_DIGEST_CONFLICT",
                    "job begin digest does not match the granted request",
                )
            if self._candidate_activation_state(connection) != "ACTIVE":
                raise UpdaterStoreError(
                    "JOB_GATE_CLOSED",
                    "the granted job cannot start before candidate activation",
                )
            gate = self._management_row(connection)
            can_resume_after_restart = (
                gate["job_gate_state"] == "LOCKED"
                and gate["block_reason_code"]
                == "ACTIVE_JOB_RECONCILIATION"
            )
            if (
                gate["job_gate_state"] not in {"OPEN", "DRAINING"}
                and not can_resume_after_restart
            ):
                raise UpdaterStoreError("JOB_GATE_CLOSED", "job gate does not allow this granted job to start")
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """UPDATE job_permit SET state='ACTIVE', begin_uid=?,
                           permit_digest_sha256=?, begun_at=?, updated_at=?
                       WHERE permit_uid=?""",
                    (begin_uid, digest, now, now, permit_uid),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "JOB_BEGIN_CONFLICT",
                    "job begin identity is already in use",
                ) from error
            return self._permit_result(
                self._require_permit(connection, permit_uid),
                disposition="ACCEPTED",
            )

    def abandon_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        disposition_uid = _require_uuid4(payload.get("dispositionUid"), "dispositionUid")
        evidence = _require_sha256(payload.get("evidenceSha256"), "evidenceSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_permit(connection, permit_uid)
            if row["disposition_uid"] is not None:
                if row["disposition_uid"] == disposition_uid and row["abandon_evidence_sha256"] == evidence:
                    return self._permit_result(row, disposition="DUPLICATE")
                raise _conflict("JOB_ABANDON_CONFLICT", "job abandonment identity conflicts")
            if row["state"] != "GRANTED":
                raise UpdaterStoreError("JOB_PERMIT_STATE_CONFLICT", "only an unused permit can be abandoned")
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """UPDATE job_permit
                       SET state='ABANDONED', disposition_uid=?,
                           abandon_evidence_sha256=?, completed_at=?, updated_at=?
                       WHERE permit_uid=?""",
                    (disposition_uid, evidence, now, now, permit_uid),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "JOB_ABANDON_CONFLICT",
                    "job abandonment identity is already in use",
                ) from error
            self._maybe_reopen_after_resolution(connection, now)
            return self._permit_result(
                self._require_permit(connection, permit_uid),
                disposition="ACCEPTED",
            )

    def complete_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        completion_uid = _require_uuid4(payload.get("completionUid"), "completionUid")
        outcome = _require_enum(payload.get("outcome"), JOB_OUTCOMES, "outcome")
        digest = _require_sha256(payload.get("completionDigestSha256"), "completionDigestSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_permit(connection, permit_uid)
            if row["completion_uid"] is not None:
                if (
                    row["completion_uid"] == completion_uid
                    and row["completion_outcome"] == outcome
                    and row["completion_digest_sha256"] == digest
                ):
                    return self._permit_result(row, disposition="DUPLICATE")
                raise _conflict("JOB_COMPLETION_CONFLICT", "job completion identity conflicts")
            if row["state"] != "ACTIVE":
                raise UpdaterStoreError("JOB_PERMIT_STATE_CONFLICT", "job permit is not active")
            pending = connection.execute(
                """SELECT COUNT(*)
                   FROM physical_action_ledger action
                   WHERE action.permit_uid=?
                     AND action.state<>'CONFIRMED'
                     AND NOT EXISTS (
                         SELECT 1
                         FROM physical_action_unknown_effect_resolution resolution
                         WHERE resolution.action_uid=action.action_uid)""",
                (permit_uid,),
            ).fetchone()[0]
            if pending:
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_UNCONFIRMED",
                    "job has an unconfirmed physical action",
                )
            quarantined = connection.execute(
                """SELECT 1
                   FROM physical_action_unknown_effect_resolution
                   WHERE permit_uid=? LIMIT 1""",
                (permit_uid,),
            ).fetchone()
            if quarantined is not None and outcome != "CANCELLED":
                raise UpdaterStoreError(
                    "JOB_QUARANTINE_REQUIRES_CANCELLATION",
                    "a job with unknown physical effect can only be cancelled",
                )
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """UPDATE job_permit
                       SET state='COMPLETED', completion_uid=?,
                           completion_outcome=?, completion_digest_sha256=?,
                           completed_at=?, updated_at=? WHERE permit_uid=?""",
                    (completion_uid, outcome, digest, now, now, permit_uid),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "JOB_COMPLETION_CONFLICT",
                    "job completion identity is already in use",
                ) from error
            self._maybe_reopen_after_resolution(connection, now)
            return self._permit_result(
                self._require_permit(connection, permit_uid),
                disposition="ACCEPTED",
            )

    def get_job_permit(self, payload: dict[str, Any]) -> dict[str, Any]:
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        with self._lock:
            connection = self._require_connection()
            self._require_candidate(connection)
            row = self._require_permit(connection, permit_uid)
            return self._permit_result(
                row,
                disposition="FOUND",
                may_start=self._permit_may_start(connection, row),
            )

    def prepare_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Durably reserve one logical action without permitting a write."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        work_uid = _require_uuid4(payload.get("workUid"), "workUid")
        command_uid = _require_uuid4(payload.get("commandUid"), "commandUid")
        action_key = _require_action_key(payload.get("actionKey"))
        action_kind = _require_token(payload.get("actionKind"), "actionKind")
        digest = _require_sha256(
            payload.get("actionDigestSha256"),
            "actionDigestSha256",
        )
        dispatch_token = _require_dispatch_attempt_token(
            payload.get("dispatchAttemptToken")
        )
        token_digest = _dispatch_token_digest(dispatch_token)
        identity = (
            permit_uid,
            work_uid,
            command_uid,
            action_key,
            action_kind,
            digest,
        )
        with self._transaction() as connection:
            self._require_candidate(connection)
            existing = self._action_row(connection, action_uid)
            if existing is not None:
                self._native_recovery_close_row(connection, action_uid)
                if self._native_close_disposition_row(connection, action_uid) is not None:
                    return self._action_result(existing, disposition="DENIED")
                actual = tuple(
                    existing[key]
                    for key in (
                        "permit_uid",
                        "work_uid",
                        "command_uid",
                        "action_key",
                        "action_kind",
                        "action_digest_sha256",
                    )
                )
                if actual != identity:
                    raise _conflict(
                        "PHYSICAL_ACTION_CONFLICT",
                        "physical action identity conflicts",
                    )
                same_preparing_attempt = (
                    isinstance(
                        existing["dispatch_attempt_token_sha256"], str
                    )
                    and hmac.compare_digest(
                        existing["dispatch_attempt_token_sha256"],
                        token_digest,
                    )
                )
                if not same_preparing_attempt:
                    return self._action_result(
                        existing,
                        disposition="DENIED",
                    )
                return self._action_result(existing, disposition="DUPLICATE")

            permit = self._require_permit(connection, permit_uid)
            if permit["state"] != "ACTIVE":
                raise UpdaterStoreError(
                    "JOB_PERMIT_STATE_CONFLICT",
                    "physical action requires an active permit",
                )
            self._require_existing_job_action_gate(connection)
            if (permit["work_uid"], permit["command_uid"]) != (
                work_uid,
                command_uid,
            ):
                raise _conflict(
                    "PHYSICAL_ACTION_CONFLICT",
                    "physical action does not belong to the permit",
                )
            logical = connection.execute(
                """SELECT action_uid FROM physical_action_ledger
                   WHERE (work_uid=? OR command_uid=?) AND action_key=?""",
                (work_uid, command_uid, action_key),
            ).fetchone()
            if logical is not None:
                raise _conflict(
                    "PHYSICAL_ACTION_LOGICAL_CONFLICT",
                    "logical physical action already has another identity",
                )

            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """INSERT INTO physical_action_ledger (
                           action_uid, permit_uid, work_uid, command_uid,
                           action_key, action_kind, action_digest_sha256,
                           state, dispatch_mode,
                           dispatch_attempt_token_sha256,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PREPARED',
                                 'PREPARED_ONLY', ?, ?, ?)""",
                    (
                        action_uid,
                        *identity,
                        token_digest,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "PHYSICAL_ACTION_CONFLICT",
                    "physical action identity is already in use",
                ) from error
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    @staticmethod
    def _native_recovery_close_schema() -> str:
        return """CREATE TABLE native_recovery_close (
            recovery_uid TEXT PRIMARY KEY,
            action_uid TEXT NOT NULL UNIQUE REFERENCES physical_action_ledger(action_uid),
            source_action_uid TEXT NOT NULL UNIQUE REFERENCES physical_action_ledger(action_uid),
            payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 1 AND 4096),
            created_at TEXT NOT NULL
        )"""

    @staticmethod
    def _native_recovery_close_successor_schema() -> str:
        return """CREATE TABLE native_recovery_close (
            recovery_uid TEXT PRIMARY KEY,
            action_uid TEXT NOT NULL UNIQUE REFERENCES physical_action_ledger(action_uid),
            source_action_uid TEXT NOT NULL REFERENCES physical_action_ledger(action_uid),
            payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 1 AND 4096),
            created_at TEXT NOT NULL,
            predecessor_action_uid TEXT NOT NULL UNIQUE REFERENCES physical_action_ledger(action_uid)
        )"""

    @staticmethod
    def _native_recovery_close_schema_kind(connection):
        table = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_recovery_close'").fetchone()
        if table is None:
            return None
        if connection.execute("""SELECT 1 FROM sqlite_master WHERE tbl_name='native_recovery_close'
                AND type IN ('index', 'trigger') AND sql IS NOT NULL LIMIT 1""").fetchone():
            raise RuntimeError("native recovery close schema has an unrecognized index or trigger")
        actual = _normalize_schema_sql(table[0])
        for kind, schema in (("root", UpdaterStore._native_recovery_close_schema()),
                ("successor", UpdaterStore._native_recovery_close_successor_schema())):
            if actual == _normalize_schema_sql(schema):
                return kind
        raise RuntimeError("native recovery close schema is incompatible")

    @staticmethod
    def _native_recovery_close_identity(payload: dict[str, Any]) -> dict[str, Any]:
        """Caller owns MCU/data-loss evidence; updater restricts ledger authority."""
        from job_safety import NATIVE_RECOVERY_CLOSE_REQUEST_FIELDS, NATIVE_RECOVERY_CLOSE_SUCCESSOR_REQUEST_FIELDS
        if not isinstance(payload, dict) or set(payload) not in (
                NATIVE_RECOVERY_CLOSE_REQUEST_FIELDS, NATIVE_RECOVERY_CLOSE_REQUEST_FIELDS - {"dispatchAttemptToken"},
                NATIVE_RECOVERY_CLOSE_SUCCESSOR_REQUEST_FIELDS, NATIVE_RECOVERY_CLOSE_SUCCESSOR_REQUEST_FIELDS - {"dispatchAttemptToken"}):
            raise UpdaterStoreError("REQUEST_INVALID", "native recovery requires exact fields")
        fields = {}
        for key in ("recoveryUid", "actionUid", "sourceActionUid", "permitUid", "workUid", "commandUid"):
            fields[key] = _require_uuid4(payload.get(key), key)
        for key in ("actionDigestSha256", "sourceActionDigestSha256", "recoveryEvidenceSha256"):
            fields[key] = _require_sha256(payload.get(key), key)
        for key in ("expectedSourceLedgerSequence", "sourceMcuBootId", "targetMcuBootId", "portNo"):
            fields[key] = _require_positive_int(payload.get(key), key)
        if "predecessorActionUid" in payload:
            for key in ("predecessorActionUid", "predecessorReceiptUid"):
                fields[key] = _require_uuid4(payload[key], key)
            fields["expectedPredecessorLedgerSequence"] = _require_positive_int(
                payload["expectedPredecessorLedgerSequence"], "expectedPredecessorLedgerSequence")
            fields["predecessorRetirementEvidenceSha256"] = _require_sha256(
                payload["predecessorRetirementEvidenceSha256"], "predecessorRetirementEvidenceSha256")
        fields["actionKey"] = _require_action_key(payload.get("actionKey"))
        fields["actionKind"] = _require_token(payload.get("actionKind"), "actionKind")
        fields["reason"] = _require_token(payload.get("reason"), "reason")
        if (fields["actionKind"] != "SAFE_CLOSE" or fields["reason"] != "MCU_RESTART_DATA_LOSS"
                or fields["actionKey"] != f"native:recovery-close:{fields['recoveryUid']}"
                or not fields["sourceMcuBootId"] < fields["targetMcuBootId"] <= 9007199254740991
                or fields["portNo"] > 6 or fields["sourceActionUid"] == fields["actionUid"]):
            raise UpdaterStoreError("NATIVE_RECOVERY_SCOPE_INVALID", "native recovery permits only a new port-scoped close after data loss")
        import uart2_protocol as uart
        from job_safety import action_digest
        decoded = {}
        try:
            for key, name in (("sourceCommandPayloadHex", "AUTHORIZE_DELIVERY_FIRST_OPEN"), ("closeCommandPayloadHex", "SAFE_CLOSE")):
                raw = payload.get(key)
                if not isinstance(raw, str) or len(raw) > uart.MAXIMUM_PAYLOAD_LENGTH * 2 or bytes.fromhex(raw).hex() != raw:
                    raise ValueError("noncanonical native payload")
                decoded[key] = uart.decode_payload(name, bytes.fromhex(raw))
                fields[key] = raw
        except (ValueError, TypeError) as error:
            raise UpdaterStoreError("NATIVE_RECOVERY_WIRE_INVALID", "native recovery wire payload is invalid") from error
        source, close = decoded["sourceCommandPayloadHex"], decoded["closeCommandPayloadHex"]
        expected_digest = action_digest(work_uid=fields["workUid"], command_uid=fields["commandUid"],
            action_key=fields["actionKey"], action_kind="SAFE_CLOSE", payload={"nativeUartPayloadHex": fields["closeCommandPayloadHex"]})
        if (source["mcuCommandUid"] != fields["sourceActionUid"] or source["sessionUid"] != fields["workUid"]
                or source["targetMcuBootId"] != fields["sourceMcuBootId"] or source["portNo"] != fields["portNo"]
                or close["mcuCommandUid"] != fields["actionUid"] or close["targetMcuBootId"] != fields["targetMcuBootId"]
                or close["scope"] != "SINGLE_DELIVERY_DOOR" or close["portNo"] != fields["portNo"]
                or expected_digest != fields["actionDigestSha256"]):
            raise UpdaterStoreError("NATIVE_RECOVERY_WIRE_INVALID", "native recovery wire scope or action digest conflicts")
        return fields

    @staticmethod
    def _native_recovery_close_single_row(connection, action_uid):
        ledger = UpdaterStore._action_row(connection, action_uid)
        required = ledger is not None and ledger["action_key"].startswith("native:recovery-close:")
        kind = UpdaterStore._native_recovery_close_schema_kind(connection)
        if kind is None:
            if required:
                raise RuntimeError("native recovery close custody is missing")
            return None
        row = connection.execute("SELECT * FROM native_recovery_close WHERE action_uid=?", (action_uid,)).fetchone()
        if row is None:
            if required:
                raise RuntimeError("native recovery close custody is missing")
            return None
        try:
            fields = UpdaterStore._native_recovery_close_identity(json.loads(row["payload_json"]))
        except (ValueError, TypeError, UpdaterStoreError) as error:
            raise RuntimeError("native recovery close evidence is corrupt") from error
        if (json.dumps(fields, sort_keys=True, separators=(",", ":")) != row["payload_json"]
                or (kind == "root" and "predecessorActionUid" in fields)
                or (kind == "successor" and row["predecessor_action_uid"] != fields.get("predecessorActionUid", fields["sourceActionUid"]))
                or any(row[column] != fields[key] for column, key in
                    (("recovery_uid", "recoveryUid"), ("action_uid", "actionUid"), ("source_action_uid", "sourceActionUid")))):
            raise RuntimeError("native recovery close evidence is corrupt")
        action = UpdaterStore._action_row(connection, action_uid)
        source = UpdaterStore._action_row(connection, fields["sourceActionUid"])
        permit = UpdaterStore._permit_row(connection, fields["permitUid"])
        identity = (("permit_uid", "permitUid"), ("work_uid", "workUid"), ("command_uid", "commandUid"))
        if (action is None or source is None or permit is None or permit["work_type"] != "DELIVERY"
                or any(action[column] != fields[key] or source[column] != fields[key] or permit[column] != fields[key]
                    for column, key in identity)
                or any(action[column] != fields[key] for column, key in
                    (("action_key", "actionKey"), ("action_kind", "actionKind"), ("action_digest_sha256", "actionDigestSha256")))
                or source["action_kind"] != "AUTHORIZE_DELIVERY_FIRST_OPEN"
                or source["ledger_sequence"] != fields["expectedSourceLedgerSequence"]
                or source["action_digest_sha256"] != fields["sourceActionDigestSha256"]
                or source["ledger_sequence"] >= action["ledger_sequence"]):
            raise RuntimeError("native recovery close ledger identity is corrupt")
        from job_safety import action_digest
        if action_digest(work_uid=source["work_uid"], command_uid=source["command_uid"],
                action_key=source["action_key"], action_kind=source["action_kind"],
                payload={"nativeUartPayloadHex": fields["sourceCommandPayloadHex"]}) != source["action_digest_sha256"]:
            raise RuntimeError("native recovery close source wire is corrupt")
        return fields

    @staticmethod
    def _native_recovery_close_row(connection, action_uid):
        """Validate the complete immutable ancestry without a recursion limit."""
        result = current = UpdaterStore._native_recovery_close_single_row(connection, action_uid)
        seen = set()
        while current is not None and "predecessorActionUid" in current:
            uid = current["actionUid"]
            if uid in seen:
                raise RuntimeError("native recovery close ancestry is cyclic")
            seen.add(uid)
            parent = UpdaterStore._native_recovery_close_single_row(connection, current["predecessorActionUid"])
            try:
                UpdaterStore._require_native_recovery_close_predecessor(connection, current, parent=parent)
            except UpdaterStoreError as error:
                raise RuntimeError("native recovery close ancestry is corrupt") from error
            current = parent
        return result

    @staticmethod
    def _require_native_recovery_close_predecessor(connection, fields, *, parent=None):
        if "predecessorActionUid" not in fields:
            return
        if parent is None:
            parent = UpdaterStore._native_recovery_close_row(connection, fields["predecessorActionUid"])
        previous = UpdaterStore._action_row(connection, fields["predecessorActionUid"])
        current = UpdaterStore._action_row(connection, fields["actionUid"])
        shared = ("sourceActionUid", "sourceActionDigestSha256", "expectedSourceLedgerSequence",
            "sourceMcuBootId", "portNo", "reason", "recoveryEvidenceSha256",
            "sourceCommandPayloadHex", "permitUid", "workUid", "commandUid")
        withdrawn = UpdaterStore._native_close_disposition_row(connection, fields["predecessorActionUid"])
        retired = (previous is not None and previous["state"] == "CONFIRMED"
            and previous["dispatch_mode"] == "PREPARED_ONLY" and previous["confirmed_outcome"] == "NOT_EXECUTED"
            and previous["confirmation_basis"] == "PREPARED_NOT_ARMED"
            and previous["receipt_uid"] == fields["predecessorReceiptUid"]
            and previous["evidence_digest_sha256"] == fields["predecessorRetirementEvidenceSha256"])
        withdrawn_matches = (withdrawn is not None and withdrawn["receiptUid"] == fields["predecessorReceiptUid"]
            and withdrawn["evidenceDigestSha256"] == fields["predecessorRetirementEvidenceSha256"])
        target = (withdrawn["observedMcuBootId"] if withdrawn is not None
            and withdrawn["state"] == "ISOLATED_BY_REBOOT" else (parent or {}).get("targetMcuBootId"))
        if (parent is None or previous is None or any(fields[key] != parent[key] for key in shared)
                or fields["targetMcuBootId"] != target
                or fields["actionUid"] in {parent["actionUid"], fields["sourceActionUid"]}
                or fields["recoveryUid"] == parent["recoveryUid"]
                or previous["ledger_sequence"] != fields["expectedPredecessorLedgerSequence"]
                or (current is not None and previous["ledger_sequence"] >= current["ledger_sequence"])
                or not (retired or withdrawn_matches)):
            raise UpdaterStoreError("NATIVE_RECOVERY_PREDECESSOR_CONFLICT", "native recovery predecessor is not the exact retired original close")
        import uart2_protocol as uart
        old = uart.decode_payload("SAFE_CLOSE", bytes.fromhex(parent["closeCommandPayloadHex"]))
        new = uart.decode_payload("SAFE_CLOSE", bytes.fromhex(fields["closeCommandPayloadHex"]))
        if new["targetMcuBootId"] == old["targetMcuBootId"] and new["commandSequence"] <= old["commandSequence"]:
            raise UpdaterStoreError("NATIVE_RECOVERY_PREDECESSOR_CONFLICT", "native recovery successor requires a new increasing command sequence")

    @staticmethod
    def _upgrade_native_recovery_close_successor(connection):
        kind = UpdaterStore._native_recovery_close_schema_kind(connection)
        if kind == "successor":
            return
        UpdaterStore._verify_native_recovery_close_extension(connection)
        schema = UpdaterStore._native_recovery_close_successor_schema()
        if kind is None:
            connection.execute(schema)
            return
        connection.execute("ALTER TABLE native_recovery_close RENAME TO native_recovery_close_previous")
        connection.execute(schema)
        connection.execute("""INSERT INTO native_recovery_close
            SELECT recovery_uid, action_uid, source_action_uid, payload_json, created_at, source_action_uid
            FROM native_recovery_close_previous""")
        connection.execute("DROP TABLE native_recovery_close_previous")

    @staticmethod
    def _verify_native_recovery_close_extension(connection):
        # Lazily created in the first successful prepare transaction. No schema
        # mutation on ordinary/default-off startup; legacy history is unchanged.
        UpdaterStore._native_recovery_close_row(connection, "")  # Validate even an empty table's schema.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close'").fetchone():
            for row in connection.execute("SELECT action_uid FROM native_recovery_close"):
                UpdaterStore._native_recovery_close_row(connection, row[0])
        for row in connection.execute("SELECT action_uid FROM physical_action_ledger WHERE action_key LIKE 'native:recovery-close:%'"):
            UpdaterStore._native_recovery_close_row(connection, row[0])
        UpdaterStore._native_close_disposition_row(connection, "")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close_disposition'").fetchone():
            for row in connection.execute("SELECT action_uid FROM native_recovery_close_disposition"):
                UpdaterStore._native_recovery_close_row(connection, row[0])
                UpdaterStore._native_close_disposition_row(connection, row[0])

    @staticmethod
    def _require_native_recovery_close_gate(connection, fields):
        state = UpdaterStore._management_row(connection)
        maintenance = connection.execute("SELECT phase FROM maintenance_lock LIMIT 1").fetchone()
        # Preserve operator stop and maintenance ownership; only the original
        # action is an exception, never the entire closed gate.
        if (state["job_gate_state"] != "LOCKED" or state["block_reason_code"] not in _ACTIVE_JOB_LOCK_REASONS
                or connection.execute("SELECT 1 FROM operator_job_gate_lock LIMIT 1").fetchone()
                or (maintenance is not None and maintenance["phase"] != "DRAINING")):
            raise UpdaterStoreError("JOB_GATE_CLOSED", "job gate does not allow recovery close")
        UpdaterStore._require_native_recovery_close_source(connection, fields)
        source = UpdaterStore._action_row(connection, fields["sourceActionUid"])
        source_eligible = source["state"] == "ARMED" or (
            source["state"] == "CONFIRMED" and source["confirmed_outcome"] == "EXECUTED"
            and source["confirmation_basis"] == "MCU_IDENTITY_BOUND_FACT")
        if (not source_eligible or connection.execute(
                "SELECT 1 FROM physical_action_unknown_effect_resolution WHERE permit_uid=? LIMIT 1",
                (fields["permitUid"],)).fetchone()):
            raise UpdaterStoreError("NATIVE_RECOVERY_SOURCE_CONFLICT", "native recovery source cannot authorize a new close")
        allowed = {fields["sourceActionUid"], fields["actionUid"]}
        current = fields
        while "predecessorActionUid" in current:
            parent_uid = current["predecessorActionUid"]
            if parent_uid in allowed:
                raise RuntimeError("native recovery close ancestry is cyclic")
            # Full predecessor validation above binds every exemption to this
            # exact original work and chain, never all withdrawn actions.
            parent = UpdaterStore._native_recovery_close_row(connection, parent_uid)
            if UpdaterStore._native_close_disposition_row(connection, parent_uid) is not None:
                allowed.add(parent_uid)
            current = parent
        for unresolved in connection.execute("SELECT action_uid FROM physical_action_ledger WHERE state<>'CONFIRMED'"):
            if unresolved[0] not in allowed:
                raise UpdaterStoreError("PHYSICAL_ACTION_RECONCILIATION_REQUIRED", "another physical action is unresolved")

    @staticmethod
    def _require_native_recovery_close_source(connection, fields):
        """Original identity only; not permission to energize an output."""
        source = UpdaterStore._action_row(connection, fields["sourceActionUid"])
        permit = UpdaterStore._permit_row(connection, fields["permitUid"])
        if (source is None or permit is None or source["dispatch_mode"] != "TWO_PHASE_V3"
                or source["action_kind"] != "AUTHORIZE_DELIVERY_FIRST_OPEN"
                or source["ledger_sequence"] != fields["expectedSourceLedgerSequence"]
                or source["action_digest_sha256"] != fields["sourceActionDigestSha256"]
                or permit["state"] != "ACTIVE" or permit["work_type"] != "DELIVERY"
                or any(source[column] != fields[key] or permit[column] != fields[key]
                    for column, key in (("permit_uid", "permitUid"), ("work_uid", "workUid"), ("command_uid", "commandUid")))):
            raise UpdaterStoreError("NATIVE_RECOVERY_SOURCE_CONFLICT", "native recovery source is not the original active native delivery action")
        from job_safety import action_digest
        expected = action_digest(work_uid=source["work_uid"], command_uid=source["command_uid"],
            action_key=source["action_key"], action_kind=source["action_kind"],
            payload={"nativeUartPayloadHex": fields["sourceCommandPayloadHex"]})
        if expected != source["action_digest_sha256"]:
            raise UpdaterStoreError("NATIVE_RECOVERY_WIRE_INVALID", "native recovery source wire differs from its permanent action")
        UpdaterStore._require_native_recovery_close_predecessor(connection, fields)

    @staticmethod
    def _insert_native_recovery_close(connection, fields, token_digest, now):
        """Shared custody insert, always inside the caller's atomic transaction."""
        if "predecessorActionUid" in fields:
            UpdaterStore._upgrade_native_recovery_close_successor(connection)
        elif not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close'").fetchone():
            connection.execute(UpdaterStore._native_recovery_close_schema())
        try:
            connection.execute("""INSERT INTO physical_action_ledger (action_uid, permit_uid, work_uid, command_uid,
                action_key, action_kind, action_digest_sha256, state, dispatch_mode, dispatch_attempt_token_sha256,
                created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'PREPARED', 'PREPARED_ONLY', ?, ?, ?)""",
                tuple(fields[key] for key in ("actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256"))
                + (token_digest, now, now))
            values = (fields["recoveryUid"], fields["actionUid"], fields["sourceActionUid"],
                json.dumps(fields, sort_keys=True, separators=(",", ":")), now)
            if UpdaterStore._native_recovery_close_schema_kind(connection) == "successor":
                connection.execute("""INSERT INTO native_recovery_close (recovery_uid, action_uid, source_action_uid,
                    payload_json, created_at, predecessor_action_uid) VALUES (?, ?, ?, ?, ?, ?)""",
                    values + (fields.get("predecessorActionUid", fields["sourceActionUid"]),))
            else:
                connection.execute("""INSERT INTO native_recovery_close (recovery_uid, action_uid, source_action_uid, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)""", values)
        except sqlite3.IntegrityError as error:
            raise _conflict("NATIVE_RECOVERY_IDENTITY_CONFLICT", "native recovery close already has an identity") from error

    def prepare_native_recovery_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        from job_safety import NATIVE_RECOVERY_CLOSE_REQUEST_FIELDS
        return self._prepare_native_recovery_close(payload, NATIVE_RECOVERY_CLOSE_REQUEST_FIELDS)

    def prepare_native_recovery_close_successor(self, payload: dict[str, Any]) -> dict[str, Any]:
        from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_REQUEST_FIELDS
        return self._prepare_native_recovery_close(payload, NATIVE_RECOVERY_CLOSE_SUCCESSOR_REQUEST_FIELDS)

    def _prepare_native_recovery_close(self, payload, expected_fields):
        """Register data-loss evidence and ONE restricted close atomically.

        This is not manual quarantine, proof of a reboot or a door position.
        The business owner must persist/check its actual MCU evidence before
        requesting, and enforce the native command deadline before one write.
        Original history is unchanged, including already confirmed native output.
        This authority alone never completes the original job.
        """
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise UpdaterStoreError("REQUEST_INVALID", "native recovery requires exact fields")
        fields = self._native_recovery_close_identity(payload)
        token = _dispatch_token_digest(_require_dispatch_attempt_token(payload.get("dispatchAttemptToken")))
        with self._transaction() as connection:
            self._require_candidate(connection)
            self._native_recovery_close_row(connection, "")
            existing = self._action_row(connection, fields["actionUid"])
            if existing is not None:
                saved = self._native_recovery_close_row(connection, fields["actionUid"])
                if saved != fields:
                    raise _conflict("NATIVE_RECOVERY_IDENTITY_CONFLICT", "native recovery identity or evidence conflicts")
                if self._native_close_disposition_row(connection, fields["actionUid"]) is not None:
                    return self._action_result(existing, disposition="DENIED")
                same = hmac.compare_digest(existing["dispatch_attempt_token_sha256"], token)
                return self._action_result(existing, disposition="DUPLICATE" if same else "DENIED")
            self._require_native_recovery_close_gate(connection, fields)
            now = _format_utc(self._utc_now())
            self._insert_native_recovery_close(connection, fields, token, now)
            return self._action_result(self._require_action(connection, fields["actionUid"]), disposition="ACCEPTED")

    def retire_native_recovery_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        from job_safety import NATIVE_RECOVERY_CLOSE_RETIRE_FIELDS
        return self._retire_native_recovery_close(payload, NATIVE_RECOVERY_CLOSE_RETIRE_FIELDS)

    def retire_native_recovery_close_successor(self, payload: dict[str, Any]) -> dict[str, Any]:
        from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_RETIRE_FIELDS
        return self._retire_native_recovery_close(payload, NATIVE_RECOVERY_CLOSE_SUCCESSOR_RETIRE_FIELDS)

    def _retire_native_recovery_close(self, payload, expected_fields):
        """Fence only this original, never-armed close; retain the active job."""
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise UpdaterStoreError("REQUEST_INVALID", "native retirement requires exact fields")
        fields = self._native_recovery_close_identity({key: value for key, value in payload.items()
            if key not in {"receiptUid", "retirementEvidenceSha256"}})
        receipt = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        evidence = _require_sha256(payload.get("retirementEvidenceSha256"), "retirementEvidenceSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            saved = self._native_recovery_close_row(connection, fields["actionUid"])
            if saved is None and self._action_row(connection, fields["actionUid"]) is None:
                self._require_native_recovery_close_source(connection, fields)
                now = _format_utc(self._utc_now())
                # This digest has no caller-owned execution token. Both rows
                # and their NOT_EXECUTED confirmation commit as one fence.
                token_digest = hashlib.sha256(os.urandom(32)).hexdigest()
                self._insert_native_recovery_close(connection, fields, token_digest, now)
                saved = self._native_recovery_close_row(connection, fields["actionUid"])
            if saved != fields:
                raise _conflict("NATIVE_RECOVERY_IDENTITY_CONFLICT", "native retirement identity or evidence conflicts")
            row = self._require_action(connection, fields["actionUid"])
            if row["dispatch_mode"] != "PREPARED_ONLY":
                raise UpdaterStoreError("PHYSICAL_ACTION_ALREADY_ARMED", "an armed close cannot be retired as unexecuted")
            if row["state"] == "CONFIRMED":
                if self._confirmation_matches(row, receipt_uid=receipt, outcome="NOT_EXECUTED",
                        confirmation_basis="PREPARED_NOT_ARMED", evidence=evidence):
                    return self._action_result(row, disposition="DUPLICATE")
                raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "native retirement receipt conflicts")
            if row["state"] != "PREPARED":
                raise UpdaterStoreError("PHYSICAL_ACTION_ALREADY_ARMED", "only a prepared close can be retired")
            self._require_native_recovery_close_source(connection, fields)
            self._confirm_action_row(connection, action_uid=fields["actionUid"], receipt_uid=receipt,
                outcome="NOT_EXECUTED", confirmation_basis="PREPARED_NOT_ARMED", evidence=evidence,
                now=_format_utc(self._utc_now()))
            return self._action_result(self._require_action(connection, fields["actionUid"]), disposition="ACCEPTED")

    def get_native_recovery_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        with self._lock:
            connection = self._require_connection()
            self._require_candidate(connection)
            fields = self._native_recovery_close_row(connection, action_uid)
            if fields is None:
                raise UpdaterStoreError("NATIVE_RECOVERY_NOT_FOUND", "native recovery close does not exist")
            return fields | {"disposition": "FOUND"}

    @staticmethod
    def _native_close_disposition_schema():
        return """CREATE TABLE native_recovery_close_disposition (
            action_uid TEXT PRIMARY KEY REFERENCES physical_action_ledger(action_uid),
            receipt_uid TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL CHECK(length(payload_json) BETWEEN 1 AND 8192),
            payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
            ledger_sha256 TEXT NOT NULL CHECK(length(ledger_sha256)=64),
            created_at TEXT NOT NULL
        )"""

    @staticmethod
    def _native_close_isolation_observation(payload, target_boot):
        from job_safety import NATIVE_RECOVERY_CLOSE_BOOT_OBSERVATION_FIELDS
        import uart2_protocol as uart
        observed = _require_positive_int(payload.get("observedMcuBootId"), "observedMcuBootId")
        name, raw = payload.get("bootObservationMessageName"), payload.get("bootObservationPayloadHex")
        try:
            if (name not in {"BOOT_PROBE_REPLY", "BIND_BOOT_REPLY"} or not isinstance(raw, str)
                    or len(raw) > 50 or bytes.fromhex(raw).hex() != raw):
                raise ValueError("invalid positive boot wire")
            values = uart.decode_payload(name, bytes.fromhex(raw))
            if not target_boot < observed <= 9007199254740991 or values["mcuBootId"] != observed:
                raise ValueError("boot does not isolate original target")
        except (ValueError, TypeError) as error:
            raise UpdaterStoreError("NATIVE_RECOVERY_BOOT_ISOLATION_INVALID", "isolation requires a newer positive MCU boot reply") from error
        return {key: payload[key] for key in NATIVE_RECOVERY_CLOSE_BOOT_OBSERVATION_FIELDS}

    @staticmethod
    def _native_close_disposition_row(connection, action_uid):
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='native_recovery_close_disposition'").fetchone()
        if schema is None:
            return None
        if ("".join(schema[0].split()).lower() != "".join(UpdaterStore._native_close_disposition_schema().split()).lower()
                or connection.execute("""SELECT 1 FROM sqlite_master WHERE tbl_name='native_recovery_close_disposition'
                    AND (type='trigger' OR (type='index' AND sql IS NOT NULL)) LIMIT 1""").fetchone()):
            raise RuntimeError("native recovery close disposition schema is incompatible")
        row = connection.execute("SELECT * FROM native_recovery_close_disposition WHERE action_uid=?", (action_uid,)).fetchone()
        if row is None:
            return None
        binding = UpdaterStore._native_recovery_close_single_row(connection, action_uid)
        ledger = UpdaterStore._action_row(connection, action_uid)
        try:
            result = json.loads(row["payload_json"])
            receipt = _require_uuid4(result.get("receiptUid"), "receiptUid")
            evidence = _require_sha256(result.get("evidenceDigestSha256"), "evidenceDigestSha256")
            sequence = _require_positive_int(result.get("ledgerSequence"), "ledgerSequence")
            expected = dict(binding or {}, receiptUid=receipt, evidenceDigestSha256=evidence,
                ledgerSequence=sequence, state="DISPATCH_WITHDRAWN",
                dispositionBasis="AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM", mayExecute=False)
            if result.get("state") == "ISOLATED_BY_REBOOT":
                expected.update(UpdaterStore._native_close_isolation_observation(result, (binding or {})["targetMcuBootId"]),
                    state="ISOLATED_BY_REBOOT", dispositionBasis="NEWER_MCU_BOOT_COMMAND_TARGET_ISOLATED", pastEffect="UNKNOWN")
            if (binding is None or ledger is None or result != expected
                    or any(type(result[key]) is not type(value) for key, value in expected.items())
                    or json.dumps(result, sort_keys=True, separators=(",", ":")) != row["payload_json"]
                    or hashlib.sha256(row["payload_json"].encode("ascii")).hexdigest() != row["payload_sha256"]
                    or row["receipt_uid"] != receipt or sequence != ledger["ledger_sequence"]
                    or ledger["state"] != "ARMED" or ledger["dispatch_mode"] != "TWO_PHASE_V3"
                    or ledger["receipt_uid"] is not None
                    or hashlib.sha256(json.dumps(dict(ledger), sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest() != row["ledger_sha256"]
                    or connection.execute("SELECT 1 FROM physical_action_ledger WHERE receipt_uid=?", (receipt,)).fetchone()
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE resolution_uid=?", (receipt,)).fetchone()
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE action_uid=?", (action_uid,)).fetchone()):
                raise ValueError("disposition identity or history differs")
        except (ValueError, TypeError, KeyError, AttributeError, UpdaterStoreError) as error:
            raise RuntimeError("native recovery close disposition is corrupt") from error
        return result

    def withdraw_native_recovery_close_dispatch(self, payload):
        from job_safety import NATIVE_RECOVERY_CLOSE_RETIRE_FIELDS
        return self._withdraw_native_recovery_close_dispatch(payload, NATIVE_RECOVERY_CLOSE_RETIRE_FIELDS)

    @staticmethod
    def _require_receipt_not_withdrawn(connection, receipt_uid):
        UpdaterStore._native_close_disposition_row(connection, "")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close_disposition'").fetchone():
            # Do not trust a damaged receipt index to hide a committed owner.
            for row in connection.execute("SELECT action_uid FROM native_recovery_close_disposition"):
                saved = UpdaterStore._native_close_disposition_row(connection, row[0])
                if saved["receiptUid"] == receipt_uid:
                    raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "native withdrawal already owns this receipt")

    def withdraw_native_recovery_close_successor_dispatch(self, payload):
        from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_RETIRE_FIELDS
        return self._withdraw_native_recovery_close_dispatch(payload, NATIVE_RECOVERY_CLOSE_SUCCESSOR_RETIRE_FIELDS)

    def _withdraw_native_recovery_close_dispatch(self, payload, expected_fields):
        """Append caller's durable claim-withdrawal fact, never rewrite ARM history.

        The business owner must commit its claim fence before this request.
        This is not the live dispatch-token abort, a never-authorized action,
        or a physical/current-door observation. It cannot complete a job.
        """
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise UpdaterStoreError("REQUEST_INVALID", "native withdrawal requires exact fields")
        fields = self._native_recovery_close_identity({key: value for key, value in payload.items()
            if key not in {"receiptUid", "retirementEvidenceSha256"}})
        receipt = _require_uuid4(payload["receiptUid"], "receiptUid")
        evidence = _require_sha256(payload["retirementEvidenceSha256"], "retirementEvidenceSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            saved = self._native_recovery_close_row(connection, fields["actionUid"])
            if saved != fields:
                raise _conflict("NATIVE_RECOVERY_IDENTITY_CONFLICT", "native withdrawal requires exact existing close custody")
            ledger = self._require_action(connection, fields["actionUid"])
            expected = fields | dict(receiptUid=receipt, evidenceDigestSha256=evidence,
                ledgerSequence=ledger["ledger_sequence"], state="DISPATCH_WITHDRAWN",
                dispositionBasis="AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM", mayExecute=False)
            existing = self._native_close_disposition_row(connection, fields["actionUid"])
            if existing is not None:
                if existing != expected:
                    raise _conflict("NATIVE_RECOVERY_DISPOSITION_CONFLICT", "native withdrawal receipt or evidence conflicts")
                return existing | {"disposition": "DUPLICATE"}
            self._require_native_recovery_close_source(connection, fields)
            if (ledger["state"] != "ARMED" or ledger["dispatch_mode"] != "TWO_PHASE_V3"
                    or ledger["receipt_uid"] is not None
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE permit_uid=?", (fields["permitUid"],)).fetchone()):
                raise UpdaterStoreError("NATIVE_RECOVERY_WITHDRAWAL_DENIED", "withdrawal requires an authorized unresolved native close")
            if (connection.execute("SELECT 1 FROM physical_action_ledger WHERE receipt_uid=?", (receipt,)).fetchone()
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE resolution_uid=?", (receipt,)).fetchone()):
                raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "withdrawal receipt is already used")
            self._require_receipt_not_withdrawn(connection, receipt)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close_disposition'").fetchone():
                connection.execute(self._native_close_disposition_schema())
            raw = json.dumps(expected, sort_keys=True, separators=(",", ":"))
            try:
                connection.execute("INSERT INTO native_recovery_close_disposition VALUES (?, ?, ?, ?, ?, ?)",
                    (fields["actionUid"], receipt, raw, hashlib.sha256(raw.encode("ascii")).hexdigest(),
                        hashlib.sha256(json.dumps(dict(ledger), sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest(),
                        _format_utc(self._utc_now())))
            except sqlite3.IntegrityError as error:
                raise _conflict("NATIVE_RECOVERY_DISPOSITION_CONFLICT", "native withdrawal receipt is already used") from error
            return expected | {"disposition": "ACCEPTED"}

    def get_native_recovery_close_disposition(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"actionUid"}:
            raise UpdaterStoreError("REQUEST_INVALID", "native disposition query requires exact actionUid")
        action_uid = _require_uuid4(payload["actionUid"], "actionUid")
        with self._lock:
            connection = self._require_connection()
            self._require_candidate(connection)
            self._native_recovery_close_row(connection, action_uid)
            result = self._native_close_disposition_row(connection, action_uid)
            if result is None:
                raise UpdaterStoreError("NATIVE_RECOVERY_DISPOSITION_NOT_FOUND", "native recovery close disposition does not exist")
            return result | {"disposition": "FOUND"}

    def isolate_native_recovery_close_after_reboot(self, payload):
        from job_safety import NATIVE_RECOVERY_CLOSE_ISOLATE_FIELDS
        return self._isolate_native_recovery_close_after_reboot(payload, NATIVE_RECOVERY_CLOSE_ISOLATE_FIELDS)

    def isolate_native_recovery_close_successor_after_reboot(self, payload):
        from job_safety import NATIVE_RECOVERY_CLOSE_SUCCESSOR_ISOLATE_FIELDS
        return self._isolate_native_recovery_close_after_reboot(payload, NATIVE_RECOVERY_CLOSE_SUCCESSOR_ISOLATE_FIELDS)

    def _isolate_native_recovery_close_after_reboot(self, payload, expected_fields):
        """Caller owns durable fresh-handshake/dispatch evidence; past effect stays unknown.

        A later positive MCU boot prevents this target from executing again. It
        does not show what happened before reset, close a job, or grant admission.
        """
        from job_safety import NATIVE_RECOVERY_CLOSE_BOOT_OBSERVATION_FIELDS
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise UpdaterStoreError("REQUEST_INVALID", "native reboot isolation requires exact fields")
        fields = self._native_recovery_close_identity({key: value for key, value in payload.items()
            if key not in NATIVE_RECOVERY_CLOSE_BOOT_OBSERVATION_FIELDS | {"receiptUid", "isolationEvidenceSha256"}})
        observation = self._native_close_isolation_observation(payload, fields["targetMcuBootId"])
        receipt = _require_uuid4(payload["receiptUid"], "receiptUid")
        evidence = _require_sha256(payload["isolationEvidenceSha256"], "isolationEvidenceSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            if self._native_recovery_close_row(connection, fields["actionUid"]) != fields:
                raise _conflict("NATIVE_RECOVERY_IDENTITY_CONFLICT", "native isolation requires exact existing close custody")
            ledger = self._require_action(connection, fields["actionUid"])
            expected = fields | observation | dict(receiptUid=receipt, evidenceDigestSha256=evidence,
                ledgerSequence=ledger["ledger_sequence"], state="ISOLATED_BY_REBOOT",
                dispositionBasis="NEWER_MCU_BOOT_COMMAND_TARGET_ISOLATED", pastEffect="UNKNOWN", mayExecute=False)
            existing = self._native_close_disposition_row(connection, fields["actionUid"])
            if existing is not None:
                if existing != expected:
                    raise _conflict("NATIVE_RECOVERY_DISPOSITION_CONFLICT", "native isolation receipt or evidence conflicts")
                return existing | {"disposition": "DUPLICATE"}
            self._require_native_recovery_close_source(connection, fields)
            if (ledger["state"] != "ARMED" or ledger["dispatch_mode"] != "TWO_PHASE_V3"
                    or ledger["receipt_uid"] is not None
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE permit_uid=?", (fields["permitUid"],)).fetchone()):
                raise UpdaterStoreError("NATIVE_RECOVERY_ISOLATION_DENIED", "isolation requires an authorized unresolved native close")
            if (connection.execute("SELECT 1 FROM physical_action_ledger WHERE receipt_uid=?", (receipt,)).fetchone()
                    or connection.execute("SELECT 1 FROM physical_action_unknown_effect_resolution WHERE resolution_uid=?", (receipt,)).fetchone()):
                raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "isolation receipt is already used")
            self._require_receipt_not_withdrawn(connection, receipt)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close_disposition'").fetchone():
                connection.execute(self._native_close_disposition_schema())
            raw = json.dumps(expected, sort_keys=True, separators=(",", ":"))
            try:
                connection.execute("INSERT INTO native_recovery_close_disposition VALUES (?, ?, ?, ?, ?, ?)",
                    (fields["actionUid"], receipt, raw, hashlib.sha256(raw.encode("ascii")).hexdigest(),
                        hashlib.sha256(json.dumps(dict(ledger), sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest(),
                        _format_utc(self._utc_now())))
            except sqlite3.IntegrityError as error:
                raise _conflict("NATIVE_RECOVERY_DISPOSITION_CONFLICT", "native isolation receipt is already used") from error
            return expected | {"disposition": "ACCEPTED"}

    def arm_physical_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bind the last-moment hardware permission to one live call token."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        dispatch_token = _require_dispatch_attempt_token(
            payload.get("dispatchAttemptToken")
        )
        token_digest = _dispatch_token_digest(dispatch_token)
        runtime_instance_uid = self._require_runtime_instance_uid()
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            recovery = self._native_recovery_close_row(connection, action_uid)
            if self._native_close_disposition_row(connection, action_uid) is not None:
                return self._action_result(row, disposition="DENIED", may_execute=False)
            if row["state"] == "ARMED":
                if recovery is not None:
                    self._require_native_recovery_close_gate(connection, recovery)
                same_live_attempt = (
                    row["dispatch_mode"] == "TWO_PHASE_V3"
                    and row["arm_runtime_instance_uid"]
                    == runtime_instance_uid
                    and isinstance(
                        row["dispatch_attempt_token_sha256"], str
                    )
                    and hmac.compare_digest(
                        row["dispatch_attempt_token_sha256"],
                        token_digest,
                    )
                )
                return self._action_result(
                    row,
                    disposition=(
                        "DUPLICATE" if same_live_attempt else "DENIED"
                    ),
                    may_execute=same_live_attempt,
                )
            if row["state"] != "PREPARED":
                return self._action_result(row, disposition="DENIED")
            prepared_token_matches = (
                isinstance(row["dispatch_attempt_token_sha256"], str)
                and hmac.compare_digest(
                    row["dispatch_attempt_token_sha256"],
                    token_digest,
                )
            )
            if not prepared_token_matches:
                return self._action_result(row, disposition="DENIED")

            permit = self._require_permit(connection, row["permit_uid"])
            if permit["state"] != "ACTIVE":
                raise UpdaterStoreError(
                    "JOB_PERMIT_STATE_CONFLICT",
                    "physical action requires an active permit",
                )
            self._require_prepared_action_dispatch_gate(
                connection,
                action_uid,
            )
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """UPDATE physical_action_ledger
                       SET state='ARMED', dispatch_mode='TWO_PHASE_V3',
                           arm_runtime_instance_uid=?,
                           armed_at=?, updated_at=?
                       WHERE action_uid=? AND state='PREPARED'""",
                    (
                        runtime_instance_uid,
                        now,
                        now,
                        action_uid,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "PHYSICAL_ACTION_DISPATCH_TOKEN_CONFLICT",
                    "physical action dispatch attempt conflicts",
                ) from error
            self._lock_for_armed_action(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
                may_execute=True,
            )

    def cancel_prepared_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Close a reservation when permanent state proves it was not armed."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        receipt_uid = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        dispatch_token = _require_dispatch_attempt_token(
            payload.get("dispatchAttemptToken")
        )
        token_digest = _dispatch_token_digest(dispatch_token)
        evidence = _require_sha256(
            payload.get("evidenceDigestSha256"),
            "evidenceDigestSha256",
        )
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            token_matches_preparing_attempt = (
                row["dispatch_mode"] == "PREPARED_ONLY"
                and isinstance(row["dispatch_attempt_token_sha256"], str)
                and hmac.compare_digest(
                    row["dispatch_attempt_token_sha256"],
                    token_digest,
                )
            )
            if not token_matches_preparing_attempt:
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_CANCEL_DENIED",
                    "physical action is not owned by this preparing attempt",
                )
            if row["state"] == "CONFIRMED":
                if self._confirmation_matches(
                    row,
                    receipt_uid=receipt_uid,
                    outcome="NOT_EXECUTED",
                    confirmation_basis="PREPARED_NOT_ARMED",
                    evidence=evidence,
                ):
                    return self._action_result(
                        row,
                        disposition="DUPLICATE",
                    )
                raise _conflict(
                    "PHYSICAL_ACTION_RECEIPT_CONFLICT",
                    "physical action receipt conflicts",
                )
            if row["state"] != "PREPARED":
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_ALREADY_ARMED",
                    "an armed physical action cannot be cancelled as unexecuted",
                )
            now = _format_utc(self._utc_now())
            self._confirm_action_row(
                connection,
                action_uid=action_uid,
                receipt_uid=receipt_uid,
                outcome="NOT_EXECUTED",
                confirmation_basis="PREPARED_NOT_ARMED",
                evidence=evidence,
                now=now,
            )
            self._maybe_reopen_after_resolution(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def abort_physical_action_dispatch(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Close an armed action only for its still-live pre-write caller."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        receipt_uid = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        dispatch_token = _require_dispatch_attempt_token(
            payload.get("dispatchAttemptToken")
        )
        token_digest = _dispatch_token_digest(dispatch_token)
        evidence = _require_sha256(
            payload.get("evidenceDigestSha256"),
            "evidenceDigestSha256",
        )
        runtime_instance_uid = self._require_runtime_instance_uid()
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            token_matches_live_attempt = (
                row["dispatch_mode"] == "TWO_PHASE_V3"
                and row["arm_runtime_instance_uid"]
                == runtime_instance_uid
                and isinstance(row["dispatch_attempt_token_sha256"], str)
                and hmac.compare_digest(
                    row["dispatch_attempt_token_sha256"],
                    token_digest,
                )
            )
            if not token_matches_live_attempt:
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_ABORT_DENIED",
                    "physical action dispatch is not owned by this live attempt",
                )
            if row["state"] == "CONFIRMED":
                if self._confirmation_matches(
                    row,
                    receipt_uid=receipt_uid,
                    outcome="NOT_EXECUTED",
                    confirmation_basis="LIVE_DISPATCH_NOT_WRITTEN",
                    evidence=evidence,
                ):
                    return self._action_result(
                        row,
                        disposition="DUPLICATE",
                    )
                raise _conflict(
                    "PHYSICAL_ACTION_RECEIPT_CONFLICT",
                    "physical action receipt conflicts",
                )
            if row["state"] != "ARMED":
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_STATE_CONFLICT",
                    "physical action is not armed",
                )
            now = _format_utc(self._utc_now())
            self._confirm_action_row(
                connection,
                action_uid=action_uid,
                receipt_uid=receipt_uid,
                outcome="NOT_EXECUTED",
                confirmation_basis="LIVE_DISPATCH_NOT_WRITTEN",
                evidence=evidence,
                now=now,
            )
            self._maybe_reopen_after_resolution(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def confirm_live_physical_action_result(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Confirm a fixed-frame result from the still-live dispatch call."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        receipt_uid = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        dispatch_token = _require_dispatch_attempt_token(
            payload.get("dispatchAttemptToken")
        )
        token_digest = _dispatch_token_digest(dispatch_token)
        outcome = _require_enum(
            payload.get("outcome"),
            LIVE_PHYSICAL_ACTION_OUTCOMES,
            "outcome",
        )
        evidence = _require_sha256(
            payload.get("evidenceDigestSha256"),
            "evidenceDigestSha256",
        )
        runtime_instance_uid = self._require_runtime_instance_uid()
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            token_matches_live_attempt = (
                row["dispatch_mode"] == "TWO_PHASE_V3"
                and row["arm_runtime_instance_uid"]
                == runtime_instance_uid
                and isinstance(row["dispatch_attempt_token_sha256"], str)
                and hmac.compare_digest(
                    row["dispatch_attempt_token_sha256"],
                    token_digest,
                )
            )
            if not token_matches_live_attempt:
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_LIVE_RESULT_DENIED",
                    "physical action result is not owned by this live attempt",
                )
            if row["state"] == "CONFIRMED":
                if self._confirmation_matches(
                    row,
                    receipt_uid=receipt_uid,
                    outcome=outcome,
                    confirmation_basis="LIVE_FIXED_FRAME_RESULT",
                    evidence=evidence,
                ):
                    return self._action_result(
                        row,
                        disposition="DUPLICATE",
                    )
                raise _conflict(
                    "PHYSICAL_ACTION_RECEIPT_CONFLICT",
                    "physical action receipt conflicts",
                )
            if row["state"] != "ARMED":
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_STATE_CONFLICT",
                    "physical action is not armed",
                )
            now = _format_utc(self._utc_now())
            self._confirm_action_row(
                connection,
                action_uid=action_uid,
                receipt_uid=receipt_uid,
                outcome=outcome,
                confirmation_basis="LIVE_FIXED_FRAME_RESULT",
                evidence=evidence,
                now=now,
            )
            self._maybe_reopen_after_resolution(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def authorize_physical_action(
        self,
        _payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject the v2 one-step operation; it cannot fence response loss."""

        raise UpdaterStoreError(
            "LEGACY_PHYSICAL_ACTION_PROTOCOL_DISABLED",
            "one-step physical action authorization is disabled",
        )

    def confirm_physical_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        receipt_uid = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        outcome = _require_enum(
            payload.get("outcome"),
            PHYSICAL_ACTION_OUTCOMES,
            "outcome",
        )
        confirmation_basis = _require_enum(
            payload.get("confirmationBasis"),
            PHYSICAL_ACTION_CONFIRMATION_BASES,
            "confirmationBasis",
        )
        if confirmation_basis != "MCU_IDENTITY_BOUND_FACT":
            raise UpdaterStoreError(
                "PHYSICAL_ACTION_CONFIRMATION_BASIS_FORBIDDEN",
                "general confirmation requires an identity-bound MCU fact",
            )
        if outcome == "NOT_EXECUTED":
            raise UpdaterStoreError(
                "PHYSICAL_ACTION_NOT_EXECUTED_REQUIRES_ABORT",
                "an armed action cannot be declared unexecuted by general confirmation",
            )
        evidence = _require_sha256(payload.get("evidenceDigestSha256"), "evidenceDigestSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            if row["receipt_uid"] is not None:
                if self._confirmation_matches(
                    row,
                    receipt_uid=receipt_uid,
                    outcome=outcome,
                    confirmation_basis=confirmation_basis,
                    evidence=evidence,
                ):
                    return self._action_result(row, disposition="DUPLICATE")
                raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "physical action receipt conflicts")
            if row["state"] != "ARMED":
                raise UpdaterStoreError("PHYSICAL_ACTION_STATE_CONFLICT", "physical action is not armed")
            now = _format_utc(self._utc_now())
            self._confirm_action_row(
                connection,
                action_uid=action_uid,
                receipt_uid=receipt_uid,
                outcome=outcome,
                confirmation_basis=confirmation_basis,
                evidence=evidence,
                now=now,
            )
            self._maybe_reopen_after_resolution(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def quarantine_unknown_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve an ARMED action without inventing its physical outcome.

        The original ledger row deliberately remains ARMED.  This append-only
        fact only records that a separately evidenced recovery procedure made
        it safe to cancel the owning job.
        """

        resolution_uid = _require_uuid4(
            payload.get("resolutionUid"), "resolutionUid"
        )
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        work_uid = _require_uuid4(payload.get("workUid"), "workUid")
        command_uid = _require_uuid4(
            payload.get("commandUid"), "commandUid"
        )
        action_key = _require_action_key(payload.get("actionKey"))
        action_kind = _require_token(payload.get("actionKind"), "actionKind")
        action_digest = _require_sha256(
            payload.get("actionDigestSha256"), "actionDigestSha256"
        )
        expected_sequence = _require_positive_int(
            payload.get("expectedLedgerSequence"),
            "expectedLedgerSequence",
        )
        evidence_digest = _require_sha256(
            payload.get("evidenceDigestSha256"),
            "evidenceDigestSha256",
        )
        requested_identity = (
            permit_uid,
            work_uid,
            command_uid,
            action_key,
            action_kind,
            action_digest,
            expected_sequence,
        )
        with self._transaction() as connection:
            self._require_candidate(connection)
            action = self._require_action(connection, action_uid)
            if self._native_close_disposition_row(connection, action_uid) is not None:
                raise UpdaterStoreError("NATIVE_RECOVERY_DISPATCH_WITHDRAWN", "native close dispatch was withdrawn or isolated by reboot")
            self._require_receipt_not_withdrawn(connection, resolution_uid)
            actual_identity = (
                action["permit_uid"],
                action["work_uid"],
                action["command_uid"],
                action["action_key"],
                action["action_kind"],
                action["action_digest_sha256"],
                action["ledger_sequence"],
            )
            if actual_identity != requested_identity:
                raise _conflict(
                    "PHYSICAL_ACTION_IDENTITY_MISMATCH",
                    "unknown-effect resolution does not match the exact action",
                )
            existing = self._unknown_effect_resolution_row(
                connection, action_uid
            )
            if existing is not None:
                if (
                    existing["resolution_uid"] == resolution_uid
                    and existing["evidence_digest_sha256"] == evidence_digest
                ):
                    return self._unknown_effect_resolution_result(
                        existing, disposition="DUPLICATE"
                    )
                raise _conflict(
                    "PHYSICAL_ACTION_QUARANTINE_CONFLICT",
                    "unknown-effect resolution identity or evidence conflicts",
                )
            if action["state"] != "ARMED":
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_STATE_CONFLICT",
                    "only an armed action with an unknown effect can be quarantined",
                )
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """INSERT INTO
                           physical_action_unknown_effect_resolution (
                               resolution_uid, action_uid, permit_uid,
                               work_uid, command_uid, action_key, action_kind,
                               action_digest_sha256,
                               expected_ledger_sequence, resolution_state,
                               evidence_digest_sha256, resolved_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                                     'UNKNOWN_EFFECT_QUARANTINED', ?, ?)""",
                    (
                        resolution_uid,
                        action_uid,
                        *requested_identity,
                        evidence_digest,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "PHYSICAL_ACTION_QUARANTINE_CONFLICT",
                    "unknown-effect resolution identity is already in use",
                ) from error
            self._maybe_reopen_after_resolution(connection, now)
            return self._unknown_effect_resolution_result(
                self._require_unknown_effect_resolution(
                    connection, action_uid
                ),
                disposition="ACCEPTED",
            )

    def get_physical_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        with self._lock:
            connection = self._require_connection()
            self._require_candidate(connection)
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="FOUND",
                unknown_effect_resolution=self._unknown_effect_resolution_row(
                    connection, action_uid
                ),
            )

    @staticmethod
    def _confirmation_matches(
        row: sqlite3.Row,
        *,
        receipt_uid: str,
        outcome: str,
        confirmation_basis: str,
        evidence: str,
    ) -> bool:
        return (
            row["receipt_uid"] == receipt_uid
            and row["confirmed_outcome"] == outcome
            and row["confirmation_basis"] == confirmation_basis
            and row["evidence_digest_sha256"] == evidence
        )

    @staticmethod
    def _confirm_action_row(
        connection: sqlite3.Connection,
        *,
        action_uid: str,
        receipt_uid: str,
        outcome: str,
        confirmation_basis: str,
        evidence: str,
        now: str,
    ) -> None:
        if UpdaterStore._native_close_disposition_row(connection, action_uid) is not None:
            raise UpdaterStoreError("NATIVE_RECOVERY_DISPATCH_WITHDRAWN", "native close dispatch was withdrawn or isolated by reboot")
        UpdaterStore._require_receipt_not_withdrawn(connection, receipt_uid)
        try:
            connection.execute(
                """UPDATE physical_action_ledger
                   SET state='CONFIRMED', receipt_uid=?,
                       confirmed_outcome=?, confirmation_basis=?,
                       evidence_digest_sha256=?, confirmed_at=?, updated_at=?
                   WHERE action_uid=?""",
                (
                    receipt_uid,
                    outcome,
                    confirmation_basis,
                    evidence,
                    now,
                    now,
                    action_uid,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise _conflict(
                "PHYSICAL_ACTION_RECEIPT_CONFLICT",
                "physical action receipt identity is already in use",
            ) from error

    @staticmethod
    def _lock_for_armed_action(
        connection: sqlite3.Connection,
        now: str,
    ) -> None:
        state = UpdaterStore._management_row(connection)
        gate = state["job_gate_state"]
        if gate == "LOCKED":
            if not (
                state["block_reason_code"] == "ACTIVE_JOB_IN_PROGRESS"
                and not state["reconciliation_required"]
            ):
                return
            block_reason = "PHYSICAL_ACTION_UNCONFIRMED"
        elif gate in {"OPEN", "DRAINING"}:
            block_reason = (
                "DRAINING_ACTION_UNCONFIRMED"
                if gate == "DRAINING"
                else "PHYSICAL_ACTION_UNCONFIRMED"
            )
        else:
            return
        connection.execute(
            """UPDATE updater_management_state
               SET management_state_sequence=management_state_sequence+1,
                   job_gate_state='LOCKED', maintenance_state='LOCKED',
                   reconciliation_required=1, block_reason_code=?,
                   updated_at=? WHERE singleton_id=1""",
            (block_reason, now),
        )

    def _maybe_reopen_after_resolution(
        self,
        connection: sqlite3.Connection,
        now: str,
    ) -> None:
        state = self._management_row(connection)
        if not state["stage4_candidate_enabled"]:
            return
        if connection.execute(
            "SELECT 1 FROM operator_job_gate_lock WHERE singleton_id=1"
        ).fetchone() is not None:
            return
        unresolved = self._count_unresolved_actions(connection)
        active = self._count_nonterminal_permits(connection)
        maintenance = connection.execute("SELECT 1 FROM maintenance_lock").fetchone()
        if unresolved:
            return
        if (
            active
            and maintenance is not None
            and state["job_gate_state"] == "LOCKED"
            and state["block_reason_code"]
            == "DRAINING_ACTION_UNCONFIRMED"
        ):
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='DRAINING',
                       maintenance_state='DRAINING',
                       reconciliation_required=0,
                       block_reason_code='MAINTENANCE_DRAINING',
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )
            return
        if active:
            if state["job_gate_state"] == "LOCKED" and state["block_reason_code"] in _AUTO_RESOLVABLE_LOCK_REASONS:
                connection.execute(
                    """UPDATE updater_management_state
                       SET management_state_sequence=
                               management_state_sequence+1,
                           reconciliation_required=0,
                           block_reason_code='ACTIVE_JOB_IN_PROGRESS',
                           updated_at=? WHERE singleton_id=1""",
                    (now,),
                )
            return
        if (
            maintenance is not None
            and state["job_gate_state"] == "LOCKED"
            and state["block_reason_code"]
            in _AUTO_RESOLVABLE_LOCK_REASONS
        ):
            # A restart can temporarily replace the maintenance reason with
            # the stricter active-job reconciliation reason.  Once that job is
            # resolved, the retained maintenance owner is still authoritative;
            # never leave a stale active-job reason or reopen the gate.
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='LOCKED', maintenance_state='LOCKED',
                       reconciliation_required=1,
                       block_reason_code='MAINTENANCE_RECOVERY_REQUIRED',
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )
            return
        if (
            maintenance is None
            and self._candidate_activation_state(connection) == "REQUIRED"
        ):
            # Resolving an inherited job must not silently activate a new
            # candidate cycle.  This fact lives outside the visible lock
            # reason because active-job and physical-action reconciliation can
            # temporarily take precedence over STAGE4_ACTIVATION_REQUIRED.
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='LOCKED', maintenance_state='LOCKED',
                       reconciliation_required=1,
                       block_reason_code='STAGE4_ACTIVATION_REQUIRED',
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )
            return
        if (
            maintenance is None
            and state["job_gate_state"] == "LOCKED"
            and state["block_reason_code"] in _AUTO_RESOLVABLE_LOCK_REASONS
        ):
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='OPEN', maintenance_state='IDLE',
                       reconciliation_required=0, block_reason_code=NULL,
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )

    @staticmethod
    def _permit_result(
        row: sqlite3.Row,
        *,
        disposition: str,
        may_start: bool | None = None,
    ) -> dict[str, Any]:
        state = row["state"]
        return {
            "disposition": disposition,
            "permitUid": row["permit_uid"],
            "workUid": row["work_uid"],
            "commandUid": row["command_uid"],
            "workType": row["work_type"],
            "requestDigestSha256": row["request_digest_sha256"],
            "state": state,
            "mayStart": state == "GRANTED" if may_start is None else may_start,
            "beginUid": row["begin_uid"],
            "permitDigestSha256": row["permit_digest_sha256"],
            "dispositionUid": row["disposition_uid"],
            "abandonEvidenceSha256": row["abandon_evidence_sha256"],
            "completionUid": row["completion_uid"],
            "completionOutcome": row["completion_outcome"],
            "completionDigestSha256": row["completion_digest_sha256"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _action_result(
        row: sqlite3.Row,
        *,
        disposition: str,
        may_execute: bool = False,
        unknown_effect_resolution: sqlite3.Row | None = None,
    ) -> dict[str, Any]:
        state = row["state"]
        return {
            "disposition": disposition,
            "actionUid": row["action_uid"],
            "permitUid": row["permit_uid"],
            "workUid": row["work_uid"],
            "commandUid": row["command_uid"],
            "actionKey": row["action_key"],
            "actionKind": row["action_kind"],
            "actionDigestSha256": row["action_digest_sha256"],
            "ledgerSequence": row["ledger_sequence"],
            "state": state,
            "dispatchMode": row["dispatch_mode"],
            # Only ARM_PHYSICAL_ACTION supplies this explicit true value.
            # GET, PREPARE, confirmations and denials report history only.
            "mayExecute": may_execute,
            "confirmedOutcome": row["confirmed_outcome"],
            "confirmationBasis": row["confirmation_basis"],
            "receiptUid": row["receipt_uid"],
            "evidenceDigestSha256": row["evidence_digest_sha256"],
            "unknownEffectResolution": (
                UpdaterStore._unknown_effect_resolution_result(
                    unknown_effect_resolution,
                    disposition="FOUND",
                )
                if unknown_effect_resolution is not None
                else None
            ),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _unknown_effect_resolution_result(
        row: sqlite3.Row,
        *,
        disposition: str,
    ) -> dict[str, Any]:
        return {
            "disposition": disposition,
            "resolutionUid": row["resolution_uid"],
            "actionUid": row["action_uid"],
            "permitUid": row["permit_uid"],
            "workUid": row["work_uid"],
            "commandUid": row["command_uid"],
            "actionKey": row["action_key"],
            "actionKind": row["action_kind"],
            "actionDigestSha256": row["action_digest_sha256"],
            "expectedLedgerSequence": row["expected_ledger_sequence"],
            "resolutionState": row["resolution_state"],
            "evidenceDigestSha256": row["evidence_digest_sha256"],
            "resolvedAt": row["resolved_at"],
        }

    @staticmethod
    def _job_gate_operation_request(
        payload: dict[str, Any],
    ) -> tuple[str, str, int]:
        return (
            _require_uuid4(payload.get("operationUid"), "operationUid"),
            _require_sha256(payload.get("evidenceDigest"), "evidenceDigest"),
            _require_positive_int(
                payload.get("expectedManagementStateSequence"),
                "expectedManagementStateSequence",
            ),
        )

    @staticmethod
    def _require_management_sequence(
        state: sqlite3.Row,
        expected_sequence: int,
    ) -> None:
        if state["management_state_sequence"] != expected_sequence:
            raise UpdaterStoreError(
                "MANAGEMENT_SEQUENCE_MISMATCH",
                "the expected updater management state sequence does not match",
            )

    @staticmethod
    def _job_gate_operation_row(
        connection: sqlite3.Connection,
        operation_uid: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT * FROM job_gate_control_operation
               WHERE operation_uid=?""",
            (operation_uid,),
        ).fetchone()

    def _existing_job_gate_operation(
        self,
        connection: sqlite3.Connection,
        *,
        operation_uid: str,
        operation_kind: str,
        evidence_digest: str,
        expected_sequence: int,
    ) -> sqlite3.Row | None:
        row = self._job_gate_operation_row(connection, operation_uid)
        if row is None:
            return None
        identity = (
            row["operation_kind"],
            row["evidence_digest_sha256"],
            row["expected_management_state_sequence"],
        )
        if identity != (
            operation_kind,
            evidence_digest,
            expected_sequence,
        ):
            raise _conflict(
                "JOB_GATE_CONTROL_OPERATION_CONFLICT",
                "job-gate control operation identity conflicts",
            )
        return row

    def _insert_job_gate_operation(
        self,
        connection: sqlite3.Connection,
        *,
        operation_uid: str,
        operation_kind: str,
        evidence_digest: str,
        expected_sequence: int,
        previous_sequence: int,
        resulting_sequence: int,
        previous_gate_state: str,
        previous_block_reason_code: str | None,
        resulting_gate_state: str,
        resulting_block_reason_code: str | None,
        state_changed: bool,
        created_at: str,
    ) -> sqlite3.Row:
        connection.execute(
            """INSERT INTO job_gate_control_operation (
                   operation_uid, operation_kind,
                   evidence_digest_sha256,
                   expected_management_state_sequence,
                   previous_management_state_sequence,
                   resulting_management_state_sequence,
                   previous_job_gate_state,
                   previous_block_reason_code,
                   resulting_job_gate_state,
                   resulting_block_reason_code,
                   state_changed, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                operation_uid,
                operation_kind,
                evidence_digest,
                expected_sequence,
                previous_sequence,
                resulting_sequence,
                previous_gate_state,
                previous_block_reason_code,
                resulting_gate_state,
                resulting_block_reason_code,
                int(state_changed),
                created_at,
            ),
        )
        row = self._job_gate_operation_row(connection, operation_uid)
        if row is None:
            raise RuntimeError("job-gate control evidence was not persisted")
        return row

    @staticmethod
    def _job_gate_operation_result(
        row: sqlite3.Row,
        *,
        disposition: str | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "operationSequence": row["operation_sequence"],
            "operationUid": row["operation_uid"],
            "operationKind": row["operation_kind"],
            "evidenceDigest": row["evidence_digest_sha256"],
            "expectedManagementStateSequence": row[
                "expected_management_state_sequence"
            ],
            "previousManagementStateSequence": row[
                "previous_management_state_sequence"
            ],
            "resultingManagementStateSequence": row[
                "resulting_management_state_sequence"
            ],
            "previousJobGateState": row["previous_job_gate_state"],
            "previousBlockReasonCode": row[
                "previous_block_reason_code"
            ],
            "resultingJobGateState": row["resulting_job_gate_state"],
            "resultingBlockReasonCode": row[
                "resulting_block_reason_code"
            ],
            "stateChanged": bool(row["state_changed"]),
            "createdAt": row["created_at"],
        }
        if disposition is not None:
            result["disposition"] = disposition
        return result

    def _job_gate_reconciliation_result(
        self,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        state = self._management_row(connection)
        maintenance = connection.execute(
            """SELECT phase FROM maintenance_lock
               WHERE singleton_id=1"""
        ).fetchone()
        maintenance_present = maintenance is not None
        operator_lock = connection.execute(
            """SELECT operator_job_gate_lock.*,
                      job_gate_control_operation.evidence_digest_sha256
               FROM operator_job_gate_lock
               JOIN job_gate_control_operation USING (operation_uid)
               WHERE operator_job_gate_lock.singleton_id=1"""
        ).fetchone()
        active_permits = self._count_nonterminal_permits(connection)
        unresolved_actions = self._count_unresolved_actions(connection)
        latest = connection.execute(
            """SELECT * FROM job_gate_control_operation
               ORDER BY operation_sequence DESC LIMIT 1"""
        ).fetchone()
        initial_activation_eligible = bool(
            self._candidate_activation_state(connection) == "REQUIRED"
            and state["stage4_candidate_enabled"]
            and state["job_gate_mode"] == "ENFORCED"
            and state["job_gate_state"] == "LOCKED"
            and state["maintenance_state"] == "LOCKED"
            and state["reconciliation_required"]
            and state["block_reason_code"]
            == "STAGE4_ACTIVATION_REQUIRED"
            and not maintenance_present
            and operator_lock is None
            and active_permits == 0
            and unresolved_actions == 0
        )
        return {
            "schemaVersion": UPDATER_SCHEMA_VERSION,
            "jobGateControlExtensionVersion": (
                JOB_GATE_CONTROL_EXTENSION_VERSION
            ),
            "candidateActivationState": self._candidate_activation_state(
                connection
            ),
            "stage4CandidateEnabled": bool(
                state["stage4_candidate_enabled"]
            ),
            "managementStateSequence": state[
                "management_state_sequence"
            ],
            "jobGateMode": state["job_gate_mode"],
            "jobGateState": state["job_gate_state"],
            "maintenanceState": state["maintenance_state"],
            "reconciliationRequired": bool(
                state["reconciliation_required"]
            ),
            "blockReasonCode": state["block_reason_code"],
            "maintenanceLockPresent": maintenance_present,
            "maintenancePhase": (
                maintenance["phase"] if maintenance is not None else None
            ),
            "operatorSafetyLockActive": operator_lock is not None,
            "operatorSafetyLock": (
                {
                    "operationUid": operator_lock["operation_uid"],
                    "evidenceDigest": operator_lock[
                        "evidence_digest_sha256"
                    ],
                    "acquiredManagementStateSequence": operator_lock[
                        "acquired_management_state_sequence"
                    ],
                    "underlyingJobGateState": operator_lock[
                        "underlying_job_gate_state"
                    ],
                    "underlyingMaintenanceState": operator_lock[
                        "underlying_maintenance_state"
                    ],
                    "underlyingReconciliationRequired": bool(
                        operator_lock[
                            "underlying_reconciliation_required"
                        ]
                    ),
                    "underlyingBlockReasonCode": operator_lock[
                        "underlying_block_reason_code"
                    ],
                    "underlyingMaintenancePhase": operator_lock[
                        "underlying_maintenance_phase"
                    ],
                    "acquiredAt": operator_lock["acquired_at"],
                }
                if operator_lock is not None
                else None
            ),
            "activeJobPermitCount": active_permits,
            "unreconciledPhysicalActionCount": unresolved_actions,
            "initialActivationEligible": initial_activation_eligible,
            "lastControlOperation": (
                self._job_gate_operation_result(
                    latest,
                    disposition=None,
                )
                if latest is not None
                else None
            ),
        }

    def _require_candidate(self, connection: sqlite3.Connection) -> sqlite3.Row:
        state = self._management_row(connection)
        if not state["stage4_candidate_enabled"]:
            raise UpdaterStoreError(
                "FEATURE_DISABLED",
                "stage-four job safety candidate is disabled",
            )
        return state

    @staticmethod
    def _require_existing_job_action_gate(
        connection: sqlite3.Connection,
    ) -> None:
        state = UpdaterStore._management_row(connection)
        gate = state["job_gate_state"]
        allowed = gate in {"OPEN", "DRAINING"} or (
            gate == "LOCKED"
            and state["block_reason_code"] in _ACTIVE_JOB_LOCK_REASONS
        )
        if not allowed:
            raise UpdaterStoreError(
                "JOB_GATE_CLOSED",
                "job gate does not allow another physical action",
            )
        quarantined_job = connection.execute(
            """SELECT 1
               FROM physical_action_unknown_effect_resolution resolution
               JOIN job_permit permit
                 ON permit.permit_uid=resolution.permit_uid
               WHERE permit.state='ACTIVE' LIMIT 1"""
        ).fetchone()
        if quarantined_job is not None:
            raise UpdaterStoreError(
                "JOB_QUARANTINE_COMPLETION_REQUIRED",
                "the quarantined job must be cancelled before another action",
            )
        unresolved = connection.execute(
            """SELECT 1
               FROM physical_action_ledger action
               WHERE action.state<>'CONFIRMED'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM physical_action_unknown_effect_resolution resolution
                     WHERE resolution.action_uid=action.action_uid)
               LIMIT 1"""
        ).fetchone()
        if unresolved is not None:
            raise UpdaterStoreError(
                "PHYSICAL_ACTION_RECONCILIATION_REQUIRED",
                "the previous physical action must be confirmed first",
            )

    @staticmethod
    def _require_prepared_action_dispatch_gate(
        connection: sqlite3.Connection,
        action_uid: str,
    ) -> None:
        recovery = UpdaterStore._native_recovery_close_row(connection, action_uid)
        if recovery is not None:
            UpdaterStore._require_native_recovery_close_gate(connection, recovery)
            return
        state = UpdaterStore._management_row(connection)
        gate = state["job_gate_state"]
        allowed = gate in {"OPEN", "DRAINING"} or (
            gate == "LOCKED"
            and state["block_reason_code"] in _ACTIVE_JOB_LOCK_REASONS
        )
        if not allowed:
            raise UpdaterStoreError(
                "JOB_GATE_CLOSED",
                "job gate does not allow this physical action to arm",
            )
        conflicting = connection.execute(
            """SELECT 1
               FROM physical_action_ledger action
               WHERE action.state<>'CONFIRMED'
                 AND action.action_uid<>?
                 AND NOT EXISTS (
                     SELECT 1
                     FROM physical_action_unknown_effect_resolution resolution
                     WHERE resolution.action_uid=action.action_uid)
               LIMIT 1""",
            (action_uid,),
        ).fetchone()
        if conflicting is not None:
            raise UpdaterStoreError(
                "PHYSICAL_ACTION_RECONCILIATION_REQUIRED",
                "the previous physical action must be confirmed first",
            )

    @staticmethod
    def _candidate_activation_state(
        connection: sqlite3.Connection,
    ) -> str:
        row = connection.execute(
            """SELECT candidate_activation_state
               FROM job_gate_control_extension WHERE singleton_id=1"""
        ).fetchone()
        if row is None or row[0] not in {"REQUIRED", "ACTIVE"}:
            raise RuntimeError(
                "updater candidate activation state is unavailable"
            )
        return str(row[0])

    @staticmethod
    def _management_row(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM updater_management_state WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("updater durable state is unavailable")
        return row

    @staticmethod
    def _permit_may_start(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> bool:
        if row["state"] != "GRANTED":
            return False
        state = UpdaterStore._management_row(connection)
        if (
            not state["stage4_candidate_enabled"]
            or UpdaterStore._candidate_activation_state(connection) != "ACTIVE"
        ):
            return False
        return state["job_gate_state"] in {"OPEN", "DRAINING"} or (
            state["job_gate_state"] == "LOCKED"
            and state["block_reason_code"] == "ACTIVE_JOB_RECONCILIATION"
        )

    @staticmethod
    def _permit_row(connection: sqlite3.Connection, permit_uid: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM job_permit WHERE permit_uid=?",
            (permit_uid,),
        ).fetchone()

    def _require_permit(self, connection: sqlite3.Connection, permit_uid: str) -> sqlite3.Row:
        row = self._permit_row(connection, permit_uid)
        if row is None:
            raise UpdaterStoreError("JOB_PERMIT_NOT_FOUND", "job permit does not exist")
        return row

    @staticmethod
    def _action_row(connection: sqlite3.Connection, action_uid: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM physical_action_ledger WHERE action_uid=?",
            (action_uid,),
        ).fetchone()

    def _require_action(self, connection: sqlite3.Connection, action_uid: str) -> sqlite3.Row:
        row = self._action_row(connection, action_uid)
        if row is None:
            raise UpdaterStoreError("PHYSICAL_ACTION_NOT_FOUND", "physical action does not exist")
        self._native_recovery_close_row(connection, action_uid)
        self._native_close_disposition_row(connection, action_uid)
        return row

    @staticmethod
    def _unknown_effect_resolution_row(
        connection: sqlite3.Connection,
        action_uid: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT *
               FROM physical_action_unknown_effect_resolution
               WHERE action_uid=?""",
            (action_uid,),
        ).fetchone()

    def _require_unknown_effect_resolution(
        self,
        connection: sqlite3.Connection,
        action_uid: str,
    ) -> sqlite3.Row:
        row = self._unknown_effect_resolution_row(connection, action_uid)
        if row is None:
            raise RuntimeError(
                "unknown-effect resolution was not persisted"
            )
        return row

    @staticmethod
    def _count_nonterminal_permits(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT COUNT(*) FROM job_permit WHERE state IN ('GRANTED', 'ACTIVE')"
        ).fetchone()[0]

    @staticmethod
    def _count_unresolved_actions(connection: sqlite3.Connection) -> int:
        return connection.execute(
            """SELECT COUNT(*)
               FROM physical_action_ledger action
               WHERE action.state<>'CONFIRMED'
                 AND NOT EXISTS (
                     SELECT 1
                     FROM physical_action_unknown_effect_resolution resolution
                     WHERE resolution.action_uid=action.action_uid)"""
        ).fetchone()[0]

    class _Transaction:
        def __init__(self, store: "UpdaterStore") -> None:
            self.store = store
            self.connection: sqlite3.Connection | None = None

        def __enter__(self) -> sqlite3.Connection:
            self.store._lock.acquire()
            try:
                self.connection = self.store._require_connection()
                self.connection.execute("BEGIN IMMEDIATE")
                return self.connection
            except Exception:
                self.store._lock.release()
                raise

        def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            assert self.connection is not None
            try:
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()
            finally:
                self.store._lock.release()

    def _transaction(self) -> "UpdaterStore._Transaction":
        return self._Transaction(self)

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            self._runtime_instance_uid = None
            if connection is not None:
                connection.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("updater store is not initialized")
        return self._connection

    def _require_runtime_instance_uid(self) -> str:
        if self._runtime_instance_uid is None:
            raise RuntimeError("updater runtime instance is unavailable")
        return self._runtime_instance_uid

    def _verify_private_parent(self) -> None:
        parent = self.path.parent
        try:
            metadata = parent.lstat()
        except FileNotFoundError as error:
            raise PermissionError("updater database parent does not exist") from error
        if parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise PermissionError("updater database parent is not a real directory")
        if os.name == "posix":
            mode = stat.S_IMODE(metadata.st_mode)
            if metadata.st_uid != os.getuid():
                raise PermissionError("updater database parent has an unexpected owner")
            if mode & 0o077 or mode & 0o700 != 0o700:
                raise PermissionError(
                    "updater database parent must have private mode 0700; group/world access is forbidden"
                )

    def _verify_existing_database_file(
        self,
        *,
        require_private_permissions: bool = True,
    ) -> None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if self.path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PermissionError("updater database path must be one single-linked regular file")
        if os.name == "posix":
            if metadata.st_uid != os.getuid():
                raise PermissionError("updater database has an unexpected owner")
            if require_private_permissions and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise PermissionError("updater database permissions are too broad")


def inspect_pristine_stage3_rollback_state(
    path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Read-only proof that removing the permanent updater cannot orphan facts.

    This deliberately does not construct :class:`UpdaterStore`: construction
    would migrate or rewrite runtime posture.  The maintenance installer calls
    this once while the updater is online and again after it has been stopped.
    A normal read-only SQLite connection is required so committed WAL content
    remains visible; ``immutable=1`` would be unsafe here.
    """

    database = Path(path).absolute()
    connection: sqlite3.Connection | None = None
    try:
        present_sidecars = {
            suffix
            for suffix in ("-wal", "-shm", "-journal")
            if os.path.lexists(Path(f"{database}{suffix}"))
        }
        if present_sidecars not in (set(), {"-wal", "-shm"}):
            # Opening WAL without its shared-memory partner may make SQLite
            # create a new -shm file in the managed directory, even for a
            # mode=ro URI.  A hot rollback journal is equally unsuitable for
            # an uninstall proof.  Fail without opening or changing either.
            raise RuntimeError(
                "updater rollback database sidecars are incomplete"
            )
        sidecars_present = bool(present_sidecars)
        if sidecars_present:
            connection = sqlite3.connect(
                f"{database.as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
        else:
            # A closed WAL database can cause SQLite to create fresh -wal/-shm
            # files even through a mode=ro URI when the directory is writable.
            # Deserializing the single, checkpointed main file avoids touching
            # the managed state directory.  The online path above is mandatory
            # whenever sidecars exist so committed WAL facts are never missed.
            raw_database = bytearray(database.read_bytes())
            if (
                len(raw_database) < 100
                or raw_database[:16] != b"SQLite format 3\x00"
                or raw_database[18] not in (1, 2)
                or raw_database[19] not in (1, 2)
            ):
                raise RuntimeError("updater rollback database is unreadable")
            # deserialize() otherwise tries to open filesystem WAL sidecars
            # because the checkpointed image retains WAL read/write markers in
            # header bytes 18/19.  Normalize only this in-memory copy to the
            # rollback journal marker; the managed database is never changed.
            raw_database[18] = 1
            raw_database[19] = 1
            connection = sqlite3.connect(":memory:", timeout=5.0)
            connection.deserialize(bytes(raw_database))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise RuntimeError("updater rollback inspection is not read-only")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN")

        UpdaterStore._verify_v3_schema(connection)
        UpdaterStore._verify_job_gate_control_extension(connection)
        UpdaterStore._verify_unknown_effect_resolution_extension(connection)
        UpdaterStore._verify_v3_invariants(connection)
        UpdaterStore._verify_job_gate_control_invariants(connection)

        v2_statements = UpdaterStore._v2_schema_statements()
        expected_base_schemas = {
            "schema_version": v2_statements[0],
            "updater_runtime_instance": v2_statements[1],
            "updater_management_state": v2_statements[2],
            "maintenance_lock": v2_statements[3],
            "job_permit": v2_statements[4],
            "physical_action_ledger": (
                UpdaterStore._v3_physical_action_schema_statement()
            ),
            "physical_action_v2_evidence_quarantine": (
                UpdaterStore._v3_legacy_evidence_schema_statement()
            ),
            "physical_action_unknown_effect_extension": (
                UpdaterStore._unknown_effect_resolution_metadata_schema_statement()
            ),
            "physical_action_unknown_effect_resolution": (
                UpdaterStore._unknown_effect_resolution_schema_statement()
            ),
        }
        for table, expected_schema in expected_base_schemas.items():
            row = connection.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type='table' AND name=?""",
                (table,),
            ).fetchone()
            if (
                row is None
                or _normalize_schema_sql(row[0])
                != _normalize_schema_sql(expected_schema)
            ):
                raise RuntimeError(
                    "updater rollback refuses a changed base schema"
                )

        explicit_indexes = connection.execute(
            """SELECT name, sql FROM sqlite_master
               WHERE type='index' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
        if (
            len(explicit_indexes) != 1
            or explicit_indexes[0]["name"] != "one_nonterminal_job_permit"
            or _normalize_schema_sql(explicit_indexes[0]["sql"])
            != _normalize_schema_sql(v2_statements[5])
        ):
            raise RuntimeError("updater rollback refuses changed safety indexes")
        if connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type IN ('view', 'trigger') AND name NOT LIKE 'sqlite_%'
               LIMIT 1"""
        ).fetchone() is not None:
            raise RuntimeError(
                "updater rollback refuses unknown database behavior"
            )

        actual_tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }
        if actual_tables != _PRISTINE_ROLLBACK_SCHEMA_TABLES:
            raise RuntimeError(
                "updater rollback refuses an unknown database extension"
            )
        internal_tables = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name LIKE 'sqlite_%'"""
            ).fetchall()
        }
        if internal_tables != {"sqlite_sequence"}:
            raise RuntimeError(
                "updater rollback refuses unknown SQLite internal state"
            )
        sequence_rows = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        }
        if sequence_rows != {"physical_action_ledger": 0}:
            # Both AUTOINCREMENT tables represent irreversible control/action
            # history.  Deleting their rows must not make that history look as
            # though it never existed; sqlite_sequence is the residual fact.
            # The v2-to-v3 empty-table rebuild deterministically leaves the
            # physical ledger at sequence zero, which is the sole pristine
            # internal baseline.
            raise PristineRollbackStateUsed(
                "updater rollback refuses deleted control or action history"
            )

        state = connection.execute(
            """SELECT singleton_id, management_state_sequence,
                      stage4_candidate_enabled, updates_enabled,
                      job_gate_mode, job_gate_state, maintenance_state,
                      reconciliation_required, block_reason_code,
                      business_update_enabled, mcu_update_enabled
               FROM updater_management_state"""
        ).fetchall()
        if len(state) != 1:
            raise RuntimeError("updater rollback management state is incompatible")
        management = state[0]
        sequence = management["management_state_sequence"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
            or tuple(
                management[key]
                for key in (
                    "singleton_id",
                    "stage4_candidate_enabled",
                    "updates_enabled",
                    "job_gate_mode",
                    "job_gate_state",
                    "maintenance_state",
                    "reconciliation_required",
                    "block_reason_code",
                    "business_update_enabled",
                    "mcu_update_enabled",
                )
            )
            != (
                1,
                0,
                0,
                "DISABLED",
                "LOCKED",
                "LOCKED",
                0,
                "STAGE4_CANDIDATE_DISABLED",
                0,
                0,
            )
        ):
            raise PristineRollbackStateUsed(
                "updater rollback requires the pristine disabled posture"
            )

        extension = connection.execute(
            """SELECT extension_version, candidate_activation_state
               FROM job_gate_control_extension WHERE singleton_id=1"""
        ).fetchone()
        if (
            extension is None
            or extension["extension_version"]
            != JOB_GATE_CONTROL_EXTENSION_VERSION
            or extension["candidate_activation_state"] != "REQUIRED"
        ):
            raise PristineRollbackStateUsed(
                "updater rollback requires an unused candidate activation"
            )

        nonempty_tables = tuple(
            table
            for table in (
                "job_gate_control_operation",
                "job_permit",
                "physical_action_ledger",
                "physical_action_v2_evidence_quarantine",
                "physical_action_unknown_effect_resolution",
                "maintenance_lock",
                "operator_job_gate_lock",
            )
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            is not None
        )
        if nonempty_tables:
            raise PristineRollbackStateUsed(
                "updater rollback refuses persisted control or safety facts"
            )

        runtime_instance_count = connection.execute(
            "SELECT COUNT(*) FROM updater_runtime_instance"
        ).fetchone()[0]
        if (
            isinstance(runtime_instance_count, bool)
            or not isinstance(runtime_instance_count, int)
            or runtime_instance_count < 1
        ):
            raise RuntimeError(
                "updater rollback requires an initialized updater database"
            )
        return {
            "schemaVersion": UPDATER_SCHEMA_VERSION,
            "controlExtensionVersion": JOB_GATE_CONTROL_EXTENSION_VERSION,
            "candidateActivationState": "REQUIRED",
            "managementStateSequence": sequence,
            "runtimeInstanceCount": runtime_instance_count,
        }
    except sqlite3.Error as error:
        raise RuntimeError("updater rollback database is unreadable") from error
    finally:
        if connection is not None:
            connection.close()


def _conflict(code: str, message: str) -> UpdaterStoreError:
    return UpdaterStoreError(code, message)


def _normalize_schema_sql(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    tokens: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character in {"'", '"', "`", "["}:
            quote_start = character
            quote_end = "]" if character == "[" else character
            quoted = [character]
            index += 1
            while index < len(value):
                current = value[index]
                quoted.append(current)
                index += 1
                if current != quote_end:
                    continue
                if index < len(value) and value[index] == quote_end:
                    quoted.append(value[index])
                    index += 1
                    continue
                break
            tokens.append(f"quoted:{quote_start}:{''.join(quoted)}")
            continue
        if character.isalnum() or character in {"_", "$"}:
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(f"word:{value[index:end].casefold()}")
            index = end
            continue
        # Punctuation remains a distinct token.  Whitespace can be ignored
        # without ever merging adjacent SQL words, so `work_uid TEXT` cannot
        # collide with the different identifier `work_uidtext`.
        tokens.append(f"punct:{character}")
        index += 1
    return tuple(tokens)


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != value:
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} must be a canonical UUIDv4")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise UpdaterStoreError(
            "REQUEST_INVALID",
            f"{field} must be a positive integer",
        )
    return value


def _require_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _TOKEN_PATTERN.fullmatch(value) is None:
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} is invalid")
    return value


def _require_optional_token(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _require_token(value, field)


def _require_enum(value: Any, values: frozenset[str], field: str) -> str:
    token = _require_token(value, field)
    if token not in values:
        raise UpdaterStoreError("REQUEST_INVALID", f"{field} is unsupported")
    return token


def _require_action_key(value: Any) -> str:
    if not isinstance(value, str) or _ACTION_KEY_PATTERN.fullmatch(value) is None:
        raise UpdaterStoreError("REQUEST_INVALID", "actionKey is invalid")
    return value


def _require_dispatch_attempt_token(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _DISPATCH_ATTEMPT_TOKEN_PATTERN.fullmatch(value) is None
    ):
        raise UpdaterStoreError(
            "REQUEST_INVALID",
            "dispatchAttemptToken must be a 32-128 character base64url token",
        )
    return value


def _dispatch_token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _require_release_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("updater release version must be a string")
    if not 1 <= len(value) <= MAX_RELEASE_VERSION_LENGTH:
        raise ValueError("updater release version length is invalid")
    if value != value.strip() or not value.isprintable():
        raise ValueError("updater release version is invalid")
    return value


def _new_instance_uid(factory: Callable[[], uuid.UUID]) -> str:
    value = factory()
    if not isinstance(value, uuid.UUID) or value.version != 4:
        raise ValueError("updater runtime instance must be a UUIDv4")
    return str(value)


def _format_utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("updater runtime clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
