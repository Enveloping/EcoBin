import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from business_message_handler import BusinessMessageHandler
from cloud_transport import CloudServiceRequest
from device_identity import DeviceIdentity
from edge_store import EdgeStore
from factory_seal.admission import FactorySealProductionGate
from factory_seal.validation import FactorySealPaths
from onenet_wire import canonical_payload_sha256, decode_service_command


EXAMPLES = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "examples"
    / "onenet-wire"
)
DEVICE_NAME = "SN-CONTRACT-0001"
EDGE_BOOT_ID = 9001


def utc_text(value):
    return value.isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def load_command(
    example_name,
    *,
    issued_at=None,
    expires_at=None,
    cos_grant_expires_at=None,
):
    with (EXAMPLES / example_name).open(encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    service_id = body["identifier"]
    params = copy.deepcopy(body["params"])
    now = datetime.now(timezone.utc)
    issued_at = issued_at or now
    expires_at = expires_at or (now + timedelta(minutes=5))

    if "scalarFields1" in params:
        fields = params["scalarFields1"]
        fields["issuedAt"] = utc_text(issued_at)
        fields["expiresAt"] = utc_text(expires_at)
        if "scalarFields2" in params:
            cos_fields = params["scalarFields2"]
            if "cosGrantExpiresAt" in cos_fields:
                cos_fields["cosGrantExpiresAt"] = utc_text(
                    cos_grant_expires_at
                    or (now + timedelta(minutes=10))
                )
    elif "scalarFields" in params:
        fields = params["scalarFields"]
        fields["issuedAt"] = utc_text(issued_at)
        fields["expiresAt"] = utc_text(expires_at)
    else:
        fields = params
        fields["issuedAt"] = utc_text(issued_at)
        fields["expiresAt"] = utc_text(expires_at)

    decoded = decode_service_command(service_id, params)
    fields["payloadSha256"] = canonical_payload_sha256(
        decoded["payload"]
    )
    return service_id, params, decode_service_command(service_id, params)


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def make_handler(
    store,
    *,
    trusted_cos_environment=None,
    unsupported_command_types=(),
    factory_seal_gate=None,
):
    return BusinessMessageHandler(
        store,
        DeviceIdentity(DEVICE_NAME),
        edge_boot_id=EDGE_BOOT_ID,
        trusted_cos_environment=trusted_cos_environment,
        unsupported_command_types=unsupported_command_types,
        factory_seal_gate=factory_seal_gate,
    )


def invoke(handler, service_id, params, *, request_id=None):
    return handler.handle_service_request(
        CloudServiceRequest(
            delivery_id=str(uuid.uuid4()),
            request_id=request_id or str(uuid.uuid4()),
            service_id=service_id,
            params=params,
            received_at=None,
            clock_quality="UNAVAILABLE",
        )
    )


def complete_reply(response):
    if response.after_reply is not None:
        response.after_reply()


def test_fixed_frame_unsupported_service_is_rejected_synchronously(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(
        store,
        unsupported_command_types={
            "END_CLEAN_BEFORE_UNLOCK",
            "RESUME_CLEAN_OPERATION",
        },
    )
    dispatched = []
    handler.on_command_received = lambda *args: dispatched.append(args)
    decoded_commands = []
    responses = []

    for example_name in (
        "end-clean-before-unlock.service-wire.json",
        "resume-clean-operation.service-wire.json",
    ):
        service_id, params, decoded = load_command(example_name)
        decoded_commands.append(decoded)
        for _ in range(2):
            response = invoke(handler, service_id, params)
            responses.append(response)
            complete_reply(response)

    for decoded in decoded_commands:
        command = store.get_command(decoded["commandUid"])
        assert command["state"] == "REJECTED"
        assert command["last_error"] == "MCU_FEATURE_NOT_SUPPORTED"
    assert dispatched == []
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 2
    assert all(
        json.loads(row["payload_json"])["payload"]["stage"]
        == "REJECTED"
        for row in observations
    )
    assert all(
        json.loads(row["payload_json"])["payload"]["errorCode"]
        == "MCU_FEATURE_NOT_SUPPORTED"
        for row in observations
    )
    assert all(
        response.data["receiptState"] == 3
        for response in responses
    )
    assert all(
        response.data["errorCode"] == "MCU_FEATURE_NOT_SUPPORTED"
        for response in responses
    )
    store.close()


def test_unsealed_physical_command_is_rejected_before_inbox_dispatch(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(
        store,
        factory_seal_gate=FactorySealProductionGate(
            FactorySealPaths(
                edge_store=tmp_path / "edge.db",
                sealed=tmp_path / "sealed.json",
            )
        ),
    )
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    service_id, params, decoded = load_command(
        "start-delivery-session.service-wire.json"
    )

    responses = []
    for _ in range(2):
        response = invoke(handler, service_id, params)
        responses.append(response)
        complete_reply(response)

    row = store.get_command(decoded["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == "FACTORY_NOT_SEALED"
    assert dispatched == []
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 1
    observed = json.loads(observations[0]["payload_json"])["payload"]
    assert observed["stage"] == "REJECTED"
    assert observed["errorCode"] == "FACTORY_NOT_SEALED"
    assert all(
        response.data["receiptState"] == 3
        for response in responses
    )
    assert all(
        response.data["errorCode"] == "FACTORY_NOT_SEALED"
        for response in responses
    )
    store.close()


def test_duplicate_apply_configuration_is_acknowledged_without_redispatch(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(store)
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    service_id, params, decoded = load_command(
        "apply-configuration.service-wire.json"
    )

    first = invoke(handler, service_id, params)
    assert dispatched == []
    complete_reply(first)
    assert len(dispatched) == 1

    duplicate = invoke(handler, service_id, params)
    complete_reply(duplicate)

    assert len(dispatched) == 1
    assert dispatched[0][0] == decoded["commandUid"]
    assert dispatched[0][1] == "APPLY_CONFIGURATION"
    assert [first.data["receiptState"], duplicate.data["receiptState"]] == [
        1,
        2,
    ]
    store.close()


def test_duplicate_firmware_command_redispatches_fresh_cos_grant(tmp_path):
    store = make_store(tmp_path)
    handler = make_handler(
        store,
        trusted_cos_environment={
            "bucket": "ecobin-contract-1250000000",
            "region": "ap-guangzhou",
            "baseUrl": (
                "https://ecobin-contract-1250000000"
                ".cos.ap-guangzhou.myqcloud.com"
            ),
        },
    )
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    service_id, params, decoded = load_command(
        "start-mcu-firmware-update.service-wire.json"
    )

    first = invoke(handler, service_id, params)
    assert dispatched == []
    complete_reply(first)
    params["scalarFields1"]["cosGrantGrantUid"] = (
        "71000000-0000-4000-8000-000000000008"
    )
    params["scalarFields1"]["cosGrantTmpSecretId"] = "FRESH_SECRET_ID"
    params["scalarFields2"]["cosGrantTmpSecretKey"] = "FRESH_SECRET_KEY"
    duplicate = invoke(handler, service_id, params)
    complete_reply(duplicate)

    assert len(dispatched) == 2
    assert dispatched[0][0] == decoded["commandUid"]
    assert dispatched[1][0] == decoded["commandUid"]
    assert dispatched[0][2]["cosGrant"]["tmpSecretId"] == (
        "TMP_SECRET_ID_7"
    )
    assert dispatched[1][2]["cosGrant"]["tmpSecretId"] == (
        "FRESH_SECRET_ID"
    )
    assert [first.data["receiptState"], duplicate.data["receiptState"]] == [
        1,
        2,
    ]
    store.close()


def test_duplicate_factory_seal_requeues_only_repairable_failed_command(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(store)
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    service_id, params, decoded = load_command(
        "authorize-factory-seal.service-wire.json"
    )

    response = invoke(handler, service_id, params)
    assert dispatched == []
    complete_reply(response)
    assert len(dispatched) == 1
    assert store.claim_next_command()["command_uid"] == decoded[
        "commandUid"
    ]
    assert store.fail_factory_seal_command_for_retry(
        decoded["commandUid"],
        "FACTORY_REPORT_INVALID",
    )

    response = invoke(handler, service_id, params)
    complete_reply(response)
    assert len(dispatched) == 2
    assert store.get_command(decoded["commandUid"])["state"] == "PENDING"

    assert store.claim_next_command()["command_uid"] == decoded[
        "commandUid"
    ]
    assert store.complete_command(decoded["commandUid"])
    response = invoke(handler, service_id, params)
    complete_reply(response)
    assert len(dispatched) == 2
    assert store.get_command(decoded["commandUid"])["state"] == "COMPLETED"

    terminal_params = copy.deepcopy(params)
    terminal_params["commandUid"] = str(uuid.uuid4())
    terminal_command = decode_service_command(
        service_id,
        terminal_params,
    )
    response = invoke(handler, service_id, terminal_params)
    complete_reply(response)
    assert len(dispatched) == 3
    assert store.claim_next_command()["command_uid"] == terminal_command[
        "commandUid"
    ]
    assert store.reject_factory_seal_command(
        terminal_command,
        "ACCEPTANCE_EVIDENCE_MISMATCH",
    )

    response = invoke(handler, service_id, terminal_params)
    complete_reply(response)
    assert len(dispatched) == 3
    assert store.get_command(terminal_command["commandUid"])[
        "state"
    ] == "REJECTED"
    store.close()


def test_expired_first_factory_seal_delivery_is_rejected_without_persisting(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(store)
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    now = datetime.now(timezone.utc)
    service_id, params, decoded = load_command(
        "authorize-factory-seal.service-wire.json",
        issued_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
    )

    response = invoke(
        handler,
        service_id,
        params,
        request_id="expired-first-seal",
    )
    complete_reply(response)

    assert dispatched == []
    assert store.get_command(decoded["commandUid"]) is None
    assert response.data["receiptState"] == 3
    assert response.data["errorCode"] == "BAD_COMMAND"
    store.close()


def test_expired_factory_seal_duplicate_uses_original_persisted_receipt(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(store)
    dispatched = []
    handler.on_command_received = (
        lambda *args: dispatched.append(args) or True
    )
    now = datetime.now(timezone.utc)
    service_id, params, command = load_command(
        "authorize-factory-seal.service-wire.json",
        issued_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(minutes=1),
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    with store.transaction():
        store._conn.execute(
            "UPDATE command_inbox SET received_at=? WHERE command_uid=?",
            (
                utc_text(now - timedelta(seconds=90)),
                command["commandUid"],
            ),
        )
    assert store.claim_next_command()["command_uid"] == command[
        "commandUid"
    ]
    assert store.fail_factory_seal_command_for_retry(
        command["commandUid"],
        "FACTORY_REPORT_INVALID",
    )

    response = invoke(
        handler,
        service_id,
        params,
        request_id="expired-duplicate-seal",
    )
    assert dispatched == []
    complete_reply(response)

    assert len(dispatched) == 1
    assert dispatched[0][0] == command["commandUid"]
    assert store.get_command(command["commandUid"])["state"] == "PENDING"
    assert response.data["receiptState"] == 2
    assert response.data["errorCode"] == ""
    store.close()


def test_accepted_business_confirmation_notifies_only_after_reply(
    tmp_path,
):
    store = make_store(tmp_path)
    handler = make_handler(store)
    handler.on_command_received = None
    reliable_count_changes = []
    outbox_wakes = []
    handler.on_reliable_event_count_changed = (
        lambda: reliable_count_changes.append("changed")
    )
    handler.on_outbox_wakeup = lambda: outbox_wakes.append("wake")
    service_id, params, _decoded = load_command(
        "confirm-edge-event.service-wire.json"
    )
    original_event_uid = params["scalarFields"]["originalEventUid"]
    store.create_edge_event(
        original_event_uid,
        "DELIVERY_COMPLETE",
        {"workUid": "delivery-work-1"},
        work_uid="delivery-work-1",
        device_name=DEVICE_NAME,
        target_type="DELIVERY_SESSION",
        target_uid="delivery-work-1",
    )
    stored_event = json.loads(
        store.get_event(original_event_uid)["payload_json"]
    )
    params["scalarFields"]["originalPayloadSha256"] = stored_event[
        "payloadSha256"
    ]
    decoded = decode_service_command(service_id, params)
    params["scalarFields"]["payloadSha256"] = (
        canonical_payload_sha256(decoded["payload"])
    )
    assert store.count_pending_reliable_events() == 1

    accepted = invoke(
        handler,
        service_id,
        params,
        request_id="request-confirmation",
    )
    assert reliable_count_changes == []
    assert outbox_wakes == []
    complete_reply(accepted)

    duplicate = invoke(
        handler,
        service_id,
        params,
        request_id="request-confirmation-duplicate",
    )
    assert reliable_count_changes == ["changed"]
    assert outbox_wakes == ["wake"]
    complete_reply(duplicate)

    assert store.count_pending_reliable_events() == 0
    assert reliable_count_changes == ["changed"]
    assert outbox_wakes == ["wake", "wake"]
    assert [
        accepted.data["receiptState"],
        duplicate.data["receiptState"],
    ] == [1, 2]
    store.close()
