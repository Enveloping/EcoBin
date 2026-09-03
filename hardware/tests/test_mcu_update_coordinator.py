from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control import (
    LocalControlRemoteError,
    LocalControlUnavailable,
    canonical_local_payload_sha256,
)
from mcu_update_coordinator import McuUpdateCoordinator, McuUpdateCoordinatorError
from mcu_update_store import McuUpdateStore
from updater_store import UpdaterStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
COMMAND_UID = "22222222-2222-4222-8222-222222222222"
TARGET_PACKAGE_SHA256 = "a" * 64
ROLLBACK_PACKAGE_SHA256 = "b" * 64
TARGET_IMAGE_SHA256 = "c" * 64
ROLLBACK_IMAGE_SHA256 = "d" * 64


def _identity(version_code: int, identity_hex: str) -> dict:
    return {
        "protocolRevision": 2,
        "firmwareVersionCode": version_code,
        "firmwareVersion": f"1.0.{version_code}",
        "firmwareIdentityHex": identity_hex,
    }


TARGET_IDENTITY = _identity(2, "2222222222222222")
ROLLBACK_IDENTITY = _identity(1, "1111111111111111")


def _manifest(identity: dict, image_sha256: str) -> dict:
    return {
        "schemaVersion": 1,
        "fixedFrameRevision": identity["protocolRevision"],
        "firmwareVersionCode": identity["firmwareVersionCode"],
        "firmwareVersion": identity["firmwareVersion"],
        "firmwareIdentityHex": identity["firmwareIdentityHex"],
        "imageSha256": image_sha256,
    }


class FakePackageStager:
    def __init__(self) -> None:
        self.calls = 0

    def stage_pair(self, **request):
        self.calls += 1
        assert request == {
            "update_uid": UPDATE_UID,
            "expected_target_package_sha256": TARGET_PACKAGE_SHA256,
            "expected_rollback_package_sha256": ROLLBACK_PACKAGE_SHA256,
        }
        return SimpleNamespace(
            target_manifest=_manifest(TARGET_IDENTITY, TARGET_IMAGE_SHA256),
            rollback_manifest=_manifest(
                ROLLBACK_IDENTITY,
                ROLLBACK_IMAGE_SHA256,
            ),
            target_package_sha256=TARGET_PACKAGE_SHA256,
            rollback_package_sha256=ROLLBACK_PACKAGE_SHA256,
        )


class FakeBusinessClient:
    def __init__(self, *, reject_target_verification: bool = False) -> None:
        self.identity = dict(ROLLBACK_IDENTITY)
        self.reject_target_verification = reject_target_verification
        self.calls: list[tuple[str, dict]] = []

    def request(self, action: str, payload: dict) -> dict:
        self.calls.append((action, dict(payload)))
        if action == "OBSERVE_MCU_MAINTENANCE_STATE":
            return self._observation()
        if action in {"QUIESCE_MCU_FOR_UPDATE", "QUIESCE_MCU_FOR_RECOVERY"}:
            return {
                "evidenceStage": "QUIESCE",
                "evidenceSha256": canonical_local_payload_sha256(
                    {"action": action, **payload}
                ),
            }
        if action == "VERIFY_MCU_AFTER_UPDATE":
            assert payload["quiesceEvidenceSha256"]
            expected = payload["expectedFirmwareIdentitySha256"]
            target_digest = canonical_local_payload_sha256(TARGET_IDENTITY)
            if self.reject_target_verification and expected == target_digest:
                raise LocalControlRemoteError(
                    "MCU_VERIFICATION_UNCONFIRMED",
                    "target self-test failed",
                    "request-id",
                )
            assert expected == canonical_local_payload_sha256(self.identity)
            return {
                "evidenceStage": "VERIFY",
                "evidenceSha256": canonical_local_payload_sha256(payload),
            }
        raise AssertionError(f"unexpected business action: {action}")

    def _observation(self) -> dict:
        return {
            "evidenceStage": "OBSERVE",
            "evidenceSha256": canonical_local_payload_sha256(
                {"identity": self.identity, "healthy": True}
            ),
            "f3FirmwareIdentity": {
                "queryStatus": "OK",
                "mode": 1,
                "statusCode": 0,
                "safeFlags": 0x0F,
                "firmwareIdentity": dict(self.identity),
            },
            "f1SelfTest": {
                "queryStatus": "OK",
                "communicationHealthy": True,
                "validFlags": 3,
                "weightGrams": 100,
                "infraredBlocked": False,
                "smokeState": "NORMAL",
                "smokeSensorHealth": "OK",
            },
            "uartHandedOff": False,
        }


