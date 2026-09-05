from __future__ import annotations

import sqlite3
import uuid

import pytest

from business_update_store import BusinessUpdateStore, BusinessUpdateStoreError
from local_control import canonical_local_payload_sha256
from updater_store import UpdaterStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT_UID = "22222222-2222-4222-8222-222222222222"
COMMAND_UID = "33333333-3333-4333-8333-333333333333"
RELEASE_ID = "44444444-4444-4444-8444-444444444444"
ACTION_UID = "55555555-5555-4555-8555-555555555555"
CANCEL_COMMAND_UID = "66666666-6666-4666-8666-666666666666"
CANCEL_EVENT_UID = "77777777-7777-4777-8777-777777777777"


def _request(**overrides) -> dict:
    result = {
        "updateUid": UPDATE_UID,
        "deploymentUid": DEPLOYMENT_UID,
        "commandUid": COMMAND_UID,
        "releaseId": RELEASE_ID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
        "packageSize": 1024,
        "signingKeyId": "business_2026",
    }
    result.update(overrides)
    return result


def _store(path) -> BusinessUpdateStore:
    store = BusinessUpdateStore(path)
    store.initialize()
    return store


def _remote_request(**overrides) -> dict:
    result = {
        **_request(),
        "stablePayloadSha256": "b" * 64,
        "controlSequence": 1,
        "objectKey": (
            f"edge-runtime/releases/{RELEASE_ID}/package.tar.gz"
        ),
        "signatureSha256": (
            "f5a5fd42d16a20302798ef6ed309979b43003d2320d9f0e8ea9831a92759fb4b"
        ),
        "packageSignatureBase64": (
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "AAAAAAAAAAAAAAAAAAAAAA=="
        ),
        "observationWindowSeconds": 1800,
        "downloadTimeoutSeconds": 1800,
        "drainTimeoutSeconds": 1800,
        "maximumRetryCount": 3,
    }
    result.update(overrides)
    return result


def _cancel_request(**overrides) -> dict:
    result = {
        "cancelCommandUid": CANCEL_COMMAND_UID,
        "updateUid": UPDATE_UID,
        "deploymentUid": DEPLOYMENT_UID,
        "controlSequence": 2,
        "reason": "operator cancelled before device mutation",
    }
    result.update(overrides)
    return result


def test_extension_coexists_with_the_permanent_safety_database(tmp_path) -> None:
    path = tmp_path / "updater.db"
    safety = UpdaterStore(path, release_version="test")
    safety.initialize()
    store = _store(path)

    assert store.create_update(_request())["disposition"] == "ACCEPTED"
    store.close()
    safety.close()

    reopened = UpdaterStore(path, release_version="test")
    reopened.initialize()
    assert reopened.get_status()["component"] == "DEVICE_UPDATER"
    reopened.close()


def test_journal_persists_complete_previous_and_installed_release_identity(
    tmp_path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path)
    store.create_update(_request())

    update = store.transition(
        UPDATE_UID,
        "MIGRATING_DATA",
        fields={
            "previous_release_id": str(uuid.uuid4()),
            "previous_version_name": "1.0.0",
            "previous_release_sequence": 1,
            "previous_package_sha256": "b" * 64,
            "installed_release_id": RELEASE_ID,
            "installed_version_name": "1.1.0",
            "installed_release_sequence": 2,
            "installed_package_sha256": "a" * 64,
        },
    )

    assert update["previousReleaseSequence"] == 1
    assert update["previousPackageSha256"] == "b" * 64
    assert update["installedVersionName"] == "1.1.0"
    assert update["installedReleaseSequence"] == 2
    assert store.confirmed_installed_release() == {
        "releaseUid": RELEASE_ID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
    }


def test_update_request_is_idempotent_and_only_one_can_be_active(tmp_path) -> None:
    store = _store(tmp_path / "updater.db")

    assert store.create_update(_request())["disposition"] == "ACCEPTED"
    assert store.create_update(_request())["disposition"] == "DUPLICATE"
    with pytest.raises(BusinessUpdateStoreError) as conflict:
        store.create_update(_request(packageSha256="b" * 64))
    assert conflict.value.code == "BUSINESS_UPDATE_IDEMPOTENCY_CONFLICT"

    with pytest.raises(BusinessUpdateStoreError) as busy:
        store.create_update(
            _request(
                updateUid=str(uuid.uuid4()),
                commandUid=str(uuid.uuid4()),
                deploymentUid=str(uuid.uuid4()),
                releaseId=str(uuid.uuid4()),
                versionName="1.2.0",
                releaseSequence=3,
                packageSha256="c" * 64,
            )
        )
    assert busy.value.code == "BUSINESS_UPDATE_BUSY"


