from __future__ import annotations

import uuid

import pytest

from local_control import canonical_local_payload_sha256
from mcu_update_store import McuUpdateStore, McuUpdateStoreError


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
COMMAND_UID = "22222222-2222-4222-8222-222222222222"
HANDOFF_UID = "33333333-3333-4333-8333-333333333333"
ACTION_UID = "44444444-4444-4444-8444-444444444444"


def _manifest(version_code: int, identity_hex: str, image_sha256: str) -> dict:
    return {
        "schemaVersion": 1,
        "fixedFrameRevision": 2,
        "firmwareVersionCode": version_code,
        "firmwareVersion": f"1.0.{version_code}",
        "firmwareIdentityHex": identity_hex,
        "imageSha256": image_sha256,
    }


def _request(update_uid: str = UPDATE_UID, command_uid: str = COMMAND_UID) -> dict:
    return {
        "updateUid": update_uid,
        "commandUid": command_uid,
        "targetPackageSha256": "a" * 64,
        "rollbackPackageSha256": "b" * 64,
    }


def _create(store: McuUpdateStore, **overrides) -> dict:
    request = _request(
        overrides.get("update_uid", UPDATE_UID),
        overrides.get("command_uid", COMMAND_UID),
    )
    return store.create_update(
        update_uid=request["updateUid"],
        command_uid=request["commandUid"],
        request_payload=request,
        target_package_sha256=request["targetPackageSha256"],
        target_manifest=_manifest(2, "2222222222222222", "c" * 64),
        rollback_package_sha256=request["rollbackPackageSha256"],
        rollback_manifest=_manifest(1, "1111111111111111", "d" * 64),
        handoff_uid=overrides.get("handoff_uid", HANDOFF_UID),
    )


def _store(tmp_path) -> McuUpdateStore:
    store = McuUpdateStore(tmp_path / "mcu-updates.db")
    store.initialize()
    return store


def _advance_to_verification(store: McuUpdateStore) -> None:
    transitions = (
        ("DRAINING", {"maintenance_fence_token": 7}),
        ("INITIAL_OBSERVE", {}),
        ("INITIAL_QUIESCE", {"observation_evidence_sha256": "e" * 64}),
        ("STOPPING_TARGET", {"quiesce_evidence_sha256": "f" * 64}),
        ("FLASHING_TARGET", {}),
        ("STARTING_TARGET_VERIFY", {"last_flash_evidence_sha256": "1" * 64}),
        ("VERIFYING_TARGET", {}),
    )
    for state, fields in transitions:
        store.transition(UPDATE_UID, state, fields=fields)


def test_update_identity_is_idempotent_and_recovery_responsibility_is_unique(
    tmp_path,
) -> None:
    store = _store(tmp_path)

    accepted = _create(store)
    duplicate = _create(store)

    assert accepted["disposition"] == "ACCEPTED"
    assert duplicate["disposition"] == "DUPLICATE"
    assert accepted["targetIdentitySha256"] != accepted["rollbackIdentitySha256"]

    with pytest.raises(McuUpdateStoreError) as reused:
        store.create_update(
            update_uid=UPDATE_UID,
            command_uid=COMMAND_UID,
            request_payload={**_request(), "targetPackageSha256": "9" * 64},
            target_package_sha256="9" * 64,
            target_manifest=_manifest(2, "2222222222222222", "c" * 64),
            rollback_package_sha256="b" * 64,
            rollback_manifest=_manifest(1, "1111111111111111", "d" * 64),
            handoff_uid=HANDOFF_UID,
        )
    assert reused.value.code == "MCU_UPDATE_IDEMPOTENCY_CONFLICT"

    with pytest.raises(McuUpdateStoreError) as busy:
        _create(
            store,
            update_uid=str(uuid.UUID("55555555-5555-4555-8555-555555555555")),
            command_uid=str(uuid.UUID("66666666-6666-4666-8666-666666666666")),
        )
    assert busy.value.code == "MCU_UPDATE_BUSY"