class AuthorizingHelperClient:
    def __init__(
        self,
        *,
        component: str,
        business: FakeBusinessClient,
        lose_first_flash_response: bool = False,
    ) -> None:
        self.component = component
        self.business = business
        self.lose_first_flash_response = lose_first_flash_response
        self.coordinator: McuUpdateCoordinator | None = None
        self.calls: list[tuple[str, dict]] = []
        self.flash_calls = 0

    def request(self, action: str, payload: dict) -> dict:
        self.calls.append((action, dict(payload)))
        assert self.coordinator is not None
        self.coordinator.authorize_privileged_action(
            {
                "helperComponent": self.component,
                "helperAction": action,
                "updateUid": payload["updateUid"],
                "actionUid": payload["actionUid"],
                "payloadSha256": canonical_local_payload_sha256(payload),
            }
        )
        if action == "FLASH_MCU_FIRMWARE":
            self.flash_calls += 1
            self.business.identity = dict(
                TARGET_IDENTITY
                if payload["source"] == "TARGET"
                else ROLLBACK_IDENTITY
            )
            if self.lose_first_flash_response and self.flash_calls == 1:
                raise LocalControlUnavailable("response lost after dispatch")
        return {"action": action, "completed": True}


def _uid_factory():
    next_number = 100

    def make() -> uuid.UUID:
        nonlocal next_number
        next_number += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{next_number:012x}")

    return make


def _activated_safety_store(path: Path) -> UpdaterStore:
    store = UpdaterStore(
        path,
        release_version="stage4-test",
        enable_stage4_candidate=True,
    )
    store.initialize()
    status = store.get_status()
    store.activate_stage4_job_gate(
        {
            "operationUid": "00000000-0000-4000-8000-000000000001",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )
    return store


def _coordinator(
    tmp_path: Path,
    *,
    reject_target_verification: bool = False,
    lose_first_flash_response: bool = False,
    utc_now=None,
):
    safety = _activated_safety_store(tmp_path / "updater.db")
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    business = FakeBusinessClient(
        reject_target_verification=reject_target_verification
    )
    business_helper = AuthorizingHelperClient(
        component="BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        business=business,
    )
    mcu_helper = AuthorizingHelperClient(
        component="MCU_FLASH_CANDIDATE_HELPER",
        business=business,
        lose_first_flash_response=lose_first_flash_response,
    )
    package_stager = FakePackageStager()
    coordinator = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=package_stager,
        business_client=business,
        business_helper_client=business_helper,
        mcu_helper_client=mcu_helper,
        uuid_factory=_uid_factory(),
        utc_now=utc_now,
    )
    business_helper.coordinator = coordinator
    mcu_helper.coordinator = coordinator
    return (
        coordinator,
        safety,
        journal,
        business,
        business_helper,
        mcu_helper,
        package_stager,
    )


def _queue(coordinator: McuUpdateCoordinator) -> None:
    coordinator.queue_local(
        {
            "updateUid": UPDATE_UID,
            "commandUid": COMMAND_UID,
            "targetPackageSha256": TARGET_PACKAGE_SHA256,
            "rollbackPackageSha256": ROLLBACK_PACKAGE_SHA256,
        }
    )


def test_default_helper_client_timeouts_outlive_fixed_systemd_helpers(
    tmp_path,
) -> None:
    safety = _activated_safety_store(tmp_path / "updater.db")
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    coordinator = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=FakePackageStager(),
        business_client=FakeBusinessClient(),
    )
    try:
        assert (
            coordinator.business_helper_client.response_timeout_seconds
            == 220.0
        )
        assert coordinator.mcu_helper_client.response_timeout_seconds == 370.0
    finally:
        journal.close()
        safety.close()


def _run_to_release(
    coordinator: McuUpdateCoordinator,
    journal: McuUpdateStore,
    *,
    limit: int = 100,
) -> dict:
    for _ in range(limit):
        coordinator.process_once()
        update = journal.get_update(UPDATE_UID)
        assert update is not None
        if update["releaseState"] == "RELEASED":
            return update
    raise AssertionError("MCU update did not reach a released terminal state")