def test_remote_update_refreshes_only_monotonic_authorization_without_url(
    tmp_path,
) -> None:
    path = tmp_path / "updater.db"
    store = BusinessUpdateStore(path, remote_trigger_enabled=True)
    store.initialize()

    accepted = store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )
    assert accepted["authorizationDisposition"] == "ACCEPTED"
    duplicate = store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )
    assert duplicate["authorizationDisposition"] == "DUPLICATE_AUTHORIZATION"
    refreshed = store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=2
    )
    assert refreshed["authorizationDisposition"] == "REFRESHED"
    assert refreshed["stageSequence"] == 2
    assert store.get_remote_update(UPDATE_UID)["authorizationSequence"] == 2
    assert store.get_status()["remoteTriggerEnabled"] is True

    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(business_remote_update)"
            )
        }
        stored_text = " ".join(
            str(value)
            for row in connection.execute(
                "SELECT * FROM business_remote_update"
            )
            for value in row
        )
    assert "url" not in {column.casefold() for column in columns}
    assert "https://" not in stored_text

    with pytest.raises(BusinessUpdateStoreError) as conflict:
        store.create_or_refresh_remote_update(
            _remote_request(stablePayloadSha256="c" * 64),
            authorization_sequence=3,
        )
    assert conflict.value.code == "BUSINESS_UPDATE_IDEMPOTENCY_CONFLICT"


def test_remote_download_state_survives_restart_and_requires_new_grant(
    tmp_path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path)
    store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=4
    )
    started = store.begin_remote_download(UPDATE_UID, 4)
    assert (started["downloadState"], started["downloadAttemptCount"]) == (
        "DOWNLOADING",
        1,
    )
    store.close()

    reopened = _store(path)
    recovery = reopened.list_remote_updates_requiring_recovery()
    assert [item["updateUid"] for item in recovery] == [UPDATE_UID]
    waiting = reopened.mark_remote_download_authorization_required(
        UPDATE_UID, 4, "DOWNLOAD_AUTHORIZATION_LOST"
    )
    assert waiting["state"] == "RECEIVED"
    assert waiting["stageSequence"] == 3
    assert reopened.get_remote_update(UPDATE_UID)["downloadState"] == (
        "WAITING_AUTHORIZATION"
    )


def test_remote_cancellation_is_monotonic_idempotent_and_durable(
    tmp_path,
) -> None:
    path = tmp_path / "updater.db"
    store = _store(path)
    store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )

    accepted = store.request_remote_cancellation(_cancel_request())
    repeated = store.request_remote_cancellation(_cancel_request())
    assert (accepted["disposition"], accepted["outcome"]) == (
        "ACCEPTED",
        "ACCEPTED",
    )
    assert (repeated["disposition"], repeated["outcome"]) == (
        "DUPLICATE",
        "ACCEPTED",
    )
    with pytest.raises(BusinessUpdateStoreError) as tampered:
        store.request_remote_cancellation(
            _cancel_request(reason="different reason")
        )
    assert tampered.value.code == "BUSINESS_CANCEL_IDEMPOTENCY_CONFLICT"

    evidence = "c" * 64
    completed = store.complete_remote_cancellation(
        UPDATE_UID,
        evidence_sha256=evidence,
    )
    assert completed["state"] == "DEFERRED"
    assert completed["errorCode"] == "BUSINESS_UPDATE_CANCELLED"
    assert completed["businessAdmission"] == "ACCEPTING"
    assert store.get_active_update() is None
    [result] = store.list_cancellation_results_requiring_delivery()
    assert result["outcome"] == "CANCELLED"
    payload = {
        "deploymentUid": DEPLOYMENT_UID,
        "updateUid": UPDATE_UID,
        "controlSequence": 2,
        "result": "CANCELLED",
        "observedStage": "RECEIVED",
        "businessAdmissionState": "OPEN",
        "errorCode": None,
    }
    delivery = store.reserve_cancellation_result_delivery(
        CANCEL_COMMAND_UID,
        CANCEL_EVENT_UID,
        "2026-09-05T01:02:03.000Z",
        "SYNCED",
        payload,
    )
    assert delivery["payload"] == payload
    assert store.list_cancellation_results_requiring_delivery() == []
    assert len(store.list_pending_cancellation_result_deliveries()) == 1
    accepted_delivery = store.mark_cancellation_result_delivery_accepted(
        CANCEL_COMMAND_UID,
        CANCEL_EVENT_UID,
    )
    assert accepted_delivery["acceptedAt"] is not None
    store.close()

    reopened = _store(path)
    duplicate = reopened.request_remote_cancellation(_cancel_request())
    assert (duplicate["disposition"], duplicate["outcome"]) == (
        "DUPLICATE",
        "CANCELLED",
    )
    assert reopened.list_pending_cancellation_result_deliveries() == []


