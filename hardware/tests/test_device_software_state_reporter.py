from __future__ import annotations

from device_software_state_reporter import DeviceSoftwareStateReporter
from business_update_store import BusinessUpdateStore
from updater_store import UpdaterStore


UPDATE_UID = "11111111-1111-4111-8111-111111111111"
DEPLOYMENT_UID = "22222222-2222-4222-8222-222222222222"
COMMAND_UID = "33333333-3333-4333-8333-333333333333"
RELEASE_UID = "44444444-4444-4444-8444-444444444444"


class CommunicationClient:
    def __init__(self, release_version="communication-v1") -> None:
        self.events = []
        self.release_version = release_version

    def request(self, action, payload):
        if action == "GET_STATUS":
            return {
                "component": "COMMUNICATION_AGENT",
                "status": "READY",
                "releaseVersion": self.release_version,
                "localProtocolName": "ecobin.communication.control",
                "localProtocolMajor": 1,
                "localProtocolMinor": 0,
                "onenetOwnership": "ENABLED",
                "businessEventIngress": "ENABLED",
                "authenticatedDeviceName": "SN-TEST-0001",
            }
        assert action == "SUBMIT_UPDATER_EVENT"
        self.events.append(payload)
        return {
            "eventUid": payload["eventUid"],
            "disposition": "ACCEPTED",
            "dispatchGeneration": 1,
            "edgeEventSequence": 9_000_000_000_001,
            "durableAccepted": True,
        }


class BusinessClient:
    status = "READY"

    def __init__(self, release_version="1.1.0") -> None:
        self.release_version = release_version

    def request(self, action, payload):
        assert (action, payload) == ("GET_SOFTWARE_RUNTIME_FACTS", {})
        return {
            "component": "BUSINESS_RUNTIME",
            "status": self.status,
            "releaseVersion": self.release_version,
            "localProtocolName": "ecobin.business.control",
            "localProtocolMajor": 1,
            "localProtocolMinor": 0,
            "cloudProxyIngressEnabled": True,
            "cloudConnectionOwner": "COMMUNICATION_AGENT",
            "mcuFirmware": {
                "versionName": "2.1.0",
                "versionCode": 20100,
                "identityHex": "0123456789abcdef",
                "fixedFrameRevision": 2,
            },
            "uartState": "READY",
            "uartProtocol": None,
            "capabilityBitmapHex": "0000000000000000",
        }


def _request() -> dict:
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
    }


def test_reporter_uses_actual_installed_identity_and_global_sequence(tmp_path):
    path = tmp_path / "updater.db"
    safety = UpdaterStore(
        path,
        release_version="updater-v1",
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    journal.create_update(_request())
    journal.transition(
        UPDATE_UID,
        "SUCCEEDED",
        fields={
            "installed_release_id": RELEASE_UID,
            "installed_version_name": "1.1.0",
            "installed_release_sequence": 2,
            "installed_package_sha256": "a" * 64,
        },
    )
    communication = CommunicationClient()
    business = BusinessClient()
    before = safety.get_status()["managementStateSequence"]
    reporter = DeviceSoftwareStateReporter(
        journal=journal,
        safety_store=safety,
        communication_client=communication,
        business_client=business,
    )

    assert reporter.process_once() is True
    event = communication.events[-1]
    payload = event["payload"]
    assert event["eventType"] == "DEVICE_SOFTWARE_STATE_REPORTED"
    assert event["targetUid"] == "SN-TEST-0001"
    assert event["commandUid"] is None
    assert payload["managementStateSequence"] == before + 1
    assert payload["activeBusinessRelease"] == {
        "releaseUid": RELEASE_UID,
        "versionName": "1.1.0",
        "releaseSequence": 2,
        "packageSha256": "a" * 64,
    }
    assert payload["businessProcessState"] == "RUNNING"
    assert payload["businessReady"] is True
    assert payload["mcuFirmware"]["fixedFrameRevision"] == 2
    assert journal.list_pending_software_state_deliveries() == []

    assert reporter.process_once() is False
    assert len(communication.events) == 1

    business.status = "STOPPING"
    assert reporter.process_once() is True
    stopped = communication.events[-1]["payload"]
    assert stopped["managementStateSequence"] == before + 2
    assert stopped["businessProcessState"] == "STOPPED"
    assert stopped["businessReady"] is False

    journal.close()
    safety.close()


def test_reporter_recognizes_the_healthy_image_bridge_without_a_release(
    tmp_path,
):
    path = tmp_path / "updater.db"
    image_version = "hardware-runtime-test-31"
    safety = UpdaterStore(
        path,
        release_version=image_version,
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    communication = CommunicationClient(image_version)
    business = BusinessClient(image_version)
    reporter = DeviceSoftwareStateReporter(
        journal=journal,
        safety_store=safety,
        communication_client=communication,
        business_client=business,
    )

    assert reporter.process_once() is True
    payload = communication.events[-1]["payload"]
    assert payload["activeBusinessRelease"] is None
    assert payload["businessProcessState"] == "RUNNING"
    assert payload["businessReady"] is True

    journal.close()
    safety.close()


def test_reporter_does_not_misidentify_an_unjournaled_release_as_the_bridge(
    tmp_path,
):
    path = tmp_path / "updater.db"
    image_version = "hardware-runtime-test-31"
    safety = UpdaterStore(
        path,
        release_version=image_version,
        enable_stage4_candidate=True,
    )
    safety.initialize()
    journal = BusinessUpdateStore(path, remote_trigger_enabled=True)
    journal.initialize()
    communication = CommunicationClient(image_version)
    business = BusinessClient("0.3.0-unknown")
    reporter = DeviceSoftwareStateReporter(
        journal=journal,
        safety_store=safety,
        communication_client=communication,
        business_client=business,
    )

    assert reporter.process_once() is True
    payload = communication.events[-1]["payload"]
    assert payload["activeBusinessRelease"] is None
    assert payload["businessReady"] is False

    journal.close()
    safety.close()
