"""Durable business-update journal inside the permanent updater database."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from local_control import canonical_local_payload_sha256


BUSINESS_UPDATE_SCHEMA_VERSION = 1
REMOTE_BUSINESS_UPDATE_EXTENSION_VERSION = 1
REMOTE_BUSINESS_CANCELLATION_EXTENSION_VERSION = 1
BUSINESS_UPDATE_TERMINAL_STATES = frozenset(
    {"SUCCEEDED", "ROLLED_BACK", "DEFERRED", "REJECTED", "FAILED_LOCKED"}
)
BUSINESS_UPDATE_CANCELLABLE_STATES = frozenset(
    {"RECEIVED", "VERIFYING_PACKAGE", "PACKAGE_READY", "WAITING_FOR_IDLE"}
)
BUSINESS_UPDATE_STATES = frozenset(
    {
        "RECEIVED",
        "VERIFYING_PACKAGE",
        "PACKAGE_READY",
        "WAITING_FOR_IDLE",
        "MIGRATING_DATA",
        "ACTIVATING",
        "VERIFYING_TARGET",
        "OBSERVING",
        "ROLLING_BACK",
        "VERIFYING_ROLLBACK",
        *BUSINESS_UPDATE_TERMINAL_STATES,
    }
)
BUSINESS_ACTION_POLICIES = {
    "INSPECT_BASELINE": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "GET_BUSINESS_RUNTIME_STATUS",
    ),
    "INSPECT_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "GET_BUSINESS_RUNTIME_STATUS",
    ),
    "INSPECT_ROLLBACK": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "GET_BUSINESS_RUNTIME_STATUS",
    ),
    "STOP_FOR_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "STOP_BUSINESS_RUNTIME",
    ),
    "STOP_BRIDGE_FOR_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "STOP_BUSINESS_BRIDGE",
    ),
    "SNAPSHOT_DATABASE": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "SNAPSHOT_BUSINESS_DATABASE",
    ),
    "INSTALL_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "INSTALL_BUSINESS_RELEASE",
    ),
    "ACTIVATE_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "ACTIVATE_BUSINESS_RELEASE",
    ),
    "START_TARGET": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "START_BUSINESS_RUNTIME",
    ),
    "STOP_FOR_ROLLBACK": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "STOP_BUSINESS_RUNTIME",
    ),
    "RESTORE_DATABASE": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "RESTORE_BUSINESS_DATABASE",
    ),
    "ACTIVATE_ROLLBACK": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "ROLLBACK_BUSINESS_RELEASE",
    ),
    "DEACTIVATE_TARGET_FOR_BRIDGE": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "DEACTIVATE_BUSINESS_RELEASE",
    ),
    "START_ROLLBACK": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "START_BUSINESS_RUNTIME",
    ),
    "START_BRIDGE_ROLLBACK": (
        "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
        "START_BUSINESS_BRIDGE",
    ),
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


class BusinessUpdateStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BusinessUpdateStore:
    """Own business-update tables without opening the business database."""

    def __init__(
        self,
        path: str | Path,
        *,
        utc_now: Callable[[], datetime] | None = None,
        remote_trigger_enabled: bool = False,
    ) -> None:
        self.path = Path(path)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._remote_trigger_enabled = bool(remote_trigger_enabled)
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            connection = sqlite3.connect(
                self.path,
                timeout=10.0,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS business_update_schema (
                        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
                        version INTEGER NOT NULL CHECK (version=1)
                    );
                    INSERT OR IGNORE INTO business_update_schema(singleton_id, version)
                    VALUES (1, 1);

                    CREATE TABLE IF NOT EXISTS business_runtime_update (
                        update_uid TEXT PRIMARY KEY,
                        deployment_uid TEXT NOT NULL,
                        command_uid TEXT NOT NULL UNIQUE,
                        request_digest_sha256 TEXT NOT NULL,
                        release_id TEXT NOT NULL,
                        version_name TEXT NOT NULL,
                        release_sequence INTEGER NOT NULL,
                        package_sha256 TEXT NOT NULL,
                        package_size INTEGER NOT NULL,
                        signing_key_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        step TEXT NOT NULL,
                        stage_sequence INTEGER NOT NULL,
                        business_admission TEXT NOT NULL,
                        maintenance_fence_token INTEGER,
                        baseline_kind TEXT,
                        previous_release_id TEXT,
                        previous_version_name TEXT,
                        previous_release_sequence INTEGER,
                        previous_package_sha256 TEXT,
                        installed_release_id TEXT,
                        installed_version_name TEXT,
                        installed_release_sequence INTEGER,
                        installed_package_sha256 TEXT,
                        manifest_json TEXT,
                        drain_started_at TEXT,
                        snapshot_created_at TEXT,
                        observation_started_at TEXT,
                        database_restored INTEGER NOT NULL DEFAULT 0,
                        reconciliation_required INTEGER NOT NULL DEFAULT 0,
                        result_evidence_sha256 TEXT,
                        last_error_code TEXT,
                        last_error_message TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        CHECK (state IN (
                            'RECEIVED','VERIFYING_PACKAGE','PACKAGE_READY',
                            'WAITING_FOR_IDLE','MIGRATING_DATA','ACTIVATING',
                            'VERIFYING_TARGET','OBSERVING','ROLLING_BACK',
                            'VERIFYING_ROLLBACK','SUCCEEDED','ROLLED_BACK',
                            'DEFERRED','REJECTED','FAILED_LOCKED'
                        )),
                        CHECK (business_admission IN ('ACCEPTING','PAUSED')),
                        CHECK (baseline_kind IS NULL OR baseline_kind IN (
                            'BUSINESS_RELEASE','IMAGE_BRIDGE'
                        )),
                        CHECK (stage_sequence >= 1),
                        CHECK (release_sequence >= 1),
                        CHECK (package_size >= 1),
                        CHECK (database_restored IN (0,1)),
                        CHECK (reconciliation_required IN (0,1))
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_business_update_active
                    ON business_runtime_update((1))
                    WHERE state NOT IN (
                        'SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED','FAILED_LOCKED'
                    );

                    CREATE TABLE IF NOT EXISTS business_privileged_action (
                        action_uid TEXT PRIMARY KEY,
                        update_uid TEXT NOT NULL,
                        action_kind TEXT NOT NULL,
                        helper_component TEXT NOT NULL,
                        helper_action TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL,
                        authorized_at TEXT,
                        response_json TEXT,
                        response_digest_sha256 TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        dispatched_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(update_uid) REFERENCES business_runtime_update(update_uid),
                        CHECK (state IN ('PREPARED','DISPATCHING','SUCCEEDED','FAILED','UNKNOWN'))
                    );
                    """
                )
                self._ensure_release_identity_columns(connection)
                self._ensure_remote_extension(connection)
                version = connection.execute(
                    "SELECT version FROM business_update_schema WHERE singleton_id=1"
                ).fetchone()
                if version is None or version[0] != BUSINESS_UPDATE_SCHEMA_VERSION:
                    raise BusinessUpdateStoreError(
                        "BUSINESS_UPDATE_SCHEMA_UNSUPPORTED",
                        "business update journal schema is unsupported",
                    )
                now = _format_utc(self._utc_now())
                connection.execute(
                    """UPDATE business_privileged_action
                       SET state='UNKNOWN', error_code='UPDATER_RESTARTED',
                           error_message='updater restarted before helper response',
                           completed_at=?, updated_at=?
                       WHERE state='DISPATCHING'""",
                    (now, now),
                )
                connection.commit()
            except Exception:
                connection.close()
                raise
            self._connection = connection

    @staticmethod
    def _ensure_release_identity_columns(
        connection: sqlite3.Connection,
    ) -> None:
        """Forward-add complete installed identities to an existing journal."""

        existing = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(business_runtime_update)"
            )
        }
        additions = {
            "previous_release_sequence": "INTEGER",
            "previous_package_sha256": "TEXT",
            "installed_version_name": "TEXT",
            "installed_release_sequence": "INTEGER",
        }
        for column, sql_type in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE business_runtime_update "
                    f"ADD COLUMN {column} {sql_type}"
                )

    def _ensure_remote_extension(self, connection: sqlite3.Connection) -> None:
        """Add remote-download facts without changing the proven base schema."""

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS business_remote_update_extension (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
                extension_version INTEGER NOT NULL CHECK (extension_version=1),
                installed_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO business_remote_update_extension(
                singleton_id, extension_version, installed_at
            ) VALUES (1, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

            CREATE TABLE IF NOT EXISTS business_remote_update (
                update_uid TEXT PRIMARY KEY,
                stable_payload_sha256 TEXT NOT NULL,
                control_sequence INTEGER NOT NULL,
                object_key TEXT NOT NULL,
                signature_sha256 TEXT NOT NULL,
                signature_bytes BLOB NOT NULL,
                observation_window_seconds INTEGER NOT NULL,
                download_timeout_seconds INTEGER NOT NULL,
                drain_timeout_seconds INTEGER NOT NULL,
                maximum_retry_count INTEGER NOT NULL,
                authorization_sequence INTEGER NOT NULL,
                download_state TEXT NOT NULL,
                download_attempt_count INTEGER NOT NULL DEFAULT 0,
                last_download_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(update_uid)
                    REFERENCES business_runtime_update(update_uid),
                CHECK (control_sequence BETWEEN 1 AND 9007199254740991),
                CHECK (authorization_sequence BETWEEN 1 AND 9007199254740991),
                CHECK (download_state IN (
                    'RECEIVED','DOWNLOADING','DOWNLOADED',
                    'WAITING_AUTHORIZATION','DEFERRED','REJECTED'
                )),
                CHECK (download_attempt_count BETWEEN 0 AND 10),
                CHECK (observation_window_seconds BETWEEN 60 AND 86400),
                CHECK (download_timeout_seconds BETWEEN 60 AND 86400),
                CHECK (drain_timeout_seconds BETWEEN 60 AND 86400),
                CHECK (maximum_retry_count BETWEEN 0 AND 10)
            );

            CREATE TABLE IF NOT EXISTS business_update_progress_delivery (
                update_uid TEXT NOT NULL,
                stage_sequence INTEGER NOT NULL,
                event_uid TEXT NOT NULL UNIQUE,
                occurred_at TEXT,
                clock_quality TEXT NOT NULL CHECK (
                    clock_quality IN ('SYNCED','ESTIMATED','UNAVAILABLE')
                ),
                payload_json TEXT NOT NULL,
                accepted_at TEXT,
                PRIMARY KEY(update_uid, stage_sequence),
                FOREIGN KEY(update_uid)
                    REFERENCES business_runtime_update(update_uid),
                CHECK (stage_sequence >= 1)
            );

            CREATE TABLE IF NOT EXISTS business_software_state_head (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
                last_payload_sha256 TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO business_software_state_head(
                singleton_id, last_payload_sha256, updated_at
            ) VALUES (
                1, NULL, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            );

            CREATE TABLE IF NOT EXISTS business_software_state_delivery (
                management_state_sequence INTEGER PRIMARY KEY,
                event_uid TEXT NOT NULL UNIQUE,
                target_uid TEXT NOT NULL,
                semantic_payload_sha256 TEXT NOT NULL,
                occurred_at TEXT,
                clock_quality TEXT NOT NULL CHECK (
                    clock_quality IN ('SYNCED','ESTIMATED','UNAVAILABLE')
                ),
                payload_json TEXT NOT NULL,
                accepted_at TEXT,
                CHECK (management_state_sequence BETWEEN 1 AND 9007199254740991)
            );

            CREATE TABLE IF NOT EXISTS business_remote_cancel_extension (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
                extension_version INTEGER NOT NULL CHECK (extension_version=1),
                installed_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO business_remote_cancel_extension(
                singleton_id, extension_version, installed_at
            ) VALUES (1, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

            CREATE TABLE IF NOT EXISTS business_update_cancellation (
                cancel_command_uid TEXT PRIMARY KEY,
                update_uid TEXT NOT NULL UNIQUE,
                request_digest_sha256 TEXT NOT NULL,
                control_sequence INTEGER NOT NULL,
                reason TEXT NOT NULL,
                observed_state TEXT NOT NULL,
                outcome TEXT NOT NULL,
                result_event_uid TEXT UNIQUE,
                result_occurred_at TEXT,
                result_clock_quality TEXT,
                result_payload_json TEXT,
                result_accepted_at TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(update_uid)
                    REFERENCES business_runtime_update(update_uid),
                CHECK (control_sequence BETWEEN 1 AND 9007199254740991),
                CHECK (length(reason) BETWEEN 1 AND 500),
                CHECK (outcome IN ('ACCEPTED','CANCELLED','TOO_LATE')),
                CHECK (
                    (outcome='ACCEPTED' AND completed_at IS NULL)
                    OR (outcome IN ('CANCELLED','TOO_LATE')
                        AND completed_at IS NOT NULL)
                ),
                CHECK (
                    (result_event_uid IS NULL
                        AND result_occurred_at IS NULL
                        AND result_clock_quality IS NULL
                        AND result_payload_json IS NULL
                        AND result_accepted_at IS NULL)
                    OR (result_event_uid IS NOT NULL
                        AND result_clock_quality IN (
                            'SYNCED','ESTIMATED','UNAVAILABLE'
                        )
                        AND result_payload_json IS NOT NULL)
                )
            );

            CREATE TRIGGER IF NOT EXISTS business_remote_extension_no_update
            BEFORE UPDATE ON business_remote_update_extension
            BEGIN
                SELECT RAISE(ABORT, 'remote update extension is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_remote_extension_no_delete
            BEFORE DELETE ON business_remote_update_extension
            BEGIN
                SELECT RAISE(ABORT, 'remote update extension is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_remote_update_no_delete
            BEFORE DELETE ON business_remote_update
            BEGIN
                SELECT RAISE(ABORT, 'remote business updates are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS business_remote_update_monotonic
            BEFORE UPDATE ON business_remote_update
            WHEN OLD.update_uid <> NEW.update_uid
              OR OLD.stable_payload_sha256 <> NEW.stable_payload_sha256
              OR OLD.control_sequence <> NEW.control_sequence
              OR OLD.object_key <> NEW.object_key
              OR OLD.signature_sha256 <> NEW.signature_sha256
              OR OLD.signature_bytes <> NEW.signature_bytes
              OR OLD.observation_window_seconds
                    <> NEW.observation_window_seconds
              OR OLD.download_timeout_seconds <> NEW.download_timeout_seconds
              OR OLD.drain_timeout_seconds <> NEW.drain_timeout_seconds
              OR OLD.maximum_retry_count <> NEW.maximum_retry_count
              OR NEW.authorization_sequence < OLD.authorization_sequence
              OR NEW.download_attempt_count < OLD.download_attempt_count
            BEGIN
                SELECT RAISE(ABORT, 'remote business update facts regressed');
            END;
            CREATE TRIGGER IF NOT EXISTS business_progress_delivery_no_delete
            BEFORE DELETE ON business_update_progress_delivery
            BEGIN
                SELECT RAISE(ABORT, 'business progress delivery is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS business_progress_delivery_monotonic
            BEFORE UPDATE ON business_update_progress_delivery
            WHEN OLD.update_uid <> NEW.update_uid
              OR OLD.stage_sequence <> NEW.stage_sequence
              OR OLD.event_uid <> NEW.event_uid
              OR NOT (OLD.occurred_at IS NEW.occurred_at)
              OR OLD.clock_quality <> NEW.clock_quality
              OR OLD.payload_json <> NEW.payload_json
              OR OLD.accepted_at IS NOT NULL
              OR NEW.accepted_at IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'business progress delivery is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_software_state_no_delete
            BEFORE DELETE ON business_software_state_delivery
            BEGIN
                SELECT RAISE(ABORT, 'business software state is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS business_software_state_monotonic
            BEFORE UPDATE ON business_software_state_delivery
            WHEN OLD.management_state_sequence
                    <> NEW.management_state_sequence
              OR OLD.event_uid <> NEW.event_uid
              OR OLD.target_uid <> NEW.target_uid
              OR OLD.semantic_payload_sha256
                    <> NEW.semantic_payload_sha256
              OR NOT (OLD.occurred_at IS NEW.occurred_at)
              OR OLD.clock_quality <> NEW.clock_quality
              OR OLD.payload_json <> NEW.payload_json
              OR OLD.accepted_at IS NOT NULL
              OR NEW.accepted_at IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'business software state is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_cancel_extension_no_update
            BEFORE UPDATE ON business_remote_cancel_extension
            BEGIN
                SELECT RAISE(ABORT, 'remote cancel extension is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_cancel_extension_no_delete
            BEFORE DELETE ON business_remote_cancel_extension
            BEGIN
                SELECT RAISE(ABORT, 'remote cancel extension is immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS business_cancellation_no_delete
            BEFORE DELETE ON business_update_cancellation
            BEGIN
                SELECT RAISE(ABORT, 'business cancellation is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS business_cancellation_monotonic
            BEFORE UPDATE ON business_update_cancellation
            WHEN OLD.cancel_command_uid <> NEW.cancel_command_uid
              OR OLD.update_uid <> NEW.update_uid
              OR OLD.request_digest_sha256 <> NEW.request_digest_sha256
              OR OLD.control_sequence <> NEW.control_sequence
              OR OLD.reason <> NEW.reason
              OR OLD.observed_state <> NEW.observed_state
              OR NOT (
                    (OLD.outcome='ACCEPTED' AND NEW.outcome IN (
                        'ACCEPTED','CANCELLED'
                    ))
                    OR OLD.outcome=NEW.outcome
              )
              OR (OLD.result_event_uid IS NOT NULL
                    AND OLD.result_event_uid <> NEW.result_event_uid)
              OR (OLD.result_occurred_at IS NOT NULL
                    AND NOT (OLD.result_occurred_at IS NEW.result_occurred_at))
              OR (OLD.result_clock_quality IS NOT NULL
                    AND OLD.result_clock_quality <> NEW.result_clock_quality)
              OR (OLD.result_payload_json IS NOT NULL
                    AND OLD.result_payload_json <> NEW.result_payload_json)
              OR (OLD.result_accepted_at IS NOT NULL
                    AND OLD.result_accepted_at <> NEW.result_accepted_at)
            BEGIN
                SELECT RAISE(ABORT, 'business cancellation facts regressed');
            END;
            """
        )
        marker = connection.execute(
            """SELECT singleton_id, extension_version
               FROM business_remote_update_extension"""
        ).fetchall()
        if [tuple(row) for row in marker] != [
            (1, REMOTE_BUSINESS_UPDATE_EXTENSION_VERSION)
        ]:
            raise BusinessUpdateStoreError(
                "BUSINESS_REMOTE_UPDATE_SCHEMA_UNSUPPORTED",
                "remote business update journal schema is unsupported",
            )
        cancel_marker = connection.execute(
            """SELECT singleton_id, extension_version
               FROM business_remote_cancel_extension"""
        ).fetchall()
        if [tuple(row) for row in cancel_marker] != [
            (1, REMOTE_BUSINESS_CANCELLATION_EXTENSION_VERSION)
        ]:
            raise BusinessUpdateStoreError(
                "BUSINESS_REMOTE_CANCEL_SCHEMA_UNSUPPORTED",
                "remote business cancellation journal schema is unsupported",
            )

    def preflight_update_request(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        normalized = _validate_request(payload)
        digest = canonical_local_payload_sha256(normalized)
        with self._lock:
            connection = self._require_connection()
            by_update = connection.execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (normalized["updateUid"],),
            ).fetchone()
            if by_update is not None:
                if by_update["request_digest_sha256"] != digest:
                    raise BusinessUpdateStoreError(
                        "BUSINESS_UPDATE_IDEMPOTENCY_CONFLICT",
                        "business update identity was reused with different content",
                    )
                return self._render_update(by_update, disposition="DUPLICATE")
            by_command = connection.execute(
                "SELECT 1 FROM business_runtime_update WHERE command_uid=?",
                (normalized["commandUid"],),
            ).fetchone()
            if by_command is not None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_COMMAND_CONFLICT",
                    "business update command identity was already used",
                )
        return None

    def create_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _validate_request(payload)
        digest = canonical_local_payload_sha256(normalized)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            duplicate = connection.execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (normalized["updateUid"],),
            ).fetchone()
            if duplicate is not None:
                if duplicate["request_digest_sha256"] != digest:
                    raise BusinessUpdateStoreError(
                        "BUSINESS_UPDATE_IDEMPOTENCY_CONFLICT",
                        "business update identity was reused with different content",
                    )
                return self._render_update(duplicate, disposition="DUPLICATE")
            try:
                connection.execute(
                    """INSERT INTO business_runtime_update (
                           update_uid, deployment_uid, command_uid,
                           request_digest_sha256, release_id, version_name,
                           release_sequence, package_sha256, package_size,
                           signing_key_id, state, step, stage_sequence,
                           business_admission, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                 'RECEIVED', 'VERIFY_PACKAGE', 1,
                                 'ACCEPTING', ?, ?)""",
                    (
                        normalized["updateUid"],
                        normalized["deploymentUid"],
                        normalized["commandUid"],
                        digest,
                        normalized["releaseId"],
                        normalized["versionName"],
                        normalized["releaseSequence"],
                        normalized["packageSha256"],
                        normalized["packageSize"],
                        normalized["signingKeyId"],
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_BUSY",
                    "another business update is active",
                ) from error
            row = self._require_update(connection, normalized["updateUid"])
            return self._render_update(row, disposition="ACCEPTED")

    def create_or_refresh_remote_update(
        self,
        payload: Mapping[str, Any],
        *,
        authorization_sequence: int,
    ) -> dict[str, Any]:
        """Atomically accept stable update facts and one transient grant sequence.

        The URL itself is deliberately absent from this API.  It remains in
        the downloader's volatile memory only.
        """

        remote = _validate_remote_request(payload)
        base = _validate_request(
            {
                field: remote[field]
                for field in (
                    "updateUid",
                    "deploymentUid",
                    "commandUid",
                    "releaseId",
                    "versionName",
                    "releaseSequence",
                    "packageSha256",
                    "packageSize",
                    "signingKeyId",
                )
            }
        )
        if (
            isinstance(authorization_sequence, bool)
            or not isinstance(authorization_sequence, int)
            or not 1 <= authorization_sequence <= 9_007_199_254_740_991
        ):
            raise BusinessUpdateStoreError(
                "REQUEST_INVALID", "download authorization sequence is invalid"
            )
        base_digest = canonical_local_payload_sha256(base)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (base["updateUid"],),
            ).fetchone()
            existing_remote = connection.execute(
                "SELECT * FROM business_remote_update WHERE update_uid=?",
                (base["updateUid"],),
            ).fetchone()
            if existing is not None:
                if (
                    existing["request_digest_sha256"] != base_digest
                    or existing_remote is None
                    or existing_remote["stable_payload_sha256"]
                    != remote["stablePayloadSha256"]
                ):
                    raise BusinessUpdateStoreError(
                        "BUSINESS_UPDATE_IDEMPOTENCY_CONFLICT",
                        "business update identity was reused with different content",
                    )
                rendered = self._render_update(
                    existing, disposition="DUPLICATE"
                )
                pending_cancellation = connection.execute(
                    """SELECT 1 FROM business_update_cancellation
                       WHERE update_uid=? AND outcome='ACCEPTED'""",
                    (base["updateUid"],),
                ).fetchone()
                if pending_cancellation is not None:
                    return {
                        **rendered,
                        "authorizationDisposition": "CANCELLATION_PENDING",
                    }
                if existing["state"] in BUSINESS_UPDATE_TERMINAL_STATES:
                    return {
                        **rendered,
                        "authorizationDisposition": "TERMINAL",
                    }
                current_authorization = int(
                    existing_remote["authorization_sequence"]
                )
                if authorization_sequence <= current_authorization:
                    return {
                        **rendered,
                        "authorizationDisposition": (
                            "DUPLICATE_AUTHORIZATION"
                            if authorization_sequence == current_authorization
                            else "STALE_AUTHORIZATION"
                        ),
                    }
                if existing["state"] != "RECEIVED" or existing_remote[
                    "download_state"
                ] == "DOWNLOADED":
                    return {
                        **rendered,
                        "authorizationDisposition": "PACKAGE_ALREADY_AVAILABLE",
                    }
                connection.execute(
                    """UPDATE business_remote_update
                       SET authorization_sequence=?, download_state='RECEIVED',
                           last_download_error_code=NULL, updated_at=?
                       WHERE update_uid=?""",
                    (authorization_sequence, now, base["updateUid"]),
                )
                connection.execute(
                    """UPDATE business_runtime_update
                       SET stage_sequence=stage_sequence + 1,
                           step='WAIT_FOR_DOWNLOAD', last_error_code=NULL,
                           last_error_message=NULL, updated_at=?
                       WHERE update_uid=? AND state='RECEIVED'""",
                    (now, base["updateUid"]),
                )
                refreshed = self._require_update(
                    connection, base["updateUid"]
                )
                return {
                    **self._render_update(
                        refreshed, disposition="DUPLICATE"
                    ),
                    "authorizationDisposition": "REFRESHED",
                }

            if existing_remote is not None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_REMOTE_UPDATE_ORPHANED",
                    "remote business update facts have no base update",
                )
            by_command = connection.execute(
                "SELECT 1 FROM business_runtime_update WHERE command_uid=?",
                (base["commandUid"],),
            ).fetchone()
            if by_command is not None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_COMMAND_CONFLICT",
                    "business update command identity was already used",
                )
            try:
                connection.execute(
                    """INSERT INTO business_runtime_update (
                           update_uid, deployment_uid, command_uid,
                           request_digest_sha256, release_id, version_name,
                           release_sequence, package_sha256, package_size,
                           signing_key_id, state, step, stage_sequence,
                           business_admission, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                 'RECEIVED', 'WAIT_FOR_DOWNLOAD', 1,
                                 'ACCEPTING', ?, ?)""",
                    (
                        base["updateUid"],
                        base["deploymentUid"],
                        base["commandUid"],
                        base_digest,
                        base["releaseId"],
                        base["versionName"],
                        base["releaseSequence"],
                        base["packageSha256"],
                        base["packageSize"],
                        base["signingKeyId"],
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO business_remote_update (
                           update_uid, stable_payload_sha256,
                           control_sequence, object_key,
                           signature_sha256, signature_bytes,
                           observation_window_seconds,
                           download_timeout_seconds, drain_timeout_seconds,
                           maximum_retry_count, authorization_sequence,
                           download_state, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                 'RECEIVED', ?, ?)""",
                    (
                        base["updateUid"],
                        remote["stablePayloadSha256"],
                        remote["controlSequence"],
                        remote["objectKey"],
                        remote["signatureSha256"],
                        remote["signatureBytes"],
                        remote["observationWindowSeconds"],
                        remote["downloadTimeoutSeconds"],
                        remote["drainTimeoutSeconds"],
                        remote["maximumRetryCount"],
                        authorization_sequence,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_BUSY",
                    "another business update is active",
                ) from error
            return {
                **self._render_update(
                    self._require_update(connection, base["updateUid"]),
                    disposition="ACCEPTED",
                ),
                "authorizationDisposition": "ACCEPTED",
            }

    def request_remote_cancellation(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist one monotonic cancel intent before reporting its outcome.

        Cancellation is deliberately separate from the update command.  The
        request wins only while the durable update state is still before the
        first business-runtime mutation; a later request is recorded as
        ``TOO_LATE`` so duplicate deliveries return the same result.
        """

        cancellation = _validate_remote_cancellation(payload)
        request_digest = canonical_local_payload_sha256(cancellation)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            by_command = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE cancel_command_uid=?""",
                (cancellation["cancelCommandUid"],),
            ).fetchone()
            if by_command is not None:
                if by_command["request_digest_sha256"] != request_digest:
                    raise BusinessUpdateStoreError(
                        "BUSINESS_CANCEL_IDEMPOTENCY_CONFLICT",
                        "business cancellation identity was reused",
                    )
                return self._render_cancellation(
                    by_command,
                    disposition="DUPLICATE",
                )

            update = self._require_update(
                connection, cancellation["updateUid"]
            )
            remote = self._require_remote_update(
                connection, cancellation["updateUid"]
            )
            if update["deployment_uid"] != cancellation["deploymentUid"]:
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_TARGET_CONFLICT",
                    "business cancellation deployment differs",
                )
            if cancellation["controlSequence"] <= int(
                remote["control_sequence"]
            ):
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_SEQUENCE_STALE",
                    "business cancellation control sequence is not newer",
                )
            existing = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE update_uid=?""",
                (cancellation["updateUid"],),
            ).fetchone()
            if existing is not None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_ALREADY_REQUESTED",
                    "a different cancellation already exists for this update",
                )

            cancellable = update["state"] in BUSINESS_UPDATE_CANCELLABLE_STATES
            outcome = "ACCEPTED" if cancellable else "TOO_LATE"
            completed_at = None if cancellable else now
            connection.execute(
                """INSERT INTO business_update_cancellation (
                       cancel_command_uid, update_uid,
                       request_digest_sha256, control_sequence, reason,
                       observed_state, outcome, created_at, completed_at,
                       updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cancellation["cancelCommandUid"],
                    cancellation["updateUid"],
                    request_digest,
                    cancellation["controlSequence"],
                    cancellation["reason"],
                    update["state"],
                    outcome,
                    now,
                    completed_at,
                    now,
                ),
            )
            row = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE cancel_command_uid=?""",
                (cancellation["cancelCommandUid"],),
            ).fetchone()
            return self._render_cancellation(row, disposition="ACCEPTED")

    def get_pending_cancellation(
        self,
        update_uid: str,
    ) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            row = self._require_connection().execute(
                """SELECT * FROM business_update_cancellation
                   WHERE update_uid=? AND outcome='ACCEPTED'""",
                (update_uid,),
            ).fetchone()
            return (
                self._render_cancellation(row)
                if row is not None
                else None
            )

    def complete_remote_cancellation(
        self,
        update_uid: str,
        *,
        evidence_sha256: str,
    ) -> dict[str, Any]:
        """Close a safely cleaned update and freeze its cancellation fact."""

        update_uid = _require_uuid4(update_uid, "updateUid")
        evidence = _require_sha256(evidence_sha256, "evidenceSha256")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            cancellation = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE update_uid=?""",
                (update_uid,),
            ).fetchone()
            if cancellation is None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_NOT_FOUND",
                    "business cancellation was not found",
                )
            if cancellation["outcome"] == "CANCELLED":
                return self._render_update(update)
            if (
                cancellation["outcome"] != "ACCEPTED"
                or update["state"] not in BUSINESS_UPDATE_CANCELLABLE_STATES
            ):
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_STATE_CONFLICT",
                    "business update can no longer complete cancellation",
                )
            connection.execute(
                """UPDATE business_update_cancellation
                   SET outcome='CANCELLED', completed_at=?, updated_at=?
                   WHERE update_uid=? AND outcome='ACCEPTED'""",
                (now, now, update_uid),
            )
            connection.execute(
                """UPDATE business_remote_update
                   SET download_state=CASE
                           WHEN download_state IN ('DEFERRED','REJECTED')
                               THEN download_state
                           ELSE 'DEFERRED'
                       END,
                       last_download_error_code='BUSINESS_UPDATE_CANCELLED',
                       updated_at=?
                   WHERE update_uid=?""",
                (now, update_uid),
            )
            connection.execute(
                """UPDATE business_runtime_update
                   SET state='DEFERRED', step='COMPLETE',
                       stage_sequence=stage_sequence + 1,
                       business_admission='ACCEPTING',
                       result_evidence_sha256=?,
                       last_error_code='BUSINESS_UPDATE_CANCELLED',
                       last_error_message='business update was cancelled before mutation',
                       updated_at=?, completed_at=?
                   WHERE update_uid=?""",
                (evidence, now, now, update_uid),
            )
            # Cancellation can leave the installed runtime unchanged.  Clear
            # the semantic de-duplication head so the reporter still emits a
            # fresh authoritative software-state fact after admission reopens.
            connection.execute(
                """UPDATE business_software_state_head
                   SET last_payload_sha256=NULL, updated_at=?
                   WHERE singleton_id=1""",
                (now,),
            )
            return self._render_update(
                self._require_update(connection, update_uid)
            )

    def list_cancellation_results_requiring_delivery(
        self,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT cancellation.*, update_row.deployment_uid,
                          update_row.command_uid AS update_command_uid
                   FROM business_update_cancellation AS cancellation
                   JOIN business_runtime_update AS update_row
                     ON update_row.update_uid=cancellation.update_uid
                   WHERE cancellation.outcome IN ('CANCELLED','TOO_LATE')
                     AND cancellation.result_event_uid IS NULL
                   ORDER BY cancellation.created_at"""
            ).fetchall()
            return [self._render_cancellation(row) for row in rows]

    def list_pending_cancellation_result_deliveries(
        self,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("business cancellation delivery limit is invalid")
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT cancellation.*, update_row.deployment_uid,
                          update_row.command_uid AS update_command_uid
                   FROM business_update_cancellation AS cancellation
                   JOIN business_runtime_update AS update_row
                     ON update_row.update_uid=cancellation.update_uid
                   WHERE cancellation.result_event_uid IS NOT NULL
                     AND cancellation.result_accepted_at IS NULL
                   ORDER BY cancellation.created_at
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._render_cancellation(row) for row in rows]

    def reserve_cancellation_result_delivery(
        self,
        cancel_command_uid: str,
        event_uid: str,
        occurred_at: str | None,
        clock_quality: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        cancel_command_uid = _require_uuid4(
            cancel_command_uid, "cancelCommandUid"
        )
        event_uid = _require_uuid4(event_uid, "eventUid")
        payload_json = _canonical_json(
            payload, "business cancellation result payload"
        )
        if clock_quality not in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}:
            raise ValueError("business cancellation clock quality is invalid")
        if (clock_quality == "SYNCED") != (occurred_at is not None):
            raise ValueError("business cancellation clock fields are inconsistent")
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE cancel_command_uid=?""",
                (cancel_command_uid,),
            ).fetchone()
            if row is None or row["outcome"] not in {"CANCELLED", "TOO_LATE"}:
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_RESULT_NOT_READY",
                    "business cancellation result is not ready",
                )
            if row["result_event_uid"] is not None:
                if (
                    row["result_event_uid"] != event_uid
                    or row["result_occurred_at"] != occurred_at
                    or row["result_clock_quality"] != clock_quality
                    or row["result_payload_json"] != payload_json
                ):
                    raise BusinessUpdateStoreError(
                        "BUSINESS_CANCEL_RESULT_IDEMPOTENCY_CONFLICT",
                        "business cancellation result was frozen differently",
                    )
            else:
                connection.execute(
                    """UPDATE business_update_cancellation
                       SET result_event_uid=?, result_occurred_at=?,
                           result_clock_quality=?, result_payload_json=?,
                           updated_at=?
                       WHERE cancel_command_uid=?
                         AND result_event_uid IS NULL""",
                    (
                        event_uid,
                        occurred_at,
                        clock_quality,
                        payload_json,
                        _format_utc(self._utc_now()),
                        cancel_command_uid,
                    ),
                )
            joined = connection.execute(
                """SELECT cancellation.*, update_row.deployment_uid,
                          update_row.command_uid AS update_command_uid
                   FROM business_update_cancellation AS cancellation
                   JOIN business_runtime_update AS update_row
                     ON update_row.update_uid=cancellation.update_uid
                   WHERE cancellation.cancel_command_uid=?""",
                (cancel_command_uid,),
            ).fetchone()
            return self._render_cancellation(joined)

    def mark_cancellation_result_delivery_accepted(
        self,
        cancel_command_uid: str,
        event_uid: str,
    ) -> dict[str, Any]:
        cancel_command_uid = _require_uuid4(
            cancel_command_uid, "cancelCommandUid"
        )
        event_uid = _require_uuid4(event_uid, "eventUid")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM business_update_cancellation
                   WHERE cancel_command_uid=?""",
                (cancel_command_uid,),
            ).fetchone()
            if row is None or row["result_event_uid"] != event_uid:
                raise BusinessUpdateStoreError(
                    "BUSINESS_CANCEL_RESULT_NOT_FOUND",
                    "business cancellation result delivery was not reserved",
                )
            if row["result_accepted_at"] is None:
                connection.execute(
                    """UPDATE business_update_cancellation
                       SET result_accepted_at=?, updated_at=?
                       WHERE cancel_command_uid=?
                         AND result_accepted_at IS NULL""",
                    (now, now, cancel_command_uid),
                )
            joined = connection.execute(
                """SELECT cancellation.*, update_row.deployment_uid,
                          update_row.command_uid AS update_command_uid
                   FROM business_update_cancellation AS cancellation
                   JOIN business_runtime_update AS update_row
                     ON update_row.update_uid=cancellation.update_uid
                   WHERE cancellation.cancel_command_uid=?""",
                (cancel_command_uid,),
            ).fetchone()
            return self._render_cancellation(joined)

    def begin_remote_download(
        self,
        update_uid: str,
        authorization_sequence: int,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            remote = self._require_remote_update(connection, update_uid)
            if (
                update["state"] != "RECEIVED"
                or int(remote["authorization_sequence"])
                != authorization_sequence
            ):
                raise BusinessUpdateStoreError(
                    "DOWNLOAD_AUTHORIZATION_SUPERSEDED",
                    "download authorization is no longer current",
                )
            maximum_attempts = max(1, int(remote["maximum_retry_count"]))
            if int(remote["download_attempt_count"]) >= maximum_attempts:
                self._finish_remote_download_locked(
                    connection,
                    update_uid,
                    remote_state="DEFERRED",
                    update_state="DEFERRED",
                    error_code="BUSINESS_DOWNLOAD_RETRY_EXHAUSTED",
                    now=now,
                )
                raise BusinessUpdateStoreError(
                    "BUSINESS_DOWNLOAD_RETRY_EXHAUSTED",
                    "business package download retry limit was reached",
                )
            connection.execute(
                """UPDATE business_remote_update
                   SET download_state='DOWNLOADING',
                       download_attempt_count=download_attempt_count + 1,
                       last_download_error_code=NULL, updated_at=?
                   WHERE update_uid=?""",
                (now, update_uid),
            )
            connection.execute(
                """UPDATE business_runtime_update
                   SET stage_sequence=stage_sequence + 1,
                       last_error_code=NULL, last_error_message=NULL,
                       updated_at=?
                   WHERE update_uid=? AND state='RECEIVED'""",
                (now, update_uid),
            )
            return self._render_remote_update(
                self._require_remote_update(connection, update_uid)
            )

    def mark_remote_downloaded(
        self,
        update_uid: str,
        authorization_sequence: int,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            remote = self._require_remote_update(connection, update_uid)
            if (
                update["state"] != "RECEIVED"
                or int(remote["authorization_sequence"])
                != authorization_sequence
            ):
                raise BusinessUpdateStoreError(
                    "DOWNLOAD_AUTHORIZATION_SUPERSEDED",
                    "download result belongs to an old authorization",
                )
            connection.execute(
                """UPDATE business_remote_update
                   SET download_state='DOWNLOADED',
                       last_download_error_code=NULL, updated_at=?
                   WHERE update_uid=?""",
                (now, update_uid),
            )
            connection.execute(
                """UPDATE business_runtime_update
                   SET stage_sequence=stage_sequence + 1,
                       step='VERIFY_PACKAGE', last_error_code=NULL,
                       last_error_message=NULL, updated_at=?
                   WHERE update_uid=? AND state='RECEIVED'""",
                (now, update_uid),
            )
            return self._render_update(
                self._require_update(connection, update_uid)
            )

    def mark_remote_download_authorization_required(
        self,
        update_uid: str,
        authorization_sequence: int,
        error_code: str,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        code = _require_token(error_code, "errorCode")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            remote = self._require_remote_update(connection, update_uid)
            if update["state"] != "RECEIVED":
                return self._render_update(update)
            if int(remote["authorization_sequence"]) != authorization_sequence:
                return self._render_update(update)
            maximum_attempts = max(1, int(remote["maximum_retry_count"]))
            exhausted = (
                int(remote["download_attempt_count"]) >= maximum_attempts
            )
            if exhausted:
                self._finish_remote_download_locked(
                    connection,
                    update_uid,
                    remote_state="DEFERRED",
                    update_state="DEFERRED",
                    error_code="BUSINESS_DOWNLOAD_RETRY_EXHAUSTED",
                    now=now,
                )
            else:
                connection.execute(
                    """UPDATE business_remote_update
                       SET download_state='WAITING_AUTHORIZATION',
                           last_download_error_code=?, updated_at=?
                       WHERE update_uid=?""",
                    (code, now, update_uid),
                )
                connection.execute(
                    """UPDATE business_runtime_update
                       SET stage_sequence=stage_sequence + 1,
                           step='WAIT_FOR_DOWNLOAD_AUTHORIZATION',
                           last_error_code=?, last_error_message=?,
                           updated_at=?
                       WHERE update_uid=? AND state='RECEIVED'""",
                    (
                        code,
                        "a new private download authorization is required",
                        now,
                        update_uid,
                    ),
                )
            return self._render_update(
                self._require_update(connection, update_uid)
            )

    def reject_remote_download(
        self,
        update_uid: str,
        authorization_sequence: int,
        error_code: str,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        code = _require_token(error_code, "errorCode")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            remote = self._require_remote_update(connection, update_uid)
            if (
                update["state"] != "RECEIVED"
                or int(remote["authorization_sequence"])
                != authorization_sequence
            ):
                return self._render_update(update)
            self._finish_remote_download_locked(
                connection,
                update_uid,
                remote_state="REJECTED",
                update_state="REJECTED",
                error_code=code,
                now=now,
            )
            return self._render_update(
                self._require_update(connection, update_uid)
            )

    @staticmethod
    def _finish_remote_download_locked(
        connection: sqlite3.Connection,
        update_uid: str,
        *,
        remote_state: str,
        update_state: str,
        error_code: str,
        now: str,
    ) -> None:
        connection.execute(
            """UPDATE business_remote_update
               SET download_state=?, last_download_error_code=?, updated_at=?
               WHERE update_uid=?""",
            (remote_state, error_code, now, update_uid),
        )
        connection.execute(
            """UPDATE business_runtime_update
               SET state=?, step='COMPLETE',
                   stage_sequence=stage_sequence + 1,
                   last_error_code=?, last_error_message=?,
                   updated_at=?, completed_at=?
               WHERE update_uid=? AND state='RECEIVED'""",
            (
                update_state,
                error_code,
                "business package download did not complete",
                now,
                now,
                update_uid,
            ),
        )

    def get_remote_update(self, update_uid: str) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM business_remote_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            return self._render_remote_update(row) if row is not None else None

    def get_remote_download_material(
        self, update_uid: str
    ) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            connection = self._require_connection()
            update = connection.execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            remote = connection.execute(
                "SELECT * FROM business_remote_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
        if update is None or remote is None:
            return None
        return {
            **self._render_update(update),
            **self._render_remote_update(remote),
            "signatureBytes": bytes(remote["signature_bytes"]),
        }

    def list_remote_updates_requiring_recovery(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT update_uid FROM business_remote_update
                   WHERE download_state IN ('RECEIVED','DOWNLOADING')
                   ORDER BY created_at"""
            ).fetchall()
        return [
            self.get_remote_download_material(row["update_uid"])
            for row in rows
        ]

    def get_update(self, update_uid: str) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            return self._render_update(row) if row is not None else None

    def list_remote_updates_requiring_progress(
        self,
    ) -> list[dict[str, Any]]:
        """Return current remote stages that do not yet have a frozen event."""

        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                """SELECT update_row.*
                   FROM business_runtime_update AS update_row
                   JOIN business_remote_update AS remote
                     ON remote.update_uid=update_row.update_uid
                   LEFT JOIN business_update_progress_delivery AS delivery
                     ON delivery.update_uid=update_row.update_uid
                    AND delivery.stage_sequence=update_row.stage_sequence
                   WHERE delivery.update_uid IS NULL
                   ORDER BY update_row.created_at, update_row.stage_sequence"""
            ).fetchall()
            return [
                self._progress_snapshot_locked(connection, row)
                for row in rows
            ]

    def reserve_progress_delivery(
        self,
        update_uid: str,
        stage_sequence: int,
        event_uid: str,
        occurred_at: str | None,
        clock_quality: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        event_uid = _require_uuid4(event_uid, "eventUid")
        if (
            isinstance(stage_sequence, bool)
            or not isinstance(stage_sequence, int)
            or stage_sequence < 1
        ):
            raise ValueError("business progress stage sequence is invalid")
        payload_json = _canonical_json(payload, "business progress payload")
        if clock_quality not in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}:
            raise ValueError("business progress clock quality is invalid")
        if (clock_quality == "SYNCED") != (occurred_at is not None):
            raise ValueError("business progress clock fields are inconsistent")
        with self._transaction() as connection:
            update = self._require_update(connection, update_uid)
            if int(update["stage_sequence"]) != stage_sequence:
                raise BusinessUpdateStoreError(
                    "BUSINESS_PROGRESS_STAGE_SUPERSEDED",
                    "business update advanced before progress was frozen",
                )
            existing = connection.execute(
                """SELECT * FROM business_update_progress_delivery
                   WHERE update_uid=? AND stage_sequence=?""",
                (update_uid, stage_sequence),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_uid"] != event_uid
                    or existing["payload_json"] != payload_json
                    or existing["occurred_at"] != occurred_at
                    or existing["clock_quality"] != clock_quality
                ):
                    raise BusinessUpdateStoreError(
                        "BUSINESS_PROGRESS_IDEMPOTENCY_CONFLICT",
                        "business progress stage was frozen differently",
                    )
                return self._render_progress_delivery(existing)
            connection.execute(
                """INSERT INTO business_update_progress_delivery (
                       update_uid, stage_sequence, event_uid,
                       occurred_at, clock_quality, payload_json, accepted_at
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (
                    update_uid,
                    stage_sequence,
                    event_uid,
                    occurred_at,
                    clock_quality,
                    payload_json,
                ),
            )
            row = connection.execute(
                """SELECT * FROM business_update_progress_delivery
                   WHERE update_uid=? AND stage_sequence=?""",
                (update_uid, stage_sequence),
            ).fetchone()
            return self._render_progress_delivery(row)

    def list_pending_progress_deliveries(
        self,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("business progress delivery limit is invalid")
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT delivery.*, update_row.deployment_uid,
                          update_row.command_uid
                   FROM business_update_progress_delivery AS delivery
                   JOIN business_runtime_update AS update_row
                     ON update_row.update_uid=delivery.update_uid
                   WHERE delivery.accepted_at IS NULL
                   ORDER BY delivery.occurred_at, delivery.stage_sequence
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._render_progress_delivery(row) for row in rows]

    def mark_progress_delivery_accepted(
        self,
        update_uid: str,
        stage_sequence: int,
        event_uid: str,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        event_uid = _require_uuid4(event_uid, "eventUid")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM business_update_progress_delivery
                   WHERE update_uid=? AND stage_sequence=?""",
                (update_uid, stage_sequence),
            ).fetchone()
            if row is None or row["event_uid"] != event_uid:
                raise BusinessUpdateStoreError(
                    "BUSINESS_PROGRESS_NOT_FOUND",
                    "business progress delivery was not reserved",
                )
            if row["accepted_at"] is None:
                connection.execute(
                    """UPDATE business_update_progress_delivery
                       SET accepted_at=?
                       WHERE update_uid=? AND stage_sequence=?
                         AND accepted_at IS NULL""",
                    (now, update_uid, stage_sequence),
                )
                row = connection.execute(
                    """SELECT * FROM business_update_progress_delivery
                       WHERE update_uid=? AND stage_sequence=?""",
                    (update_uid, stage_sequence),
                ).fetchone()
            return self._render_progress_delivery(row)

    def confirmed_installed_release(self) -> dict[str, Any] | None:
        """Return only a complete identity proven by the update journal."""

        with self._lock:
            rows = self._require_connection().execute(
                """SELECT * FROM business_runtime_update
                   ORDER BY updated_at DESC, created_at DESC, update_uid DESC"""
            ).fetchall()
        for row in rows:
            release_id = row["installed_release_id"]
            version_name = row["installed_version_name"]
            release_sequence = row["installed_release_sequence"]
            package_sha256 = row["installed_package_sha256"]
            if release_id == row["release_id"]:
                version_name = version_name or row["version_name"]
                release_sequence = release_sequence or row["release_sequence"]
                if package_sha256 is None:
                    package_sha256 = row["package_sha256"]
            if _complete_release_identity(
                release_id,
                version_name,
                release_sequence,
                package_sha256,
            ):
                return {
                    "releaseUid": release_id,
                    "versionName": version_name,
                    "releaseSequence": release_sequence,
                    "packageSha256": package_sha256,
                }
        return None

    def reserve_software_state_delivery(
        self,
        target_uid: str,
        event_uid: str,
        occurred_at: str | None,
        clock_quality: str,
        semantic_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Freeze a changed actual-state fact and allocate its global sequence."""

        if (
            not isinstance(target_uid, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target_uid) is None
        ):
            raise ValueError("software state target identity is invalid")
        event_uid = _require_uuid4(event_uid, "eventUid")
        if "managementStateSequence" in semantic_payload:
            raise ValueError("software state sequence is store-owned")
        semantic_json = _canonical_json(
            semantic_payload,
            "software state semantic payload",
        )
        semantic_sha256 = hashlib.sha256(
            semantic_json.encode("utf-8")
        ).hexdigest()
        if clock_quality not in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}:
            raise ValueError("software state clock quality is invalid")
        if (clock_quality == "SYNCED") != (occurred_at is not None):
            raise ValueError("software state clock fields are inconsistent")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            head = connection.execute(
                """SELECT last_payload_sha256
                   FROM business_software_state_head
                   WHERE singleton_id=1"""
            ).fetchone()
            if head is None:
                raise RuntimeError("software state delivery head is unavailable")
            if head["last_payload_sha256"] == semantic_sha256:
                return None
            management = connection.execute(
                """SELECT management_state_sequence
                   FROM updater_management_state
                   WHERE singleton_id=1"""
            ).fetchone()
            if management is None:
                raise RuntimeError("updater management state is unavailable")
            sequence = int(management["management_state_sequence"]) + 1
            if sequence > 9_007_199_254_740_991:
                raise RuntimeError("updater management state sequence exhausted")
            payload = {
                "managementStateSequence": sequence,
                **dict(semantic_payload),
            }
            payload_json = _canonical_json(
                payload,
                "software state payload",
            )
            updated = connection.execute(
                """UPDATE updater_management_state
                   SET management_state_sequence=?, updated_at=?
                   WHERE singleton_id=1
                     AND management_state_sequence=?""",
                (sequence, now, sequence - 1),
            ).rowcount
            if updated != 1:
                raise RuntimeError("updater management state allocation lost")
            connection.execute(
                """INSERT INTO business_software_state_delivery (
                       management_state_sequence, event_uid, target_uid,
                       semantic_payload_sha256, occurred_at, clock_quality,
                       payload_json, accepted_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    sequence,
                    event_uid,
                    target_uid,
                    semantic_sha256,
                    occurred_at,
                    clock_quality,
                    payload_json,
                ),
            )
            connection.execute(
                """UPDATE business_software_state_head
                   SET last_payload_sha256=?, updated_at=?
                   WHERE singleton_id=1""",
                (semantic_sha256, now),
            )
            row = connection.execute(
                """SELECT * FROM business_software_state_delivery
                   WHERE management_state_sequence=?""",
                (sequence,),
            ).fetchone()
            return self._render_software_state_delivery(row)

    def list_pending_software_state_deliveries(
        self,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("software state delivery limit is invalid")
        with self._lock:
            rows = self._require_connection().execute(
                """SELECT * FROM business_software_state_delivery
                   WHERE accepted_at IS NULL
                   ORDER BY management_state_sequence
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._render_software_state_delivery(row) for row in rows]

    def mark_software_state_delivery_accepted(
        self,
        management_state_sequence: int,
        event_uid: str,
    ) -> dict[str, Any]:
        event_uid = _require_uuid4(event_uid, "eventUid")
        if (
            isinstance(management_state_sequence, bool)
            or not isinstance(management_state_sequence, int)
            or management_state_sequence < 1
        ):
            raise ValueError("software state sequence is invalid")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT * FROM business_software_state_delivery
                   WHERE management_state_sequence=?""",
                (management_state_sequence,),
            ).fetchone()
            if row is None or row["event_uid"] != event_uid:
                raise BusinessUpdateStoreError(
                    "SOFTWARE_STATE_DELIVERY_NOT_FOUND",
                    "software state delivery was not reserved",
                )
            if row["accepted_at"] is None:
                connection.execute(
                    """UPDATE business_software_state_delivery
                       SET accepted_at=?
                       WHERE management_state_sequence=?
                         AND accepted_at IS NULL""",
                    (now, management_state_sequence),
                )
                row = connection.execute(
                    """SELECT * FROM business_software_state_delivery
                       WHERE management_state_sequence=?""",
                    (management_state_sequence,),
                ).fetchone()
            return self._render_software_state_delivery(row)

    def get_active_update(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in BUSINESS_UPDATE_TERMINAL_STATES)
        with self._lock:
            row = self._require_connection().execute(
                f"""SELECT * FROM business_runtime_update
                    WHERE state NOT IN ({placeholders})
                    ORDER BY created_at LIMIT 1""",
                tuple(sorted(BUSINESS_UPDATE_TERMINAL_STATES)),
            ).fetchone()
            return self._render_update(row) if row is not None else None

    def transition(
        self,
        update_uid: str,
        state: str,
        *,
        expected_states: set[str] | None = None,
        step: str | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        if state not in BUSINESS_UPDATE_STATES:
            raise ValueError("business update state is invalid")
        assignments: dict[str, Any] = dict(fields or {})
        allowed_fields = {
            "maintenance_fence_token",
            "baseline_kind",
            "previous_release_id",
            "previous_version_name",
            "previous_release_sequence",
            "previous_package_sha256",
            "installed_release_id",
            "installed_version_name",
            "installed_release_sequence",
            "installed_package_sha256",
            "manifest_json",
            "drain_started_at",
            "snapshot_created_at",
            "observation_started_at",
            "database_restored",
            "reconciliation_required",
            "result_evidence_sha256",
            "last_error_code",
            "last_error_message",
            "completed_at",
        }
        if not set(assignments).issubset(allowed_fields):
            raise ValueError("business update transition field is invalid")
        if step is not None:
            assignments["step"] = _require_token(step, "step")
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            row = self._require_update(connection, update_uid)
            if expected_states is not None and row["state"] not in expected_states:
                raise BusinessUpdateStoreError(
                    "BUSINESS_UPDATE_STATE_CONFLICT",
                    "business update is no longer in the expected state",
                )
            terminal = state in BUSINESS_UPDATE_TERMINAL_STATES
            assignments.update(
                {
                    "state": state,
                    "stage_sequence": int(row["stage_sequence"]) + 1,
                    "business_admission": (
                        "PAUSED"
                        if state
                        in {
                            "WAITING_FOR_IDLE",
                            "MIGRATING_DATA",
                            "ACTIVATING",
                            "VERIFYING_TARGET",
                            "ROLLING_BACK",
                            "VERIFYING_ROLLBACK",
                            "FAILED_LOCKED",
                        }
                        else "ACCEPTING"
                    ),
                    "updated_at": now,
                }
            )
            if terminal:
                assignments.setdefault("completed_at", now)
            sql = ", ".join(f"{name}=?" for name in assignments)
            connection.execute(
                f"UPDATE business_runtime_update SET {sql} WHERE update_uid=?",
                (*assignments.values(), update_uid),
            )
            return self._render_update(self._require_update(connection, update_uid))

    def prepare_action(
        self,
        *,
        update_uid: str,
        action_uid: str,
        action_kind: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        action_uid = _require_uuid4(action_uid, "actionUid")
        if action_kind not in BUSINESS_ACTION_POLICIES:
            raise ValueError("business privileged action kind is invalid")
        component, action = BUSINESS_ACTION_POLICIES[action_kind]
        document = dict(payload)
        if document.get("updateUid") != update_uid or document.get("actionUid") != action_uid:
            raise ValueError("business helper payload identity differs")
        digest = canonical_local_payload_sha256(document)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            self._require_nonterminal_update(connection, update_uid)
            existing = connection.execute(
                "SELECT * FROM business_privileged_action WHERE action_uid=?",
                (action_uid,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["update_uid"] == update_uid
                    and existing["action_kind"] == action_kind
                    and existing["payload_sha256"] == digest
                ):
                    return self._render_action(existing, disposition="DUPLICATE")
                raise BusinessUpdateStoreError(
                    "BUSINESS_ACTION_IDEMPOTENCY_CONFLICT",
                    "business helper action identity was reused",
                )
            connection.execute(
                """INSERT INTO business_privileged_action (
                       action_uid, update_uid, action_kind, helper_component,
                       helper_action, payload_sha256, state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)""",
                (action_uid, update_uid, action_kind, component, action, digest, now, now),
            )
            return self._render_action(
                self._require_action(connection, action_uid), disposition="ACCEPTED"
            )

    def begin_action(self, action_uid: str) -> dict[str, Any]:
        action_uid = _require_uuid4(action_uid, "actionUid")
        with self._transaction() as connection:
            row = self._require_action(connection, action_uid)
            if row["state"] != "PREPARED":
                raise BusinessUpdateStoreError(
                    "BUSINESS_ACTION_STATE_CONFLICT",
                    "business helper action cannot begin",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE business_privileged_action
                   SET state='DISPATCHING', dispatched_at=?, updated_at=?
                   WHERE action_uid=?""",
                (now, now, action_uid),
            )
            return self._render_action(self._require_action(connection, action_uid))

    def authorize_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "helperComponent",
            "helperAction",
            "updateUid",
            "actionUid",
            "payloadSha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise BusinessUpdateStoreError(
                "REQUEST_INVALID", "business helper authorization fields are invalid"
            )
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        action_uid = _require_uuid4(payload["actionUid"], "actionUid")
        digest = _require_sha256(payload["payloadSha256"], "payloadSha256")
        with self._transaction() as connection:
            update = self._require_nonterminal_update(connection, update_uid)
            action = self._require_action(connection, action_uid)
            if (
                action["update_uid"] != update_uid
                or action["helper_component"] != payload["helperComponent"]
                or action["helper_action"] != payload["helperAction"]
                or action["payload_sha256"] != digest
                or action["state"] != "DISPATCHING"
                or action["authorized_at"] is not None
                or update["maintenance_fence_token"] is None
            ):
                raise BusinessUpdateStoreError(
                    "PRIVILEGED_ACTION_NOT_AUTHORIZED",
                    "business helper action does not match the durable journal",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE business_privileged_action
                   SET authorized_at=?, updated_at=? WHERE action_uid=?""",
                (now, now, action_uid),
            )
            return {
                "authorized": True,
                "helperComponent": payload["helperComponent"],
                "helperAction": payload["helperAction"],
                "updateUid": update_uid,
                "actionUid": action_uid,
                "payloadSha256": digest,
                "maintenanceFenceToken": update["maintenance_fence_token"],
            }

    def finish_action(
        self,
        action_uid: str,
        outcome: str,
        *,
        response: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        action_uid = _require_uuid4(action_uid, "actionUid")
        if outcome not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
            raise ValueError("business helper action outcome is invalid")
        response_json = (
            json.dumps(dict(response), sort_keys=True, separators=(",", ":"))
            if response is not None
            else None
        )
        response_digest = (
            canonical_local_payload_sha256(dict(response))
            if response is not None
            else None
        )
        if outcome == "SUCCEEDED" and (response is None or error_code is not None):
            raise ValueError("successful business action requires only a response")
        if outcome != "SUCCEEDED":
            error_code = _require_token(error_code, "errorCode")
            error_message = _normalize_message(error_message)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            row = self._require_action(connection, action_uid)
            if row["state"] != "DISPATCHING":
                raise BusinessUpdateStoreError(
                    "BUSINESS_ACTION_STATE_CONFLICT",
                    "business helper action was not dispatching",
                )
            if outcome == "SUCCEEDED" and row["authorized_at"] is None:
                raise BusinessUpdateStoreError(
                    "BUSINESS_ACTION_NOT_AUTHORIZED",
                    "successful business helper action was not authorized",
                )
            connection.execute(
                """UPDATE business_privileged_action
                   SET state=?, response_json=?, response_digest_sha256=?,
                       error_code=?, error_message=?, completed_at=?, updated_at=?
                   WHERE action_uid=?""",
                (
                    outcome,
                    response_json,
                    response_digest,
                    error_code,
                    error_message,
                    now,
                    now,
                    action_uid,
                ),
            )
            return self._render_action(self._require_action(connection, action_uid))

    def get_status(self) -> dict[str, Any]:
        active = self.get_active_update()
        return {
            "schemaVersion": BUSINESS_UPDATE_SCHEMA_VERSION,
            "activeUpdate": active,
            "businessUpdateCandidateEnabled": True,
            "remoteTriggerEnabled": self._remote_trigger_enabled,
        }

    def get_action(self, action_uid: str) -> dict[str, Any] | None:
        action_uid = _require_uuid4(action_uid, "actionUid")
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM business_privileged_action WHERE action_uid=?",
                (action_uid,),
            ).fetchone()
            return self._render_action(row) if row is not None else None

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def _transaction(self):
        with self._lock:
            connection = self._require_connection()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("business update journal is not initialized")
        return self._connection

    @staticmethod
    def _require_update(connection: sqlite3.Connection, update_uid: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM business_runtime_update WHERE update_uid=?", (update_uid,)
        ).fetchone()
        if row is None:
            raise BusinessUpdateStoreError(
                "BUSINESS_UPDATE_NOT_FOUND", "business update was not found"
            )
        return row

    def _require_nonterminal_update(
        self, connection: sqlite3.Connection, update_uid: str
    ) -> sqlite3.Row:
        row = self._require_update(connection, update_uid)
        if row["state"] in BUSINESS_UPDATE_TERMINAL_STATES:
            raise BusinessUpdateStoreError(
                "BUSINESS_UPDATE_TERMINAL", "business update is already terminal"
            )
        return row

    @staticmethod
    def _require_remote_update(
        connection: sqlite3.Connection,
        update_uid: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM business_remote_update WHERE update_uid=?",
            (update_uid,),
        ).fetchone()
        if row is None:
            raise BusinessUpdateStoreError(
                "BUSINESS_REMOTE_UPDATE_NOT_FOUND",
                "remote business update was not found",
            )
        return row

    @staticmethod
    def _require_action(connection: sqlite3.Connection, action_uid: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM business_privileged_action WHERE action_uid=?", (action_uid,)
        ).fetchone()
        if row is None:
            raise BusinessUpdateStoreError(
                "BUSINESS_ACTION_NOT_FOUND", "business helper action was not found"
            )
        return row

    @staticmethod
    def _render_update(
        row: sqlite3.Row, *, disposition: str | None = None
    ) -> dict[str, Any]:
        result = {
            "updateUid": row["update_uid"],
            "deploymentUid": row["deployment_uid"],
            "commandUid": row["command_uid"],
            "releaseId": row["release_id"],
            "versionName": row["version_name"],
            "releaseSequence": row["release_sequence"],
            "packageSha256": row["package_sha256"],
            "packageSize": row["package_size"],
            "signingKeyId": row["signing_key_id"],
            "state": row["state"],
            "step": row["step"],
            "stageSequence": row["stage_sequence"],
            "businessAdmission": row["business_admission"],
            "maintenanceFenceToken": row["maintenance_fence_token"],
            "baselineKind": row["baseline_kind"],
            "previousReleaseId": row["previous_release_id"],
            "previousVersionName": row["previous_version_name"],
            "previousReleaseSequence": row["previous_release_sequence"],
            "previousPackageSha256": row["previous_package_sha256"],
            "installedReleaseId": row["installed_release_id"],
            "installedVersionName": row["installed_version_name"],
            "installedReleaseSequence": row["installed_release_sequence"],
            "installedPackageSha256": row["installed_package_sha256"],
            "manifest": json.loads(row["manifest_json"]) if row["manifest_json"] else None,
            "drainStartedAt": row["drain_started_at"],
            "snapshotCreatedAt": row["snapshot_created_at"],
            "observationStartedAt": row["observation_started_at"],
            "databaseRestored": bool(row["database_restored"]),
            "reconciliationRequired": bool(row["reconciliation_required"]),
            "resultEvidenceSha256": row["result_evidence_sha256"],
            "errorCode": row["last_error_code"],
            "errorMessage": row["last_error_message"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
        }
        if disposition is not None:
            result["disposition"] = disposition
        return result

    @staticmethod
    def _render_remote_update(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "updateUid": row["update_uid"],
            "stablePayloadSha256": row["stable_payload_sha256"],
            "controlSequence": row["control_sequence"],
            "objectKey": row["object_key"],
            "signatureSha256": row["signature_sha256"],
            "observationWindowSeconds": row[
                "observation_window_seconds"
            ],
            "downloadTimeoutSeconds": row["download_timeout_seconds"],
            "drainTimeoutSeconds": row["drain_timeout_seconds"],
            "maximumRetryCount": row["maximum_retry_count"],
            "authorizationSequence": row["authorization_sequence"],
            "downloadState": row["download_state"],
            "downloadAttemptCount": row["download_attempt_count"],
            "downloadErrorCode": row["last_download_error_code"],
            "remoteCreatedAt": row["created_at"],
            "remoteUpdatedAt": row["updated_at"],
        }

    @staticmethod
    def _render_progress_delivery(row: sqlite3.Row) -> dict[str, Any]:
        result = {
            "updateUid": row["update_uid"],
            "stageSequence": row["stage_sequence"],
            "eventUid": row["event_uid"],
            "occurredAt": row["occurred_at"],
            "clockQuality": row["clock_quality"],
            "payload": json.loads(row["payload_json"]),
            "acceptedAt": row["accepted_at"],
        }
        if "deployment_uid" in row.keys():
            result["deploymentUid"] = row["deployment_uid"]
            result["commandUid"] = row["command_uid"]
        return result

    @staticmethod
    def _render_software_state_delivery(
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        return {
            "managementStateSequence": row["management_state_sequence"],
            "eventUid": row["event_uid"],
            "targetUid": row["target_uid"],
            "semanticPayloadSha256": row["semantic_payload_sha256"],
            "occurredAt": row["occurred_at"],
            "clockQuality": row["clock_quality"],
            "payload": json.loads(row["payload_json"]),
            "acceptedAt": row["accepted_at"],
        }

    @staticmethod
    def _render_cancellation(
        row: sqlite3.Row,
        *,
        disposition: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "cancelCommandUid": row["cancel_command_uid"],
            "updateUid": row["update_uid"],
            "controlSequence": row["control_sequence"],
            "reason": row["reason"],
            "observedState": row["observed_state"],
            "outcome": row["outcome"],
            "eventUid": row["result_event_uid"],
            "occurredAt": row["result_occurred_at"],
            "clockQuality": row["result_clock_quality"],
            "payload": (
                json.loads(row["result_payload_json"])
                if row["result_payload_json"]
                else None
            ),
            "acceptedAt": row["result_accepted_at"],
            "createdAt": row["created_at"],
            "completedAt": row["completed_at"],
        }
        if "deployment_uid" in row.keys():
            result["deploymentUid"] = row["deployment_uid"]
            result["updateCommandUid"] = row["update_command_uid"]
        if disposition is not None:
            result["disposition"] = disposition
        return result

    def _progress_snapshot_locked(
        self,
        connection: sqlite3.Connection,
        update: sqlite3.Row,
    ) -> dict[str, Any]:
        remote = self._require_remote_update(
            connection, update["update_uid"]
        )
        action_counts = {
            row["action_kind"]: int(row["count"])
            for row in connection.execute(
                """SELECT action_kind, COUNT(*) AS count
                   FROM business_privileged_action
                   WHERE update_uid=? AND state <> 'PREPARED'
                   GROUP BY action_kind""",
                (update["update_uid"],),
            ).fetchall()
        }
        return {
            **self._render_update(update),
            **self._render_remote_update(remote),
            "targetAttemptCount": min(
                10, action_counts.get("INSTALL_TARGET", 0)
            ),
            "rollbackAttemptCount": min(
                10,
                action_counts.get("START_ROLLBACK", 0)
                + action_counts.get("START_BRIDGE_ROLLBACK", 0),
            ),
        }

    @staticmethod
    def _render_action(
        row: sqlite3.Row, *, disposition: str | None = None
    ) -> dict[str, Any]:
        result = {
            "actionUid": row["action_uid"],
            "updateUid": row["update_uid"],
            "actionKind": row["action_kind"],
            "helperComponent": row["helper_component"],
            "helperAction": row["helper_action"],
            "payloadSha256": row["payload_sha256"],
            "state": row["state"],
            "authorizedAt": row["authorized_at"],
            "response": json.loads(row["response_json"]) if row["response_json"] else None,
            "responseDigestSha256": row["response_digest_sha256"],
            "errorCode": row["error_code"],
            "errorMessage": row["error_message"],
        }
        if disposition is not None:
            result["disposition"] = disposition
        return result


def _validate_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "updateUid",
        "deploymentUid",
        "commandUid",
        "releaseId",
        "versionName",
        "releaseSequence",
        "packageSha256",
        "packageSize",
        "signingKeyId",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "local business update request fields are invalid"
        )
    result = dict(payload)
    for field in ("updateUid", "deploymentUid", "commandUid", "releaseId"):
        result[field] = _require_uuid4(result[field], field)
    if not isinstance(result["versionName"], str) or _VERSION.fullmatch(result["versionName"]) is None:
        raise BusinessUpdateStoreError("REQUEST_INVALID", "versionName is invalid")
    sequence = result["releaseSequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise BusinessUpdateStoreError("REQUEST_INVALID", "releaseSequence is invalid")
    result["packageSha256"] = _require_sha256(
        result["packageSha256"], "packageSha256"
    )
    size = result["packageSize"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise BusinessUpdateStoreError("REQUEST_INVALID", "packageSize is invalid")
    key = result["signingKeyId"]
    if not isinstance(key, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key) is None:
        raise BusinessUpdateStoreError("REQUEST_INVALID", "signingKeyId is invalid")
    return result


def _validate_remote_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "updateUid",
        "deploymentUid",
        "commandUid",
        "releaseId",
        "versionName",
        "releaseSequence",
        "packageSha256",
        "packageSize",
        "signingKeyId",
        "stablePayloadSha256",
        "controlSequence",
        "objectKey",
        "signatureSha256",
        "packageSignatureBase64",
        "observationWindowSeconds",
        "downloadTimeoutSeconds",
        "drainTimeoutSeconds",
        "maximumRetryCount",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "remote business update request fields are invalid"
        )
    result = dict(payload)
    for field in ("updateUid", "deploymentUid", "commandUid", "releaseId"):
        result[field] = _require_uuid4(result[field], field)
    if (
        not isinstance(result["versionName"], str)
        or _VERSION.fullmatch(result["versionName"]) is None
    ):
        raise BusinessUpdateStoreError("REQUEST_INVALID", "versionName is invalid")
    for field in ("releaseSequence", "controlSequence"):
        value = result[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 9_007_199_254_740_991
        ):
            raise BusinessUpdateStoreError(
                "REQUEST_INVALID", f"{field} is invalid"
            )
    result["packageSha256"] = _require_sha256(
        result["packageSha256"], "packageSha256"
    )
    result["stablePayloadSha256"] = _require_sha256(
        result["stablePayloadSha256"], "stablePayloadSha256"
    )
    result["signatureSha256"] = _require_sha256(
        result["signatureSha256"], "signatureSha256"
    )
    size = result["packageSize"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= 1_500_000_000
    ):
        raise BusinessUpdateStoreError("REQUEST_INVALID", "packageSize is invalid")
    key = result["signingKeyId"]
    if (
        not isinstance(key, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", key) is None
    ):
        raise BusinessUpdateStoreError("REQUEST_INVALID", "signingKeyId is invalid")
    expected_key = (
        f"edge-runtime/releases/{result['releaseId']}/package.tar.gz"
    )
    if result["objectKey"] != expected_key:
        raise BusinessUpdateStoreError("REQUEST_INVALID", "objectKey is invalid")
    signature_text = result.pop("packageSignatureBase64")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (TypeError, ValueError) as error:
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "package signature is invalid"
        ) from error
    if (
        len(signature) != 64
        or hashlib.sha256(signature).hexdigest()
        != result["signatureSha256"]
    ):
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "package signature digest differs"
        )
    result["signatureBytes"] = signature
    for field in (
        "observationWindowSeconds",
        "downloadTimeoutSeconds",
        "drainTimeoutSeconds",
    ):
        value = result[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 60 <= value <= 86_400
        ):
            raise BusinessUpdateStoreError(
                "REQUEST_INVALID", f"{field} is invalid"
            )
    retries = result["maximumRetryCount"]
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not 0 <= retries <= 10
    ):
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "maximumRetryCount is invalid"
        )
    return result


def _validate_remote_cancellation(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "cancelCommandUid",
        "updateUid",
        "deploymentUid",
        "controlSequence",
        "reason",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID",
            "remote business cancellation fields are invalid",
        )
    result = dict(payload)
    for field in ("cancelCommandUid", "updateUid", "deploymentUid"):
        result[field] = _require_uuid4(result[field], field)
    sequence = result["controlSequence"]
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or not 1 <= sequence <= 9_007_199_254_740_991
    ):
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", "controlSequence is invalid"
        )
    reason = result["reason"]
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or reason != reason.strip()
        or len(reason) > 500
    ):
        raise BusinessUpdateStoreError("REQUEST_INVALID", "reason is invalid")
    return result


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BusinessUpdateStoreError("REQUEST_INVALID", f"{field} must be UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise BusinessUpdateStoreError(
            "REQUEST_INVALID", f"{field} must be UUIDv4"
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise BusinessUpdateStoreError("REQUEST_INVALID", f"{field} must be UUIDv4")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BusinessUpdateStoreError("REQUEST_INVALID", f"{field} must be SHA-256")
    return value


def _require_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _complete_release_identity(
    release_uid: Any,
    version_name: Any,
    release_sequence: Any,
    package_sha256: Any,
) -> bool:
    try:
        parsed_uid = uuid.UUID(release_uid)
    except (ValueError, TypeError, AttributeError):
        return False
    return bool(
        parsed_uid.version == 4
        and str(parsed_uid) == release_uid
        and isinstance(version_name, str)
        and _VERSION.fullmatch(version_name) is not None
        and isinstance(release_sequence, int)
        and not isinstance(release_sequence, bool)
        and 1 <= release_sequence <= 9_007_199_254_740_991
        and isinstance(package_sha256, str)
        and _SHA256.fullmatch(package_sha256) is not None
    )


def _normalize_message(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        return "business update step failed"
    return value


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("business update clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _canonical_json(value: Mapping[str, Any], field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must contain JSON values") from error
    if not 2 <= len(encoded.encode("utf-8")) <= 196_608:
        raise ValueError(f"{field} size is invalid")
    return encoded


__all__ = [
    "BUSINESS_ACTION_POLICIES",
    "BUSINESS_UPDATE_CANCELLABLE_STATES",
    "BUSINESS_UPDATE_TERMINAL_STATES",
    "BusinessUpdateStore",
    "BusinessUpdateStoreError",
]
