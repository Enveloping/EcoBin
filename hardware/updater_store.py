"""Private durable state for the permanent device updater.

Stage three installs the updater as an independently supervised process, but
does not yet enable either software updates or the permanent job gate.  This
store therefore persists only facts that are true in that stage: its schema,
real process instances, and the fixed management posture.  In particular it
does not open ``edge.db`` and does not create placeholder update jobs.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UPDATER_SCHEMA_VERSION = 1
UPDATER_COMPONENT = "DEVICE_UPDATER"
MAX_RELEASE_VERSION_LENGTH = 32

class UpdaterStore:
    """Single-owner SQLite store for updater facts.

    A new immutable runtime-instance row is appended whenever ``initialize``
    succeeds.  The instance identity returned by status is consequently the
    identity of this actual process start, not a value inferred from a target
    release or retained only in memory.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        release_version: str,
        utc_now: Callable[[], datetime] | None = None,
        instance_uid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.path = Path(path)
        if self.path.name.casefold() == "edge.db":
            raise ValueError(
                "updater store must not use the business edge.db"
            )
        self.release_version = _require_release_version(release_version)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._instance_uid_factory = instance_uid_factory
        self._connection: sqlite3.Connection | None = None
        self._runtime_instance_uid: str | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        """Open the private database and durably register this process run."""

        with self._lock:
            if self._connection is not None:
                return
            self._verify_private_parent()
            self._verify_existing_database_file()
            database_existed = self.path.exists()
            connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
            )
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
                self._create_schema(connection)
                self._verify_schema_version(connection)
                self._ensure_stage3_management_state(connection)
                self._verify_stage3_management_state(connection)
                instance_uid = _new_instance_uid(
                    self._instance_uid_factory
                )
                started_at = _format_utc(self._utc_now())
                connection.execute(
                    """INSERT INTO updater_runtime_instance (
                           instance_uid,
                           component,
                           release_version,
                           started_at
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

    def _verify_private_parent(self) -> None:
        parent = self.path.parent
        try:
            metadata = parent.lstat()
        except FileNotFoundError as error:
            raise PermissionError(
                "updater database parent does not exist"
            ) from error
        if parent.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise PermissionError(
                "updater database parent is not a real directory"
            )
        # Windows does not implement POSIX owner/group permission semantics.
        # The deployed service is Linux and must fail rather than put WAL/SHM
        # files in a directory writable by another account.
        if os.name == "posix":
            mode = stat.S_IMODE(metadata.st_mode)
            if metadata.st_uid != os.getuid():
                raise PermissionError(
                    "updater database parent has an unexpected owner"
                )
            if mode & 0o077 or mode & 0o700 != 0o700:
                raise PermissionError(
                    "updater database parent must have private mode 0700; "
                    "group/world access is forbidden"
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
        if (
            self.path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise PermissionError(
                "updater database path must be one single-linked regular file"
            )
        if os.name == "posix":
            if metadata.st_uid != os.getuid():
                raise PermissionError(
                    "updater database has an unexpected owner"
                )
            if (
                require_private_permissions
                and stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise PermissionError(
                    "updater database permissions are too broad"
                )

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        # Keep every DDL statement in the explicit transaction opened by
        # ``initialize``.  ``sqlite3.executescript`` commits a pending
        # transaction before running, which could otherwise leave a partial
        # stage-three schema behind after an incompatibility is detected.
        statements = (
            """CREATE TABLE IF NOT EXISTS schema_version (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                version INTEGER NOT NULL CHECK (version > 0)
            )""",
            """INSERT OR IGNORE INTO schema_version(singleton_id, version)
               VALUES (1, 1)""",
            """CREATE TABLE IF NOT EXISTS updater_runtime_instance (
                instance_uid TEXT PRIMARY KEY,
                component TEXT NOT NULL CHECK (component = 'DEVICE_UPDATER'),
                release_version TEXT NOT NULL
                    CHECK (length(release_version) BETWEEN 1 AND 32),
                started_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS updater_management_state (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                management_state_sequence INTEGER NOT NULL
                    CHECK (management_state_sequence >= 1),
                updates_enabled INTEGER NOT NULL
                    CHECK (updates_enabled = 0),
                job_gate_mode TEXT NOT NULL
                    CHECK (job_gate_mode = 'NOT_ENFORCED_STAGE3'),
                maintenance_state TEXT NOT NULL
                    CHECK (maintenance_state = 'IDLE'),
                business_update_enabled INTEGER NOT NULL
                    CHECK (business_update_enabled = 0),
                mcu_update_enabled INTEGER NOT NULL
                    CHECK (mcu_update_enabled = 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _verify_schema_version(connection: sqlite3.Connection) -> None:
        rows = [
            (row[0], row[1])
            for row in connection.execute(
                """SELECT singleton_id, version
                   FROM schema_version ORDER BY singleton_id"""
            ).fetchall()
        ]
        if rows != [(1, UPDATER_SCHEMA_VERSION)]:
            raise RuntimeError("updater database schema is incompatible")

    def _ensure_stage3_management_state(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        now = _format_utc(self._utc_now())
        connection.execute(
            """INSERT OR IGNORE INTO updater_management_state (
                   singleton_id,
                   management_state_sequence,
                   updates_enabled,
                   job_gate_mode,
                   maintenance_state,
                   business_update_enabled,
                   mcu_update_enabled,
                   created_at,
                   updated_at
               ) VALUES (1, 1, 0, 'NOT_ENFORCED_STAGE3', 'IDLE',
                         0, 0, ?, ?)""",
            (now, now),
        )

    @staticmethod
    def _verify_stage3_management_state(
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            """SELECT singleton_id,
                      management_state_sequence,
                      updates_enabled,
                      job_gate_mode,
                      maintenance_state,
                      business_update_enabled,
                      mcu_update_enabled
               FROM updater_management_state"""
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("updater management state is incompatible")
        row = rows[0]
        expected = (
            1,
            1,
            0,
            "NOT_ENFORCED_STAGE3",
            "IDLE",
            0,
            0,
        )
        if tuple(row) != expected:
            raise RuntimeError("updater management state is incompatible")

    def get_status(self) -> dict[str, Any]:
        """Return the persisted facts for this running updater instance."""

        with self._lock:
            connection = self._require_connection()
            instance_uid = self._require_runtime_instance_uid()
            instance = connection.execute(
                """SELECT component, release_version, started_at
                   FROM updater_runtime_instance
                   WHERE instance_uid=?""",
                (instance_uid,),
            ).fetchone()
            state = connection.execute(
                """SELECT management_state_sequence,
                          updates_enabled,
                          job_gate_mode,
                          maintenance_state,
                          business_update_enabled,
                          mcu_update_enabled
                   FROM updater_management_state
                   WHERE singleton_id=1"""
            ).fetchone()
            if instance is None or state is None:
                raise RuntimeError("updater durable state is unavailable")
            return {
                "component": instance["component"],
                "schemaVersion": UPDATER_SCHEMA_VERSION,
                "runtimeInstanceUid": instance_uid,
                "releaseVersion": instance["release_version"],
                "startedAt": instance["started_at"],
                "managementStateSequence": state[
                    "management_state_sequence"
                ],
                "updatesEnabled": bool(state["updates_enabled"]),
                "jobGateMode": state["job_gate_mode"],
                "maintenanceState": state["maintenance_state"],
                "businessUpdateEnabled": bool(
                    state["business_update_enabled"]
                ),
                "mcuUpdateEnabled": bool(state["mcu_update_enabled"]),
            }

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
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("updater runtime clock must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