def test_remote_cancellation_records_too_late_without_changing_update(
    tmp_path,
) -> None:
    store = _store(tmp_path / "updater.db")
    store.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )
    store.transition(UPDATE_UID, "MIGRATING_DATA")

    result = store.request_remote_cancellation(_cancel_request())

    assert result["outcome"] == "TOO_LATE"
    assert result["observedState"] == "MIGRATING_DATA"
    assert store.get_update(UPDATE_UID)["state"] == "MIGRATING_DATA"
    assert store.get_pending_cancellation(UPDATE_UID) is None
    [pending] = store.list_cancellation_results_requiring_delivery()
    assert pending["cancelCommandUid"] == CANCEL_COMMAND_UID


def test_remote_cancellation_rejects_stale_control_sequence(tmp_path) -> None:
    store = _store(tmp_path / "updater.db")
    store.create_or_refresh_remote_update(
        _remote_request(controlSequence=7), authorization_sequence=1
    )

    with pytest.raises(BusinessUpdateStoreError) as stale:
        store.request_remote_cancellation(
            _cancel_request(controlSequence=7)
        )

    assert stale.value.code == "BUSINESS_CANCEL_SEQUENCE_STALE"


def test_privileged_action_is_exactly_authorized_once(tmp_path) -> None:
    store = _store(tmp_path / "updater.db")
    store.create_update(_request())
    store.transition(
        UPDATE_UID,
        "MIGRATING_DATA",
        fields={"maintenance_fence_token": 7},
    )
    payload = {"updateUid": UPDATE_UID, "actionUid": ACTION_UID}
    prepared = store.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=ACTION_UID,
        action_kind="INSPECT_BASELINE",
        payload=payload,
    )
    store.begin_action(ACTION_UID)
    authorization = {
        "helperComponent": prepared["helperComponent"],
        "helperAction": prepared["helperAction"],
        "updateUid": UPDATE_UID,
        "actionUid": ACTION_UID,
        "payloadSha256": canonical_local_payload_sha256(payload),
    }

    assert store.authorize_action(authorization)["authorized"] is True
    with pytest.raises(BusinessUpdateStoreError) as replay:
        store.authorize_action(authorization)
    assert replay.value.code == "PRIVILEGED_ACTION_NOT_AUTHORIZED"
    assert store.finish_action(
        ACTION_UID,
        "SUCCEEDED",
        response={"businessRuntimeState": "ACTIVE"},
    )["state"] == "SUCCEEDED"


def test_restart_marks_an_inflight_helper_result_unknown(tmp_path) -> None:
    path = tmp_path / "updater.db"
    store = _store(path)
    store.create_update(_request())
    store.transition(
        UPDATE_UID,
        "MIGRATING_DATA",
        fields={"maintenance_fence_token": 7},
    )
    payload = {"updateUid": UPDATE_UID, "actionUid": ACTION_UID}
    store.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=ACTION_UID,
        action_kind="INSPECT_BASELINE",
        payload=payload,
    )
    store.begin_action(ACTION_UID)
    store.close()

    reopened = _store(path)
    action = reopened.get_action(ACTION_UID)
    assert action is not None
    assert (action["state"], action["errorCode"]) == (
        "UNKNOWN",
        "UPDATER_RESTARTED",
    )
