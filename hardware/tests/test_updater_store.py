from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from updater_store import (
    PristineRollbackStateUsed,
    UpdaterStore,
    UpdaterStoreError,
    _normalize_schema_sql,
    inspect_pristine_stage3_rollback_state,
)


def _now() -> datetime:
    return datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)


def _store(
    path: Path,
    release_version: str,
    *,
    candidate: bool = False,
) -> UpdaterStore:
    result = UpdaterStore(
        path,
        release_version=release_version,
        enable_stage4_candidate=candidate,
        utc_now=_now,
    )
    result.initialize()
    return result


def _activate_candidate(store: UpdaterStore) -> None:
    status = store.get_status()
    assert status["stage4CandidateEnabled"] is True
    assert status["jobGateState"] == "LOCKED"
    assert status["reconciliationRequired"] is True
    assert status["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
    opened = store.activate_stage4_job_gate(
        {
            "operationUid": _uid(900),
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )
    assert opened["resultingJobGateState"] == "OPEN"
    assert opened["stateChanged"] is True
    assert store.get_status()["reconciliationRequired"] is False


def _operator_lock(store: UpdaterStore, number: int) -> None:
    status = store.get_status()
    store.lock_stage4_job_gate(
        {
            "operationUid": _uid(number),
            "evidenceDigest": "e" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )


def _uid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def _permit_payload(number: int = 1) -> dict[str, str]:
    return {
        "permitUid": _uid(number),
        "workUid": _uid(number + 1),
        "commandUid": _uid(number + 2),
        "workType": "DELIVERY",
        "requestDigestSha256": "a" * 64,
    }


def _begin_payload(number: int = 1) -> dict[str, str]:
    return {
        "permitUid": _uid(number),
        "beginUid": _uid(number + 3),
        "permitDigestSha256": "a" * 64,
    }


def _action_payload(
    number: int = 1,
    token: str = "A" * 43,
) -> dict[str, str]:
    return {
        "actionUid": _uid(number + 4),
        "permitUid": _uid(number),
        "workUid": _uid(number + 1),
        "commandUid": _uid(number + 2),
        "actionKey": "delivery.door.unlock.1",
        "actionKind": "DELIVERY_DOOR_UNLOCK",
        "actionDigestSha256": "c" * 64,
        "dispatchAttemptToken": token,
    }


def _arm_payload(
    number: int = 1,
    token: str = "A" * 43,
) -> dict[str, str]:
    return {
        "actionUid": _uid(number + 4),
        "dispatchAttemptToken": token,
    }


def _prepare_and_arm(
    store: UpdaterStore,
    number: int = 1,
    token: str = "A" * 43,
) -> dict[str, object]:
    store.prepare_physical_action(_action_payload(number, token))
    return store.arm_physical_action(_arm_payload(number, token))


def _confirmation_payload(
    number: int = 1,
    *,
    receipt_number: int = 7,
    outcome: str = "EXECUTED",
    evidence: str = "d" * 64,
) -> dict[str, str]:
    return {
        "actionUid": _uid(number + 4),
        "receiptUid": _uid(receipt_number),
        "outcome": outcome,
        "confirmationBasis": "MCU_IDENTITY_BOUND_FACT",
        "evidenceDigestSha256": evidence,
    }


def _unknown_effect_quarantine_payload(
    number: int = 1,
    *,
    resolution_number: int = 17,
    evidence: str = "9" * 64,
) -> dict[str, object]:
    return {
        "resolutionUid": _uid(resolution_number),
        "actionUid": _uid(number + 4),
        "permitUid": _uid(number),
        "workUid": _uid(number + 1),
        "commandUid": _uid(number + 2),
        "actionKey": "delivery.door.unlock.1",
        "actionKind": "DELIVERY_DOOR_UNLOCK",
        "actionDigestSha256": "c" * 64,
        "expectedLedgerSequence": 1,
        "evidenceDigestSha256": evidence,
    }


def _live_result_payload(
    number: int = 1,
    *,
    receipt_number: int = 16,
    token: str = "A" * 43,
    outcome: str = "EXECUTED",
    evidence: str = "e" * 64,
) -> dict[str, str]:
    return {
        "actionUid": _uid(number + 4),
        "receiptUid": _uid(receipt_number),
        "dispatchAttemptToken": token,
        "outcome": outcome,
        "evidenceDigestSha256": evidence,
    }


def _create_v1_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE schema_version (
                   singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                   version INTEGER NOT NULL CHECK (version > 0)
               )"""
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 1)")
        connection.execute(
            """CREATE TABLE updater_runtime_instance (
                   instance_uid TEXT PRIMARY KEY,
                   component TEXT NOT NULL CHECK (component = 'DEVICE_UPDATER'),
                   release_version TEXT NOT NULL,
                   started_at TEXT NOT NULL
               )"""
        )
        connection.execute(
            """INSERT INTO updater_runtime_instance
                   VALUES (?, 'DEVICE_UPDATER', 'stage3', ?)""",
            (_uid(100), "2026-09-01T00:00:00.000Z"),
        )
        connection.execute(
            """CREATE TABLE updater_management_state (
                   singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                   management_state_sequence INTEGER NOT NULL,
                   updates_enabled INTEGER NOT NULL CHECK (updates_enabled = 0),
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
               )"""
        )
        connection.execute(
            """INSERT INTO updater_management_state VALUES
                   (1, 1, 0, 'NOT_ENFORCED_STAGE3', 'IDLE', 0, 0, ?, ?)""",
            (
                "2026-09-01T00:00:00.000Z",
                "2026-09-01T00:00:00.000Z",
            ),
        )
    if os.name == "posix":
        path.chmod(0o600)


def _create_v2_database_with_uncertain_action(path: Path) -> None:
    timestamp = "2026-09-01T00:00:00.000Z"
    with sqlite3.connect(path) as connection:
        for statement in UpdaterStore._v2_schema_statements():
            connection.execute(statement)
        connection.execute("INSERT INTO schema_version VALUES (1, 2)")
        connection.execute(
            """INSERT INTO updater_runtime_instance
                   VALUES (?, 'DEVICE_UPDATER', 'stage4-v2', ?)""",
            (_uid(100), timestamp),
        )
        connection.execute(
            """INSERT INTO updater_management_state (
                   singleton_id, management_state_sequence,
                   stage4_candidate_enabled, updates_enabled,
                   job_gate_mode, job_gate_state, maintenance_state,
                   reconciliation_required, block_reason_code,
                   business_update_enabled, mcu_update_enabled,
                   created_at, updated_at
               ) VALUES (1, 7, 1, 0, 'ENFORCED', 'LOCKED', 'LOCKED',
                         1, 'PHYSICAL_ACTION_UNCONFIRMED', 0, 0, ?, ?)""",
            (timestamp, timestamp),
        )
        connection.execute(
            """INSERT INTO job_permit (
                   permit_uid, work_uid, command_uid, work_type,
                   request_digest_sha256, state, grant_gate_sequence,
                   begin_uid, permit_digest_sha256, created_at, begun_at,
                   updated_at
               ) VALUES (?, ?, ?, 'DELIVERY', ?, 'ACTIVE', 2, ?, ?, ?, ?, ?)""",
            (
                _uid(1),
                _uid(2),
                _uid(3),
                "a" * 64,
                _uid(4),
                "a" * 64,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """INSERT INTO physical_action_ledger (
                   action_uid, permit_uid, work_uid, command_uid,
                   action_key, action_kind, action_digest_sha256,
                   state, arm_uid, created_at, armed_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'MAY_HAVE_EXECUTED',
                         ?, ?, ?, ?)""",
            (
                _uid(5),
                _uid(1),
                _uid(2),
                _uid(3),
                "delivery.door.unlock.1",
                "DELIVERY_DOOR_UNLOCK",
                "c" * 64,
                _uid(6),
                timestamp,
                timestamp,
                timestamp,
            ),
        )
    if os.name == "posix":
        path.chmod(0o600)


def _create_v2_database_with_confirmed_action(
    path: Path,
    outcome: str,
) -> None:
    _create_v2_database_with_uncertain_action(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE physical_action_ledger
               SET state='CONFIRMED', receipt_uid=?, confirmed_outcome=?,
                   evidence_digest_sha256=?, confirmed_at=?, updated_at=?
               WHERE action_uid=?""",
            (
                _uid(7),
                outcome,
                "d" * 64,
                "2026-09-01T00:01:00.000Z",
                "2026-09-01T00:01:00.000Z",
                _uid(5),
            ),
        )


def _create_v3_database_with_uncertain_action(path: Path) -> None:
    _create_v2_database_with_uncertain_action(path)
    legacy = UpdaterStore(
        path,
        release_version="stage4-v3",
        utc_now=_now,
    )
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        legacy._migrate_v2_to_v3(connection)
    if os.name == "posix":
        path.chmod(0o600)


def _create_v3_open_database_without_extension(path: Path) -> None:
    timestamp = "2026-09-01T00:00:00.000Z"
    legacy = UpdaterStore(
        path,
        release_version="stage4-v3",
        utc_now=_now,
    )
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        legacy._create_v3_schema(connection)
        connection.execute(
            """INSERT INTO updater_runtime_instance
                   VALUES (?, 'DEVICE_UPDATER', 'stage4-v3', ?)""",
            (_uid(100), timestamp),
        )
        connection.execute(
            """UPDATE updater_management_state
               SET management_state_sequence=9,
                   stage4_candidate_enabled=1,
                   job_gate_mode='ENFORCED', job_gate_state='OPEN',
                   maintenance_state='IDLE', reconciliation_required=0,
                   block_reason_code=NULL, updated_at=?
               WHERE singleton_id=1""",
            (timestamp,),
        )
    if os.name == "posix":
        path.chmod(0o600)


def test_store_keeps_schema_v3_and_installs_gate_control_extension(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "updater-v1")
    first_status = first.get_status()
    first.close()

    second = _store(path, "updater-v2")
    second_status = second.get_status()

    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert first_status == {
        "component": "DEVICE_UPDATER",
        "schemaVersion": 3,
        "jobGateControlExtensionVersion": 1,
        "candidateActivationState": "REQUIRED",
        "runtimeInstanceUid": first_status["runtimeInstanceUid"],
        "releaseVersion": "updater-v1",
        "startedAt": "2026-09-02T08:30:00.000Z",
        "managementStateSequence": 1,
        "stage4CandidateEnabled": False,
        "updatesEnabled": False,
        "jobGateMode": "DISABLED",
        "jobGateState": "LOCKED",
        "jobPermitRpcEnabled": False,
        "maintenanceState": "LOCKED",
        "maintenanceOwnerUid": None,
        "maintenanceType": None,
        "maintenancePhase": None,
        "maintenanceFenceToken": None,
        "reconciliationRequired": False,
        "blockReasonCode": "STAGE4_CANDIDATE_DISABLED",
        "activeJobPermitCount": 0,
        "unreconciledPhysicalActionCount": 0,
        "businessUpdateEnabled": False,
        "mcuUpdateEnabled": False,
        "privilegedHelperMutationEnabled": False,
    }
    assert uuid.UUID(first_status["runtimeInstanceUid"]).version == 4
    assert second_status["runtimeInstanceUid"] != first_status[
        "runtimeInstanceUid"
    ]
    assert second_status["releaseVersion"] == "updater-v2"

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchall() == [(3,)]
        assert connection.execute(
            """SELECT component, release_version, started_at
               FROM updater_runtime_instance
               ORDER BY rowid"""
        ).fetchall() == [
            (
                "DEVICE_UPDATER",
                "updater-v1",
                "2026-09-02T08:30:00.000Z",
            ),
            (
                "DEVICE_UPDATER",
                "updater-v2",
                "2026-09-02T08:30:00.000Z",
            ),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM updater_management_state"
        ).fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "job_permit",
            "physical_action_ledger",
            "maintenance_lock",
            "job_gate_control_extension",
            "job_gate_control_operation",
            "operator_job_gate_lock",
        }.issubset(tables)
    second.close()


def test_v1_migrates_in_one_start_to_locked_v3_with_extension(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v1_database(path)

    store = _store(path, "stage4")
    try:
        status = store.get_status()
        assert status["schemaVersion"] == 3
        assert status["jobGateControlExtensionVersion"] == 1
        assert status["stage4CandidateEnabled"] is False
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "STAGE4_CANDIDATE_DISABLED"
        assert status["managementStateSequence"] == 2
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT COUNT(*) FROM updater_runtime_instance"
            ).fetchone() == (2,)
            assert connection.execute(
                """SELECT created_at FROM updater_management_state
                   WHERE singleton_id=1"""
            ).fetchone() == ("2026-09-01T00:00:00.000Z",)
    finally:
        store.close()


def test_v1_migration_rolls_back_all_ddl_when_a_late_statement_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v1_database(path)

    class BrokenMigrationStore(UpdaterStore):
        @staticmethod
        def _v2_schema_statements(
            *,
            include_existing_base: bool = True,
        ) -> tuple[str, ...]:
            return UpdaterStore._v2_schema_statements(
                include_existing_base=include_existing_base,
            ) + ("THIS IS NOT SQL",)

    store = BrokenMigrationStore(path, release_version="stage4")
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone() == (1,)
        assert connection.execute(
            """SELECT job_gate_mode FROM updater_management_state
               WHERE singleton_id=1"""
        ).fetchone() == ("NOT_ENFORCED_STAGE3",)
        assert connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name='physical_action_ledger'"""
        ).fetchone() is None


def test_existing_v3_atomically_gains_extension_without_losing_safety_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v3_database_with_uncertain_action(path)

    store = _store(path, "stage4-extension-v1", candidate=True)
    try:
        status = store.get_status()
        action = store.get_physical_action({"actionUid": _uid(5)})

        assert status["schemaVersion"] == 3
        assert status["jobGateControlExtensionVersion"] == 1
        assert status["jobGateState"] == "LOCKED"
        assert status["activeJobPermitCount"] == 1
        assert status["unreconciledPhysicalActionCount"] == 1
        assert action["state"] == "ARMED"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT COUNT(*) FROM updater_runtime_instance"
            ).fetchone() == (2,)
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (0,)
    finally:
        store.close()


def test_schema_v3_extension_is_accepted_by_legacy_subset_verifier(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4-extension-v1")
    store.close()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        UpdaterStore._verify_v3_schema(connection)
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == 3
        assert connection.execute(
            """SELECT extension_version
               FROM job_gate_control_extension"""
        ).fetchone()[0] == 1


def test_legacy_v3_open_gate_without_activation_evidence_is_relocked(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v3_open_database_without_extension(path)

    store = _store(path, "stage4-extension-v1", candidate=True)
    try:
        status = store.get_status()
        assert status["managementStateSequence"] == 10
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        assert store.get_job_gate_reconciliation_status()[
            "initialActivationEligible"
        ] is True
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (0,)
    finally:
        store.close()


def test_resolving_legacy_permit_without_activation_keeps_gate_locked(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v3_open_database_without_extension(path)
    timestamp = "2026-09-01T00:00:00.000Z"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO job_permit (
                   permit_uid, work_uid, command_uid, work_type,
                   request_digest_sha256, state, grant_gate_sequence,
                   created_at, updated_at
               ) VALUES (?, ?, ?, 'DELIVERY', ?, 'GRANTED', 9, ?, ?)""",
            (
                _uid(1),
                _uid(2),
                _uid(3),
                "a" * 64,
                timestamp,
                timestamp,
            ),
        )

    store = _store(path, "stage4-extension-v1", candidate=True)
    try:
        inherited = store.get_status()
        assert inherited["candidateActivationState"] == "REQUIRED"
        assert inherited["activeJobPermitCount"] == 1
        assert inherited["blockReasonCode"] == "ACTIVE_JOB_RECONCILIATION"
        assert store.request_job_permit(_permit_payload())["mayStart"] is False
        assert store.get_job_permit({"permitUid": _uid(1)})["mayStart"] is False
        with pytest.raises(UpdaterStoreError) as begin_without_activation:
            store.begin_job(_begin_payload())
        assert begin_without_activation.value.code == "JOB_GATE_CLOSED"

        store.abandon_job_permit(
            {
                "permitUid": _uid(1),
                "dispositionUid": _uid(108),
                "evidenceSha256": "8" * 64,
            }
        )
        waiting = store.get_status()
        assert waiting["jobGateState"] == "LOCKED"
        assert waiting["candidateActivationState"] == "REQUIRED"
        assert waiting["reconciliationRequired"] is True
        assert waiting["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        with pytest.raises(UpdaterStoreError) as new_permit:
            store.request_job_permit(_permit_payload(20))
        assert new_permit.value.code == "JOB_GATE_CLOSED"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (0,)
    finally:
        store.close()


def test_v3_extension_creation_failure_rolls_back_every_extension_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v3_database_with_uncertain_action(path)

    class BrokenMigrationStore(UpdaterStore):
        @staticmethod
        def _operator_job_gate_lock_schema_statement() -> str:
            return "THIS IS NOT SQL"

    store = BrokenMigrationStore(
        path,
        release_version="stage4-extension-v1",
    )
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone() == (3,)
        for extension_table in (
            "job_gate_control_extension",
            "job_gate_control_operation",
            "operator_job_gate_lock",
        ):
            assert connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name=?""",
                (extension_table,),
            ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM physical_action_ledger"
        ).fetchone() == (1,)


def test_initial_activation_is_evidence_bearing_idempotent_and_conflict_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    try:
        waiting = store.get_job_gate_reconciliation_status()
        payload = {
            "operationUid": _uid(101),
            "evidenceDigest": "1" * 64,
            "expectedManagementStateSequence": waiting[
                "managementStateSequence"
            ],
        }
        assert waiting["initialActivationEligible"] is True
        assert waiting["lastControlOperation"] is None

        with pytest.raises(UpdaterStoreError) as stale:
            store.activate_stage4_job_gate(
                {
                    **payload,
                    "operationUid": _uid(102),
                    "expectedManagementStateSequence": (
                        payload["expectedManagementStateSequence"] + 1
                    ),
                }
            )
        assert stale.value.code == "MANAGEMENT_SEQUENCE_MISMATCH"

        accepted = store.activate_stage4_job_gate(payload)
        duplicate = store.activate_stage4_job_gate(payload)
        assert accepted["disposition"] == "ACCEPTED"
        assert accepted["operationKind"] == "INITIAL_ACTIVATION"
        assert accepted["evidenceDigest"] == "1" * 64
        assert accepted["previousJobGateState"] == "LOCKED"
        assert accepted["resultingJobGateState"] == "OPEN"
        assert accepted["stateChanged"] is True
        assert duplicate == {**accepted, "disposition": "DUPLICATE"}

        with pytest.raises(UpdaterStoreError) as conflict:
            store.activate_stage4_job_gate(
                {**payload, "evidenceDigest": "2" * 64}
            )
        assert (
            conflict.value.code
            == "JOB_GATE_CONTROL_OPERATION_CONFLICT"
        )
        with pytest.raises(UpdaterStoreError) as second_activation:
            store.activate_stage4_job_gate(
                {
                    "operationUid": _uid(103),
                    "evidenceDigest": "3" * 64,
                    "expectedManagementStateSequence": accepted[
                        "resultingManagementStateSequence"
                    ],
                }
            )
        assert second_activation.value.code == (
            "STAGE4_ACTIVATION_NOT_ALLOWED"
        )

        reconciled = store.get_job_gate_reconciliation_status()
        assert reconciled["jobGateState"] == "OPEN"
        assert reconciled["initialActivationEligible"] is False
        assert reconciled["lastControlOperation"] == {
            key: value
            for key, value in accepted.items()
            if key != "disposition"
        }
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                """SELECT operation_uid, operation_kind,
                          evidence_digest_sha256,
                          previous_management_state_sequence,
                          resulting_management_state_sequence
                   FROM job_gate_control_operation"""
            ).fetchall() == [
                (
                    _uid(101),
                    "INITIAL_ACTIVATION",
                    "1" * 64,
                    payload["expectedManagementStateSequence"],
                    payload["expectedManagementStateSequence"] + 1,
                )
            ]
    finally:
        store.close()


def test_initial_activation_failure_rolls_back_gate_evidence_and_marker(
    tmp_path: Path,
) -> None:
    class BrokenActivationStore(UpdaterStore):
        def _insert_job_gate_operation(
            self,
            *args: object,
            **kwargs: object,
        ) -> sqlite3.Row:
            raise RuntimeError("injected activation evidence failure")

    path = tmp_path / "updater.db"
    store = BrokenActivationStore(
        path,
        release_version="stage4-broken-activation",
        enable_stage4_candidate=True,
        utc_now=_now,
    )
    store.initialize()
    try:
        waiting = store.get_status()
        with pytest.raises(RuntimeError, match="injected activation"):
            store.activate_stage4_job_gate(
                {
                    "operationUid": _uid(104),
                    "evidenceDigest": "4" * 64,
                    "expectedManagementStateSequence": waiting[
                        "managementStateSequence"
                    ],
                }
            )

        unchanged = store.get_status()
        assert unchanged["managementStateSequence"] == waiting[
            "managementStateSequence"
        ]
        assert unchanged["candidateActivationState"] == "REQUIRED"
        assert unchanged["jobGateState"] == "LOCKED"
        assert unchanged["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (0,)
    finally:
        store.close()


def test_activation_required_and_operator_lock_cannot_use_generic_open(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    waiting = store.get_status()
    with pytest.raises(UpdaterStoreError) as generic_open:
        store.transition_job_gate("OPEN")
    assert generic_open.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"

    locked = store.lock_stage4_job_gate(
        {
            "operationUid": _uid(105),
            "evidenceDigest": "9" * 64,
            "expectedManagementStateSequence": waiting[
                "managementStateSequence"
            ],
        }
    )
    with pytest.raises(UpdaterStoreError) as operator_open:
        store.transition_job_gate("OPEN")
    assert operator_open.value.code == "OPERATOR_SAFETY_LOCK_ACTIVE"
    with pytest.raises(UpdaterStoreError) as operator_activation:
        store.activate_stage4_job_gate(
            {
                "operationUid": _uid(106),
                "evidenceDigest": "a" * 64,
                "expectedManagementStateSequence": locked[
                    "resultingManagementStateSequence"
                ],
            }
        )
    assert operator_activation.value.code == "OPERATOR_SAFETY_LOCK_ACTIVE"
    reconciliation = store.get_job_gate_reconciliation_status()
    assert reconciliation["operatorSafetyLock"][
        "underlyingBlockReasonCode"
    ] == "STAGE4_ACTIVATION_REQUIRED"
    store.close()

    restarted = _store(path, "stage4", candidate=True)
    try:
        status = restarted.get_status()
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "MANUAL_SAFETY_LOCK"
        assert restarted.get_job_gate_reconciliation_status()[
            "operatorSafetyLockActive"
        ] is True
    finally:
        restarted.close()


def test_generic_locked_transition_cannot_erase_initial_activation_requirement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        waiting = store.get_status()

        with pytest.raises(UpdaterStoreError) as rewrite:
            store.transition_job_gate(
                "LOCKED",
                block_reason_code="UPDATE_FAILED",
            )
        assert rewrite.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"

        unchanged = store.get_status()
        assert unchanged["managementStateSequence"] == waiting[
            "managementStateSequence"
        ]
        assert unchanged["jobGateState"] == "LOCKED"
        assert unchanged["maintenanceState"] == "LOCKED"
        assert unchanged["reconciliationRequired"] is True
        assert unchanged["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"

        with pytest.raises(UpdaterStoreError) as generic_open:
            store.transition_job_gate("OPEN")
        assert generic_open.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"
        with pytest.raises(UpdaterStoreError) as not_activated:
            store.request_job_permit(_permit_payload())
        assert not_activated.value.code == "JOB_GATE_CLOSED"
    finally:
        store.close()


def test_reenabled_candidate_requires_fresh_exact_activation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4-first", candidate=True)
    first_waiting = first.get_status()
    _activate_candidate(first)
    first.close()

    disabled = _store(path, "stage4-disabled", candidate=False)
    try:
        assert disabled.get_status()["blockReasonCode"] == (
            "STAGE4_CANDIDATE_DISABLED"
        )
    finally:
        disabled.close()

    reenabled = _store(path, "stage4-reenabled", candidate=True)
    try:
        waiting = reenabled.get_status()
        assert waiting["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"

        with pytest.raises(UpdaterStoreError) as stale_activation:
            reenabled.activate_stage4_job_gate(
                {
                    "operationUid": _uid(900),
                    "evidenceDigest": "f" * 64,
                    "expectedManagementStateSequence": first_waiting[
                        "managementStateSequence"
                    ],
                }
            )
        assert stale_activation.value.code == "STAGE4_ACTIVATION_NOT_ALLOWED"

        with pytest.raises(UpdaterStoreError) as rewrite:
            reenabled.transition_job_gate(
                "LOCKED",
                block_reason_code="UPDATE_FAILED",
            )
        assert rewrite.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"
        with pytest.raises(UpdaterStoreError) as generic_open:
            reenabled.transition_job_gate("OPEN")
        assert generic_open.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"

        activated = reenabled.activate_stage4_job_gate(
            {
                "operationUid": _uid(107),
                "evidenceDigest": "b" * 64,
                "expectedManagementStateSequence": waiting[
                    "managementStateSequence"
                ],
            }
        )
        assert activated["resultingJobGateState"] == "OPEN"
    finally:
        reenabled.close()


def test_forward_start_detects_disable_written_by_extension_unaware_binary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4-first", candidate=True)
    _activate_candidate(first)
    first.close()

    # This is the base-v3 posture an older default-off updater writes.  That
    # binary intentionally ignores extension tables, so ACTIVE remains stale.
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE updater_management_state
               SET management_state_sequence=management_state_sequence+1,
                   stage4_candidate_enabled=0, job_gate_mode='DISABLED',
                   job_gate_state='LOCKED', maintenance_state='LOCKED',
                   reconciliation_required=0,
                   block_reason_code='STAGE4_CANDIDATE_DISABLED'
               WHERE singleton_id=1"""
        )
        assert connection.execute(
            """SELECT candidate_activation_state
               FROM job_gate_control_extension WHERE singleton_id=1"""
        ).fetchone() == ("ACTIVE",)

    forward = _store(path, "stage4-forward", candidate=True)
    try:
        status = forward.get_status()
        assert status["candidateActivationState"] == "REQUIRED"
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        with pytest.raises(UpdaterStoreError) as generic_open:
            forward.transition_job_gate("OPEN")
        assert generic_open.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"
    finally:
        forward.close()


def test_reenabled_candidate_does_not_reuse_activation_after_job_drains(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4-first", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.close()

    disabled = _store(path, "stage4-disabled", candidate=False)
    try:
        disabled_status = disabled.get_status()
        assert disabled_status["candidateActivationState"] == "REQUIRED"
        assert disabled_status["activeJobPermitCount"] == 1
    finally:
        disabled.close()

    reenabled = _store(path, "stage4-reenabled", candidate=True)
    try:
        inherited = reenabled.get_status()
        assert inherited["candidateActivationState"] == "REQUIRED"
        assert inherited["blockReasonCode"] == "ACTIVE_JOB_RECONCILIATION"

        reenabled.abandon_job_permit(
            {
                "permitUid": _uid(1),
                "dispositionUid": _uid(109),
                "evidenceSha256": "9" * 64,
            }
        )
        waiting = reenabled.get_status()
        assert waiting["jobGateState"] == "LOCKED"
        assert waiting["candidateActivationState"] == "REQUIRED"
        assert waiting["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"

        with pytest.raises(UpdaterStoreError) as rewrite:
            reenabled.transition_job_gate(
                "LOCKED",
                block_reason_code="UPDATE_FAILED",
            )
        assert rewrite.value.code == "JOB_GATE_RELEASE_NOT_ALLOWED"

        activated = reenabled.activate_stage4_job_gate(
            {
                "operationUid": _uid(110),
                "evidenceDigest": "c" * 64,
                "expectedManagementStateSequence": waiting[
                    "managementStateSequence"
                ],
            }
        )
        assert activated["resultingJobGateState"] == "OPEN"
        assert reenabled.get_status()["candidateActivationState"] == "ACTIVE"
    finally:
        reenabled.close()


def test_reenabled_candidate_does_not_reopen_after_active_job_completes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4-first", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.begin_job(_begin_payload())
    first.close()

    disabled = _store(path, "stage4-disabled", candidate=False)
    disabled.close()

    reenabled = _store(path, "stage4-reenabled", candidate=True)
    try:
        inherited = reenabled.get_status()
        assert inherited["candidateActivationState"] == "REQUIRED"
        assert inherited["blockReasonCode"] == "ACTIVE_JOB_RECONCILIATION"

        reenabled.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(111),
                "outcome": "SUCCEEDED",
                "completionDigestSha256": "d" * 64,
            }
        )
        waiting = reenabled.get_status()
        assert waiting["activeJobPermitCount"] == 0
        assert waiting["candidateActivationState"] == "REQUIRED"
        assert waiting["jobGateState"] == "LOCKED"
        assert waiting["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        with pytest.raises(UpdaterStoreError) as new_permit:
            reenabled.request_job_permit(_permit_payload(20))
        assert new_permit.value.code == "JOB_GATE_CLOSED"
    finally:
        reenabled.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operationUid", "not-a-uuid"),
        ("evidenceDigest", "A" * 64),
        ("evidenceDigest", "a" * 63),
        ("expectedManagementStateSequence", True),
        ("expectedManagementStateSequence", 0),
    ],
)
def test_job_gate_control_rejects_invalid_operation_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        status = store.get_status()
        payload: dict[str, object] = {
            "operationUid": _uid(110),
            "evidenceDigest": "4" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
        payload[field] = value
        with pytest.raises(UpdaterStoreError) as invalid:
            store.activate_stage4_job_gate(payload)
        assert invalid.value.code == "REQUEST_INVALID"
        assert store.get_status()["jobGateState"] == "LOCKED"
    finally:
        store.close()


def test_disabled_candidate_rejects_new_root_gate_mutations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage3")
    try:
        status = store.get_status()
        payload = {
            "operationUid": _uid(120),
            "evidenceDigest": "5" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
        for operation in (
            store.activate_stage4_job_gate,
            store.lock_stage4_job_gate,
        ):
            with pytest.raises(UpdaterStoreError) as disabled:
                operation(payload)
            assert disabled.value.code == "FEATURE_DISABLED"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (0,)
    finally:
        store.close()


def test_safety_lock_is_idempotent_and_retains_nonterminal_permit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        before = store.get_status()
        payload = {
            "operationUid": _uid(130),
            "evidenceDigest": "6" * 64,
            "expectedManagementStateSequence": before[
                "managementStateSequence"
            ],
        }

        accepted = store.lock_stage4_job_gate(payload)
        duplicate = store.lock_stage4_job_gate(payload)
        assert accepted["disposition"] == "ACCEPTED"
        assert accepted["operationKind"] == "SAFETY_LOCK"
        assert accepted["stateChanged"] is True
        assert accepted["resultingJobGateState"] == "LOCKED"
        assert accepted["resultingBlockReasonCode"] == "MANUAL_SAFETY_LOCK"
        assert duplicate == {**accepted, "disposition": "DUPLICATE"}

        locked = store.get_status()
        assert locked["activeJobPermitCount"] == 1
        assert locked["jobGateState"] == "LOCKED"
        assert locked["managementStateSequence"] == accepted[
            "resultingManagementStateSequence"
        ]
        noop = store.lock_stage4_job_gate(
            {
                "operationUid": _uid(131),
                "evidenceDigest": "7" * 64,
                "expectedManagementStateSequence": locked[
                    "managementStateSequence"
                ],
            }
        )
        assert noop["stateChanged"] is False
        assert noop["previousManagementStateSequence"] == (
            noop["resultingManagementStateSequence"]
        )
        reconciliation = store.get_job_gate_reconciliation_status()
        assert reconciliation["activeJobPermitCount"] == 1
        assert reconciliation["operatorSafetyLockActive"] is True
        assert reconciliation["operatorSafetyLock"]["operationUid"] == (
            _uid(130)
        )
        assert reconciliation["operatorSafetyLock"][
            "underlyingBlockReasonCode"
        ] is None
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                """SELECT state FROM job_permit WHERE permit_uid=?""",
                (_uid(1),),
            ).fetchone() == ("GRANTED",)
            assert connection.execute(
                "SELECT COUNT(*) FROM job_gate_control_operation"
            ).fetchone() == (3,)
    finally:
        store.close()


def test_safety_lock_retains_maintenance_and_unconfirmed_action_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.transition_job_gate(
            "DRAINING",
            owner_update_uid=_uid(140),
            maintenance_type="MCU_FIRMWARE_UPDATE",
        )
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)
        before = store.get_status()
        assert before["jobGateState"] == "LOCKED"

        locked = store.lock_stage4_job_gate(
            {
                "operationUid": _uid(141),
                "evidenceDigest": "8" * 64,
                "expectedManagementStateSequence": before[
                    "managementStateSequence"
                ],
            }
        )
        assert locked["stateChanged"] is True
        after = store.get_status()
        assert after["blockReasonCode"] == "MANUAL_SAFETY_LOCK"
        assert after["maintenanceOwnerUid"] == _uid(140)
        assert after["activeJobPermitCount"] == 1
        assert after["unreconciledPhysicalActionCount"] == 1
        reconciliation = store.get_job_gate_reconciliation_status()
        assert reconciliation["maintenancePhase"] == "DRAINING"
        assert reconciliation["operatorSafetyLockActive"] is True
        assert reconciliation["operatorSafetyLock"] == {
            "operationUid": _uid(141),
            "evidenceDigest": "8" * 64,
            "acquiredManagementStateSequence": locked[
                "resultingManagementStateSequence"
            ],
            "underlyingJobGateState": "LOCKED",
            "underlyingMaintenanceState": "LOCKED",
            "underlyingReconciliationRequired": True,
            "underlyingBlockReasonCode": (
                "DRAINING_ACTION_UNCONFIRMED"
            ),
            "underlyingMaintenancePhase": "DRAINING",
            "acquiredAt": "2026-09-02T08:30:00.000Z",
        }
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM maintenance_lock"
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT state FROM job_permit WHERE permit_uid=?",
                (_uid(1),),
            ).fetchone() == ("ACTIVE",)
            assert connection.execute(
                "SELECT state FROM physical_action_ledger WHERE action_uid=?",
                (_uid(5),),
            ).fetchone() == ("ARMED",)
            assert connection.execute(
                "SELECT phase FROM maintenance_lock"
            ).fetchone() == ("DRAINING",)
    finally:
        store.close()


@pytest.mark.parametrize("maintenance_phase", ["DRAINING", "MAINTENANCE"])
def test_operator_lock_preserves_underlying_maintenance_phase(
    tmp_path: Path,
    maintenance_phase: str,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.transition_job_gate(
            "DRAINING",
            owner_update_uid=_uid(145),
            maintenance_type="MCU_FIRMWARE_UPDATE",
        )
        if maintenance_phase == "MAINTENANCE":
            store.transition_job_gate("MAINTENANCE")
        before = store.get_status()
        store.lock_stage4_job_gate(
            {
                "operationUid": _uid(146),
                "evidenceDigest": "b" * 64,
                "expectedManagementStateSequence": before[
                    "managementStateSequence"
                ],
            }
        )

        reconciliation = store.get_job_gate_reconciliation_status()
        assert reconciliation["maintenanceState"] == "LOCKED"
        assert reconciliation["maintenancePhase"] == maintenance_phase
        assert reconciliation["operatorSafetyLock"][
            "underlyingMaintenancePhase"
        ] == maintenance_phase
        assert reconciliation["operatorSafetyLock"][
            "underlyingBlockReasonCode"
        ] == (
            "MAINTENANCE_DRAINING"
            if maintenance_phase == "DRAINING"
            else "MAINTENANCE_ACTIVE"
        )
        with pytest.raises(UpdaterStoreError) as release:
            store.transition_job_gate("OPEN")
        assert release.value.code == "OPERATOR_SAFETY_LOCK_ACTIVE"
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT phase FROM maintenance_lock"
            ).fetchone() == (maintenance_phase,)
    finally:
        store.close()


def test_operator_lock_prevents_auto_reopen_after_action_reconciliation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)
        before = store.get_status()
        store.lock_stage4_job_gate(
            {
                "operationUid": _uid(147),
                "evidenceDigest": "c" * 64,
                "expectedManagementStateSequence": before[
                    "managementStateSequence"
                ],
            }
        )
        store.confirm_physical_action(_confirmation_payload())
        store.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(148),
                "outcome": "SUCCEEDED",
                "completionDigestSha256": "d" * 64,
            }
        )

        status = store.get_status()
        assert status["activeJobPermitCount"] == 0
        assert status["unreconciledPhysicalActionCount"] == 0
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "MANUAL_SAFETY_LOCK"
    finally:
        store.close()


def test_candidate_permit_and_physical_action_complete_durable_handshake(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    try:
        status = store.get_status()
        assert status["stage4CandidateEnabled"] is True
        assert status["jobGateMode"] == "ENFORCED"
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "STAGE4_ACTIVATION_REQUIRED"
        assert status["updatesEnabled"] is False
        assert status["privilegedHelperMutationEnabled"] is False

        with pytest.raises(UpdaterStoreError) as not_activated:
            store.request_job_permit(_permit_payload())
        assert not_activated.value.code == "JOB_GATE_CLOSED"
        _activate_candidate(store)

        permit = store.request_job_permit(_permit_payload())
        assert permit["disposition"] == "ACCEPTED"
        assert permit["state"] == "GRANTED"
        assert permit["mayStart"] is True
        assert store.begin_job(_begin_payload())["state"] == "ACTIVE"

        prepared = store.prepare_physical_action(_action_payload())
        assert prepared["state"] == "PREPARED"
        assert prepared["dispatchMode"] == "PREPARED_ONLY"
        assert prepared["mayExecute"] is False
        assert store.get_status()["jobGateState"] == "OPEN"

        armed = store.arm_physical_action(_arm_payload())
        assert armed["state"] == "ARMED"
        assert armed["dispatchMode"] == "TWO_PHASE_V3"
        assert armed["mayExecute"] is True
        duplicate_arm = store.arm_physical_action(_arm_payload())
        assert duplicate_arm["disposition"] == "DUPLICATE"
        assert duplicate_arm["mayExecute"] is True
        denied_arm = store.arm_physical_action(
            _arm_payload(token="B" * 43)
        )
        assert denied_arm["disposition"] == "DENIED"
        assert denied_arm["mayExecute"] is False
        assert store.get_physical_action({"actionUid": _uid(5)})[
            "mayExecute"
        ] is False
        assert store.get_status()["jobGateState"] == "LOCKED"
        armed_sequence = store.get_status()["managementStateSequence"]

        with pytest.raises(UpdaterStoreError) as previous_unknown:
            store.prepare_physical_action(
                {
                    **_action_payload(),
                    "actionUid": _uid(50),
                    "actionKey": "delivery.door.unlock.2",
                }
            )
        assert (
            previous_unknown.value.code
            == "PHYSICAL_ACTION_RECONCILIATION_REQUIRED"
        )

        with pytest.raises(
            UpdaterStoreError,
            match="unconfirmed physical action",
        ) as incomplete:
            store.complete_job(
                {
                    "permitUid": _uid(1),
                    "completionUid": _uid(8),
                    "outcome": "SUCCEEDED",
                    "completionDigestSha256": "e" * 64,
                }
            )
        assert incomplete.value.code == "PHYSICAL_ACTION_UNCONFIRMED"

        confirmed = store.confirm_physical_action(_confirmation_payload())
        assert confirmed["state"] == "CONFIRMED"
        assert confirmed["mayExecute"] is False
        confirmed_sequence = store.get_status()[
            "managementStateSequence"
        ]
        assert confirmed_sequence > armed_sequence
        completed = store.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(8),
                "outcome": "SUCCEEDED",
                "completionDigestSha256": "e" * 64,
            }
        )
        assert completed["state"] == "COMPLETED"
        assert completed["mayStart"] is False
        completed_status = store.get_status()
        assert completed_status["jobGateState"] == "OPEN"
        assert (
            completed_status["managementStateSequence"]
            > confirmed_sequence
        )
    finally:
        store.close()


