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
CANCEL_COMMAND_UID = "88888888-8888-4888-8888-888888888888"


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


def _remote_command() -> dict:
    return {
        "commandUid": COMMAND_UID,
        "payloadSha256": "b" * 64,
        "payload": {
            "updateUid": UPDATE_UID,
            "deploymentUid": DEPLOYMENT_UID,
            "releaseUid": TARGET_RELEASE,
            "versionName": "1.1.0",
            "releaseSequence": 2,
            "packageSha256": "a" * 64,
            "packageSize": 1024,
            "signingKeyId": "business_2026",
            "controlSequence": 1,
            "objectKey": (
                f"edge-runtime/releases/{TARGET_RELEASE}/package.tar.gz"
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
        },
        "downloadGrant": {
            "authorizationSequence": 1,
            "url": "https://private.example.invalid/package.tar.gz",
            "expiresAt": "2026-09-04T01:00:00Z",
        },
    }


def _cancel_command() -> dict:
    return {
        "commandUid": CANCEL_COMMAND_UID,
        "payload": {
            "updateUid": UPDATE_UID,
            "deploymentUid": DEPLOYMENT_UID,
            "controlSequence": 2,
            "reason": "platform administrator cancelled validation",
        },
    }


class FakePackageStager:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.cleaned: list[str] = []
        self.cleanup_failures_remaining = 0

    def stage(self, **request):
        self.calls.append(dict(request))
        return SimpleNamespace(
            manifest={"ECOBIN_RELEASE_ID": TARGET_RELEASE},
            staged_path=Path("/fixed/staging") / TARGET_RELEASE,
        )

    def cleanup(self, update_uid: str) -> None:
        if self.cleanup_failures_remaining > 0:
            self.cleanup_failures_remaining -= 1
            raise OSError("simulated staging cleanup failure")
        self.cleaned.append(update_uid)


class FakeRemoteDownloader:
    def __init__(self) -> None:
        self.authorizations: list[dict] = []
        self.cancelled: list[str] = []
        self.cleanup_ready = True
        self.terminal_cleanup_ready = True
        self.terminal_cleaned: list[str] = []

    def accept_authorization(self, **authorization) -> None:
        self.authorizations.append(dict(authorization))

    def cancel(self, update_uid: str) -> None:
        self.cancelled.append(update_uid)

    def cleanup_cancelled(self, update_uid: str) -> bool:
        assert update_uid == UPDATE_UID
        return self.cleanup_ready

    def cleanup_terminal(self, update_uid: str) -> bool:
        assert update_uid == UPDATE_UID
        if not self.terminal_cleanup_ready:
            return False
        self.terminal_cleaned.append(update_uid)
        return True


class FakeBusinessClient:
    def __init__(self) -> None:
        self.version = "1.0.0"
        self.target_health_calls = 0
        self.fail_during_observation = False
        self.unavailable_health_calls = 0

    def request(self, action: str, payload: dict) -> dict:
        assert action == "GET_STATUS"
        assert payload == {}
        if self.unavailable_health_calls > 0:
            self.unavailable_health_calls -= 1
            raise LocalControlUnavailable("business runtime is still starting")
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


def _uid_factory(start: int = 100):
    counter = start

    def make() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{counter:012x}")

    return make


def _coordinator(
    tmp_path: Path,
    *,
    bridge_baseline: bool = False,
    remote: bool = False,
):
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
    downloader = FakeRemoteDownloader() if remote else None
    coordinator = BusinessUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=stager,
        remote_downloader=downloader,
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
    assert completed["installedVersionName"] == "1.1.0"
    assert completed["installedReleaseSequence"] == 2
    assert completed["installedPackageSha256"] == "a" * 64
    assert completed["businessAdmission"] == "ACCEPTING"
    assert helper.current == TARGET_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert stager.calls[0]["business_database_size"] == 4096
    assert stager.cleaned == [UPDATE_UID]


def test_successful_remote_update_cleans_download_and_staging(tmp_path) -> None:
    coordinator, safety, journal, _business, helper, stager, clock = _coordinator(
        tmp_path,
        remote=True,
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)

    command = _remote_command()
    command["payload"]["observationWindowSeconds"] = 60
    coordinator.queue_remote(command)
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "SUCCEEDED"
    assert helper.current == TARGET_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert stager.cleaned == [UPDATE_UID]
    assert downloader.terminal_cleaned == [UPDATE_UID]


def test_observation_failure_restores_database_and_previous_release(tmp_path) -> None:
    coordinator, safety, journal, business, helper, _stager, clock = _coordinator(
        tmp_path
    )
    business.fail_during_observation = True

    coordinator.queue_local(_request())
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "ROLLED_BACK"
    assert completed["databaseRestored"] is True
    assert completed["installedReleaseId"] == BASELINE_RELEASE
    assert completed["installedVersionName"] == "1.0.0"
    assert completed["installedReleaseSequence"] == 1
    assert completed["installedPackageSha256"] == "b" * 64
    assert helper.database_restored is True
    assert helper.current == BASELINE_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"


def test_remote_rollback_cleans_download_and_staging(tmp_path) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path,
        remote=True,
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)
    business.fail_during_observation = True

    command = _remote_command()
    command["payload"]["observationWindowSeconds"] = 60
    coordinator.queue_remote(command)
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    completed = _advance_to_terminal(coordinator, journal, clock)

    assert completed["state"] == "ROLLED_BACK"
    assert helper.current == BASELINE_RELEASE
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert stager.cleaned == [UPDATE_UID]
    assert downloader.terminal_cleaned == [UPDATE_UID]