def test_target_update_drains_quiesces_flashes_verifies_and_reopens_gate(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        business_helper,
        mcu_helper,
        _package_stager,
    ) = (
        _coordinator(tmp_path)
    )
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "SUCCEEDED"
    assert update["targetAttemptCount"] == 1
    assert update["rollbackAttemptCount"] == 0
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert mcu_helper.flash_calls == 1
    assert [action for action, _ in business_helper.calls] == [
        "STOP_BUSINESS_RUNTIME",
        "START_BUSINESS_RUNTIME",
    ]
    verification_payload = next(
        payload
        for action, payload in business.calls
        if action == "VERIFY_MCU_AFTER_UPDATE"
    )
    assert verification_payload["quiesceEvidenceSha256"] == update[
        "quiesceEvidenceSha256"
    ]


def test_three_failed_target_verifications_end_in_verified_rollback(tmp_path) -> None:
    (
        coordinator,
        safety,
        journal,
        _business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = (
        _coordinator(tmp_path, reject_target_verification=True)
    )
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "ROLLED_BACK"
    assert update["targetAttemptCount"] == 3
    assert update["rollbackAttemptCount"] == 1
    assert mcu_helper.flash_calls == 4
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_lost_flash_response_is_observed_without_blind_reflash(tmp_path) -> None:
    (
        coordinator,
        safety,
        journal,
        _business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = (
        _coordinator(tmp_path, lose_first_flash_response=True)
    )
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "SUCCEEDED"
    assert mcu_helper.flash_calls == 1
    assert journal.get_status()["unresolvedPrivilegedActionCount"] == 0
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_unknown_quiesce_result_recovers_actual_firmware_without_flash(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    original_request = business.request
    lost = False

    def request(action: str, payload: dict) -> dict:
        nonlocal lost
        if action == "QUIESCE_MCU_FOR_UPDATE" and not lost:
            lost = True
            raise LocalControlRemoteError(
                "RESULT_UNKNOWN",
                "quiesce response timed out",
                "request-id",
            )
        return original_request(action, payload)

    business.request = request  # type: ignore[method-assign]
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "ROLLED_BACK"
    assert mcu_helper.flash_calls == 0
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_unknown_business_stop_result_enters_observed_recovery(tmp_path) -> None:
    (
        coordinator,
        safety,
        journal,
        _business,
        business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    original_request = business_helper.request
    lost = False

    def request(action: str, payload: dict) -> dict:
        nonlocal lost
        result = original_request(action, payload)
        if action == "STOP_BUSINESS_RUNTIME" and not lost:
            lost = True
            raise LocalControlUnavailable("business stop response was lost")
        return result

    business_helper.request = request  # type: ignore[method-assign]
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "ROLLED_BACK"
    assert mcu_helper.flash_calls == 0
    assert journal.get_status()["unresolvedPrivilegedActionCount"] == 0
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_unknown_verification_result_retries_read_without_reflashing(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    original_request = business.request
    lost = False

    def request(action: str, payload: dict) -> dict:
        nonlocal lost
        if action == "VERIFY_MCU_AFTER_UPDATE" and not lost:
            lost = True
            raise LocalControlUnavailable("verification response was lost")
        return original_request(action, payload)

    business.request = request  # type: ignore[method-assign]
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "SUCCEEDED"
    assert update["targetAttemptCount"] == 1
    assert mcu_helper.flash_calls == 1
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_retry_refuses_an_identity_outside_the_signed_update_pair(tmp_path) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path, reject_target_verification=True)
    _queue(coordinator)
    for _ in range(30):
        coordinator.process_once()
        update = journal.get_update(UPDATE_UID)
        assert update is not None
        if update["state"] == "PREPARING_TARGET_RETRY":
            break
    else:
        raise AssertionError("target verification did not reach retry preparation")

    business.identity = _identity(9, "9999999999999999")
    coordinator.process_once()

    failed = journal.get_update(UPDATE_UID)
    assert failed is not None
    assert failed["state"] == "FAILED_LOCKED"
    assert failed["lastErrorCode"] == "MCU_RECOVERY_IDENTITY_UNEXPECTED"
    assert mcu_helper.flash_calls == 1
    assert safety.get_status()["jobGateState"] == "LOCKED"


def test_restart_recovery_never_reflashes_an_unexpected_identity(tmp_path) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        _business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path, lose_first_flash_response=True)
    _queue(coordinator)
    for _ in range(30):
        coordinator.process_once()
        update = journal.get_update(UPDATE_UID)
        assert update is not None
        if update["state"] == "RECOVERING":
            break
    else:
        raise AssertionError("lost flash response did not enter recovery")

    business.identity = _identity(9, "9999999999999999")
    coordinator.process_once()

    failed = journal.get_update(UPDATE_UID)
    assert failed is not None
    assert failed["state"] == "FAILED_LOCKED"
    assert failed["lastErrorCode"] == "MCU_RECOVERY_IDENTITY_UNEXPECTED"
    assert mcu_helper.flash_calls == 1
    assert journal.get_status()["unresolvedPrivilegedActionCount"] == 0
    assert safety.get_status()["jobGateState"] == "LOCKED"


def test_duplicate_queue_does_not_need_incoming_packages_again(tmp_path) -> None:
    (
        coordinator,
        _safety,
        _journal,
        _business,
        _business_helper,
        _mcu_helper,
        package_stager,
    ) = _coordinator(tmp_path)

    first = coordinator.queue_local(
        {
            "updateUid": UPDATE_UID,
            "commandUid": COMMAND_UID,
            "targetPackageSha256": TARGET_PACKAGE_SHA256,
            "rollbackPackageSha256": ROLLBACK_PACKAGE_SHA256,
        }
    )
    duplicate = coordinator.queue_local(
        {
            "updateUid": UPDATE_UID,
            "commandUid": COMMAND_UID,
            "targetPackageSha256": TARGET_PACKAGE_SHA256,
            "rollbackPackageSha256": ROLLBACK_PACKAGE_SHA256,
        }
    )

    assert first["disposition"] == "ACCEPTED"
    assert duplicate["disposition"] == "DUPLICATE"
    assert package_stager.calls == 1


def test_concurrent_queue_requests_stage_only_the_single_durable_owner(
    tmp_path,
) -> None:
    (
        coordinator,
        _safety,
        journal,
        _business,
        _business_helper,
        _mcu_helper,
        package_stager,
    ) = _coordinator(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original_stage = package_stager.stage_pair

    def blocking_stage(**request):
        entered.set()
        assert release.wait(timeout=2.0)
        return original_stage(**request)

    package_stager.stage_pair = blocking_stage
    second_payload = {
        "updateUid": "44444444-4444-4444-8444-444444444444",
        "commandUid": "55555555-5555-4555-8555-555555555555",
        "targetPackageSha256": "1" * 64,
        "rollbackPackageSha256": "2" * 64,
    }
    outcomes: list[object] = []

    def invoke(payload: dict) -> None:
        try:
            outcomes.append(coordinator.queue_local(payload))
        except BaseException as error:  # noqa: BLE001 - assert exact result below
            outcomes.append(error)

    first = threading.Thread(
        target=invoke,
        args=(
            {
                "updateUid": UPDATE_UID,
                "commandUid": COMMAND_UID,
                "targetPackageSha256": TARGET_PACKAGE_SHA256,
                "rollbackPackageSha256": ROLLBACK_PACKAGE_SHA256,
            },
        ),
    )
    second = threading.Thread(target=invoke, args=(second_payload,))
    first.start()
    assert entered.wait(timeout=1.0)
    second.start()
    release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert package_stager.calls == 1
    assert journal.get_status()["activeUpdate"]["updateUid"] == UPDATE_UID
    assert any(
        isinstance(outcome, dict) and outcome.get("disposition") == "ACCEPTED"
        for outcome in outcomes
    )
    busy = next(
        outcome
        for outcome in outcomes
        if isinstance(outcome, McuUpdateCoordinatorError)
    )
    assert busy.code == "MCU_UPDATE_BUSY"


def test_process_restart_observes_actual_target_and_never_repeats_flash(
    tmp_path,
) -> None:
    (
        first,
        first_safety,
        first_journal,
        business,
        _business_helper,
        first_mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path, lose_first_flash_response=True)
    _queue(first)
    for _ in range(30):
        first.process_once()
        interrupted = first_journal.get_update(UPDATE_UID)
        assert interrupted is not None
        if interrupted["state"] == "RECOVERING":
            break
    else:
        raise AssertionError("lost flash response did not enter recovery")
    assert first_mcu_helper.flash_calls == 1
    first_journal.close()
    first_safety.close()

    safety = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-restarted",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    assert safety.get_status()["jobGateState"] == "LOCKED"
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    business_helper = AuthorizingHelperClient(
        component="BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        business=business,
    )
    mcu_helper = AuthorizingHelperClient(
        component="MCU_FLASH_CANDIDATE_HELPER",
        business=business,
    )
    restarted = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=FakePackageStager(),
        business_client=business,
        business_helper_client=business_helper,
        mcu_helper_client=mcu_helper,
        uuid_factory=_uid_factory(),
    )
    business_helper.coordinator = restarted
    mcu_helper.coordinator = restarted

    update = _run_to_release(restarted, journal)

    assert update["state"] == "SUCCEEDED"
    assert first_mcu_helper.flash_calls == 1
    assert mcu_helper.flash_calls == 0
    assert journal.get_status()["unresolvedPrivilegedActionCount"] == 0
    assert safety.get_status()["jobGateState"] == "OPEN"


@pytest.mark.parametrize(
    ("actual_identity", "expected_outcome"),
    [
        (TARGET_IDENTITY, "SUCCEEDED"),
        (ROLLBACK_IDENTITY, "ROLLED_BACK"),
    ],
)
def test_restart_reconciles_dispatching_flash_from_actual_identity_only(
    tmp_path,
    actual_identity: dict,
    expected_outcome: str,
) -> None:
    (
        first,
        first_safety,
        first_journal,
        business,
        _business_helper,
        _mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    _queue(first)
    for _ in range(20):
        first.process_once()
        current = first_journal.get_update(UPDATE_UID)
        assert current is not None
        if current["state"] == "FLASHING_TARGET":
            break
    else:
        raise AssertionError("update did not reach target flash dispatch")

    first_journal.record_attempt(UPDATE_UID, "TARGET")
    action_uid = "33333333-3333-4333-8333-333333333333"
    action_payload = {
        "updateUid": UPDATE_UID,
        "actionUid": action_uid,
        "source": "TARGET",
    }
    first_journal.prepare_action(
        update_uid=UPDATE_UID,
        action_uid=action_uid,
        action_kind="FLASH_TARGET",
        payload=action_payload,
    )
    first_journal.begin_action(action_uid)
    business.identity = dict(actual_identity)
    first_journal.close()
    first_safety.close()

    safety = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-dispatch-restarted",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    assert journal.get_update(UPDATE_UID)["state"] == "RECOVERING"
    business_helper = AuthorizingHelperClient(
        component="BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        business=business,
    )
    mcu_helper = AuthorizingHelperClient(
        component="MCU_FLASH_CANDIDATE_HELPER",
        business=business,
    )
    restarted = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=FakePackageStager(),
        business_client=business,
        business_helper_client=business_helper,
        mcu_helper_client=mcu_helper,
        uuid_factory=_uid_factory(),
    )
    business_helper.coordinator = restarted
    mcu_helper.coordinator = restarted

    update = _run_to_release(restarted, journal)

    assert update["state"] == expected_outcome
    assert update["targetAttemptCount"] == 1
    assert mcu_helper.flash_calls == 0
    assert journal.get_status()["unresolvedPrivilegedActionCount"] == 0
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_process_restart_resumes_exact_pre_hardware_drain(tmp_path) -> None:
    (
        first,
        first_safety,
        first_journal,
        business,
        _business_helper,
        _mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    _queue(first)
    first.process_once()  # QUEUED -> DRAINING
    fence = first_journal.get_update(UPDATE_UID)["maintenanceFenceToken"]
    first_journal.close()
    first_safety.close()

    safety = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-drain-restarted",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    assert safety.get_status()["jobGateState"] == "LOCKED"
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    business_helper = AuthorizingHelperClient(
        component="BUSINESS_ACTIVATION_CANDIDATE_HELPER",
        business=business,
    )
    mcu_helper = AuthorizingHelperClient(
        component="MCU_FLASH_CANDIDATE_HELPER",
        business=business,
    )
    restarted = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=FakePackageStager(),
        business_client=business,
        business_helper_client=business_helper,
        mcu_helper_client=mcu_helper,
        uuid_factory=_uid_factory(),
    )
    business_helper.coordinator = restarted
    mcu_helper.coordinator = restarted

    assert restarted.process_once() is False  # LOCKED -> DRAINING; journal unchanged
    resumed = safety.get_status()
    assert resumed["jobGateState"] == "DRAINING"
    assert resumed["maintenanceFenceToken"] == fence
    update = _run_to_release(restarted, journal)

    assert update["state"] == "SUCCEEDED"
    assert mcu_helper.flash_calls == 1
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_restart_releases_prepared_pre_hardware_result_after_reconciliation(
    tmp_path,
) -> None:
    (
        first,
        first_safety,
        first_journal,
        _business,
        _business_helper,
        _mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    _queue(first)
    first.process_once()  # QUEUED -> DRAINING
    current = first_journal.get_update(UPDATE_UID)
    evidence = canonical_local_payload_sha256({"result": "deferred-before-hardware"})
    first_journal.finish_pre_hardware_update(
        UPDATE_UID,
        "DEFERRED",
        evidence,
        error_code="BUSINESS_DRAIN_TIMEOUT",
        error_message="drain expired",
    )
    first_journal.close()
    first_safety.close()

    safety = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage4-release-restarted",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = McuUpdateStore(tmp_path / "mcu-updates.db")
    journal.initialize()
    restarted = McuUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=FakePackageStager(),
        business_client=FakeBusinessClient(),
        business_helper_client=object(),
        mcu_helper_client=object(),
        uuid_factory=_uid_factory(),
    )

    assert current is not None
    assert safety.get_status()["maintenanceFenceToken"] == current[
        "maintenanceFenceToken"
    ]
    assert restarted.process_once() is True

    released = journal.get_update(UPDATE_UID)
    assert released is not None
    assert released["state"] == "DEFERRED"
    assert released["releaseState"] == "RELEASED"
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_drain_timeout_defers_before_hardware_and_reopens_new_business(
    tmp_path,
) -> None:
    far_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    (
        coordinator,
        safety,
        journal,
        business,
        business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path, utc_now=lambda: far_future)
    _queue(coordinator)

    coordinator.process_once()  # QUEUED -> DRAINING
    coordinator.process_once()  # durable DEFERRED release
    update = journal.get_update(UPDATE_UID)

    assert update is not None
    assert update["state"] == "DEFERRED"
    assert update["releaseState"] == "RELEASED"
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert business.calls == []
    assert business_helper.calls == []
    assert mcu_helper.calls == []


def test_queue_rejects_existing_safety_lock_before_staging_or_journaling(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        _business,
        _business_helper,
        _mcu_helper,
        package_stager,
    ) = _coordinator(tmp_path)
    safety.transition_job_gate("LOCKED", block_reason_code="TEST_SAFETY_LOCK")

    with pytest.raises(McuUpdateCoordinatorError) as raised:
        _queue(coordinator)

    assert raised.value.code == "MCU_UPDATE_BUSY"
    assert package_stager.calls == 0
    assert journal.get_status()["activeUpdate"] is None


def test_installed_mcu_must_match_signed_rollback_before_any_hardware_action(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    business.identity = _identity(9, "9999999999999999")
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "REJECTED"
    assert update["lastErrorCode"] == "MCU_ROLLBACK_IDENTITY_MISMATCH"
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert business_helper.calls == []
    assert mcu_helper.calls == []
    assert all(
        action not in {"QUIESCE_MCU_FOR_UPDATE", "QUIESCE_MCU_FOR_RECOVERY"}
        for action, _payload in business.calls
    )


def test_invalid_initial_observation_rejects_before_hardware_and_reopens_gate(
    tmp_path,
) -> None:
    (
        coordinator,
        safety,
        journal,
        business,
        business_helper,
        mcu_helper,
        _package_stager,
    ) = _coordinator(tmp_path)
    original_request = business.request

    def request(action: str, payload: dict) -> dict:
        if action == "OBSERVE_MCU_MAINTENANCE_STATE":
            return {
                "evidenceStage": "OBSERVE",
                "evidenceSha256": "not-a-digest",
            }
        return original_request(action, payload)

    business.request = request  # type: ignore[method-assign]
    _queue(coordinator)

    update = _run_to_release(coordinator, journal)

    assert update["state"] == "REJECTED"
    assert update["lastErrorCode"] == "MCU_MAINTENANCE_EVIDENCE_INVALID"
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert business_helper.calls == []
    assert mcu_helper.calls == []
