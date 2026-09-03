from __future__ import annotations

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