def test_terminal_artifact_cleanup_is_retried_after_coordinator_restart(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path,
        remote=True,
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)
    downloader.terminal_cleanup_ready = False
    stager.cleanup_failures_remaining = 1

    command = _remote_command()
    command["payload"]["observationWindowSeconds"] = 60
    coordinator.queue_remote(command)
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    completed = _advance_to_terminal(coordinator, journal, clock)
    assert completed["state"] == "SUCCEEDED"
    assert downloader.terminal_cleaned == []
    assert stager.cleaned == []

    downloader.terminal_cleanup_ready = True
    restarted = BusinessUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=stager,
        remote_downloader=downloader,
        business_client=business,
        helper_client=helper,
        uuid_factory=_uid_factory(500),
        utc_now=lambda: clock[0],
        poll_seconds=0.01,
        observation_seconds=1,
    )
    helper.coordinator = restarted

    assert restarted.process_once() is True
    assert journal.get_update(UPDATE_UID)["state"] == "SUCCEEDED"
    assert downloader.terminal_cleaned == [UPDATE_UID]
    assert stager.cleaned == [UPDATE_UID]


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
    assert completed["installedReleaseId"] is None
    assert completed["installedVersionName"] is None
    assert completed["installedReleaseSequence"] is None
    assert completed["installedPackageSha256"] is None
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


def _restart_business_coordinator(
    *,
    safety,
    journal,
    business,
    helper,
    stager,
    clock,
    monotonic=None,
    restart_business_health_grace_seconds: int = 210,
) -> BusinessUpdateCoordinator:
    options = {}
    if monotonic is not None:
        options["monotonic"] = monotonic
    restarted = BusinessUpdateCoordinator(
        safety_store=safety,
        journal=journal,
        package_stager=stager,
        business_client=business,
        helper_client=helper,
        uuid_factory=_uid_factory(1000),
        utc_now=lambda: clock[0],
        poll_seconds=0.01,
        observation_seconds=1,
        restart_business_health_grace_seconds=(
            restart_business_health_grace_seconds
        ),
        **options,
    )
    helper.coordinator = restarted
    return restarted


def test_restart_waits_for_target_health_before_initial_verification(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="VERIFYING_TARGET",
        step="VERIFY_TARGET",
    )
    business.unavailable_health_calls = 1
    restarted = _restart_business_coordinator(
        safety=safety,
        journal=journal,
        business=business,
        helper=helper,
        stager=stager,
        clock=clock,
    )

    restarted.process_once()
    waiting = journal.get_update(UPDATE_UID)
    assert waiting is not None
    assert (waiting["state"], waiting["step"]) == (
        "VERIFYING_TARGET",
        "VERIFY_TARGET",
    )

    restarted.process_once()
    verified = journal.get_update(UPDATE_UID)
    assert verified is not None
    assert (verified["state"], verified["step"]) == (
        "VERIFYING_TARGET",
        "RELEASE_TARGET",
    )


