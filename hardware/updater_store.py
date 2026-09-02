"""Private durable safety state for the permanent device updater.

Schema version two introduces the stage-four candidate job gate and the
minimum physical-action ledger.  The candidate remains fail-closed unless the
process is started with its explicit enable flag.  Software update execution
and privileged-helper mutations remain outside this module and disabled.
"""

from __future__ import annotations

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


UPDATER_SCHEMA_VERSION = 2
UPDATER_COMPONENT = "DEVICE_UPDATER"
MAX_RELEASE_VERSION_LENGTH = 32

JOB_GATE_STATES = frozenset({"OPEN", "DRAINING", "MAINTENANCE", "LOCKED"})
JOB_PERMIT_STATES = frozenset(
    {"GRANTED", "ACTIVE", "ABANDONED", "COMPLETED"}
)
PHYSICAL_ACTION_STATES = frozenset({"MAY_HAVE_EXECUTED", "CONFIRMED"})
JOB_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
JOB_WORK_TYPES = frozenset({"DELIVERY", "CLEAN", "FULLNESS", "BASELINE"})
PHYSICAL_ACTION_OUTCOMES = frozenset(
    {"EXECUTED", "NOT_EXECUTED", "FAILED_SAFE"}
)

_TOKEN_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_ACTION_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
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


