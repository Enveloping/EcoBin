"""Durable MCU-update journal owned by the permanent device updater.

The business database is deliberately not involved.  A rollback of
``edge.db`` therefore cannot erase a flash attempt, helper authorization, or
maintenance-release receipt.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from local_control import canonical_local_payload_sha256


MCU_UPDATE_SCHEMA_VERSION = 1
MCU_UPDATE_TERMINAL_STATES = frozenset(
    {"SUCCEEDED", "ROLLED_BACK", "DEFERRED", "REJECTED", "FAILED_LOCKED"}
)
MCU_UPDATE_STATES = frozenset(
    {
        "QUEUED",
        "DRAINING",
        "INITIAL_OBSERVE",
        "INITIAL_QUIESCE",
        "STOPPING_TARGET",
        "FLASHING_TARGET",
        "STARTING_TARGET_VERIFY",
        "VERIFYING_TARGET",
        "PREPARING_TARGET_RETRY",
        "PREPARING_ROLLBACK",
        "STOPPING_ROLLBACK",
        "FLASHING_ROLLBACK",
        "STARTING_ROLLBACK_VERIFY",
        "VERIFYING_ROLLBACK",
        "RECOVERING",
        *MCU_UPDATE_TERMINAL_STATES,
    }
)
MCU_ACTION_STATES = frozenset(
    {
        "PREPARED",
        "DISPATCHING",
        "SUCCEEDED",
        "FAILED",
        "UNKNOWN",
        "RECONCILED",
    }
)
MCU_ACTION_POLICIES = {
    "STOP_BUSINESS_TARGET": (
        "BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        "STOP_BUSINESS_RUNTIME",
    ),
    "START_BUSINESS_TARGET": (
        "BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        "START_BUSINESS_RUNTIME",
    ),
    "STOP_BUSINESS_ROLLBACK": (
        "BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        "STOP_BUSINESS_RUNTIME",
    ),
    "START_BUSINESS_ROLLBACK": (
        "BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        "START_BUSINESS_RUNTIME",
    ),
    "FLASH_TARGET": (
        "MCU_FLASH_CANDIDATE_HELPER",
        "FLASH_MCU_FIRMWARE",
    ),
    "FLASH_ROLLBACK": (
        "MCU_FLASH_CANDIDATE_HELPER",
        "FLASH_MCU_FIRMWARE",
    ),
    "RECOVER_APPLICATION": (
        "MCU_FLASH_CANDIDATE_HELPER",
        "RECOVER_MCU_APPLICATION",
    ),
}

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_STATE_TRANSITIONS = {
    "QUEUED": {"DRAINING", "FAILED_LOCKED"},
    "DRAINING": {
        "INITIAL_OBSERVE",
        "DEFERRED",
        "REJECTED",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "INITIAL_OBSERVE": {
        "INITIAL_QUIESCE",
        "DEFERRED",
        "REJECTED",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "INITIAL_QUIESCE": {"STOPPING_TARGET", "RECOVERING", "FAILED_LOCKED"},
    "STOPPING_TARGET": {"FLASHING_TARGET", "RECOVERING", "FAILED_LOCKED"},
    "FLASHING_TARGET": {
        "STARTING_TARGET_VERIFY",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "STARTING_TARGET_VERIFY": {
        "VERIFYING_TARGET",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "VERIFYING_TARGET": {
        "PREPARING_TARGET_RETRY",
        "PREPARING_ROLLBACK",
        "RECOVERING",
        "SUCCEEDED",
        "FAILED_LOCKED",
    },
    "PREPARING_TARGET_RETRY": {
        "STOPPING_TARGET",
        "PREPARING_ROLLBACK",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "PREPARING_ROLLBACK": {
        "STOPPING_ROLLBACK",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "STOPPING_ROLLBACK": {
        "FLASHING_ROLLBACK",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "FLASHING_ROLLBACK": {
        "STARTING_ROLLBACK_VERIFY",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "STARTING_ROLLBACK_VERIFY": {
        "VERIFYING_ROLLBACK",
        "RECOVERING",
        "FAILED_LOCKED",
    },
    "VERIFYING_ROLLBACK": {
        "PREPARING_ROLLBACK",
        "RECOVERING",
        "ROLLED_BACK",
        "FAILED_LOCKED",
    },
    "RECOVERING": {
        "PREPARING_TARGET_RETRY",
        "PREPARING_ROLLBACK",
        "SUCCEEDED",
        "ROLLED_BACK",
        "FAILED_LOCKED",
    },
}


class McuUpdateStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class McuUpdateStore:
    """One private SQLite journal for update and helper-dispatch facts."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        if self.path.name.casefold() == "edge.db":
            raise ValueError("MCU update journal must not use the business database")
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            self._verify_parent()
            self._verify_file()
            existed = self.path.exists()
            connection = sqlite3.connect(str(self.path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            try:
                self._verify_file(require_private_permissions=existed)
                os.chmod(self.path, 0o600)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._create_or_verify_schema(connection)
                now = _format_utc(self._utc_now())
                connection.execute(
                    """UPDATE mcu_privileged_action
                       SET state='UNKNOWN', error_code='UPDATER_RESTARTED',
                           error_message='helper result requires reconciliation',
                           completed_at=?, updated_at=?
                       WHERE state='DISPATCHING'""",
                    (now, now),
                )
                active = connection.execute(
                    """SELECT update_uid, state FROM mcu_firmware_update
                       WHERE release_state <> 'RELEASED'
                         AND state NOT IN
                             ('SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED','FAILED_LOCKED')"""
                ).fetchall()
                if len(active) > 1:
                    raise RuntimeError("MCU update journal has multiple active updates")
                if active and active[0]["state"] not in {"QUEUED", "DRAINING"}:
                    connection.execute(
                        """UPDATE mcu_firmware_update
                           SET state='RECOVERING',
                               last_error_code='UPDATER_RESTART_RECOVERY',
                               last_error_message='interrupted update requires observed recovery',
                               updated_at=? WHERE update_uid=?""",
                        (now, active[0]["update_uid"]),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                connection.close()
                raise
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def create_update(
        self,
        *,
        update_uid: str,
        command_uid: str,
        request_payload: Mapping[str, Any],
        target_package_sha256: str,
        target_manifest: Mapping[str, Any],
        rollback_package_sha256: str,
        rollback_manifest: Mapping[str, Any],
        handoff_uid: str,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        command_uid = _require_uuid4(command_uid, "commandUid")
        handoff_uid = _require_uuid4(handoff_uid, "handoffUid")
        target_package = _require_sha256(target_package_sha256, "targetPackageSha256")
        rollback_package = _require_sha256(
            rollback_package_sha256,
            "rollbackPackageSha256",
        )
        request_digest = canonical_local_payload_sha256(dict(request_payload))
        target = _normalize_manifest(target_manifest)
        rollback = _normalize_manifest(rollback_manifest)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            duplicate = connection.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=? OR command_uid=?",
                (update_uid, command_uid),
            ).fetchall()
            if duplicate:
                if len(duplicate) == 1 and _same_update_request(
                    duplicate[0],
                    update_uid=update_uid,
                    command_uid=command_uid,
                    request_digest=request_digest,
                    target_package_sha256=target_package,
                    rollback_package_sha256=rollback_package,
                ):
                    return self._render_update(duplicate[0], disposition="DUPLICATE")
                raise McuUpdateStoreError(
                    "MCU_UPDATE_IDEMPOTENCY_CONFLICT",
                    "MCU update identity was reused with different content",
                )
            pending = connection.execute(
                """SELECT update_uid FROM mcu_firmware_update
                   WHERE release_state <> 'RELEASED' LIMIT 1"""
            ).fetchone()
            if pending is not None:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_BUSY",
                    "another MCU update still owns recovery responsibility",
                )
            connection.execute(
                """INSERT INTO mcu_firmware_update (
                       update_uid, command_uid, request_digest_sha256, state,
                       target_package_sha256, target_image_sha256,
                       target_identity_sha256, target_manifest_json,
                       rollback_package_sha256, rollback_image_sha256,
                       rollback_identity_sha256, rollback_manifest_json,
                       handoff_uid, target_attempt_count, rollback_attempt_count,
                       release_state, created_at, updated_at
                   ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                             'NONE', ?, ?)""",
                (
                    update_uid,
                    command_uid,
                    request_digest,
                    target_package,
                    target["imageSha256"],
                    target["identitySha256"],
                    target["json"],
                    rollback_package,
                    rollback["imageSha256"],
                    rollback["identitySha256"],
                    rollback["json"],
                    handoff_uid,
                    now,
                    now,
                ),
            )
            row = self._require_update(connection, update_uid)
            return self._render_update(row, disposition="ACCEPTED")

    def preflight_update_request(
        self,
        request_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve duplicates and busy/conflicting identities before staging."""

        if not isinstance(request_payload, Mapping):
            raise ValueError("MCU update request must be an object")
        request = dict(request_payload)
        if set(request) != {
            "updateUid",
            "commandUid",
            "targetPackageSha256",
            "rollbackPackageSha256",
        }:
            raise ValueError("MCU update request fields are invalid")
        update_uid = _require_uuid4(request["updateUid"], "updateUid")
        command_uid = _require_uuid4(request["commandUid"], "commandUid")
        target_package = _require_sha256(
            request["targetPackageSha256"],
            "targetPackageSha256",
        )
        rollback_package = _require_sha256(
            request["rollbackPackageSha256"],
            "rollbackPackageSha256",
        )
        request_digest = canonical_local_payload_sha256(request)
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=? OR command_uid=?",
                (update_uid, command_uid),
            ).fetchall()
            if rows:
                if len(rows) == 1 and _same_update_request(
                    rows[0],
                    update_uid=update_uid,
                    command_uid=command_uid,
                    request_digest=request_digest,
                    target_package_sha256=target_package,
                    rollback_package_sha256=rollback_package,
                ):
                    return self._render_update(
                        rows[0],
                        disposition="DUPLICATE",
                    )
                raise McuUpdateStoreError(
                    "MCU_UPDATE_IDEMPOTENCY_CONFLICT",
                    "MCU update identity was reused with different content",
                )
            pending = connection.execute(
                """SELECT 1 FROM mcu_firmware_update
                   WHERE release_state <> 'RELEASED' LIMIT 1"""
            ).fetchone()
            if pending is not None:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_BUSY",
                    "another MCU update still owns recovery responsibility",
                )
        return None

    def get_update(self, update_uid: str) -> dict[str, Any] | None:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._lock:
            connection = self._require_connection()
            row = connection.execute(
                "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
                (update_uid,),
            ).fetchone()
            return None if row is None else self._render_update(row)

    def get_active_update(self) -> dict[str, Any] | None:
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                """SELECT * FROM mcu_firmware_update
                   WHERE state NOT IN
                       ('SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED','FAILED_LOCKED')
                   ORDER BY created_at"""
            ).fetchall()
            if len(rows) > 1:
                raise RuntimeError("MCU update journal has multiple active updates")
            return None if not rows else self._render_update(rows[0])

    def get_pending_release(self) -> dict[str, Any] | None:
        with self._lock:
            connection = self._require_connection()
            rows = connection.execute(
                """SELECT * FROM mcu_firmware_update
                   WHERE state IN ('SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED')
                     AND release_state <> 'RELEASED'
                   ORDER BY created_at"""
            ).fetchall()
            if len(rows) > 1:
                raise RuntimeError("MCU update journal has multiple pending releases")
            return None if not rows else self._render_update(rows[0])

    def transition(
        self,
        update_uid: str,
        target_state: str,
        *,
        expected_states: Iterable[str] | None = None,
        fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        target = _require_state(target_state)
        updates = dict(fields or {})
        allowed_fields = {
            "maintenance_fence_token",
            "observation_evidence_sha256",
            "quiesce_evidence_sha256",
            "last_flash_evidence_sha256",
            "last_error_code",
            "last_error_message",
            "result_evidence_sha256",
            "handoff_uid",
        }
        if not set(updates).issubset(allowed_fields):
            raise ValueError("MCU update transition fields are invalid")
        normalized = _normalize_transition_fields(updates)
        expected = None
        if expected_states is not None:
            expected = {_require_state(value) for value in expected_states}
            if not expected:
                raise ValueError("expected MCU update states must not be empty")
        with self._transaction() as connection:
            row = self._require_update(connection, update_uid)
            current = row["state"]
            if current == target:
                if normalized:
                    assignments = ["updated_at=?"]
                    values: list[Any] = [_format_utc(self._utc_now())]
                    for name, value in normalized.items():
                        assignments.append(f"{name}=?")
                        values.append(value)
                    values.append(update_uid)
                    connection.execute(
                        f"UPDATE mcu_firmware_update SET {', '.join(assignments)} WHERE update_uid=?",
                        values,
                    )
                    row = self._require_update(connection, update_uid)
                    return self._render_update(row, disposition="UPDATED")
                return self._render_update(row, disposition="DUPLICATE")
            if expected is not None and current not in expected:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_STATE_CONFLICT",
                    "MCU update is not in the expected state",
                )
            if target not in _STATE_TRANSITIONS.get(current, set()):
                raise McuUpdateStoreError(
                    "MCU_UPDATE_TRANSITION_INVALID",
                    f"MCU update cannot move from {current} to {target}",
                )
            assignments = ["state=?", "updated_at=?"]
            transition_time = _format_utc(self._utc_now())
            values: list[Any] = [target, transition_time]
            if target == "DRAINING":
                assignments.append("drain_started_at=COALESCE(drain_started_at, ?)")
                values.append(transition_time)
            for name, value in normalized.items():
                assignments.append(f"{name}=?")
                values.append(value)
            values.append(update_uid)
            connection.execute(
                f"UPDATE mcu_firmware_update SET {', '.join(assignments)} WHERE update_uid=?",
                values,
            )
            return self._render_update(
                self._require_update(connection, update_uid),
                disposition="ACCEPTED",
            )

    def rotate_handoff(self, update_uid: str, handoff_uid: str) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        handoff_uid = _require_uuid4(handoff_uid, "handoffUid")
        with self._transaction() as connection:
            row = self._require_nonterminal_update(connection, update_uid)
            connection.execute(
                """UPDATE mcu_firmware_update SET handoff_uid=?,
                       observation_evidence_sha256=NULL,
                       quiesce_evidence_sha256=NULL, updated_at=?
                   WHERE update_uid=?""",
                (handoff_uid, _format_utc(self._utc_now()), update_uid),
            )
            del row
            return self._render_update(self._require_update(connection, update_uid))

    def record_attempt(self, update_uid: str, source: str) -> int:
        update_uid = _require_uuid4(update_uid, "updateUid")
        if source not in {"TARGET", "ROLLBACK"}:
            raise ValueError("MCU attempt source is invalid")
        column = (
            "target_attempt_count" if source == "TARGET" else "rollback_attempt_count"
        )
        expected_state = "FLASHING_TARGET" if source == "TARGET" else "FLASHING_ROLLBACK"
        with self._transaction() as connection:
            row = self._require_nonterminal_update(connection, update_uid)
            if row["state"] != expected_state:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_STATE_CONFLICT",
                    "MCU flash attempt is not allowed in the current state",
                )
            count = int(row[column]) + 1
            if count > 3:
                raise McuUpdateStoreError(
                    "MCU_ATTEMPTS_EXHAUSTED",
                    "MCU flash attempt limit has been reached",
                )
            connection.execute(
                f"UPDATE mcu_firmware_update SET {column}=?, updated_at=? WHERE update_uid=?",
                (count, _format_utc(self._utc_now()), update_uid),
            )
            return count

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
        if action_kind not in MCU_ACTION_POLICIES:
            raise ValueError("MCU privileged action kind is invalid")
        helper_component, helper_action = MCU_ACTION_POLICIES[action_kind]
        payload_document = dict(payload)
        if (
            payload_document.get("updateUid") != update_uid
            or payload_document.get("actionUid") != action_uid
        ):
            raise ValueError("MCU privileged action payload identity differs")
        payload_sha256 = canonical_local_payload_sha256(payload_document)
        now = _format_utc(self._utc_now())
        with self._transaction() as connection:
            self._require_nonterminal_update(connection, update_uid)
            duplicate = connection.execute(
                "SELECT * FROM mcu_privileged_action WHERE action_uid=?",
                (action_uid,),
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["update_uid"] == update_uid
                    and duplicate["action_kind"] == action_kind
                    and duplicate["payload_sha256"] == payload_sha256
                ):
                    return self._render_action(duplicate, disposition="DUPLICATE")
                raise McuUpdateStoreError(
                    "MCU_ACTION_IDEMPOTENCY_CONFLICT",
                    "MCU helper action identity was reused with different content",
                )
            dispatching = connection.execute(
                "SELECT 1 FROM mcu_privileged_action WHERE state='DISPATCHING' LIMIT 1"
            ).fetchone()
            if dispatching is not None:
                raise McuUpdateStoreError(
                    "MCU_ACTION_BUSY",
                    "another privileged action is being dispatched",
                )
            connection.execute(
                """INSERT INTO mcu_privileged_action (
                       action_uid, update_uid, action_kind, helper_component,
                       helper_action, payload_sha256, state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)""",
                (
                    action_uid,
                    update_uid,
                    action_kind,
                    helper_component,
                    helper_action,
                    payload_sha256,
                    now,
                    now,
                ),
            )
            return self._render_action(
                self._require_action(connection, action_uid),
                disposition="ACCEPTED",
            )

    def begin_action(self, action_uid: str) -> dict[str, Any]:
        action_uid = _require_uuid4(action_uid, "actionUid")
        with self._transaction() as connection:
            row = self._require_action(connection, action_uid)
            if row["state"] == "DISPATCHING":
                return self._render_action(row, disposition="DUPLICATE")
            if row["state"] != "PREPARED":
                raise McuUpdateStoreError(
                    "MCU_ACTION_STATE_CONFLICT",
                    "MCU helper action cannot begin in its current state",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE mcu_privileged_action
                   SET state='DISPATCHING', dispatched_at=?, updated_at=?
                   WHERE action_uid=?""",
                (now, now, action_uid),
            )
            return self._render_action(self._require_action(connection, action_uid))

    def authorize_action(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        expected_fields = {
            "helperComponent",
            "helperAction",
            "updateUid",
            "actionUid",
            "payloadSha256",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected_fields:
            raise McuUpdateStoreError(
                "REQUEST_INVALID",
                "privileged helper authorization fields are invalid",
            )
        update_uid = _require_uuid4(payload["updateUid"], "updateUid")
        action_uid = _require_uuid4(payload["actionUid"], "actionUid")
        payload_sha256 = _require_sha256(payload["payloadSha256"], "payloadSha256")
        helper_component = payload["helperComponent"]
        helper_action = payload["helperAction"]
        with self._transaction() as connection:
            update = self._require_nonterminal_update(connection, update_uid)
            action = self._require_action(connection, action_uid)
            if (
                action["update_uid"] != update_uid
                or action["helper_component"] != helper_component
                or action["helper_action"] != helper_action
                or action["payload_sha256"] != payload_sha256
                or action["state"] != "DISPATCHING"
            ):
                raise McuUpdateStoreError(
                    "PRIVILEGED_ACTION_NOT_AUTHORIZED",
                    "privileged helper action does not match the durable journal",
                )
            if action["authorized_at"] is not None:
                raise McuUpdateStoreError(
                    "PRIVILEGED_ACTION_ALREADY_AUTHORIZED",
                    "privileged helper action authorization was already consumed",
                )
            if update["maintenance_fence_token"] is None:
                raise McuUpdateStoreError(
                    "MAINTENANCE_LOCK_REQUIRED",
                    "MCU update has no durable maintenance fence",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE mcu_privileged_action SET authorized_at=?, updated_at=?
                   WHERE action_uid=? AND authorized_at IS NULL""",
                (now, now, action_uid),
            )
            return {
                "authorized": True,
                "helperComponent": helper_component,
                "helperAction": helper_action,
                "updateUid": update_uid,
                "actionUid": action_uid,
                "payloadSha256": payload_sha256,
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
            raise ValueError("MCU helper action outcome is invalid")
        response_sha256 = (
            canonical_local_payload_sha256(dict(response))
            if response is not None
            else None
        )
        code, message = _normalize_error(error_code, error_message)
        if outcome == "SUCCEEDED" and (response_sha256 is None or code is not None):
            raise ValueError("successful MCU helper action requires only a response")
        if outcome != "SUCCEEDED" and code is None:
            raise ValueError("failed MCU helper action requires a stable error")
        with self._transaction() as connection:
            row = self._require_action(connection, action_uid)
            if row["state"] in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
                same = (
                    row["state"] == outcome
                    and row["response_digest_sha256"] == response_sha256
                    and row["error_code"] == code
                )
                if same:
                    return self._render_action(row, disposition="DUPLICATE")
                raise McuUpdateStoreError(
                    "MCU_ACTION_RESULT_CONFLICT",
                    "MCU helper action already has another result",
                )
            if row["state"] != "DISPATCHING":
                raise McuUpdateStoreError(
                    "MCU_ACTION_STATE_CONFLICT",
                    "MCU helper action was not dispatched",
                )
            if outcome == "SUCCEEDED" and row["authorized_at"] is None:
                raise McuUpdateStoreError(
                    "MCU_ACTION_NOT_AUTHORIZED",
                    "successful MCU helper action has no consumed authorization",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE mcu_privileged_action
                   SET state=?, response_digest_sha256=?, error_code=?,
                       error_message=?, completed_at=?, updated_at=?
                   WHERE action_uid=?""",
                (
                    outcome,
                    response_sha256,
                    code,
                    message,
                    now,
                    now,
                    action_uid,
                ),
            )
            return self._render_action(self._require_action(connection, action_uid))

    def reconcile_unknown_actions(
        self,
        update_uid: str,
        evidence_sha256: str,
    ) -> int:
        """Close uncertain helper receipts after observing actual MCU state."""

        update_uid = _require_uuid4(update_uid, "updateUid")
        evidence = _require_sha256(
            evidence_sha256,
            "reconciliationEvidenceSha256",
        )
        with self._transaction() as connection:
            self._require_nonterminal_update(connection, update_uid)
            now = _format_utc(self._utc_now())
            updated = connection.execute(
                """UPDATE mcu_privileged_action
                   SET state='RECONCILED', reconciliation_evidence_sha256=?,
                       reconciled_at=?, updated_at=?
                   WHERE update_uid=? AND state='UNKNOWN'""",
                (evidence, now, now, update_uid),
            )
            return int(updated.rowcount)

    def complete_update(
        self,
        update_uid: str,
        outcome: str,
        evidence_sha256: str,
    ) -> dict[str, Any]:
        if outcome not in {"SUCCEEDED", "ROLLED_BACK"}:
            raise ValueError("MCU update completion outcome is invalid")
        evidence = _require_sha256(evidence_sha256, "resultEvidenceSha256")
        update = self.transition(
            update_uid,
            outcome,
            fields={"result_evidence_sha256": evidence},
        )
        with self._transaction() as connection:
            row = self._require_update(connection, update_uid)
            if row["completed_at"] is None:
                now = _format_utc(self._utc_now())
                connection.execute(
                    """UPDATE mcu_firmware_update SET completed_at=?, updated_at=?
                       WHERE update_uid=?""",
                    (now, now, update_uid),
                )
            return self._render_update(
                self._require_update(connection, update_uid),
                disposition=update.get("disposition", "ACCEPTED"),
            )

    def finish_pre_hardware_update(
        self,
        update_uid: str,
        outcome: str,
        evidence_sha256: str,
        *,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        """Prepare a safe release before any MCU/UART ownership change."""

        if outcome not in {"DEFERRED", "REJECTED"}:
            raise ValueError("pre-hardware MCU update outcome is invalid")
        evidence = _require_sha256(evidence_sha256, "resultEvidenceSha256")
        code, message = _normalize_error(error_code, error_message)
        assert code is not None and message is not None
        with self._transaction() as connection:
            row = self._require_update(
                connection,
                _require_uuid4(update_uid, "updateUid"),
            )
            if row["state"] in {"DEFERRED", "REJECTED"}:
                if (
                    row["state"] != outcome
                    or row["result_evidence_sha256"] != evidence
                    or row["last_error_code"] != code
                ):
                    raise McuUpdateStoreError(
                        "MCU_UPDATE_RESULT_CONFLICT",
                        "pre-hardware MCU update already has different evidence",
                    )
                return self._render_update(row, disposition="DUPLICATE")
            if row["state"] not in {"DRAINING", "INITIAL_OBSERVE"}:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_STATE_CONFLICT",
                    "only a pre-hardware MCU update may be released",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE mcu_firmware_update
                   SET state=?, result_evidence_sha256=?,
                       last_error_code=?, last_error_message=?,
                       release_state='PREPARED', completed_at=?, updated_at=?
                   WHERE update_uid=?""",
                (outcome, evidence, code, message, now, now, update_uid),
            )
            return self._render_update(
                self._require_update(connection, update_uid),
                disposition="ACCEPTED",
            )

    def fail_locked(
        self,
        update_uid: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        code, message = _normalize_error(error_code, error_message)
        assert code is not None and message is not None
        with self._transaction() as connection:
            row = self._require_update(connection, _require_uuid4(update_uid, "updateUid"))
            if row["state"] == "FAILED_LOCKED":
                return self._render_update(row, disposition="DUPLICATE")
            if row["state"] in {
                "SUCCEEDED",
                "ROLLED_BACK",
                "DEFERRED",
                "REJECTED",
            }:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_STATE_CONFLICT",
                    "completed MCU update cannot become failed",
                )
            now = _format_utc(self._utc_now())
            connection.execute(
                """UPDATE mcu_firmware_update
                   SET state='FAILED_LOCKED', last_error_code=?,
                       last_error_message=?, completed_at=?, updated_at=?
                   WHERE update_uid=?""",
                (code, message, now, now, update_uid),
            )
            return self._render_update(self._require_update(connection, update_uid))

    def prepare_release(
        self,
        update_uid: str,
        fence_token: int,
    ) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        if isinstance(fence_token, bool) or not isinstance(fence_token, int) or fence_token < 1:
            raise ValueError("maintenance fence token is invalid")
        with self._transaction() as connection:
            row = self._require_update(connection, update_uid)
            if row["state"] not in {"SUCCEEDED", "ROLLED_BACK"}:
                raise McuUpdateStoreError(
                    "MCU_UPDATE_NOT_COMPLETE",
                    "maintenance cannot be released before MCU verification",
                )
            unresolved = connection.execute(
                """SELECT 1 FROM mcu_privileged_action
                   WHERE update_uid=? AND state IN ('DISPATCHING','UNKNOWN')
                   LIMIT 1""",
                (update_uid,),
            ).fetchone()
            if unresolved is not None:
                raise McuUpdateStoreError(
                    "MCU_ACTION_RECONCILIATION_REQUIRED",
                    "uncertain MCU helper action must be reconciled before release",
                )
            if row["release_state"] == "RELEASED":
                return self._render_update(row, disposition="DUPLICATE")
            existing = row["maintenance_fence_token"]
            if existing is not None and int(existing) != fence_token:
                raise McuUpdateStoreError(
                    "MAINTENANCE_FENCE_MISMATCH",
                    "maintenance release fence differs from the update journal",
                )
            connection.execute(
                """UPDATE mcu_firmware_update
                   SET maintenance_fence_token=?, release_state='PREPARED', updated_at=?
                   WHERE update_uid=?""",
                (fence_token, _format_utc(self._utc_now()), update_uid),
            )
            return self._render_update(self._require_update(connection, update_uid))

    def finish_release(self, update_uid: str) -> dict[str, Any]:
        update_uid = _require_uuid4(update_uid, "updateUid")
        with self._transaction() as connection:
            row = self._require_update(connection, update_uid)
            if row["release_state"] == "RELEASED":
                return self._render_update(row, disposition="DUPLICATE")
            if row["release_state"] != "PREPARED":
                raise McuUpdateStoreError(
                    "MAINTENANCE_RELEASE_NOT_PREPARED",
                    "maintenance release has no durable prepared receipt",
                )
            connection.execute(
                """UPDATE mcu_firmware_update
                   SET release_state='RELEASED', released_at=?, updated_at=?
                   WHERE update_uid=?""",
                (
                    _format_utc(self._utc_now()),
                    _format_utc(self._utc_now()),
                    update_uid,
                ),
            )
            return self._render_update(self._require_update(connection, update_uid))

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            connection = self._require_connection()
            active = connection.execute(
                """SELECT * FROM mcu_firmware_update
                   WHERE release_state <> 'RELEASED' ORDER BY created_at LIMIT 1"""
            ).fetchone()
            unresolved = connection.execute(
                """SELECT COUNT(*) FROM mcu_privileged_action
                   WHERE state IN ('DISPATCHING','UNKNOWN')"""
            ).fetchone()[0]
            return {
                "schemaVersion": MCU_UPDATE_SCHEMA_VERSION,
                "candidateEnabled": True,
                "remoteTriggerEnabled": False,
                "activeUpdate": (
                    None if active is None else self._render_update(active)
                ),
                "unresolvedPrivilegedActionCount": int(unresolved),
            }

    def _create_or_verify_schema(self, connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if table is None:
            others = connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
            if others:
                raise RuntimeError("MCU update journal schema is incompatible")
            for statement in _schema_statements():
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_version(singleton_id, version) VALUES (1, ?)",
                (MCU_UPDATE_SCHEMA_VERSION,),
            )
        rows = connection.execute(
            "SELECT singleton_id, version FROM schema_version"
        ).fetchall()
        if [(row[0], row[1]) for row in rows] != [
            (1, MCU_UPDATE_SCHEMA_VERSION)
        ]:
            raise RuntimeError("MCU update journal schema is incompatible")
        expected = {
            "schema_version",
            "mcu_firmware_update",
            "mcu_privileged_action",
        }
        actual = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not expected.issubset(actual):
            raise RuntimeError("MCU update journal schema is incompatible")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("MCU update journal foreign keys are invalid")
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            raise RuntimeError("MCU update journal integrity check failed")

    def _transaction(self):
        store = self

        class Transaction:
            def __enter__(self_nonlocal):
                store._lock.acquire()
                connection = store._require_connection()
                connection.execute("BEGIN IMMEDIATE")
                self_nonlocal.connection = connection
                return connection

            def __exit__(self_nonlocal, exc_type, exc, traceback):
                try:
                    if exc_type is None:
                        self_nonlocal.connection.commit()
                    else:
                        self_nonlocal.connection.rollback()
                finally:
                    store._lock.release()
                return False

        return Transaction()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("MCU update journal is not initialized")
        return self._connection

    @staticmethod
    def _require_update(
        connection: sqlite3.Connection,
        update_uid: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM mcu_firmware_update WHERE update_uid=?",
            (update_uid,),
        ).fetchone()
        if row is None:
            raise McuUpdateStoreError("MCU_UPDATE_NOT_FOUND", "MCU update was not found")
        return row

    @staticmethod
    def _require_nonterminal_update(
        connection: sqlite3.Connection,
        update_uid: str,
    ) -> sqlite3.Row:
        row = McuUpdateStore._require_update(connection, update_uid)
        if row["state"] in MCU_UPDATE_TERMINAL_STATES:
            raise McuUpdateStoreError(
                "MCU_UPDATE_ALREADY_TERMINAL",
                "MCU update is already terminal",
            )
        return row

    @staticmethod
    def _require_action(
        connection: sqlite3.Connection,
        action_uid: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM mcu_privileged_action WHERE action_uid=?",
            (action_uid,),
        ).fetchone()
        if row is None:
            raise McuUpdateStoreError("MCU_ACTION_NOT_FOUND", "MCU helper action was not found")
        return row

    @staticmethod
    def _render_update(
        row: sqlite3.Row,
        *,
        disposition: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "updateUid": row["update_uid"],
            "commandUid": row["command_uid"],
            "requestDigestSha256": row["request_digest_sha256"],
            "state": row["state"],
            "targetPackageSha256": row["target_package_sha256"],
            "targetImageSha256": row["target_image_sha256"],
            "targetIdentitySha256": row["target_identity_sha256"],
            "targetManifest": json.loads(row["target_manifest_json"]),
            "rollbackPackageSha256": row["rollback_package_sha256"],
            "rollbackImageSha256": row["rollback_image_sha256"],
            "rollbackIdentitySha256": row["rollback_identity_sha256"],
            "rollbackManifest": json.loads(row["rollback_manifest_json"]),
            "handoffUid": row["handoff_uid"],
            "maintenanceFenceToken": row["maintenance_fence_token"],
            "drainStartedAt": row["drain_started_at"],
            "observationEvidenceSha256": row["observation_evidence_sha256"],
            "quiesceEvidenceSha256": row["quiesce_evidence_sha256"],
            "lastFlashEvidenceSha256": row["last_flash_evidence_sha256"],
            "targetAttemptCount": row["target_attempt_count"],
            "rollbackAttemptCount": row["rollback_attempt_count"],
            "lastErrorCode": row["last_error_code"],
            "lastErrorMessage": row["last_error_message"],
            "resultEvidenceSha256": row["result_evidence_sha256"],
            "releaseState": row["release_state"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "releasedAt": row["released_at"],
        }
        if disposition is not None:
            result["disposition"] = disposition
        return result

    @staticmethod
    def _render_action(
        row: sqlite3.Row,
        *,
        disposition: str | None = None,
    ) -> dict[str, Any]:
        result = {
            "actionSequence": row["action_sequence"],
            "actionUid": row["action_uid"],
            "updateUid": row["update_uid"],
            "actionKind": row["action_kind"],
            "helperComponent": row["helper_component"],
            "helperAction": row["helper_action"],
            "payloadSha256": row["payload_sha256"],
            "state": row["state"],
            "authorizedAt": row["authorized_at"],
            "responseDigestSha256": row["response_digest_sha256"],
            "reconciliationEvidenceSha256": row[
                "reconciliation_evidence_sha256"
            ],
            "errorCode": row["error_code"],
            "errorMessage": row["error_message"],
            "createdAt": row["created_at"],
            "dispatchedAt": row["dispatched_at"],
            "completedAt": row["completed_at"],
            "reconciledAt": row["reconciled_at"],
            "updatedAt": row["updated_at"],
        }
        if disposition is not None:
            result["disposition"] = disposition
        return result

    def _verify_parent(self) -> None:
        metadata = self.path.parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or self.path.parent.is_symlink():
            raise PermissionError("MCU update journal parent is not a real directory")
        if os.name == "posix":
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise PermissionError("MCU update journal parent must be owned privately")

    def _verify_file(self, *, require_private_permissions: bool = False) -> None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if self.path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PermissionError("MCU update journal must be one regular file")
        if os.name == "posix":
            if metadata.st_uid != os.getuid():
                raise PermissionError("MCU update journal owner is invalid")
            if require_private_permissions and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise PermissionError("MCU update journal permissions are too broad")


def _schema_statements() -> tuple[str, ...]:
    states = ",".join(f"'{value}'" for value in sorted(MCU_UPDATE_STATES))
    action_states = ",".join(f"'{value}'" for value in sorted(MCU_ACTION_STATES))
    action_kinds = ",".join(f"'{value}'" for value in sorted(MCU_ACTION_POLICIES))
    return (
        """CREATE TABLE schema_version (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
            version INTEGER NOT NULL CHECK (version=1)
        )""",
        f"""CREATE TABLE mcu_firmware_update (
            update_uid TEXT PRIMARY KEY,
            command_uid TEXT NOT NULL UNIQUE,
            request_digest_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ({states})),
            target_package_sha256 TEXT NOT NULL,
            target_image_sha256 TEXT NOT NULL,
            target_identity_sha256 TEXT NOT NULL,
            target_manifest_json TEXT NOT NULL,
            rollback_package_sha256 TEXT NOT NULL,
            rollback_image_sha256 TEXT NOT NULL,
            rollback_identity_sha256 TEXT NOT NULL,
            rollback_manifest_json TEXT NOT NULL,
            handoff_uid TEXT NOT NULL,
            maintenance_fence_token INTEGER CHECK (maintenance_fence_token >= 1),
            drain_started_at TEXT,
            observation_evidence_sha256 TEXT,
            quiesce_evidence_sha256 TEXT,
            last_flash_evidence_sha256 TEXT,
            target_attempt_count INTEGER NOT NULL CHECK (target_attempt_count BETWEEN 0 AND 3),
            rollback_attempt_count INTEGER NOT NULL CHECK (rollback_attempt_count BETWEEN 0 AND 3),
            last_error_code TEXT,
            last_error_message TEXT,
            result_evidence_sha256 TEXT,
            release_state TEXT NOT NULL CHECK (release_state IN ('NONE','PREPARED','RELEASED')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            released_at TEXT,
            CHECK ((state IN ('SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED')
                    AND result_evidence_sha256 IS NOT NULL)
                   OR state NOT IN ('SUCCEEDED','ROLLED_BACK','DEFERRED','REJECTED')),
            CHECK ((release_state='RELEASED' AND released_at IS NOT NULL)
                   OR release_state<>'RELEASED')
        )""",
        """CREATE UNIQUE INDEX one_mcu_update_with_recovery_responsibility
           ON mcu_firmware_update((1)) WHERE release_state <> 'RELEASED'""",
        f"""CREATE TABLE mcu_privileged_action (
            action_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            action_uid TEXT NOT NULL UNIQUE,
            update_uid TEXT NOT NULL REFERENCES mcu_firmware_update(update_uid),
            action_kind TEXT NOT NULL CHECK (action_kind IN ({action_kinds})),
            helper_component TEXT NOT NULL,
            helper_action TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ({action_states})),
            authorized_at TEXT,
            response_digest_sha256 TEXT,
            error_code TEXT,
            error_message TEXT,
            reconciliation_evidence_sha256 TEXT,
            created_at TEXT NOT NULL,
            dispatched_at TEXT,
            completed_at TEXT,
            reconciled_at TEXT,
            updated_at TEXT NOT NULL,
            CHECK ((state='PREPARED' AND dispatched_at IS NULL AND authorized_at IS NULL)
                   OR state<>'PREPARED'),
            CHECK ((state='SUCCEEDED' AND response_digest_sha256 IS NOT NULL
                    AND error_code IS NULL) OR state<>'SUCCEEDED'),
            CHECK ((state IN ('FAILED','UNKNOWN') AND error_code IS NOT NULL)
                   OR state NOT IN ('FAILED','UNKNOWN')),
            CHECK ((state='RECONCILED'
                    AND reconciliation_evidence_sha256 IS NOT NULL
                    AND reconciled_at IS NOT NULL)
                   OR state<>'RECONCILED')
        )""",
        """CREATE UNIQUE INDEX one_dispatching_mcu_privileged_action
           ON mcu_privileged_action((1)) WHERE state='DISPATCHING'""",
    )


def _normalize_manifest(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("MCU firmware manifest must be an object")
    manifest = dict(value)
    required = {
        "fixedFrameRevision",
        "firmwareVersionCode",
        "firmwareVersion",
        "firmwareIdentityHex",
        "imageSha256",
    }
    if not required.issubset(manifest):
        raise ValueError("MCU firmware manifest lacks identity fields")
    identity = {
        "protocolRevision": manifest["fixedFrameRevision"],
        "firmwareVersionCode": manifest["firmwareVersionCode"],
        "firmwareVersion": manifest["firmwareVersion"],
        "firmwareIdentityHex": manifest["firmwareIdentityHex"],
    }
    identity_sha = canonical_local_payload_sha256(identity)
    image_sha = _require_sha256(manifest["imageSha256"], "imageSha256")
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {"identitySha256": identity_sha, "imageSha256": image_sha, "json": encoded}


def _normalize_transition_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in fields.items():
        if name.endswith("_sha256") and value is not None:
            result[name] = _require_sha256(value, name)
        elif name == "maintenance_fence_token":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("maintenance fence token is invalid")
            result[name] = value
        elif name == "handoff_uid":
            result[name] = _require_uuid4(value, "handoffUid")
        elif name == "last_error_code":
            code, _message = _normalize_error(value, fields.get("last_error_message"))
            result[name] = code
        elif name == "last_error_message":
            _code, message = _normalize_error(fields.get("last_error_code"), value)
            result[name] = message
        else:
            result[name] = value
    return result


def _same_update_request(
    row: sqlite3.Row,
    *,
    update_uid: str,
    command_uid: str,
    request_digest: str,
    target_package_sha256: str,
    rollback_package_sha256: str,
) -> bool:
    return bool(
        row["update_uid"] == update_uid
        and row["command_uid"] == command_uid
        and row["request_digest_sha256"] == request_digest
        and row["target_package_sha256"] == target_package_sha256
        and row["rollback_package_sha256"] == rollback_package_sha256
    )


def _normalize_error(
    code: Any,
    message: Any,
) -> tuple[str | None, str | None]:
    if code is None and message is None:
        return None, None
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        raise ValueError("MCU update error code is invalid")
    if not isinstance(message, str):
        raise ValueError("MCU update error message is invalid")
    cleaned = " ".join(message.replace("\x00", "").split())[:512]
    if not cleaned:
        raise ValueError("MCU update error message is empty")
    return code, cleaned


def _require_state(value: Any) -> str:
    if value not in MCU_UPDATE_STATES:
        raise ValueError("MCU update state is invalid")
    return str(value)


def _require_uuid4(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise McuUpdateStoreError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise McuUpdateStoreError(
            "REQUEST_INVALID",
            f"{field} must be a UUIDv4",
        ) from error
    if parsed.version != 4 or str(parsed) != value:
        raise McuUpdateStoreError("REQUEST_INVALID", f"{field} must be a UUIDv4")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise McuUpdateStoreError(
            "REQUEST_INVALID",
            f"{field} must be a lowercase SHA-256",
        )
    return value


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("MCU update clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )
