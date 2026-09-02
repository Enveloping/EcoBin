from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from updater_store import UpdaterStore, UpdaterStoreError


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
    opened = store.transition_job_gate("OPEN")
    assert opened["jobGateState"] == "OPEN"
    assert opened["reconciliationRequired"] is False


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


def _action_payload(number: int = 1) -> dict[str, str]:
    return {
        "actionUid": _uid(number + 4),
        "permitUid": _uid(number),
        "workUid": _uid(number + 1),
        "commandUid": _uid(number + 2),
        "actionKey": "delivery.door.unlock.1",
        "actionKind": "DELIVERY_DOOR_UNLOCK",
        "actionDigestSha256": "c" * 64,
    }


def _authorization_payload(number: int = 1) -> dict[str, str]:
    return {
        **_action_payload(number),
        "armUid": _uid(number + 5),
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


def test_store_defaults_to_locked_disabled_v2_and_persists_real_instances(
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
        "schemaVersion": 2,
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
        ).fetchall() == [(2,)]
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
        }.issubset(tables)
    second.close()


def test_v1_migrates_in_one_start_to_locked_v2_without_losing_instances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "updater.db"
    _create_v1_database(path)

    store = _store(path, "stage4")
    try:
        status = store.get_status()
        assert status["schemaVersion"] == 2
        assert status["stage4CandidateEnabled"] is False
        assert status["jobGateState"] == "LOCKED"
        assert status["blockReasonCode"] == "STAGE4_CANDIDATE_DISABLED"
        assert status["managementStateSequence"] == 2
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT version FROM schema_version"
            ).fetchone() == (2,)
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

        armed = store.authorize_physical_action(_authorization_payload())
        assert armed["state"] == "MAY_HAVE_EXECUTED"
        assert armed["mayExecute"] is True
        duplicate_arm = store.authorize_physical_action(
            _authorization_payload()
        )
        assert duplicate_arm["disposition"] == "DUPLICATE"
        assert duplicate_arm["mayExecute"] is False
        assert store.get_physical_action({"actionUid": _uid(5)})[
            "mayExecute"
        ] is False
        assert store.get_status()["jobGateState"] == "LOCKED"
        armed_sequence = store.get_status()["managementStateSequence"]

        with pytest.raises(UpdaterStoreError) as previous_unknown:
            store.authorize_physical_action(
                {
                    **_authorization_payload(),
                    "actionUid": _uid(50),
                    "armUid": _uid(51),
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

        confirmed = store.confirm_physical_action(
            {
                "actionUid": _uid(5),
                "receiptUid": _uid(7),
                "outcome": "EXECUTED",
                "evidenceDigestSha256": "d" * 64,
            }
        )
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
        action = _authorization_payload()
        assert store.authorize_physical_action(action)["disposition"] == "ACCEPTED"
        assert store.authorize_physical_action(action)["disposition"] == "DUPLICATE"
        store.confirm_physical_action(
            {
                "actionUid": action["actionUid"],
                "receiptUid": _uid(11),
                "outcome": "EXECUTED",
                "evidenceDigestSha256": "d" * 64,
            }
        )

        regenerated = {
            **action,
            "actionUid": _uid(9),
            "armUid": _uid(10),
        }
        with pytest.raises(UpdaterStoreError) as logical_conflict:
            store.authorize_physical_action(regenerated)
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
        store.authorize_physical_action(_authorization_payload())
        armed = store.get_status()
        assert armed["jobGateState"] == "LOCKED"
        assert armed["reconciliationRequired"] is True
        assert armed["unreconciledPhysicalActionCount"] == 1
        store.confirm_physical_action(
            {
                "actionUid": _uid(5),
                "receiptUid": _uid(7),
                "outcome": "EXECUTED",
                "evidenceDigestSha256": "d" * 64,
            }
        )
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


def test_manual_gate_lock_blocks_authorizing_actions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "updater.db", "stage4", candidate=True)
    try:
        _activate_candidate(store)
        store.request_job_permit(_permit_payload())
        store.begin_job(_begin_payload())
        store.transition_job_gate(
            "LOCKED",
            block_reason_code="MANUAL_SAFETY_LOCK",
        )

        with pytest.raises(UpdaterStoreError) as authorize_closed:
            store.authorize_physical_action(
                {
                    **_authorization_payload(),
                    "actionUid": _uid(50),
                    "armUid": _uid(51),
                    "actionKey": "delivery.door.unlock.2",
                }
            )
        assert authorize_closed.value.code == "JOB_GATE_CLOSED"
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
    first.authorize_physical_action(_authorization_payload())
    first.close()

    second = _store(path, "stage4", candidate=True)
    try:
        status = second.get_status()
        assert status["jobGateState"] == "LOCKED"
        assert status["reconciliationRequired"] is True
        assert status["unreconciledPhysicalActionCount"] == 1
        assert second.get_physical_action({"actionUid": _uid(5)})[
            "state"
        ] == "MAY_HAVE_EXECUTED"
        second.confirm_physical_action(
            {
                "actionUid": _uid(5),
                "receiptUid": _uid(7),
                "outcome": "EXECUTED",
                "evidenceDigestSha256": "d" * 64,
            }
        )
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
        authorized = second.authorize_physical_action(
            _authorization_payload()
        )
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
        first.authorize_physical_action(_authorization_payload())
    first.transition_job_gate(
        "LOCKED",
        block_reason_code="MANUAL_SAFETY_LOCK",
    )
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
                second.authorize_physical_action(
                    {
                        **_authorization_payload(),
                        "actionUid": _uid(70),
                        "armUid": _uid(71),
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