def test_second_armed_action_restores_unconfirmed_reconciliation_status(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    second_token = "B" * 43
    second_action = {
        **_action_payload(token=second_token),
        "actionUid": _uid(20),
        "actionKey": "delivery.door.unlock.2",
        "actionDigestSha256": "f" * 64,
    }
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)
        store.confirm_physical_action(_confirmation_payload())

        active_job = store.get_status()
        assert active_job["jobGateState"] == "LOCKED"
        assert active_job["reconciliationRequired"] is False
        assert active_job["blockReasonCode"] == "ACTIVE_JOB_IN_PROGRESS"

        store.prepare_physical_action(second_action)
        prepared = store.get_status()
        assert prepared["reconciliationRequired"] is False
        assert prepared["blockReasonCode"] == "ACTIVE_JOB_IN_PROGRESS"

        armed = store.arm_physical_action(
            {
                "actionUid": second_action["actionUid"],
                "dispatchAttemptToken": second_token,
            }
        )
        status = store.get_status()
        assert armed["state"] == "ARMED"
        assert armed["mayExecute"] is True
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["blockReasonCode"] == "PHYSICAL_ACTION_UNCONFIRMED"
        assert status["unreconciledPhysicalActionCount"] == 1
        assert (
            status["managementStateSequence"]
            > active_job["managementStateSequence"]
        )
    finally:
        store.close()


def test_dispatch_token_is_hashed_hidden_and_only_same_live_retry_executes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    token = "token_response_loss_retry_1234567890ABCDE"
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        store.prepare_physical_action(_action_payload(token=token))

        first = store.arm_physical_action(_arm_payload(token=token))
        retry = store.arm_physical_action(_arm_payload(token=token))
        lookup = store.get_physical_action({"actionUid": _uid(5)})
        status = store.get_status()

        assert first["disposition"] == "ACCEPTED"
        assert first["mayExecute"] is True
        assert retry["disposition"] == "DUPLICATE"
        assert retry["mayExecute"] is True
        assert lookup["disposition"] == "FOUND"
        assert lookup["mayExecute"] is False
        for result in (first, retry, lookup):
            assert "dispatchAttemptToken" not in result
            assert "dispatchAttemptTokenSha256" not in result
            assert "armRuntimeInstanceUid" not in result
            assert token not in repr(result)
        assert "dispatchAttemptToken" not in status
        assert "dispatchAttemptTokenSha256" not in status
        assert "armRuntimeInstanceUid" not in status
        assert token not in repr(status)

        with sqlite3.connect(path) as connection:
            persisted = connection.execute(
                """SELECT dispatch_attempt_token_sha256
                   FROM physical_action_ledger WHERE action_uid=?""",
                (_uid(5),),
            ).fetchone()[0]
            database_dump = "\n".join(connection.iterdump())
        assert persisted != token
        assert len(persisted) == 64
        assert token not in database_dump
    finally:
        store.close()


def test_prepared_action_cannot_be_taken_over_by_a_new_business_token(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    original_token = "J" * 43
    restarted_business_token = "K" * 43
    store = _store(path, "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        missing_token = _action_payload(token=original_token)
        del missing_token["dispatchAttemptToken"]
        with pytest.raises(UpdaterStoreError) as invalid_prepare:
            store.prepare_physical_action(missing_token)
        assert invalid_prepare.value.code == "REQUEST_INVALID"
        first = store.prepare_physical_action(
            _action_payload(token=original_token)
        )
        retry = store.prepare_physical_action(
            _action_payload(token=original_token)
        )
        takeover = store.prepare_physical_action(
            _action_payload(token=restarted_business_token)
        )
        assert first["disposition"] == "ACCEPTED"
        assert first["state"] == "PREPARED"
        assert retry["disposition"] == "DUPLICATE"
        assert takeover["disposition"] == "DENIED"
        assert takeover["mayExecute"] is False
        with sqlite3.connect(path) as connection:
            prepared_state = connection.execute(
                """SELECT state, dispatch_attempt_token_sha256
                   FROM physical_action_ledger WHERE action_uid=?""",
                (_uid(5),),
            ).fetchone()
        assert prepared_state[0] == "PREPARED"
        assert len(prepared_state[1]) == 64
        assert prepared_state[1] not in {
            original_token,
            restarted_business_token,
        }

        with pytest.raises(UpdaterStoreError) as rollback_cancel:
            store.cancel_prepared_physical_action(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(17),
                    "dispatchAttemptToken": restarted_business_token,
                    "evidenceDigestSha256": "1" * 64,
                }
            )
        assert rollback_cancel.value.code == "PHYSICAL_ACTION_CANCEL_DENIED"
        assert restarted_business_token not in str(rollback_cancel.value)
        denied_arm = store.arm_physical_action(
            _arm_payload(token=restarted_business_token)
        )
        assert denied_arm["disposition"] == "DENIED"
        current = store.get_physical_action({"actionUid": _uid(5)})
        assert current["state"] == "PREPARED"
        assert current["confirmedOutcome"] is None
        assert original_token not in repr(current)
        assert restarted_business_token not in repr(current)

        cancellation = {
            "actionUid": _uid(5),
            "receiptUid": _uid(18),
            "dispatchAttemptToken": original_token,
            "evidenceDigestSha256": "2" * 64,
        }
        cancelled = store.cancel_prepared_physical_action(cancellation)
        duplicate_cancel = store.cancel_prepared_physical_action(
            cancellation
        )
        assert cancelled["confirmedOutcome"] == "NOT_EXECUTED"
        assert duplicate_cancel["disposition"] == "DUPLICATE"
        with pytest.raises(UpdaterStoreError) as confirmed_takeover:
            store.cancel_prepared_physical_action(
                {
                    **cancellation,
                    "dispatchAttemptToken": restarted_business_token,
                }
            )
        assert confirmed_takeover.value.code == (
            "PHYSICAL_ACTION_CANCEL_DENIED"
        )
    finally:
        store.close()


def test_concurrent_different_dispatch_tokens_only_arm_one_attempt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        store.prepare_physical_action(_action_payload(token="C" * 43))

        payloads = [
            _arm_payload(token="C" * 43),
            _arm_payload(token="D" * 43),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(store.arm_physical_action, payloads))

        assert sorted(result["disposition"] for result in results) == [
            "ACCEPTED",
            "DENIED",
        ]
        assert sum(bool(result["mayExecute"]) for result in results) == 1
    finally:
        store.close()


def test_prepared_action_can_only_close_through_explicit_cancellation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    cancellation = {
        "actionUid": _uid(5),
        "receiptUid": _uid(12),
        "dispatchAttemptToken": "A" * 43,
        "evidenceDigestSha256": "6" * 64,
    }
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        store.prepare_physical_action(_action_payload())

        with pytest.raises(UpdaterStoreError) as generic_confirmation:
            store.confirm_physical_action(_confirmation_payload())
        assert generic_confirmation.value.code == (
            "PHYSICAL_ACTION_STATE_CONFLICT"
        )

        cancelled = store.cancel_prepared_physical_action(cancellation)
        duplicate = store.cancel_prepared_physical_action(cancellation)
        assert cancelled["state"] == "CONFIRMED"
        assert cancelled["confirmedOutcome"] == "NOT_EXECUTED"
        assert cancelled["confirmationBasis"] == "PREPARED_NOT_ARMED"
        assert duplicate["disposition"] == "DUPLICATE"
        denied = store.arm_physical_action(_arm_payload())
        assert denied["disposition"] == "DENIED"
        assert denied["mayExecute"] is False
    finally:
        store.close()


def test_armed_action_not_executed_requires_same_live_dispatch_abort(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    token = "E" * 43
    abort = {
        "actionUid": _uid(5),
        "receiptUid": _uid(13),
        "dispatchAttemptToken": token,
        "evidenceDigestSha256": "7" * 64,
    }
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store, token=token)

        with pytest.raises(UpdaterStoreError) as generic_not_executed:
            store.confirm_physical_action(
                _confirmation_payload(outcome="NOT_EXECUTED")
            )
        assert generic_not_executed.value.code == (
            "PHYSICAL_ACTION_NOT_EXECUTED_REQUIRES_ABORT"
        )
        with pytest.raises(UpdaterStoreError) as forged_basis:
            store.confirm_physical_action(
                {
                    **_confirmation_payload(),
                    "confirmationBasis": "PREPARED_NOT_ARMED",
                }
            )
        assert forged_basis.value.code == (
            "PHYSICAL_ACTION_CONFIRMATION_BASIS_FORBIDDEN"
        )
        with pytest.raises(UpdaterStoreError) as wrong_token:
            store.abort_physical_action_dispatch(
                {**abort, "dispatchAttemptToken": "F" * 43}
            )
        assert wrong_token.value.code == "PHYSICAL_ACTION_ABORT_DENIED"
        assert "F" * 43 not in str(wrong_token.value)

        aborted = store.abort_physical_action_dispatch(abort)
        duplicate = store.abort_physical_action_dispatch(abort)
        assert aborted["state"] == "CONFIRMED"
        assert aborted["confirmedOutcome"] == "NOT_EXECUTED"
        assert aborted["confirmationBasis"] == (
            "LIVE_DISPATCH_NOT_WRITTEN"
        )
        assert duplicate["disposition"] == "DUPLICATE"
    finally:
        store.close()


@pytest.mark.parametrize("outcome", ["EXECUTED", "FAILED_SAFE"])
def test_live_fixed_frame_result_confirms_only_the_same_dispatch_attempt(
    tmp_path: Path,
    outcome: str,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "stage4", candidate=True)
    token = "G" * 43
    result_payload = _live_result_payload(token=token, outcome=outcome)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store, token=token)

        with pytest.raises(UpdaterStoreError) as wrong_token:
            store.confirm_live_physical_action_result(
                {
                    **result_payload,
                    "dispatchAttemptToken": "H" * 43,
                }
            )
        assert wrong_token.value.code == (
            "PHYSICAL_ACTION_LIVE_RESULT_DENIED"
        )
        assert "H" * 43 not in str(wrong_token.value)

        confirmed = store.confirm_live_physical_action_result(
            result_payload
        )
        response_loss_retry = store.confirm_live_physical_action_result(
            result_payload
        )
        lookup = store.get_physical_action({"actionUid": _uid(5)})
        assert confirmed["disposition"] == "ACCEPTED"
        assert confirmed["state"] == "CONFIRMED"
        assert confirmed["confirmedOutcome"] == outcome
        assert confirmed["confirmationBasis"] == (
            "LIVE_FIXED_FRAME_RESULT"
        )
        assert confirmed["mayExecute"] is False
        assert response_loss_retry["disposition"] == "DUPLICATE"
        assert response_loss_retry["mayExecute"] is False
        for response in (confirmed, response_loss_retry, lookup):
            assert "dispatchAttemptToken" not in response
            assert "dispatchAttemptTokenSha256" not in response
            assert "armRuntimeInstanceUid" not in response
            assert token not in repr(response)
    finally:
        store.close()


