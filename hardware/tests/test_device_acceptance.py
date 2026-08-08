import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from command_processor import CommandProcessor
from device_acceptance import DeviceAcceptanceRunner
from edge_store import EdgeStore
from onenet_wire import canonical_payload_sha256, encode_event_post


DEVICE_NAME = "SN-ACCEPTANCE-0001"
DEVICE_ENTRY_URL = (
    "https://www.jinshoubao.com/device-entry/"
    "public-code-acceptance-0001"
)
DEVICE_ENTRY_URL_SHA256 = hashlib.sha256(
    DEVICE_ENTRY_URL.encode("ascii")
).hexdigest()


def _instant(delta=timedelta()):
    return (
        datetime.now(timezone.utc) + delta
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RealFixedFrameUart:
    compatibility_mode = True
    is_simulated = False
    is_open = True
    mcu_session_ready = True
    port_count = 1
    _mcu_firmware_version = "fixed-frame-1.0.0"

    def __init__(self, result=None):
        self.query_count = 0
        self.sent_device_entry_urls = []
        self._result = result or {
            "queryStatus": "OK",
            "communicationHealthy": True,
            "portNo": 1,
            "validFlags": 3,
            "weightValid": True,
            "weightGrams": 1234,
            "weightMeasurementUid": str(uuid.uuid4()),
            "infraredValid": True,
            "infraredBlocked": False,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
            "faultCode": None,
            "rawFrameHex": "f1030004d20000f1",
        }

    def query_self_test(self, timeout_ms=3000, on_result=None):
        assert timeout_ms == 3000
        self.query_count += 1
        result = dict(self._result)
        if on_result is not None:
            on_result(result)
        return result

    def send_device_entry_url(self, url):
        self.sent_device_entry_urls.append(url)
        return {
            "disposition": "LOCALLY_DISPATCHED",
            "responseExpected": False,
        }


class SimulatedFixedFrameUart(RealFixedFrameUart):
    is_simulated = True


class ProbePhotoManager:
    def __init__(self, tmp_path, *, simulated=False):
        self._tmp_path = tmp_path
        self._simulated = simulated

    def capture_acceptance_probe(self, challenge_uid):
        captures = []
        for name in ("OUTSIDE", "INSIDE"):
            path = self._tmp_path / f"{name.lower()}.jpg"
            content = b"\xff\xd8\xff" + name.encode("ascii") + b"\xff\xd9"
            path.write_bytes(content)
            captures.append({
                "camera": name,
                "simulated": self._simulated,
                "path": str(path),
                "contentSha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
                "error": None,
            })
        return {
            "challengeUid": challenge_uid,
            "camerasSimulated": self._simulated,
            "captures": captures,
        }


class ReadbackUploader:
    def __init__(self):
        self.keys = []

    def upload_and_readback(self, grant, local_path, object_key):
        self.keys.append(object_key)
        digest = hashlib.sha256(
            __import__("pathlib").Path(local_path).read_bytes()
        ).hexdigest()
        return {
            "url": f"{grant['baseUrl']}/{object_key}",
            "uploadedSha256": digest,
            "readbackSha256": digest,
        }


def _command(challenge_uid):
    payload = {
        "challengeUid": challenge_uid,
        "expectedPortCount": 1,
        "deviceEntryUrl": DEVICE_ENTRY_URL,
        "deviceEntryUrlSha256": DEVICE_ENTRY_URL_SHA256,
    }
    return {
        "schemaVersion": 2,
        "commandUid": str(uuid.uuid4()),
        "commandType": "REQUEST_DEVICE_ACCEPTANCE",
        "targetDeviceName": DEVICE_NAME,
        "target": {"type": "DEVICE_ASSET", "uid": DEVICE_NAME},
        "issuedAt": _instant(),
        "expiresAt": _instant(timedelta(minutes=5)),
        "payloadSchemaVersion": 2,
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
        "cosGrant": {
            "grantUid": str(uuid.uuid4()),
            "tmpSecretId": "TEMPORARY_ID_NOT_FOR_SQLITE",
            "tmpSecretKey": "TEMPORARY_KEY_NOT_FOR_SQLITE",
            "sessionTokenParts": ["TEMPORARY_TOKEN_NOT_FOR_SQLITE"],
            "bucket": "ecobin-contract-1250000000",
            "region": "ap-guangzhou",
            "baseUrl": (
                "https://ecobin-contract-1250000000.cos."
                "ap-guangzhou.myqcloud.com"
            ),
            "keyPrefix": (
                f"ecobin/device-acceptance/{challenge_uid}/"
            ),
            "expiresAt": _instant(timedelta(minutes=10)),
        },
    }


def _sync_command():
    payload = {
        "deviceEntryUrl": DEVICE_ENTRY_URL,
        "deviceEntryUrlSha256": DEVICE_ENTRY_URL_SHA256,
    }
    return {
        "schemaVersion": 2,
        "commandUid": str(uuid.uuid4()),
        "commandType": "SYNC_DEVICE_ENTRY_URL",
        "targetDeviceName": DEVICE_NAME,
        "target": {"type": "DEVICE_ASSET", "uid": DEVICE_NAME},
        "issuedAt": _instant(),
        "expiresAt": _instant(timedelta(minutes=5)),
        "payloadSchemaVersion": 2,
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
        "cosGrant": None,
    }


def _store_with_stale_business_sample(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.set_state(
        "fixed_frame_latest_observation_json",
        json.dumps({
            "sourceWorkType": "FACTORY_TEST",
            "sourceWorkUid": str(uuid.uuid4()),
            "portNo": 1,
            "postWeightGrams": 1234,
            "infraredBlocked": False,
            "mcuBootId": 42,
            "mcuEventSequence": 7,
            "measurementUid": str(uuid.uuid4()),
        }),
    )
    return store


def _process(tmp_path, monkeypatch, uart, *, cameras_simulated=False):
    monkeypatch.setattr("device_acceptance._clock_state", lambda: "SYNCED")
    store = _store_with_stale_business_sample(tmp_path)
    uploader = ReadbackUploader()
    runner = DeviceAcceptanceRunner(
        store,
        uart,
        ProbePhotoManager(
            tmp_path,
            simulated=cameras_simulated,
        ),
        uploader,
        device_name=DEVICE_NAME,
    )
    command = _command(str(uuid.uuid4()))
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    processor = CommandProcessor(
        store,
        uart,
        acceptance_runner=runner,
    )
    assert processor.offer_cos_grant(
        command["commandUid"],
        command["cosGrant"],
    )
    assert processor.process_next()
    event_row = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_ACCEPTANCE_EVIDENCE'"""
    ).fetchone()
    return store, command, uploader, json.loads(event_row["payload_json"])


def test_real_hardware_acceptance_records_reliable_evidence(
    tmp_path,
    monkeypatch,
):
    store, command, uploader, event = _process(
        tmp_path,
        monkeypatch,
        RealFixedFrameUart(),
    )

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert event["commandUid"] == command["commandUid"]
    assert event["payload"]["mcuSimulated"] is False
    assert event["payload"]["camerasSimulated"] is False
    assert event["payload"]["sensorsHealthy"] is True
    assert event["payload"]["cameraUploadHealthy"] is True
    assert event["payload"]["evidenceSchemaVersion"] == 2
    assert event["payload"]["deviceEntryUrlStored"] is True
    assert (
        event["payload"]["deviceEntryUrlSha256"]
        == DEVICE_ENTRY_URL_SHA256
    )
    assert store.get_device_entry_url()["deviceEntryUrl"] == DEVICE_ENTRY_URL
    assert store.get_state("fixed_frame_latest_self_test_json")
    assert len(uploader.keys) == 2
    assert all(
        key.startswith(
            f"ecobin/device-acceptance/"
            f"{command['payload']['challengeUid']}/"
        )
        for key in uploader.keys
    )
    assert encode_event_post("DEVICE_ACCEPTANCE_EVIDENCE", event)
    database = (tmp_path / "edge.db").read_bytes()
    assert b"TEMPORARY_KEY_NOT_FOR_SQLITE" not in database
    store.close()


def test_smoke_unavailable_keeps_mcu_communication_but_fails_sensors(
    tmp_path,
    monkeypatch,
):
    result = {
        "queryStatus": "OK",
        "communicationHealthy": True,
        "portNo": 1,
        "validFlags": 3,
        "weightValid": True,
        "weightGrams": 1234,
        "weightMeasurementUid": str(uuid.uuid4()),
        "infraredValid": True,
        "infraredBlocked": False,
        "smokeCode": 2,
        "smokeState": "UNKNOWN",
        "smokeSensorHealth": "SENSOR_FAULT",
        "faultCode": "SMOKE_SENSOR",
        "rawFrameHex": "f1030004d20002f1",
    }
    store, _, _, event = _process(
        tmp_path,
        monkeypatch,
        RealFixedFrameUart(result),
    )

    assert event["payload"]["mcuCommunicationHealthy"] is True
    assert event["payload"]["sensorsHealthy"] is False
    assert store.get_state("smoke_state") == "UNKNOWN"
    assert store.get_state("smoke_sensor_health") == "SENSOR_FAULT"
    store.close()


def test_timeout_does_not_fall_back_to_stale_delivery_sample(
    tmp_path,
    monkeypatch,
):
    result = {
        "queryStatus": "TIMEOUT",
        "communicationHealthy": False,
        "portNo": 1,
        "validFlags": 0,
        "weightValid": False,
        "weightGrams": None,
        "weightMeasurementUid": None,
        "infraredValid": False,
        "infraredBlocked": None,
        "smokeCode": None,
        "smokeState": "UNKNOWN",
        "smokeSensorHealth": "TIMEOUT",
        "faultCode": "SMOKE_SENSOR",
        "rawFrameHex": None,
    }
    store, _, _, event = _process(
        tmp_path,
        monkeypatch,
        RealFixedFrameUart(result),
    )

    assert event["payload"]["mcuCommunicationHealthy"] is False
    assert event["payload"]["sensorsHealthy"] is False
    stored = json.loads(
        store.get_state("fixed_frame_latest_self_test_json")
    )
    assert stored["queryStatus"] == "TIMEOUT"
    assert stored["weightGrams"] is None
    store.close()


def test_simulators_are_diagnostic_and_functional_checks_still_pass(
    tmp_path,
    monkeypatch,
):
    store, command, _, event = _process(
        tmp_path,
        monkeypatch,
        SimulatedFixedFrameUart(),
        cameras_simulated=True,
    )

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert event["payload"]["mcuSimulated"] is True
    assert event["payload"]["camerasSimulated"] is True
    assert event["payload"]["mcuCommunicationHealthy"] is True
    assert event["payload"]["sensorsHealthy"] is True
    assert event["payload"]["camerasCaptureHealthy"] is True
    assert event["payload"]["cameraUploadHealthy"] is True
    store.close()


def test_edge_store_instance_uid_is_stable_for_one_database(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()

    first = store.get_or_create_edge_store_instance_uid()
    second = store.get_or_create_edge_store_instance_uid()

    assert first == second
    assert uuid.UUID(first).version == 4
    store.close()


def test_global_url_sync_completes_after_local_save_without_mcu_ack(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    uart = RealFixedFrameUart()
    command = _sync_command()
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert CommandProcessor(store, uart).process_next()

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_device_entry_url()["deviceEntryUrl"] == DEVICE_ENTRY_URL
    assert uart.sent_device_entry_urls == [DEVICE_ENTRY_URL]
    store.close()
