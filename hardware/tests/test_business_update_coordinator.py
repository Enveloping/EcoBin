from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from business_update_coordinator import BusinessUpdateCoordinator
from business_update_store import BUSINESS_UPDATE_TERMINAL_STATES, BusinessUpdateStore
from local_control import LocalControlUnavailable, canonical_local_payload_sha256
from updater_store import UpdaterStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT_UID = "22222222-2222-4222-8222-222222222222"
COMMAND_UID = "33333333-3333-4333-8333-333333333333"
TARGET_RELEASE = "44444444-4444-4444-8444-444444444444"
BASELINE_RELEASE = "55555555-5555-4555-8555-555555555555"


def _request() -> dict:
    return {
        "updateUid": UPDATE_UID,
        "deploymentUid": DEPLOYMENT_UID,
        "commandUid": COMMAND_UID,
        "releaseId": TARGET_RELEASE,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
        "packageSize": 1024,
        "signingKeyId": "business_2026",
    }


class FakePackageStager:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.cleaned: list[str] = []

    def stage(self, **request):
        self.calls.append(dict(request))
        return SimpleNamespace(
            manifest={"ECOBIN_RELEASE_ID": TARGET_RELEASE},
            staged_path=Path("/fixed/staging") / TARGET_RELEASE,
        )

    def cleanup(self, update_uid: str) -> None:
        self.cleaned.append(update_uid)


class FakeBusinessClient:
    def __init__(self) -> None:
        self.version = "1.0.0"
        self.target_health_calls = 0
        self.fail_during_observation = False

    def request(self, action: str, payload: dict) -> dict:
        assert action == "GET_STATUS"
        assert payload == {}
        if self.version == "1.1.0":
            self.target_health_calls += 1
            if self.fail_during_observation and self.target_health_calls >= 2:
                raise LocalControlUnavailable("target business runtime stopped")
        return {
            "component": "BUSINESS_RUNTIME",
            "status": "READY",
            "runtimeInstanceUid": "66666666-6666-4666-8666-666666666666",
            "releaseVersion": self.version,
            "managementArchitectureGeneration": "LOCAL_PROXY",
            "cloudConnectionOwner": "COMMUNICATION_AGENT",
            "jobPermitEnforced": True,
            "cloudProxyIngressEnabled": True,
            "businessDatabaseSize": 4096,
        }


class AuthorizingBusinessHelper:
    def __init__(
        self,
        business: FakeBusinessClient,
        *,
        bridge_baseline: bool = False,
    ) -> None:
        self.business = business
        self.coordinator: BusinessUpdateCoordinator | None = None
        self.bridge_version = "hardware-runtime-bridge"
        self.bridge_active = bridge_baseline
        self.current = None if bridge_baseline else BASELINE_RELEASE
        self.previous: str | None = None
        self.markers = {} if bridge_baseline else {
            BASELINE_RELEASE: {
                "schemaVersion": 2,
                "updateUid": "77777777-7777-4777-8777-777777777777",
                "releaseUid": BASELINE_RELEASE,
                "packageSha256": "b" * 64,
                "versionName": "1.0.0",
                "releaseSequence": 1,
            }
        }
        self.database_restored = False
        self.calls: list[tuple[str, dict]] = []
        self.unavailable_once_actions: set[str] = set()

    def request(self, action: str, payload: dict) -> dict:
        self.calls.append((action, dict(payload)))
        if action in self.unavailable_once_actions:
            self.unavailable_once_actions.remove(action)
            raise LocalControlUnavailable("simulated helper receipt loss")
        assert self.coordinator is not None
        self.coordinator.authorize_privileged_action(
            {
                "helperComponent": "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER",
                "helperAction": action,
                "updateUid": payload["updateUid"],
                "actionUid": payload["actionUid"],
                "payloadSha256": canonical_local_payload_sha256(payload),
            }
        )
        if action == "GET_BUSINESS_RUNTIME_STATUS":
            return {
                "businessRuntimeState": (
                    "ACTIVE" if self.current is not None else "INACTIVE"
                ),
                "bridgeBusinessRuntimeState": (
                    "ACTIVE" if self.bridge_active else "INACTIVE"
                ),
                "currentReleaseUid": self.current,
                "previousReleaseUid": self.previous,
                "currentRelease": (
                    dict(self.markers[self.current])
                    if self.current is not None
                    else None
                ),
                "installedReleaseCount": len(self.markers),
            }
        if action == "STOP_BUSINESS_RUNTIME":
            return {"businessRuntimeState": "INACTIVE"}
        if action == "STOP_BUSINESS_BRIDGE":
            self.bridge_active = False
            return {"businessRuntimeState": "INACTIVE"}
        if action == "SNAPSHOT_BUSINESS_DATABASE":
            return {"disposition": "CREATED", "snapshotSize": 4096}
        if action == "INSTALL_BUSINESS_RELEASE":
            release_uid = payload["releaseUid"]
            self.markers[release_uid] = {
                "schemaVersion": 2,
                "updateUid": payload["updateUid"],
                "releaseUid": release_uid,
                "packageSha256": payload["packageSha256"],
                "versionName": payload["versionName"],
                "releaseSequence": payload["releaseSequence"],
            }
            return {"disposition": "INSTALLED"}
        if action == "ACTIVATE_BUSINESS_RELEASE":
            old = self.current
            if old is not None:
                self.previous = old
            self.current = payload["releaseUid"]
            return {"previousReleaseUid": old, "disposition": "ACTIVATED"}
        if action == "RESTORE_BUSINESS_DATABASE":
            self.database_restored = True
            return {"disposition": "RESTORED"}
        if action == "ROLLBACK_BUSINESS_RELEASE":
            old = self.current
            self.current = payload["releaseUid"]
            self.previous = old
            return {"replacedReleaseUid": old, "disposition": "ROLLED_BACK"}
        if action == "DEACTIVATE_BUSINESS_RELEASE":
            assert self.current == payload["releaseUid"]
            self.current = None
            return {"disposition": "DEACTIVATED"}
        if action == "START_BUSINESS_RUNTIME":
            self.bridge_active = False
            self.business.version = self.markers[self.current]["versionName"]
            return {"businessRuntimeState": "ACTIVE"}
        if action == "START_BUSINESS_BRIDGE":
            assert self.current is None
            self.bridge_active = True
            self.business.version = self.bridge_version
            return {"businessRuntimeState": "ACTIVE"}
        raise AssertionError(f"unexpected helper action: {action}")