def test_privileged_action_authorization_is_exact_and_consumed_once(tmp_path) -> None:
    store = _store(tmp_path)
    _create(store)
    store.transition(
        UPDATE_UID,
        "DRAINING",
        fields={"maintenance_fence_token": 7},
    )
    payload = {"updateUid": UPDATE_UID, "actionUid": ACTION_UID}
    prepared = store.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=ACTION_UID,
        action_kind="STOP_BUSINESS_TARGET",
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

    granted = store.authorize_action(authorization)

    assert granted["authorized"] is True
    assert granted["maintenanceFenceToken"] == 7
    with pytest.raises(McuUpdateStoreError) as replay:
        store.authorize_action(authorization)
    assert replay.value.code == "PRIVILEGED_ACTION_ALREADY_AUTHORIZED"

    completed = store.finish_action(
        ACTION_UID,
        "SUCCEEDED",
        response={"serviceState": "INACTIVE"},
    )
    assert completed["state"] == "SUCCEEDED"
    assert completed["responseDigestSha256"] is not None


def test_helper_success_without_consumed_authorization_is_rejected(tmp_path) -> None:
    store = _store(tmp_path)
    _create(store)
    store.transition(
        UPDATE_UID,
        "DRAINING",
        fields={"maintenance_fence_token": 7},
    )
    payload = {"updateUid": UPDATE_UID, "actionUid": ACTION_UID}
    store.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=ACTION_UID,
        action_kind="STOP_BUSINESS_TARGET",
        payload=payload,
    )
    store.begin_action(ACTION_UID)

    with pytest.raises(McuUpdateStoreError) as raised:
        store.finish_action(
            ACTION_UID,
            "SUCCEEDED",
            response={"serviceState": "INACTIVE"},
        )

    assert raised.value.code == "MCU_ACTION_NOT_AUTHORIZED"


def test_restart_marks_unknown_helper_result_and_enters_observed_recovery(
    tmp_path,
) -> None:
    path = tmp_path / "mcu-updates.db"
    store = McuUpdateStore(path)
    store.initialize()
    _create(store)
    for state, fields in (
        ("DRAINING", {"maintenance_fence_token": 9}),
        ("INITIAL_OBSERVE", {}),
        ("INITIAL_QUIESCE", {"observation_evidence_sha256": "e" * 64}),
        ("STOPPING_TARGET", {"quiesce_evidence_sha256": "f" * 64}),
        ("FLASHING_TARGET", {}),
    ):
        store.transition(UPDATE_UID, state, fields=fields)
    payload = {
        "updateUid": UPDATE_UID,
        "actionUid": ACTION_UID,
        "source": "TARGET",
    }
    store.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=ACTION_UID,
        action_kind="FLASH_TARGET",
        payload=payload,
    )
    store.begin_action(ACTION_UID)
    store.close()

    reopened = McuUpdateStore(path)
    reopened.initialize()

    update = reopened.get_update(UPDATE_UID)
    assert update is not None
    assert update["state"] == "RECOVERING"
    assert update["lastErrorCode"] == "UPDATER_RESTART_RECOVERY"
    assert reopened.get_status()["unresolvedPrivilegedActionCount"] == 1

    assert reopened.reconcile_unknown_actions(UPDATE_UID, "8" * 64) == 1
    assert reopened.get_status()["unresolvedPrivilegedActionCount"] == 0


def test_only_verified_update_can_release_and_then_allows_next_update(tmp_path) -> None:
    store = _store(tmp_path)
    _create(store)
    _advance_to_verification(store)

    completed = store.complete_update(UPDATE_UID, "SUCCEEDED", "2" * 64)
    assert completed["state"] == "SUCCEEDED"
    assert store.get_pending_release() is not None

    store.prepare_release(UPDATE_UID, 7)
    released = store.finish_release(UPDATE_UID)
    assert released["releaseState"] == "RELEASED"
    assert store.get_pending_release() is None

    second = _create(
        store,
        update_uid="55555555-5555-4555-8555-555555555555",
        command_uid="66666666-6666-4666-8666-666666666666",
        handoff_uid="77777777-7777-4777-8777-777777777777",
    )
    assert second["disposition"] == "ACCEPTED"