def test_restart_waits_for_target_health_during_observation(tmp_path: Path) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="OBSERVING",
        step="OBSERVE_TARGET",
    )
    business.unavailable_health_calls = 1
    restarted = _restart_business_coordinator(
        safety=safety,
        journal=journal,
        business=business,
        helper=helper,
        stager=stager,
        clock=clock,
    )

    restarted.process_once()
    waiting = journal.get_update(UPDATE_UID)
    assert waiting is not None
    assert (waiting["state"], waiting["step"]) == (
        "OBSERVING",
        "OBSERVE_TARGET",
    )

    restarted.process_once()
    healthy = journal.get_update(UPDATE_UID)
    assert healthy is not None
    assert (healthy["state"], healthy["step"]) == (
        "OBSERVING",
        "OBSERVE_TARGET",
    )


def test_restart_health_grace_expires_for_persistently_unavailable_target(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="VERIFYING_TARGET",
        step="VERIFY_TARGET",
    )
    monotonic_clock = [0.0]
    business.unavailable_health_calls = 2
    restarted = _restart_business_coordinator(
        safety=safety,
        journal=journal,
        business=business,
        helper=helper,
        stager=stager,
        clock=clock,
        monotonic=lambda: monotonic_clock[0],
        restart_business_health_grace_seconds=210,
    )

    restarted.process_once()
    waiting = journal.get_update(UPDATE_UID)
    assert waiting is not None
    assert (waiting["state"], waiting["step"]) == (
        "VERIFYING_TARGET",
        "VERIFY_TARGET",
    )

    monotonic_clock[0] = 210.0
    restarted.process_once()
    rolling_back = journal.get_update(UPDATE_UID)
    assert rolling_back is not None
    assert (rolling_back["state"], rolling_back["step"]) == (
        "ROLLING_BACK",
        "STOP_TARGET",
    )
    assert rolling_back["errorCode"] == "BUSINESS_RUNTIME_NOT_READY"


def test_restart_health_grace_does_not_hide_target_version_mismatch(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
        tmp_path
    )
    coordinator.queue_local(_request())
    _advance_until(
        coordinator,
        journal,
        state="VERIFYING_TARGET",
        step="VERIFY_TARGET",
    )
    business.version = "1.2.0"
    restarted = _restart_business_coordinator(
        safety=safety,
        journal=journal,
        business=business,
        helper=helper,
        stager=stager,
        clock=clock,
    )

    restarted.process_once()
    rolling_back = journal.get_update(UPDATE_UID)
    assert rolling_back is not None
    assert (rolling_back["state"], rolling_back["step"]) == (
        "ROLLING_BACK",
        "STOP_TARGET",
    )
    assert rolling_back["errorCode"] == "BUSINESS_RUNTIME_VERSION_MISMATCH"


def test_restart_waits_for_health_before_verifying_rollback(tmp_path: Path) -> None:
    coordinator, safety, journal, business, helper, stager, clock = _coordinator(
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
        state="VERIFYING_ROLLBACK",
        step="VERIFY_ROLLBACK",
    )
    business.fail_during_observation = False
    business.unavailable_health_calls = 1
    restarted = _restart_business_coordinator(
        safety=safety,
        journal=journal,
        business=business,
        helper=helper,
        stager=stager,
        clock=clock,
    )

    restarted.process_once()
    waiting = journal.get_update(UPDATE_UID)
    assert waiting is not None
    assert (waiting["state"], waiting["step"]) == (
        "VERIFYING_ROLLBACK",
        "VERIFY_ROLLBACK",
    )

    restarted.process_once()
    verified = journal.get_update(UPDATE_UID)
    assert verified is not None
    assert (verified["state"], verified["step"]) == (
        "VERIFYING_ROLLBACK",
        "RELEASE_ROLLBACK",
    )


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


