import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from edge_store import EdgeStore
from onenet_wire import (
    build_configuration_progress_event,
    canonical_payload_sha256,
    decode_service_command,
    encode_command_receipt,
    encode_event_post,
    validate_cos_grant,
)


def test_all_service_wire_examples_reconstruct_stable_payload_digest():
    examples = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "examples"
        / "onenet-wire"
    )
    for path in examples.glob("*.service-wire.json"):
        with path.open(encoding="utf-8") as source:
            body = json.load(source)["callServiceApiBodyTemplate"]
        command = decode_service_command(body["identifier"], body["params"])
        assert canonical_payload_sha256(command["payload"]) == command["payloadSha256"], path.name


def test_decode_start_delivery_session_wire_example():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "start-delivery-session.service-wire.json",
    )
    with open(path, encoding="utf-8") as f:
        wire = json.load(f)
    body = wire["callServiceApiBodyTemplate"]

    command = decode_service_command(body["identifier"], body["params"])

    assert command["commandType"] == "START_DELIVERY_SESSION"
    assert command["commandUid"] == "30000000-0000-4000-8000-000000000003"
    assert command["deploymentCode"] == "Dp_demo_01"
    assert command["target"] == {
        "type": "DELIVERY_SESSION",
        "uid": "30000000-0000-4000-8000-000000000001",
    }
    assert command["payload"]["sessionUid"] == "30000000-0000-4000-8000-000000000001"
    assert command["payload"]["portNo"] == 2
    assert command["cosGrant"] is None


def test_decode_confirm_edge_event_wire_example():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "confirm-edge-event.service-wire.json",
    )
    with open(path, encoding="utf-8") as f:
        wire = json.load(f)
    body = wire["callServiceApiBodyTemplate"]

    command = decode_service_command(body["identifier"], body["params"])

    assert command["commandType"] == "CONFIRM_EDGE_EVENT"
    assert command["payload"]["outcome"] == "BUSINESS_APPLIED"
    assert command["payload"]["effectKind"] == "CREATED"
    assert command["payload"]["quarantineUid"] is None
    assert command["payload"]["resultReferences"] == [
        {"key": "DO202607240001", "type": "DELIVERY_ORDER"}
    ]


def test_decode_confirmation_maps_all_result_reference_types():
    expected_types = [
        "DELIVERY_ORDER",
        "CLEAN_RECORD",
        "FULLNESS_DETECTION",
        "BASELINE_MEASUREMENT",
        "CONFIGURATION_APPLICATION",
        "PHOTO_SLOT",
        "DEVICE_FAULT",
        "PORT_FULLNESS_STATE",
    ]
    params = {
        "scalarFields": {
            "outcome": 1,
            "effectKind": 1,
        },
        "resultReferences": [
            {"type": code, "key": f"reference-{code}"}
            for code in range(1, 9)
        ],
    }

    command = decode_service_command("confirmEdgeEvent", params)

    assert [
        reference["type"]
        for reference in command["payload"]["resultReferences"]
    ] == expected_types


def test_decode_required_photo_grant_without_presence_flag():
    path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "examples"
        / "onenet-wire"
        / "provide-photo-upload-grant.service-wire.json"
    )
    with path.open(encoding="utf-8") as source:
        body = json.load(source)["callServiceApiBodyTemplate"]

    command = decode_service_command(
        body["identifier"],
        body["params"],
    )

    assert command["commandType"] == "PROVIDE_PHOTO_UPLOAD_GRANT"
    assert command["cosGrant"]["tmpSecretId"] == "TMP_SECRET_ID_5"
    assert command["cosGrant"]["sessionTokenParts"] == [
        "TOKEN_5_PART_1",
        "TOKEN_5_PART_2",
    ]


