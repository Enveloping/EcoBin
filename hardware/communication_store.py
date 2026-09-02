"""Private durable state owned only by the permanent communication agent.

This store must never be pointed at the business program's ``edge.db``.  The
stage-three skeleton records only its schema and immutable process-start facts;
later stages can add transport inbox/outbox tables through explicit migrations.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_RELEASE_VERSION_LENGTH = 32


class CommunicationStore:
    """Single-process SQLite owner for communication-layer facts."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        utc_now: Callable[[], datetime] | None = None,
        process_id: Callable[[], int] = os.getpid,
    ) -> None:
        self.path = Path(path)
        if self.path.name.casefold() == "edge.db":
            raise ValueError("communication store must not use the business edge.db")
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._process_id = process_id
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            try:
                parent = self.path.parent.lstat()
            except FileNotFoundError as error:
                raise PermissionError(
                    "communication database parent does not exist"
                ) from error
            if self.path.parent.is_symlink() or not stat.S_ISDIR(parent.st_mode):
                raise PermissionError(
                    "communication database parent is not a real directory"
                )
            if os.name == "posix":
                if parent.st_uid != os.getuid():
                    raise PermissionError(
                        "communication database parent has an unexpected owner"
                    )
                if stat.S_IMODE(parent.st_mode) & 0o077:
                    raise PermissionError(
                        "communication database parent must have private mode 0700"
                    )
            if self.path.is_symlink():
                raise PermissionError("communication database path must not be a symlink")
            if self.path.exists():
                metadata = self.path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise PermissionError(
                        "communication database path is not a regular file"
                    )
                if os.name == "posix":
                    if metadata.st_nlink != 1:
                        raise PermissionError(
                            "communication database must not have hard links"
                        )
                    if metadata.st_uid != os.getuid():
                        raise PermissionError(
                            "communication database has an unexpected owner"
                        )
                    if stat.S_IMODE(metadata.st_mode) & 0o077:
                        raise PermissionError(
                            "communication database permissions are too broad"
                        )

            connection = sqlite3.connect(
                str(self.path),
                check_same_thread=False,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            try:
                os.chmod(self.path, 0o600)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        singleton_id INTEGER PRIMARY KEY
                            CHECK (singleton_id = 1),
                        version INTEGER NOT NULL CHECK (version > 0)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS process_start_fact (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        start_uid TEXT NOT NULL UNIQUE,
                        release_version TEXT NOT NULL,
                        process_id INTEGER NOT NULL CHECK (process_id > 0),
                        started_at TEXT NOT NULL
                    )
                    """
                )
                row = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton_id=1"
                ).fetchone()
                count = connection.execute(
                    "SELECT COUNT(*) FROM schema_version"
                ).fetchone()[0]
                if row is None and count == 0:
                    connection.execute(
                        "INSERT INTO schema_version(singleton_id, version) VALUES (1, ?)",
                        (SCHEMA_VERSION,),
                    )
                elif row is None or count != 1 or int(row["version"]) != SCHEMA_VERSION:
                    raise RuntimeError(
                        "communication database schema is incompatible"
                    )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
                raise
            self._connection = connection

    def record_process_start(self, release_version: str) -> dict[str, Any]:
        version = _require_release_version(release_version)
        start_uid = str(uuid.uuid4())
        process_id = self._process_id()
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            raise ValueError("communication process ID must be positive")
        started_at = self._now_text()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO process_start_fact
                   (start_uid, release_version, process_id, started_at)
                   VALUES (?, ?, ?, ?)""",
                (start_uid, version, process_id, started_at),
            )
        return {
            "startUid": start_uid,
            "releaseVersion": version,
            "processId": process_id,
            "startedAt": started_at,
        }

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            connection = self._require_connection()
            schema_version = int(
                connection.execute(
                    "SELECT version FROM schema_version WHERE singleton_id=1"
                ).fetchone()[0]
            )
            row = connection.execute(
                """SELECT start_uid, release_version, process_id, started_at
                   FROM process_start_fact ORDER BY sequence DESC LIMIT 1"""
            ).fetchone()
            start_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM process_start_fact"
                ).fetchone()[0]
            )
        latest = None
        if row is not None:
            latest = {
                "startUid": row["start_uid"],
                "releaseVersion": row["release_version"],
                "processId": int(row["process_id"]),
                "startedAt": row["started_at"],
            }
        return {
            "schemaVersion": schema_version,
            "processStartCount": start_count,
            "latestProcessStart": latest,
        }

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("communication store is not initialized")
        return self._connection

    def _now_text(self) -> str:
        value = self._utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("communication clock must be timezone-aware")
        rendered = value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        return rendered.replace("+00:00", "Z")


def _require_release_version(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_RELEASE_VERSION_LENGTH
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError("communication release version is invalid")
    return value
