from __future__ import annotations

import pytest

from business_update_reporter import (
    BusinessUpdateProgressReporter,
    _cancellation_result_payload,
    _progress_payload,
)
from business_update_store import BusinessUpdateStore
from updater_store import UpdaterStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT_UID = "22222222-2222-4222-8222-222222222222"
COMMAND_UID = "33333333-3333-4333-8333-333333333333"
RELEASE_UID = "44444444-4444-4444-8444-444444444444"
CANCEL_COMMAND_UID = "55555555-5555-4555-8555-555555555555"


class CommunicationClient:
    def __init__(self) -> None:
        self.calls = []

    def request(self, action, payload):
        self.calls.append((action, payload))
        return {
            "eventUid": payload["eventUid"],
            "disposition": "ACCEPTED",
            "dispatchGeneration": 1,
            "edgeEventSequence": 9_000_000_000_000 + len(self.calls),
            "durableAccepted": True,
        }


def _remote_request() -> dict:
    return {
        "updateUid": UPDATE_UID,
        "deploymentUid": DEPLOYMENT_UID,
        "commandUid": COMMAND_UID,
        "releaseId": RELEASE_UID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
        "packageSize": 1024,
        "signingKeyId": "business_2026",
        "stablePayloadSha256": "b" * 64,
        "controlSequence": 1,
        "objectKey": f"edge-runtime/releases/{RELEASE_UID}/package.tar.gz",
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


def test_reporter_freezes_and_durably_hands_off_each_observed_stage(tmp_path):
    path = tmp_path / "updater.db"
    safety = UpdaterStore(
        path,
        release_version="updater-v1",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    journal.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )
    communication = CommunicationClient()
    reporter = BusinessUpdateProgressReporter(
        journal=journal,
        safety_store=safety,
        communication_client=communication,
    )

    assert reporter.process_once() is True
    first = communication.calls[-1]
    assert first[0] == "SUBMIT_UPDATER_EVENT"
    assert first[1]["targetUid"] == DEPLOYMENT_UID
    assert first[1]["payload"]["stage"] == "RECEIVED"
    assert first[1]["payload"]["businessAdmissionState"] == "LOCKED"
    assert journal.list_pending_progress_deliveries() == []

    journal.begin_remote_download(UPDATE_UID, 1)
    assert reporter.process_once() is True
    assert communication.calls[-1][1]["payload"]["stage"] == "DOWNLOADING"
    assert communication.calls[-1][1]["payload"]["downloadAttemptCount"] == 1

    journal.close()
    safety.close()


def test_rollback_progress_reports_the_complete_restored_release_identity():
    snapshot = {
        "deploymentUid": DEPLOYMENT_UID,
        "updateUid": UPDATE_UID,
        "releaseId": RELEASE_UID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
        "state": "ROLLED_BACK",
        "downloadState": "DOWNLOADED",
        "stageSequence": 12,
        "downloadAttemptCount": 1,
        "targetAttemptCount": 1,
        "rollbackAttemptCount": 1,
        "databaseRestored": True,
        "installedReleaseId": "55555555-5555-4555-8555-555555555555",
        "installedVersionName": "1.0.0",
        "installedReleaseSequence": 1,
        "installedPackageSha256": "c" * 64,
        "errorCode": "TARGET_HEALTH_CHECK_FAILED",
    }

    payload = _progress_payload(snapshot, {"jobGateState": "OPEN"})

    assert payload["stage"] == "ROLLED_BACK"
    assert payload["databaseRestored"] is True
    assert payload["installedReleaseUid"] == snapshot["installedReleaseId"]
    assert payload["installedVersionName"] == "1.0.0"
    assert payload["installedReleaseSequence"] == 1
    assert payload["installedPackageSha256"] == "c" * 64
    assert payload["errorCode"] is None


def test_progress_refuses_a_partial_installed_release_identity():
    snapshot = {
        "deploymentUid": DEPLOYMENT_UID,
        "updateUid": UPDATE_UID,
        "releaseId": RELEASE_UID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
        "state": "OBSERVING",
        "downloadState": "DOWNLOADED",
        "stageSequence": 8,
        "downloadAttemptCount": 1,
        "targetAttemptCount": 1,
        "rollbackAttemptCount": 0,
        "databaseRestored": False,
        "installedReleaseId": RELEASE_UID,
        "installedVersionName": None,
        "installedReleaseSequence": 2,
        "installedPackageSha256": "a" * 64,
        "errorCode": None,
    }

    with pytest.raises(RuntimeError, match="identity is incomplete"):
        _progress_payload(snapshot, {"jobGateState": "MAINTENANCE"})


def test_reporter_sends_cancellation_result_before_cancelled_progress(tmp_path):
    path = tmp_path / "updater.db"
    safety = UpdaterStore(
        path,
        release_version="updater-v1",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    status = safety.get_status()
    safety.activate_stage4_job_gate(
        {
            "operationUid": "66666666-6666-4666-8666-666666666666",
            "evidenceDigest": "f" * 64,
            "expectedManagementStateSequence": status[
                "managementStateSequence"
            ],
        }
    )
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    journal.create_or_refresh_remote_update(
        _remote_request(), authorization_sequence=1
    )
    journal.request_remote_cancellation(
        {
            "cancelCommandUid": CANCEL_COMMAND_UID,
            "updateUid": UPDATE_UID,
            "deploymentUid": DEPLOYMENT_UID,
            "controlSequence": 2,
            "reason": "platform administrator stopped validation",
        }
    )
    journal.complete_remote_cancellation(
        UPDATE_UID,
        evidence_sha256="c" * 64,
    )
    communication = CommunicationClient()
    reporter = BusinessUpdateProgressReporter(
        journal=journal,
        safety_store=safety,
        communication_client=communication,
    )

    assert reporter.process_once() is True
    cancellation_event = communication.calls[-1][1]
    assert cancellation_event["eventType"] == (
        "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT"
    )
    assert cancellation_event["commandUid"] == CANCEL_COMMAND_UID
    assert cancellation_event["payload"]["result"] == "CANCELLED"
    assert cancellation_event["payload"]["errorCode"] is None
    assert journal.list_pending_cancellation_result_deliveries() == []

    assert reporter.process_once() is True
    progress_event = communication.calls[-1][1]
    assert progress_event["eventType"] == "BUSINESS_RUNTIME_UPDATE_PROGRESS"
    assert progress_event["commandUid"] == COMMAND_UID
    assert progress_event["payload"]["stage"] == "CANCELLED"
    assert progress_event["payload"]["errorCode"] is None
    journal.close()
    safety.close()


def test_too_late_cancellation_result_keeps_explicit_error():
    payload = _cancellation_result_payload(
        {
            "deploymentUid": DEPLOYMENT_UID,
            "updateUid": UPDATE_UID,
            "controlSequence": 2,
            "outcome": "TOO_LATE",
            "observedState": "MIGRATING_DATA",
        },
        {"jobGateState": "MAINTENANCE"},
    )

    assert payload == {
        "deploymentUid": DEPLOYMENT_UID,
        "updateUid": UPDATE_UID,
        "controlSequence": 2,
        "result": "TOO_LATE",
        "observedStage": "MIGRATING_DATA",
        "businessAdmissionState": "MAINTENANCE",
        "errorCode": "BUSINESS_UPDATE_CANCEL_TOO_LATE",
    }