def test_live_fixed_frame_result_rejects_not_executed(tmp_path: Path) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)

        with pytest.raises(UpdaterStoreError) as unsupported:
            store.confirm_live_physical_action_result(
                _live_result_payload(outcome="NOT_EXECUTED")
            )
        assert unsupported.value.code == "REQUEST_INVALID"
        assert store.get_physical_action({"actionUid": _uid(5)})[
            "state"
        ] == "ARMED"
    finally:
        store.close()


def test_live_fixed_frame_result_is_denied_after_updater_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    token = "I" * 43
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.begin_job(_begin_payload())
    _prepare_and_arm(first, token=token)
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        with pytest.raises(UpdaterStoreError) as restarted:
            second.confirm_live_physical_action_result(
                _live_result_payload(token=token)
            )
        assert restarted.value.code == "PHYSICAL_ACTION_LIVE_RESULT_DENIED"
        action = second.get_physical_action({"actionUid": _uid(5)})
        assert action["state"] == "ARMED"
        assert action["confirmedOutcome"] is None
    finally:
        second.close()


def test_v2_may_have_executed_migrates_to_locked_legacy_armed_fact(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v2_database_with_uncertain_action(path)

    store = _store(path, "stage4-v3", candidate=True)
    try:
        status = store.get_status()
        action = store.get_physical_action({"actionUid": _uid(5)})
        assert status["schemaVersion"] == 3
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["unreconciledPhysicalActionCount"] == 1
        assert action["state"] == "ARMED"
        assert action["dispatchMode"] == "LEGACY_V2_UNCERTAIN"
        assert action["mayExecute"] is False

        denied = store.arm_physical_action(_arm_payload())
        assert denied["disposition"] == "DENIED"
        assert denied["state"] == "ARMED"
        with pytest.raises(UpdaterStoreError) as cancel:
            store.cancel_prepared_physical_action(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(14),
                    "dispatchAttemptToken": "A" * 43,
                    "evidenceDigestSha256": "8" * 64,
                }
            )
        assert cancel.value.code == "PHYSICAL_ACTION_CANCEL_DENIED"
        with pytest.raises(UpdaterStoreError) as abort:
            store.abort_physical_action_dispatch(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(14),
                    "dispatchAttemptToken": "A" * 43,
                    "evidenceDigestSha256": "8" * 64,
                }
            )
        assert abort.value.code == "PHYSICAL_ACTION_ABORT_DENIED"
        with pytest.raises(UpdaterStoreError) as live_result:
            store.confirm_live_physical_action_result(
                _live_result_payload()
            )
        assert live_result.value.code == (
            "PHYSICAL_ACTION_LIVE_RESULT_DENIED"
        )

        confirmed = store.confirm_physical_action(_confirmation_payload())
        assert confirmed["state"] == "CONFIRMED"
        assert confirmed["confirmationBasis"] == (
            "MCU_IDENTITY_BOUND_FACT"
        )
        with sqlite3.connect(path) as connection:
            persisted = connection.execute(
                """SELECT state, dispatch_mode,
                          dispatch_attempt_token_sha256
                   FROM physical_action_ledger WHERE action_uid=?""",
                (_uid(5),),
            ).fetchone()
        assert persisted == ("CONFIRMED", "LEGACY_V2_UNCERTAIN", None)
    finally:
        store.close()


@pytest.mark.parametrize(
    "legacy_outcome",
    ["EXECUTED", "NOT_EXECUTED", "FAILED_SAFE"],
)
def test_all_v2_confirmed_outcomes_are_quarantined_and_migrate_to_armed(
    tmp_path: Path,
    legacy_outcome: str,
) -> None:
    path = tmp_path / "updater.db"
    _create_v2_database_with_confirmed_action(path, legacy_outcome)

    store = _store(path, "stage4-v3", candidate=True)
    try:
        status = store.get_status()
        action = store.get_physical_action({"actionUid": _uid(5)})
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["unreconciledPhysicalActionCount"] == 1
        assert action["state"] == "ARMED"
        assert action["dispatchMode"] == "LEGACY_V2_UNCERTAIN"
        assert action["receiptUid"] is None
        assert action["confirmedOutcome"] is None
        assert action["confirmationBasis"] is None
        assert action["evidenceDigestSha256"] is None
        assert action["mayExecute"] is False

        denied = store.arm_physical_action(_arm_payload())
        assert denied["disposition"] == "DENIED"
        assert denied["mayExecute"] is False
        with pytest.raises(UpdaterStoreError) as cancelled:
            store.cancel_prepared_physical_action(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(14),
                    "dispatchAttemptToken": "A" * 43,
                    "evidenceDigestSha256": "8" * 64,
                }
            )
        assert cancelled.value.code == "PHYSICAL_ACTION_CANCEL_DENIED"
        with pytest.raises(UpdaterStoreError) as aborted:
            store.abort_physical_action_dispatch(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(14),
                    "dispatchAttemptToken": "A" * 43,
                    "evidenceDigestSha256": "8" * 64,
                }
            )
        assert aborted.value.code == "PHYSICAL_ACTION_ABORT_DENIED"
        with pytest.raises(UpdaterStoreError) as live_result:
            store.confirm_live_physical_action_result(
                _live_result_payload()
            )
        assert live_result.value.code == (
            "PHYSICAL_ACTION_LIVE_RESULT_DENIED"
        )
        with pytest.raises(UpdaterStoreError) as negative_confirmation:
            store.confirm_physical_action(
                _confirmation_payload(outcome="NOT_EXECUTED")
            )
        assert negative_confirmation.value.code == (
            "PHYSICAL_ACTION_NOT_EXECUTED_REQUIRES_ABORT"
        )

        with sqlite3.connect(path) as connection:
            authoritative = connection.execute(
                """SELECT state, receipt_uid, confirmed_outcome,
                          confirmation_basis, evidence_digest_sha256,
                          confirmed_at
                   FROM physical_action_ledger WHERE action_uid=?""",
                (_uid(5),),
            ).fetchone()
            quarantined = connection.execute(
                """SELECT legacy_receipt_uid, legacy_outcome,
                          legacy_evidence_digest_sha256,
                          legacy_confirmed_at
                   FROM physical_action_v2_evidence_quarantine
                   WHERE action_uid=?""",
                (_uid(5),),
            ).fetchone()
        assert authoritative == (
            "ARMED",
            None,
            None,
            None,
            None,
            None,
        )
        assert quarantined == (
            _uid(7),
            legacy_outcome,
            "d" * 64,
            "2026-09-01T00:01:00.000Z",
        )
    finally:
        store.close()