def test_remote_cancellation_cleans_download_and_staging_before_completion(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, _helper, stager, _clock = (
        _coordinator(tmp_path, remote=True)
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)
    coordinator.queue_remote(_remote_command())

    accepted = coordinator.cancel_remote(_cancel_command())
    assert accepted["outcome"] == "ACCEPTED"
    assert journal.get_update(UPDATE_UID)["state"] == "RECEIVED"

    assert coordinator.process_once() is True
    completed = journal.get_update(UPDATE_UID)
    assert completed is not None
    assert completed["state"] == "DEFERRED"
    assert completed["errorCode"] == "BUSINESS_UPDATE_CANCELLED"
    assert journal.get_pending_cancellation(UPDATE_UID) is None
    result = journal.list_cancellation_results_requiring_delivery()[0]
    assert result["outcome"] == "CANCELLED"
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert downloader.cancelled == [UPDATE_UID, UPDATE_UID]
    assert stager.cleaned == [UPDATE_UID]


def test_remote_cancellation_reopens_gate_while_waiting_for_jobs(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, _helper, _stager, _clock = (
        _coordinator(tmp_path, remote=True)
    )
    coordinator.queue_remote(_remote_command())
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    waiting = _advance_until(
        coordinator,
        journal,
        state="WAITING_FOR_IDLE",
        step="WAIT_FOR_IDLE",
    )
    assert waiting["maintenanceFenceToken"] is not None
    assert safety.get_status()["jobGateState"] == "DRAINING"

    coordinator.cancel_remote(_cancel_command())
    coordinator.process_once()

    assert journal.get_update(UPDATE_UID)["errorCode"] == (
        "BUSINESS_UPDATE_CANCELLED"
    )
    assert safety.get_status()["jobGateState"] == "OPEN"
    assert safety.get_status()["maintenanceOwnerUid"] is None


def test_remote_cancellation_waits_for_inflight_download_cleanup(
    tmp_path: Path,
) -> None:
    coordinator, _safety, journal, _business, _helper, _stager, _clock = (
        _coordinator(tmp_path, remote=True)
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)
    downloader.cleanup_ready = False
    coordinator.queue_remote(_remote_command())
    coordinator.cancel_remote(_cancel_command())

    assert coordinator.process_once() is False
    assert journal.get_pending_cancellation(UPDATE_UID) is not None
    assert journal.get_update(UPDATE_UID)["state"] == "RECEIVED"

    downloader.cleanup_ready = True
    assert coordinator.process_once() is True
    assert journal.get_update(UPDATE_UID)["errorCode"] == (
        "BUSINESS_UPDATE_CANCELLED"
    )


def test_remote_cancellation_is_too_late_after_maintenance_boundary(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, _helper, _stager, _clock = (
        _coordinator(tmp_path, remote=True)
    )
    downloader = coordinator.remote_downloader
    assert isinstance(downloader, FakeRemoteDownloader)
    coordinator.queue_remote(_remote_command())
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    _advance_until(
        coordinator,
        journal,
        state="WAITING_FOR_IDLE",
        step="WAIT_FOR_IDLE",
    )
    coordinator.process_once()
    assert journal.get_update(UPDATE_UID)["state"] == "MIGRATING_DATA"
    assert safety.get_status()["jobGateState"] == "MAINTENANCE"

    result = coordinator.cancel_remote(_cancel_command())

    assert result["outcome"] == "TOO_LATE"
    assert journal.get_update(UPDATE_UID)["state"] == "MIGRATING_DATA"
    assert downloader.cancelled == []


def test_recovered_maintenance_boundary_cannot_be_cancelled_as_safe(
    tmp_path: Path,
) -> None:
    coordinator, safety, journal, _business, _helper, _stager, _clock = (
        _coordinator(tmp_path, remote=True)
    )
    coordinator.queue_remote(_remote_command())
    journal.mark_remote_downloaded(UPDATE_UID, 1)
    _advance_until(
        coordinator,
        journal,
        state="WAITING_FOR_IDLE",
        step="WAIT_FOR_IDLE",
    )
    safety.transition_job_gate("MAINTENANCE")
    assert journal.get_update(UPDATE_UID)["state"] == "WAITING_FOR_IDLE"

    result = coordinator.cancel_remote(_cancel_command())

    assert result["outcome"] == "TOO_LATE"
    assert journal.get_update(UPDATE_UID)["state"] == "MIGRATING_DATA"