def _uid_factory():
    counter = 100

    def make() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{counter:012x}")

    return make


def _coordinator(tmp_path: Path, *, bridge_baseline: bool = False):
    clock = [datetime(2026, 9, 4, tzinfo=timezone.utc)]
    now = lambda: clock[0]
    path = tmp_path / "updater.db"
    safety = UpdaterStore(
        path,
        release_version="stage6-test",
        enable_stage4_candidate=True,
        utc_now=now,
    )
    safety.initialize()
    status = safety.get_status()
    safety.activate_stage4_job_gate(
        {
            "operationUid": "99999999-9999-4999-8999-999999999999",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status["managementStateSequence"],
        }
    )
    journal = BusinessUpdateStore(path, utc_now=now)
    journal.initialize()
    business = FakeBusinessClient()
    helper = AuthorizingBusinessHelper(
        business,
        bridge_baseline=bridge_baseline,
    )
    if bridge_baseline:
        business.version = helper.bridge_version
    stager = FakePackageStager()
    coordinator = BusinessUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=stager,
        business_client=business,
        helper_client=helper,
        uuid_factory=_uid_factory(),
        utc_now=now,
        poll_seconds=0.01,
        observation_seconds=1,
    )
    helper.coordinator = coordinator
    return coordinator, safety, journal, business, helper, stager, clock


def _advance_to_terminal(coordinator, journal, clock) -> dict:
    for _ in range(80):
        update = journal.get_update(UPDATE_UID)
        if update is not None and update["state"] == "OBSERVING":
            clock[0] += timedelta(seconds=2)
        coordinator.process_once()
        update = journal.get_update(UPDATE_UID)
        if update is not None and update["state"] in BUSINESS_UPDATE_TERMINAL_STATES:
            return update
    raise AssertionError("business update did not reach a terminal state")


def _advance_until(coordinator, journal, *, state: str, step: str) -> dict:
    for _ in range(80):
        update = journal.get_update(UPDATE_UID)
        if update is not None and (update["state"], update["step"]) == (
            state,
            step,
        ):
            return update
        coordinator.process_once()
    raise AssertionError(f"business update did not reach {state}/{step}")


def test_signed_local_candidate_switches_and_observes_before_success(tmp_path) -> None:
    coordinator, safety, journal, _business, helper, stager, clock = _coordinator(
        tmp_path
    )

    accepted = coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert accepted["disposition"] == "ACCEPTED"
    assert completed["state"] == "SUCCEEDED"
    assert completed["installedReleaseId"] == TARGET_RELEASE
    assert completed["businessAdmission"] == "ACCEPTING"
    assert helper.current == TARGET_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert stager.calls[0]["business_database_size"] == 4096
    assert stager.cleaned == [UPDATE_UID]