def test_stable_ids_are_idempotent_and_changed_content_conflicts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        request = _permit_payload()
        assert store.request_job_permit(request)["disposition"] == "ACCEPTED"
        assert store.request_job_permit(request)["disposition"] == "DUPLICATE"
        with pytest.raises(UpdaterStoreError) as conflict:
            store.request_job_permit(
                {**request, "requestDigestSha256": "f" * 64}
            )
        assert conflict.value.code == "JOB_PERMIT_CONFLICT"

        with pytest.raises(UpdaterStoreError) as unsupported_work:
            store.request_job_permit(
                {
                    **_permit_payload(30),
                    "workType": "ARBITRARY_PHYSICAL_WORK",
                }
            )
        assert unsupported_work.value.code == "REQUEST_INVALID"

        begin = _begin_payload()
        with pytest.raises(UpdaterStoreError) as begin_digest_conflict:
            store.begin_job(
                {**begin, "permitDigestSha256": "f" * 64}
            )
        assert (
            begin_digest_conflict.value.code
            == "JOB_BEGIN_DIGEST_CONFLICT"
        )
        assert store.begin_job(begin)["disposition"] == "ACCEPTED"
        assert store.begin_job(begin)["disposition"] == "DUPLICATE"
        action = _action_payload()
        assert store.prepare_physical_action(action)["disposition"] == "ACCEPTED"
        assert store.prepare_physical_action(action)["disposition"] == "DUPLICATE"
        assert store.arm_physical_action(_arm_payload())["disposition"] == "ACCEPTED"
        store.confirm_physical_action(
            _confirmation_payload(receipt_number=11)
        )

        regenerated = {
            **action,
            "actionUid": _uid(9),
        }
        with pytest.raises(UpdaterStoreError) as logical_conflict:
            store.prepare_physical_action(regenerated)
        assert logical_conflict.value.code == "PHYSICAL_ACTION_LOGICAL_CONFLICT"
    finally:
        store.close()


def test_unused_permit_can_be_abandoned_idempotently(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        abandonment = {
            "permitUid": _uid(1),
            "dispositionUid": _uid(10),
            "evidenceSha256": "1" * 64,
        }
        first = store.abandon_job_permit(abandonment)
        duplicate = store.abandon_job_permit(abandonment)
        assert first["state"] == "ABANDONED"
        assert first["mayStart"] is False
        assert duplicate["disposition"] == "DUPLICATE"
        assert store.get_status()["activeJobPermitCount"] == 0
    finally:
        store.close()


def test_gate_draining_honours_granted_job_but_rejects_new_permits(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        status = store.transition_job_gate(
            "DRAINING",
            owner_update_uid=_uid(40),
            maintenance_type="MCU_FIRMWARE_UPDATE",
        )
        assert status["jobGateState"] == "DRAINING"
        assert status["maintenanceOwnerUid"] == _uid(40)
        assert store.begin_job(_begin_payload())["state"] == "ACTIVE"
        _prepare_and_arm(store)
        armed = store.get_status()
        assert armed["jobGateState"] == "LOCKED"
        assert armed["reconciliationRequired"] is True
        assert armed["unreconciledPhysicalActionCount"] == 1
        store.confirm_physical_action(_confirmation_payload())
        draining_again = store.get_status()
        assert draining_again["jobGateState"] == "DRAINING"
        assert draining_again["reconciliationRequired"] is False
        with pytest.raises(UpdaterStoreError) as still_working:
            store.transition_job_gate("MAINTENANCE")
        assert still_working.value.code == "JOB_DRAIN_INCOMPLETE"
        with pytest.raises(UpdaterStoreError) as closed:
            store.request_job_permit(_permit_payload(20))
        assert closed.value.code == "JOB_GATE_CLOSED"

        store.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(11),
                "outcome": "SUCCEEDED",
                "completionDigestSha256": "2" * 64,
            }
        )
        assert store.transition_job_gate("MAINTENANCE")[
            "jobGateState"
        ] == "MAINTENANCE"
        with pytest.raises(UpdaterStoreError) as recovery_required:
            store.transition_job_gate("OPEN")
        assert (
            recovery_required.value.code
            == "MAINTENANCE_RECOVERY_REQUIRED"
        )
        locked = store.get_status()
        assert locked["jobGateState"] == "MAINTENANCE"
        assert locked["maintenanceOwnerUid"] == _uid(40)
    finally:
        store.close()


def test_exact_pre_hardware_drain_can_be_aborted_without_cancelling_active_job(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        draining = store.transition_job_gate(
            "DRAINING",
            owner_update_uid=_uid(40),
            maintenance_type="MCU_FIRMWARE_UPDATE",
        )

        reopened = store.abort_update_drain(
            _uid(40),
            draining["maintenanceFenceToken"],
            evidence_sha256="3" * 64,
        )

        assert reopened["jobGateState"] == "OPEN"
        assert reopened["maintenanceOwnerUid"] is None
        assert reopened["activeJobPermitCount"] == 1
        assert store.begin_job(_begin_payload())["state"] == "ACTIVE"
        replay = store.abort_update_drain(
            _uid(40),
            draining["maintenanceFenceToken"],
            evidence_sha256="3" * 64,
        )
        assert replay["jobGateState"] == "OPEN"
    finally:
        store.close()


def test_drain_abort_is_forbidden_after_entering_hardware_maintenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        draining = store.transition_job_gate(
            "DRAINING",
            owner_update_uid=_uid(40),
            maintenance_type="MCU_FIRMWARE_UPDATE",
        )
        store.transition_job_gate("MAINTENANCE")

        with pytest.raises(UpdaterStoreError) as raised:
            store.abort_update_drain(
                _uid(40),
                draining["maintenanceFenceToken"],
                evidence_sha256="3" * 64,
            )

        assert raised.value.code == "MAINTENANCE_DRAIN_ABORT_NOT_ALLOWED"
        assert store.get_status()["jobGateState"] == "MAINTENANCE"
    finally:
        store.close()


def test_manual_gate_lock_blocks_preparing_actions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _operator_lock(store, 160)

        with pytest.raises(UpdaterStoreError) as prepare_closed:
            store.prepare_physical_action(
                {
                    **_action_payload(),
                    "actionUid": _uid(50),
                    "actionKey": "delivery.door.unlock.2",
                }
            )
        assert prepare_closed.value.code == "JOB_GATE_CLOSED"
    finally:
        store.close()


def test_restart_with_unconfirmed_action_stays_locked_until_receipt_and_completion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.begin_job(_begin_payload())
    _prepare_and_arm(first)
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        status = second.get_status()
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["unreconciledPhysicalActionCount"] == 1
        assert second.get_physical_action({"actionUid": _uid(5)})[
            "state"
        ] == "ARMED"
        restarted_arm = second.arm_physical_action(_arm_payload())
        assert restarted_arm["disposition"] == "DENIED"
        assert restarted_arm["mayExecute"] is False
        with pytest.raises(UpdaterStoreError) as restarted_abort:
            second.abort_physical_action_dispatch(
                {
                    "actionUid": _uid(5),
                    "receiptUid": _uid(15),
                    "dispatchAttemptToken": "A" * 43,
                    "evidenceDigestSha256": "9" * 64,
                }
            )
        assert restarted_abort.value.code == "PHYSICAL_ACTION_ABORT_DENIED"
        second.confirm_physical_action(_confirmation_payload())
        second.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(8),
                "outcome": "SUCCEEDED",
                "completionDigestSha256": "e" * 64,
            }
        )
        assert second.get_status()["jobGateState"] == "OPEN"
    finally:
        second.close()


