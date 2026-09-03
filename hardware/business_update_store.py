"""Durable business-update journal inside the permanent updater database."""

from __future__ import annotations

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
BUSINESS_UPDATE_TERMINAL_STATES = frozenset(
    {"SUCCEEDED", "ROLLED_BACK", "DEFERRED", "REJECTED", "FAILED_LOCKED"}
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
    ) -> None:
        self.path = Path(path)
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
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
                        installed_release_id TEXT,
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

    def get_update(self, update_uid: str) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM business_runtime_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            return self._render_update(row) if row is not None else None

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
            "installed_release_id",
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
            "remoteTriggerEnabled": False,
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
            "installedReleaseId": row["installed_release_id"],
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


__all__ = [
    "BUSINESS_ACTION_POLICIES",
    "BUSINESS_UPDATE_TERMINAL_STATES",
    "BusinessUpdateStore",
    "BusinessUpdateStoreError",
]