def test_cos_grant_must_match_trusted_runtime_environment():
    work_uid = str(uuid.uuid4())
    grant = {
        "grantUid": str(uuid.uuid4()),
        "tmpSecretId": "temporary-id",
        "tmpSecretKey": "temporary-key",
        "sessionTokenParts": ["temporary-token"],
        "bucket": "untrusted-1250000000",
        "region": "ap-beijing",
        "baseUrl": (
            "https://untrusted-1250000000.cos."
            "ap-beijing.myqcloud.com"
        ),
        "keyPrefix": (
            f"ecobin/Dp_demo_01/delivery-session/{work_uid}/"
        ),
        "expiresAt": (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }
    validate_cos_grant(
        grant,
        deployment_code="Dp_demo_01",
        work_type="DELIVERY_SESSION",
        work_uid=work_uid,
        trusted_environment={
            "bucket": grant["bucket"],
            "region": grant["region"],
            "baseUrl": grant["baseUrl"],
        },
    )

    with pytest.raises(
        ValueError,
        match="trusted runtime environment",
    ):
        validate_cos_grant(
            grant,
            deployment_code="Dp_demo_01",
            work_type="DELIVERY_SESSION",
            work_uid=work_uid,
            trusted_environment={
                "bucket": "ecobin-1258140596",
                "region": "ap-shanghai",
                "baseUrl": (
                    "https://ecobin-1258140596.cos."
                    "ap-shanghai.myqcloud.com"
                ),
            },
        )


def test_decode_apply_configuration_excludes_envelope_fields_from_payload():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(path, encoding="utf-8") as f:
        wire = json.load(f)
    body = wire["callServiceApiBodyTemplate"]

    command = decode_service_command(body["identifier"], body["params"])

    assert set(command["payload"]) == {
        "applicationUid",
        "config",
        "deviceConfig",
        "ports",
    }
    assert command["payload"]["deviceConfig"]["edgeHeartbeatIntervalMs"] == 30_000
    assert command["payload"]["deviceConfig"]["edgeHeartbeatMissThreshold"] == 3
    assert command["payloadSha256"] == (
        "46e8b2c7239ba04b94b7b5046c71d119e4b2f5c4bd9b802cad7d7810702e7e02"
    )


def test_encode_receipt_uses_target_numeric_state():
    receipt = encode_command_receipt("cmd-1", "DUPLICATE_ACCEPTED", 9001)

    assert receipt == {
        "schemaVersion": 1,
        "commandUid": "cmd-1",
        "receiptState": 2,
        "errorCodePresent": False,
        "errorCode": "",
        "edgeBootId": 9001,
    }


def test_encode_business_confirmation_receipt_event_shape():
    event = {
        "schemaVersion": 1,
        "eventUid": "60000000-0000-4000-8000-000000000003",
        "deploymentCode": "Dp_demo_01",
        "edgeEventSequence": 1045,
        "eventType": "BUSINESS_CONFIRMATION_RECEIPT",
        "deliveryClass": "CONTROL_RECEIPT",
        "target": {"type": "BUSINESS_CONFIRMATION", "uid": "conf-1"},
        "commandUid": "cmd-1",
        "occurredAt": "2026-07-24T01:00:30.000Z",
        "clockQuality": "SYNCED",
        "payloadSha256": "abc",
        "payload": {
            "confirmationUid": "conf-1",
            "originalEventUid": "evt-1",
            "originalPayloadSha256": "def",
            "outcome": "BUSINESS_APPLIED",
        },
    }

    wire = encode_event_post("BUSINESS_CONFIRMATION_RECEIPT", event)

    value = wire["params"]["businessConfirmationReceipt"]["value"]
    assert wire["id"] == "1045"
    assert value["eventType"] == 1
    assert value["deliveryClass"] == 1
    assert value["outcome"] == 1
    assert value["target"] == {"type": 1, "uid": "conf-1"}


def test_event_post_requires_bounded_numeric_edge_sequence():
    event = {
        "schemaVersion": 1,
        "eventUid": str(uuid.uuid4()),
        "deploymentCode": "Dp_demo_01",
        "eventType": "BUSINESS_CONFIRMATION_RECEIPT",
        "deliveryClass": "CONTROL_RECEIPT",
        "target": {"type": "BUSINESS_CONFIRMATION", "uid": "conf-1"},
        "commandUid": "cmd-1",
        "occurredAt": "2026-07-24T01:00:30.000Z",
        "clockQuality": "SYNCED",
        "payloadSha256": "abc",
        "payload": {
            "confirmationUid": "conf-1",
            "originalEventUid": "evt-1",
            "originalPayloadSha256": "def",
            "outcome": "BUSINESS_APPLIED",
        },
    }

    with pytest.raises(ValueError, match="edgeEventSequence"):
        encode_event_post("BUSINESS_CONFIRMATION_RECEIPT", event)


def test_encode_configuration_progress_presence_and_enum_fields():
    event = build_configuration_progress_event(
        deployment_code="Dp_demo_01",
        command_uid="20000000-0000-4000-8000-000000000001",
        application_uid="10000000-0000-4000-8000-000000000001",
        stage="EDGE_SAVED",
        version=8,
        content_sha256="a" * 64,
        mcu_payload_sha256="b" * 64,
        edge_event_sequence=1,
    )

    wire = encode_event_post("CONFIGURATION_PROGRESS", event)
    value = wire["params"]["configurationProgress"]["value"]

    assert value["stage"] == 1
    assert value["mcuCommandUidPresent"] is False
    assert value["mcuCommandUid"] == ""
    assert value["errorCodePresent"] is False
    assert value["errorCode"] == ""


def test_all_event_wire_examples_match_runtime_projection():
    root = Path(__file__).resolve().parents[2] / "contracts" / "examples"
    for wire_path in (root / "onenet-wire").glob("*.event-wire.json"):
        canonical_path = root / "onenet" / wire_path.name.replace(
            ".event-wire.json",
            ".event.json",
        )
        with canonical_path.open(encoding="utf-8") as source:
            event = json.load(source)
        with wire_path.open(encoding="utf-8") as source:
            expected = json.load(source)["oneJsonPayload"]

        actual = encode_event_post(event["eventType"], event)

        assert actual["version"] == expected["version"], wire_path.name
        assert actual["params"] == expected["params"], wire_path.name


def test_nullable_measurement_uses_presence_flag_and_typed_placeholder():
    root = Path(__file__).resolve().parents[2] / "contracts" / "examples"
    with (root / "onenet" / "delivery-complete.event.json").open(
        encoding="utf-8"
    ) as source:
        event = json.load(source)
    event["payload"]["finalPostCloseMeasurement"] = None

    value = encode_event_post("DELIVERY_COMPLETE", event)["params"][
        "deliveryComplete"
    ]["value"]

    assert value["finalPostCloseMeasurementPresent"] is False
    assert value["finalPostCloseMeasurement"]["reportedWeightGramsPresent"] is False
    assert value["finalPostCloseMeasurement"]["faultCodePresent"] is False


def test_store_confirmation_creates_receipt_event(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    store.receive_mcu_event(
        "30000000-0000-4000-8000-000000000006",
        "DELIVERY_COMPLETE",
        {"payloadSha256": "95396998abe4dddc1e6007ba383d1fc82009f9df6a48407b33b23c99ace78b4d"},
    )

    result = store.receive_business_confirmation_and_create_receipt(
        command_uid="60000000-0000-4000-8000-000000000002",
        deployment_code="Dp_demo_01",
        confirmation_payload={
            "confirmationUid": "60000000-0000-4000-8000-000000000001",
            "originalEventUid": "30000000-0000-4000-8000-000000000006",
            "originalPayloadSha256": "95396998abe4dddc1e6007ba383d1fc82009f9df6a48407b33b23c99ace78b4d",
            "outcome": "BUSINESS_APPLIED",
        },
    )

    assert result == "ACCEPTED"
    receipts = store._conn.execute(
        "SELECT * FROM event_outbox WHERE event_type='BUSINESS_CONFIRMATION_RECEIPT'"
    ).fetchall()
    assert len(receipts) == 1
    store.close()


def test_duplicate_confirmation_reuses_and_requeues_exact_receipt(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    original_event_uid = str(uuid.uuid4())
    original_sha256 = "9" * 64
    store.receive_mcu_event(
        original_event_uid,
        "DELIVERY_COMPLETE",
        {"payloadSha256": original_sha256},
    )
    confirmation_uid = str(uuid.uuid4())
    command_uid = str(uuid.uuid4())
    confirmation = {
        "confirmationUid": confirmation_uid,
        "originalEventUid": original_event_uid,
        "originalPayloadSha256": original_sha256,
        "outcome": "BUSINESS_APPLIED",
        "processedAt": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "errorCode": None,
        "quarantineUid": None,
    }
    command = {
        "schemaVersion": 1,
        "commandUid": command_uid,
        "commandType": "CONFIRM_EDGE_EVENT",
        "deploymentCode": "Dp_demo_01",
        "target": {
            "type": "EDGE_EVENT",
            "uid": original_event_uid,
        },
        "issuedAt": confirmation["processedAt"],
        "expiresAt": (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "payloadSchemaVersion": 1,
        "payloadSha256": canonical_payload_sha256(confirmation),
        "payload": confirmation,
    }

    assert store.receive_business_confirmation_and_create_receipt(
        deployment_code="Dp_demo_01",
        command=command,
    ) == "ACCEPTED"
    receipt = store._conn.execute(
        """SELECT event_uid FROM event_outbox
           WHERE event_type='BUSINESS_CONFIRMATION_RECEIPT'"""
    ).fetchone()
    receipt_uid = receipt["event_uid"]
    assert store.mark_control_receipt_published(receipt_uid)
    assert store.get_event(receipt_uid)["state"] == "CONFIRMED"

    assert store.receive_business_confirmation_and_create_receipt(
        deployment_code="Dp_demo_01",
        command=command,
    ) == "DUPLICATE"
    assert store.get_event(receipt_uid)["state"] == "PENDING"
    receipt_count = store._conn.execute(
        """SELECT COUNT(*) AS count FROM event_outbox
           WHERE event_type='BUSINESS_CONFIRMATION_RECEIPT'"""
    ).fetchone()["count"]
    assert receipt_count == 1

    conflicting = {
        **command,
        "payload": {
            **confirmation,
            "processedAt": (
                datetime.now(timezone.utc) + timedelta(seconds=1)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        },
    }
    conflicting["payloadSha256"] = canonical_payload_sha256(
        conflicting["payload"]
    )
    assert store.receive_business_confirmation_and_create_receipt(
        deployment_code="Dp_demo_01",
        command=conflicting,
    ) == "CONFLICT"
    store.close()