def test_observation_failure_restores_database_and_previous_release(tmp_path) -> None:
    coordinator, safety, journal, business, helper, _stager, clock = _coordinator(
        tmp_path
    )
    business.fail_during_observation = True

    coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "ROLLED_BACK"
    assert completed["databaseRestored"] is True
    assert helper.database_restored is True
    assert helper.current == BASELINE_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_rollback_keeps_root_failure_when_helper_receipt_is_temporarily_unknown(
    tmp_path: Path,
) -> None:
    coordinator, _safety, journal, business, helper, _stager, _clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="OBSERVING",
        step="OBSERVE_TARGET",
    )
    business.fail_during_observation = True
    business.target_health_calls = 1
    coordinator.process_once()
    _advance_until(
        coordinator,
        journal,
        state="ROLLING_BACK",
        step="STOP_TARGET",
    )
    before_retry = journal.get_update(UPDATE_UID)
    assert before_retry is not None
    assert before_retry["errorCode"] == "BUSINESS_RUNTIME_NOT_READY"
    helper.unavailable_once_actions.add("STOP_BUSINESS_RUNTIME")

    coordinator.process_once()

    after_retry = journal.get_update(UPDATE_UID)
    assert after_retry is not None
    assert after_retry["errorCode"] == "BUSINESS_RUNTIME_NOT_READY"


def test_first_signed_package_replaces_image_bridge_and_becomes_baseline(
    tmp_path,
) -> None:
    coordinator, safety, journal, _business, helper, _stager, clock = _coordinator(
        tmp_path,
        bridge_baseline=True,
    )

    coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "SUCCEEDED"
    assert completed["baselineKind"] == "IMAGE_BRIDGE"
    assert completed["previousReleaseId"] is None
    assert completed["previousVersionName"] == helper.bridge_version
    assert helper.current == TARGET_RELEASE
    assert helper.bridge_active is False
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_failed_first_package_restores_database_and_image_bridge(tmp_path) -> None:
    coordinator, safety, journal, business, helper, _stager, clock = _coordinator(
        tmp_path,
        bridge_baseline=True,
    )
    business.fail_during_observation = True

    coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "ROLLED_BACK"
    assert completed["baselineKind"] == "IMAGE_BRIDGE"
    assert completed["databaseRestored"] is True
    assert helper.current is None
    assert helper.bridge_active is True
    assert business.version == helper.bridge_version
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_image_bridge_accepts_valid_inactive_release_from_prior_attempt(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, helper, _stager, clock = _coordinator(
        tmp_path,
        bridge_baseline=True,
    )
    helper.markers[BASELINE_RELEASE] = {
        "schemaVersion": 2,
        "updateUid": "77777777-7777-4777-8777-777777777777",
        "releaseUid": BASELINE_RELEASE,
        "packageSha256": "b" * 64,
        "versionName": "1.0.0",
        "releaseSequence": 1,
    }

    coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "SUCCEEDED"
    assert completed["baselineKind"] == "IMAGE_BRIDGE"
    assert helper.current == TARGET_RELEASE
    assert helper.bridge_active is False
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_non_newer_release_is_rejected_before_stopping_baseline(tmp_path) -> None:
    coordinator, safety, journal, _business, _helper, _stager, clock = _coordinator(
        tmp_path
    )
    request = _request()
    request["releaseSequence"] = 1

    coordinator.queue_local(request)
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "REJECTED"
    assert completed["errorCode"] == "BUSINESS_RELEASE_SEQUENCE_NOT_NEWER"
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_restart_between_maintenance_lock_and_journal_transition_resumes(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, _helper, _stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    waiting = _advance_until(
        coordinator,
        journal,
        state="WAITING_FOR_IDLE",
        step="WAIT_FOR_IDLE",
    )
    safety.transition_job_gate("MAINTENANCE")
    safety.close()

    reopened = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage6-test",
        enable_stage4_candidate=True,
        utc_now=lambda: clock[0],
    )
    reopened.initialize()
    assert reopened.get_status()["jobGateState"] == "LOCKED"
    coordinator.safety_store = reopened

    assert coordinator.process_once() is True
    resumed = journal.get_update(waiting["updateUid"])
    assert resumed is not None
    assert (resumed["state"], resumed["step"]) == (
        "MIGRATING_DATA",
        "INSPECT_BASELINE",
    )
    assert reopened.get_status()["jobGateState"] == "MAINTENANCE"


def test_health_failure_after_updater_restart_can_still_roll_back(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, business, helper, _stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="VERIFYING_TARGET",
        step="VERIFY_TARGET",
    )
    safety.close()
    reopened = UpdaterStore(
        tmp_path / "updater.db",
        release_version="stage6-test",
        enable_stage4_candidate=True,
        utc_now=lambda: clock[0],
    )
    reopened.initialize()
    coordinator.safety_store = reopened
    business.fail_during_observation = True
    business.target_health_calls = 1

    coordinator.process_once()
    rolling_back = journal.get_update(UPDATE_UID)
    assert rolling_back is not None
    assert (rolling_back["state"], rolling_back["step"]) == (
        "ROLLING_BACK",
        "STOP_TARGET",
    )
    completed = _advance_to_terminal(coordinator, journal, clock)
    assert completed["state"] == "ROLLED_BACK"
    assert helper.current == BASELINE_RELEASE
    assert reopened.get_status()["jobGateState"] == "OPEN"