def test_armed_action_can_be_quarantined_without_rewriting_unknown_effect(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)

        first = store.quarantine_unknown_physical_action(
            _unknown_effect_quarantine_payload()
        )
        duplicate = store.quarantine_unknown_physical_action(
            _unknown_effect_quarantine_payload()
        )

        assert first["disposition"] == "ACCEPTED"
        assert duplicate["disposition"] == "DUPLICATE"
        assert first["resolutionState"] == "UNKNOWN_EFFECT_QUARANTINED"
        # The historical action remains truthful: it was armed and may have
        # executed.  The append-only resolution only permits safe closure.
        action = store.get_physical_action({"actionUid": _uid(5)})
        assert action["state"] == "ARMED"
        assert action["confirmedOutcome"] is None
        assert action["unknownEffectResolution"]["resolutionUid"] == _uid(17)
        assert store.get_status()["unreconciledPhysicalActionCount"] == 0

        with pytest.raises(UpdaterStoreError) as wrong_outcome:
            store.complete_job(
                {
                    "permitUid": _uid(1),
                    "completionUid": _uid(18),
                    "outcome": "SUCCEEDED",
                    "completionDigestSha256": "8" * 64,
                }
            )
        assert wrong_outcome.value.code == (
            "JOB_QUARANTINE_REQUIRES_CANCELLATION"
        )

        completed = store.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(18),
                "outcome": "CANCELLED",
                "completionDigestSha256": "8" * 64,
            }
        )
        assert completed["state"] == "COMPLETED"
        assert completed["completionOutcome"] == "CANCELLED"
        assert store.get_status()["jobGateState"] == "OPEN"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("resolutionUid", _uid(19)),
        ("permitUid", _uid(20)),
        ("workUid", _uid(21)),
        ("commandUid", _uid(22)),
        ("actionKey", "delivery.door.unlock.2"),
        ("actionKind", "CLEAN_DOOR_UNLOCK"),
        ("actionDigestSha256", "b" * 64),
        ("expectedLedgerSequence", 2),
        ("evidenceDigestSha256", "7" * 64),
    ],
)
def test_unknown_effect_quarantine_rejects_any_identity_or_evidence_change(
    tmp_path: Path,
    changed_field: str,
    changed_value: object,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        _prepare_and_arm(store)
        store.quarantine_unknown_physical_action(
            _unknown_effect_quarantine_payload()
        )

        changed = {
            **_unknown_effect_quarantine_payload(),
            changed_field: changed_value,
        }
        with pytest.raises(UpdaterStoreError) as conflict:
            store.quarantine_unknown_physical_action(changed)

        assert conflict.value.code in {
            "PHYSICAL_ACTION_QUARANTINE_CONFLICT",
            "PHYSICAL_ACTION_IDENTITY_MISMATCH",
        }
        assert store.get_physical_action({"actionUid": _uid(5)})[
            "state"
        ] == "ARMED"
    finally:
        store.close()


def test_only_armed_action_can_receive_unknown_effect_quarantine(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        store.prepare_physical_action(_action_payload())

        with pytest.raises(UpdaterStoreError) as prepared:
            store.quarantine_unknown_physical_action(
                _unknown_effect_quarantine_payload()
            )
        assert prepared.value.code == "PHYSICAL_ACTION_STATE_CONFLICT"

        store.arm_physical_action(_arm_payload())
        store.confirm_physical_action(_confirmation_payload())
        with pytest.raises(UpdaterStoreError) as confirmed:
            store.quarantine_unknown_physical_action(
                _unknown_effect_quarantine_payload()
            )
        assert confirmed.value.code == "PHYSICAL_ACTION_STATE_CONFLICT"
    finally:
        store.close()


@pytest.mark.parametrize("restart_after_begin", [False, True])
def test_restart_can_resume_the_one_durable_job_before_its_first_action(
    tmp_path: Path,
    restart_after_begin: bool,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    if restart_after_begin:
        first.begin_job(_begin_payload())
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        status = second.get_status()
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "ACTIVE_JOB_RECONCILIATION"
        if not restart_after_begin:
            assert second.begin_job(_begin_payload())["state"] == "ACTIVE"
        authorized = _prepare_and_arm(second)
        assert authorized["disposition"] == "ACCEPTED"
        assert authorized["mayExecute"] is True
    finally:
        second.close()


def test_resolving_restarted_job_restores_retained_maintenance_lock_reason(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    owner_uid = _uid(60)
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.transition_job_gate(
        "DRAINING",
        owner_update_uid=owner_uid,
        maintenance_type="MCU_FIRMWARE_UPDATE",
    )
    first.begin_job(_begin_payload())
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        restarted = second.get_status()
        assert restarted["jobGateState"] == "LOCKED"
        assert restarted["blockReasonCode"] == "ACTIVE_JOB_RECONCILIATION"
        assert restarted["maintenanceOwnerUid"] == owner_uid

        second.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(11),
                "outcome": "FAILED",
                "completionDigestSha256": "2" * 64,
            }
        )

        retained = second.get_status()
        assert retained["jobGateState"] == "LOCKED"
        assert retained["maintenanceState"] == "LOCKED"
        assert retained["blockReasonCode"] == (
            "MAINTENANCE_RECOVERY_REQUIRED"
        )
        assert retained["reconciliationRequired"] is True
        assert retained["maintenanceOwnerUid"] == owner_uid
    finally:
        second.close()


def test_restart_cannot_erase_a_maintenance_fence_with_generic_open(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    owner_uid = _uid(60)
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.transition_job_gate(
        "DRAINING",
        owner_update_uid=owner_uid,
        maintenance_type="MCU_FIRMWARE_UPDATE",
    )
    first.transition_job_gate("MAINTENANCE")
    fence = first.get_status()["maintenanceFenceToken"]
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        recovered = second.get_status()
        assert recovered["jobGateState"] == "LOCKED"
        assert recovered["blockReasonCode"] == (
            "MAINTENANCE_RECOVERY_REQUIRED"
        )
        with pytest.raises(UpdaterStoreError) as blocked:
            second.transition_job_gate("OPEN")
        assert blocked.value.code == "MAINTENANCE_RECOVERY_REQUIRED"
        retained = second.get_status()
        assert retained["maintenanceOwnerUid"] == owner_uid
        assert retained["maintenanceFenceToken"] == fence
    finally:
        second.close()


def test_restart_can_resume_only_the_exact_drained_update_wait(tmp_path: Path) -> None:
    path = tmp_path / "updater.db"
    owner_uid = _uid(60)
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.transition_job_gate(
        "DRAINING",
        owner_update_uid=owner_uid,
        maintenance_type="MCU_FIRMWARE_UPDATE",
    )
    fence = first.get_status()["maintenanceFenceToken"]
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        assert second.get_status()["jobGateState"] == "LOCKED"
        with pytest.raises(UpdaterStoreError) as wrong_owner:
            second.resume_update_drain(_uid(61), fence)
        assert wrong_owner.value.code == (
            "MAINTENANCE_DRAIN_RECOVERY_NOT_AUTHORIZED"
        )

        resumed = second.resume_update_drain(owner_uid, fence)

        assert resumed["jobGateState"] == "DRAINING"
        assert resumed["maintenanceOwnerUid"] == owner_uid
        assert resumed["maintenanceFenceToken"] == fence
    finally:
        second.close()


def test_restart_drain_waits_for_active_job_reconciliation_before_resume(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    owner_uid = _uid(60)
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    first.begin_job(_begin_payload())
    first.transition_job_gate(
        "DRAINING",
        owner_update_uid=owner_uid,
        maintenance_type="MCU_FIRMWARE_UPDATE",
    )
    fence = first.get_status()["maintenanceFenceToken"]
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        with pytest.raises(UpdaterStoreError) as active:
            second.resume_update_drain(owner_uid, fence)
        assert active.value.code == (
            "MAINTENANCE_DRAIN_RECOVERY_NOT_AUTHORIZED"
        )

        second.complete_job(
            {
                "permitUid": _uid(1),
                "completionUid": _uid(62),
                "outcome": "FAILED",
                "completionDigestSha256": "8" * 64,
            }
        )
        recovered = second.get_status()
        assert recovered["jobGateState"] == "LOCKED"
        assert recovered["blockReasonCode"] == "MAINTENANCE_RECOVERY_REQUIRED"

        resumed = second.resume_update_drain(owner_uid, fence)
        assert resumed["jobGateState"] == "DRAINING"
    finally:
        second.close()


@pytest.mark.parametrize("with_unresolved_action", [False, True])
def test_restart_preserves_explicit_manual_lock_over_active_job_state(
    tmp_path: Path,
    with_unresolved_action: bool,
) -> None:
    path = tmp_path / "updater.db"
    first = _store(path, "stage4", candidate=True)
    _activate_candidate(first)
    first.request_job_permit(_permit_payload())
    if with_unresolved_action:
        first.begin_job(_begin_payload())
        _prepare_and_arm(first)
    _operator_lock(first, 161)
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        status = second.get_status()
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "MANUAL_SAFETY_LOCK"
        if not with_unresolved_action:
            with pytest.raises(UpdaterStoreError) as begin_blocked:
                second.begin_job(_begin_payload())
            assert begin_blocked.value.code == "JOB_GATE_CLOSED"
        else:
            with pytest.raises(UpdaterStoreError) as action_blocked:
                second.prepare_physical_action(
                    {
                        **_action_payload(),
                        "actionUid": _uid(70),
                        "actionKey": "delivery.door.unlock.2",
                    }
                )
            assert action_blocked.value.code == "JOB_GATE_CLOSED"
    finally:
        second.close()


def test_store_rejects_incompatible_schema_without_registering_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE schema_version (
                   singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                   version INTEGER NOT NULL CHECK (version > 0)
               )"""
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 2)")
    if os.name == "posix":
        path.chmod(0o600)

    store = UpdaterStore(path, release_version="updater-v1")
    with pytest.raises(
        RuntimeError,
        match="updater database schema is incompatible",
    ):
        store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='updater_runtime_instance'"
        ).fetchone() is None


def test_pristine_stage3_rollback_inspection_accepts_only_initial_posture(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "updater-v1")
    store.close()

    result = inspect_pristine_stage3_rollback_state(path)

    assert result["schemaVersion"] == 3
    assert result["candidateActivationState"] == "REQUIRED"
    assert result["runtimeInstanceCount"] == 1


def test_pristine_stage3_rollback_inspection_rejects_deleted_history_sequence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path, "updater-v1")
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE sqlite_sequence SET seq=1 WHERE name='physical_action_ledger'"
        )

    with pytest.raises(
        PristineRollbackStateUsed, match="deleted control or action history"
    ):
        inspect_pristine_stage3_rollback_state(path)


def test_schema_sql_normalizer_never_merges_adjacent_identifiers() -> None:
    assert _normalize_schema_sql("work_uid TEXT") != _normalize_schema_sql(
        "work_uidtext"
    )
    assert _normalize_schema_sql(
        "CREATE TABLE demo (Value TEXT)"
    ) == _normalize_schema_sql(" create\n table DEMO( value text ) ")


@pytest.mark.parametrize(
    "release_version",
    ["", "x" * 33, " updater-v1", "updater-v1\n"],
)
def test_store_rejects_invalid_release_version(
    tmp_path: Path,
    release_version: str,
) -> None:
    with pytest.raises(ValueError, match="release version"):
        UpdaterStore(
            tmp_path / "updater.db",
            release_version=release_version,
        )


def test_store_never_accepts_the_business_database_name(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="must not use the business edge.db"):
        UpdaterStore(tmp_path / "edge.db", release_version="updater-v1")


def test_store_rejects_database_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.db"
    target.touch()
    link = tmp_path / "updater.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("database symlinks are unavailable")

    store = UpdaterStore(link, release_version="updater-v1")
    with pytest.raises(PermissionError, match="single-linked regular file"):
        store.initialize()


def test_store_rejects_hard_linked_database(tmp_path: Path) -> None:
    target = tmp_path / "real.db"
    target.touch()
    link = tmp_path / "updater.db"
    try:
        os.link(target, link)
    except OSError:
        pytest.skip("database hard links are unavailable")

    store = UpdaterStore(link, release_version="updater-v1")
    with pytest.raises(PermissionError, match="single-linked regular file"):
        store.initialize()


def test_store_requires_precreated_private_state_directory(
    tmp_path: Path,
) -> None:
    store = UpdaterStore(
        tmp_path / "missing" / "updater.db",
        release_version="updater-v1",
    )

    with pytest.raises(PermissionError, match="parent does not exist"):
        store.initialize()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are required")
def test_store_rejects_shared_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "updater.db"
    path.touch(mode=0o600)
    path.chmod(0o640)

    store = UpdaterStore(path, release_version="updater-v1")
    with pytest.raises(PermissionError, match="permissions are too broad"):
        store.initialize()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are required")
def test_store_rejects_shared_database_parent(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o770)

    store = UpdaterStore(
        parent / "updater.db",
        release_version="updater-v1",
    )
    with pytest.raises(PermissionError, match="group/world access"):
        store.initialize()