class UpdaterStoreError(RuntimeError):
    """A stable failure that can safely cross the local-control boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
                    self._create_v2_schema(connection)
                elif version == 1:
                    self._migrate_v1_to_v2(connection)
                elif version != UPDATER_SCHEMA_VERSION:
                    raise RuntimeError("updater database schema is incompatible")
                self._verify_v2_schema(connection)
                self._apply_runtime_candidate_posture(connection)
                self._verify_v2_invariants(connection)

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
    def _verify_v2_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version ORDER BY singleton_id"
        ).fetchall()
        if [(row[0], row[1]) for row in rows] != [(1, UPDATER_SCHEMA_VERSION)]:
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
        now = _format_utc(self._utc_now())
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
                or state["reconciliation_required"]
            )
            reason = (
                state["block_reason_code"]
                if reconciliation and state["block_reason_code"]
                else "STAGE4_CANDIDATE_DISABLED"
            )
            desired = (0, "DISABLED", "LOCKED", "LOCKED", int(reconciliation), reason)
        elif preserved_explicit_lock:
            desired = (
                1,
                "ENFORCED",
                "LOCKED",
                "LOCKED",
                1,
                state["block_reason_code"],
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
        elif (
            not state["stage4_candidate_enabled"]
            or state["block_reason_code"] == "STAGE4_CANDIDATE_DISABLED"
        ):
            # Merely starting the candidate binary must not activate the
            # physical-job boundary.  Stage three's low-privilege hardware
            # evidence is a separate, controlled prerequisite.  A migration
            # operator must explicitly transition this locked state to OPEN
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
    def _verify_v2_invariants(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT * FROM updater_management_state"
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("updater management state is incompatible")
        state = rows[0]
        unresolved = connection.execute(
            """SELECT COUNT(*) FROM physical_action_ledger
               WHERE state='MAY_HAVE_EXECUTED'"""
        ).fetchone()[0]
        if unresolved and (
            state["job_gate_state"] != "LOCKED"
            or not state["reconciliation_required"]
        ):
            raise RuntimeError("updater safety ledger is inconsistent")
        locks = connection.execute("SELECT COUNT(*) FROM maintenance_lock").fetchone()[0]
        if state["job_gate_state"] in {"DRAINING", "MAINTENANCE"} and locks != 1:
            raise RuntimeError("updater maintenance lock is inconsistent")

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
            maintenance = connection.execute(
                """SELECT owner_update_uid, maintenance_type, phase, fence_token
                   FROM maintenance_lock WHERE singleton_id=1"""
            ).fetchone()
            if instance is None:
                raise RuntimeError("updater durable state is unavailable")
            return {
                "component": instance["component"],
                "schemaVersion": UPDATER_SCHEMA_VERSION,
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
                return self._permit_result(existing, disposition="DUPLICATE")
            state = self._management_row(connection)
            if state["job_gate_state"] != "OPEN":
                raise UpdaterStoreError("JOB_GATE_CLOSED", "job gate does not accept new work")
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
            return self._permit_result(row, disposition="ACCEPTED")

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
                """SELECT COUNT(*) FROM physical_action_ledger
                   WHERE permit_uid=? AND state<>'CONFIRMED'""",
                (permit_uid,),
            ).fetchone()[0]
            if pending:
                raise UpdaterStoreError(
                    "PHYSICAL_ACTION_UNCONFIRMED",
                    "job has an unconfirmed physical action",
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
            return self._permit_result(
                self._require_permit(connection, permit_uid),
                disposition="FOUND",
            )

    def authorize_physical_action(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically record the action and issue its one execution fence."""

        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        arm_uid = _require_uuid4(payload.get("armUid"), "armUid")
        permit_uid = _require_uuid4(payload.get("permitUid"), "permitUid")
        work_uid = _require_uuid4(payload.get("workUid"), "workUid")
        command_uid = _require_uuid4(payload.get("commandUid"), "commandUid")
        action_key = _require_action_key(payload.get("actionKey"))
        action_kind = _require_token(payload.get("actionKind"), "actionKind")
        digest = _require_sha256(
            payload.get("actionDigestSha256"),
            "actionDigestSha256",
        )
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
                if actual != identity or existing["arm_uid"] != arm_uid:
                    raise _conflict(
                        "PHYSICAL_ACTION_CONFLICT",
                        "physical action identity conflicts",
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
                           state, arm_uid, created_at, armed_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'MAY_HAVE_EXECUTED',
                                 ?, ?, ?, ?)""",
                    (
                        action_uid,
                        *identity,
                        arm_uid,
                        now,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "PHYSICAL_ACTION_CONFLICT",
                    "physical action identity is already in use",
                ) from error
            state = self._management_row(connection)
            if state["job_gate_state"] in {
                "OPEN",
                "DRAINING",
                "LOCKED",
            }:
                block_reason = (
                    "DRAINING_ACTION_UNCONFIRMED"
                    if state["job_gate_state"] == "DRAINING"
                    else "PHYSICAL_ACTION_UNCONFIRMED"
                )
                connection.execute(
                    """UPDATE updater_management_state
                       SET management_state_sequence=management_state_sequence+1,
                           job_gate_state='LOCKED', maintenance_state='LOCKED',
                           reconciliation_required=1,
                            block_reason_code=?,
                            updated_at=? WHERE singleton_id=1""",
                    (block_reason, now),
                )
            return self._action_result(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def confirm_physical_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_uid = _require_uuid4(payload.get("actionUid"), "actionUid")
        receipt_uid = _require_uuid4(payload.get("receiptUid"), "receiptUid")
        outcome = _require_enum(
            payload.get("outcome"),
            PHYSICAL_ACTION_OUTCOMES,
            "outcome",
        )
        evidence = _require_sha256(payload.get("evidenceDigestSha256"), "evidenceDigestSha256")
        with self._transaction() as connection:
            self._require_candidate(connection)
            row = self._require_action(connection, action_uid)
            if row["receipt_uid"] is not None:
                if (
                    row["receipt_uid"] == receipt_uid
                    and row["confirmed_outcome"] == outcome
                    and row["evidence_digest_sha256"] == evidence
                ):
                    return self._action_result(row, disposition="DUPLICATE")
                raise _conflict("PHYSICAL_ACTION_RECEIPT_CONFLICT", "physical action receipt conflicts")
            if row["state"] != "MAY_HAVE_EXECUTED":
                raise UpdaterStoreError("PHYSICAL_ACTION_STATE_CONFLICT", "physical action is not armed")
            now = _format_utc(self._utc_now())
            try:
                connection.execute(
                    """UPDATE physical_action_ledger
                       SET state='CONFIRMED', receipt_uid=?, confirmed_outcome=?,
                           evidence_digest_sha256=?, confirmed_at=?, updated_at=?
                       WHERE action_uid=?""",
                    (receipt_uid, outcome, evidence, now, now, action_uid),
                )
            except sqlite3.IntegrityError as error:
                raise _conflict(
                    "PHYSICAL_ACTION_RECEIPT_CONFLICT",
                    "physical action receipt identity is already in use",
                ) from error
            self._maybe_reopen_after_resolution(connection, now)
            return self._action_result(
                self._require_action(connection, action_uid),
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
            )

    def _maybe_reopen_after_resolution(
        self,
        connection: sqlite3.Connection,
        now: str,
    ) -> None:
        state = self._management_row(connection)
        if not state["stage4_candidate_enabled"]:
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
        if maintenance is None and state["job_gate_state"] == "LOCKED" and state["block_reason_code"] in _AUTO_RESOLVABLE_LOCK_REASONS:
            connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=management_state_sequence+1,
                       job_gate_state='OPEN', maintenance_state='IDLE',
                       reconciliation_required=0, block_reason_code=NULL,
                       updated_at=? WHERE singleton_id=1""",
                (now,),
            )

    @staticmethod
    def _permit_result(row: sqlite3.Row, *, disposition: str) -> dict[str, Any]:
        state = row["state"]
        return {
            "disposition": disposition,
            "permitUid": row["permit_uid"],
            "workUid": row["work_uid"],
            "commandUid": row["command_uid"],
            "workType": row["work_type"],
            "requestDigestSha256": row["request_digest_sha256"],
            "state": state,
            "mayStart": state == "GRANTED",
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
    def _action_result(row: sqlite3.Row, *, disposition: str) -> dict[str, Any]:
        state = row["state"]
        return {
            "disposition": disposition,
            "actionUid": row["action_uid"],
            "permitUid": row["permit_uid"],
            "workUid": row["work_uid"],
            "commandUid": row["command_uid"],
            "actionKey": row["action_key"],
            "actionKind": row["action_kind"],
            "ledgerSequence": row["ledger_sequence"],
            "state": state,
            # A duplicate ARM or a later GET reports history only.  It must
            # never renew the one response that allowed a hardware write.
            "mayExecute": (
                state == "MAY_HAVE_EXECUTED"
                and disposition == "ACCEPTED"
            ),
            "confirmedOutcome": row["confirmed_outcome"],
            "receiptUid": row["receipt_uid"],
            "evidenceDigestSha256": row["evidence_digest_sha256"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
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
        unresolved = connection.execute(
            """SELECT 1 FROM physical_action_ledger
               WHERE state='MAY_HAVE_EXECUTED' LIMIT 1"""
        ).fetchone()
        if unresolved is not None:
            raise UpdaterStoreError(
                "PHYSICAL_ACTION_RECONCILIATION_REQUIRED",
                "the previous physical action must be confirmed first",
            )

    @staticmethod
    def _management_row(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM updater_management_state WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("updater durable state is unavailable")
        return row

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
        return row

    @staticmethod
    def _count_nonterminal_permits(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT COUNT(*) FROM job_permit WHERE state IN ('GRANTED', 'ACTIVE')"
        ).fetchone()[0]

    @staticmethod
    def _count_unresolved_actions(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT COUNT(*) FROM physical_action_ledger WHERE state='MAY_HAVE_EXECUTED'"
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


def _conflict(code: str, message: str) -> UpdaterStoreError:
    return UpdaterStoreError(code, message)


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
