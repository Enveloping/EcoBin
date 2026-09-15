import hashlib
import json
import threading
from types import SimpleNamespace
import uuid

from command_processor import CommandProcessor
from device_acceptance import DeviceAcceptanceRunner
from edge_store import EdgeStore
from mcu_session import McuCommandDispatcher
from native_business_runtime import NativeBusinessRuntime
from native_device_entry_url import enrolled_source
from onenet_wire import canonical_payload_sha256, encode_event_post
import uart2_protocol as uart


DEVICE_NAME = "device-qr-native-1"
URL = "https://www.jinshoubao.com/device-entry/native-qr-1"
URL_SHA256 = hashlib.sha256(URL.encode("ascii")).hexdigest()


def command(command_type="SYNC_DEVICE_ENTRY_URL"):
    payload = {
        "deviceEntryUrl": URL,
        "deviceEntryUrlSha256": URL_SHA256,
    }
    if command_type == "REQUEST_DEVICE_ACCEPTANCE":
        payload |= {
            "challengeUid": str(uuid.uuid4()),
            "expectedPortCount": 1,
            "factoryBagRevision": 1,
            "factoryBagSetSha256": "a" * 64,
        }
    return {
        "schemaVersion": 2,
        "commandUid": str(uuid.uuid4()),
        "commandType": command_type,
        "targetDeviceName": DEVICE_NAME,
        "target": {"type": "DEVICE_ASSET", "uid": DEVICE_NAME},
        "issuedAt": "2030-01-01T00:00:00.000Z",
        "expiresAt": "2030-01-01T00:05:00.000Z",
        "payloadSchemaVersion": 2,
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
        "cosGrant": None,
    }


def owner(store, boot_id, sent, *, enrolled_url=False):
    runtime = NativeBusinessRuntime.__new__(NativeBusinessRuntime)
    runtime.store = store
    runtime.device_name = DEVICE_NAME
    runtime.clock = lambda: 0
    runtime.boot = SimpleNamespace(
        current_boot=lambda now: boot_id,
        current_boot_window=lambda now: (boot_id, 1_000),
    )
    runtime._identity_boot_id = boot_id
    runtime.verified_firmware_identity = {"firmwareIdentityHex": "0123456789abcdef"}
    runtime._mcu_firmware_version = "1.0.1-hil.4"
    runtime._mcu_capability = 0
    runtime._runtime_observation = {
        "observedMonotonicMs": 0,
        "mcuBootId": boot_id,
        "currentMcuBootId": boot_id,
        "currentMcuBootValidUntilMs": 1_000,
        "mcuSessionReady": True,
        "deviceFactsValidUntilMs": 0,
        "freshCommunicationConfirmed": False,
        "freshCommunicationValidUntilMs": 0,
        "mcuCapability": 0,
        "mcuFirmwareVersion": runtime._mcu_firmware_version,
        "mcuFirmwareIdentity": dict(runtime.verified_firmware_identity),
        "deviceFacts": None,
    }
    runtime.safety = SimpleNamespace(get_mcu_maintenance_status=lambda: None)
    runtime._dispatch_authority = None
    runtime._device_entry_url_link_refresh_pending = True
    runtime._enrolled_device_entry_url = (
        enrolled_source(DEVICE_NAME, URL)
        if enrolled_url
        else None
    )
    runtime.dispatcher = McuCommandDispatcher(
        store,
        runtime.boot,
        lambda frame: sent.append(frame) or len(frame),
        arm=runtime._arm,
        clock=runtime.clock,
    )
    return runtime


class AcceptanceProbe:
    def __init__(self):
        self.progress = []
        self.commands = []

    def report_progress(self, *values):
        self.progress.append(values)

    def run(self, command):
        self.commands.append(command)


def test_native_session_readiness_uses_only_foreground_snapshot(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    runtime = NativeBusinessRuntime.__new__(NativeBusinessRuntime)
    runtime.store = store
    runtime._port = SimpleNamespace(is_open=True)
    now = [100]
    runtime.clock = lambda: now[0]
    runtime.boot = SimpleNamespace(current_boot=lambda observed: (_ for _ in ()).throw(
        AssertionError("a read-only observer must not inspect the live UART session")
    ))
    runtime._mcu_boot_id = 42
    runtime._identity_boot_id = 0
    runtime.verified_firmware_identity = None
    runtime._mcu_firmware_version = ""
    runtime._runtime_observation = {
        "observedMonotonicMs": 100,
        "mcuBootId": 42,
        "currentMcuBootId": 42,
        "currentMcuBootValidUntilMs": 1_000,
        "mcuSessionReady": False,
        "deviceFactsValidUntilMs": 100,
        "freshCommunicationConfirmed": False,
        "freshCommunicationValidUntilMs": 100,
        "mcuCapability": 7,
        "mcuFirmwareVersion": "",
        "mcuFirmwareIdentity": None,
        "deviceFacts": None,
    }
    assert runtime.current_mcu_boot_id == 42
    assert not runtime.mcu_session_ready
    assert runtime.communication_fault_status()[
        "freshCommunicationConfirmed"
    ] is False
    runtime._runtime_observation = {
        **runtime._runtime_observation,
        "mcuSessionReady": True,
        "deviceFactsValidUntilMs": 851,
        "freshCommunicationConfirmed": True,
        "freshCommunicationValidUntilMs": 851,
        "mcuFirmwareVersion": "1.0.1-hil.4",
        "mcuFirmwareIdentity": {"firmwareIdentityHex": "0123456789abcdef"},
        "deviceFacts": {"status": "AVAILABLE", "currentMcuBootId": 42},
    }
    assert runtime.mcu_session_ready
    assert runtime.current_device_facts() == {
        "status": "AVAILABLE",
        "currentMcuBootId": 42,
    }
    assert runtime.communication_fault_status() == {
        "reasonCode": None,
        "faultUid": None,
        "mcuBootId": 42,
        "freshCommunicationConfirmed": True,
        "activeWorkUid": None,
        "manualRecoveryEligible": False,
    }
    assert runtime.current_runtime_observation()["mcuBootId"] == 42
    assert runtime.current_runtime_observation()["mcuCapability"] == 7
    assert set(runtime.current_runtime_observation()) == {
        "mcuBootId",
        "mcuCapability",
        "mcuFirmwareVersion",
        "mcuFirmwareIdentity",
        "deviceFacts",
        "uartState",
    }
    now[0] = 1_000
    assert runtime.current_mcu_boot_id == 0
    assert not runtime.mcu_session_ready
    assert runtime.current_device_facts() is None
    assert runtime.communication_fault_status()[
        "freshCommunicationConfirmed"
    ] is False
    expired = runtime.current_runtime_observation()
    assert expired["mcuBootId"] == 0
    assert expired["mcuCapability"] == 0
    assert expired["deviceFacts"] is None
    assert runtime.uart_state == "STARTING"
    store.close()


def test_background_status_cannot_advance_foreground_session_clock(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    runtime = owner(store, 42, sent)
    runtime._port = SimpleNamespace(is_open=True)

    class StrictBoot:
        def __init__(self):
            self.last_now = -1

        def current_boot(self, now):
            if now < self.last_now:
                raise ValueError(
                    "session clock must be non-negative monotonic milliseconds"
                )
            self.last_now = now
            return 42

        def current_boot_window(self, now):
            return self.current_boot(now), 1_000

    runtime.boot = StrictBoot()
    runtime.clock = lambda: 101
    runtime._runtime_observation = {
        **runtime._runtime_observation,
        "observedMonotonicMs": 100,
        "currentMcuBootId": 42,
        "currentMcuBootValidUntilMs": 1_000,
        "mcuSessionReady": True,
        "freshCommunicationConfirmed": True,
        "freshCommunicationValidUntilMs": 851,
    }
    foreground_inside_store = threading.Event()
    resume_foreground = threading.Event()
    original_list = store.list_native_device_entry_url_applications

    def pause_between_same_timestamp_boot_reads():
        foreground_inside_store.set()
        assert resume_foreground.wait(2)
        return original_list()

    store.list_native_device_entry_url_applications = (
        pause_between_same_timestamp_boot_reads
    )
    errors = []
    before_slot = store.get_work_slot()

    def foreground_poll_fragment():
        try:
            runtime._device_entry_url_poll(100)
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    foreground = threading.Thread(target=foreground_poll_fragment)
    foreground.start()
    assert foreground_inside_store.wait(2)
    assert runtime.uart_state == "READY"
    resume_foreground.set()
    foreground.join(2)

    assert not foreground.is_alive()
    assert errors == []
    assert runtime.boot.last_now == 100
    assert store.get_work_slot() == before_slot
    assert store.list_native_commands() == []
    store.close()


def accept_last_command(store, runtime, sent, boot_id):
    decoded = uart.decode_frame(sent[-1], sender_role="EDGE")
    values = uart.decode_payload(decoded["messageName"], decoded["payload"])
    reply_values = {
        key: values[key]
        for key in (
            "mcuCommandUid",
            "commandDigestSha256",
            "targetMcuBootId",
            "commandSequence",
        )
    } | {
        "currentMcuBootId": boot_id,
        "outcome": "ACCEPTED",
        "errorCode": "NONE",
    }
    frame = uart.encode_frame(
        "COMMAND_DECISION",
        values["commandSequence"],
        uart.encode_payload("COMMAND_DECISION", reply_values),
    )
    assert runtime.dispatcher.accept_frame(frame, 0)
    return values


def finish_application(
    store,
    runtime,
    sent,
    boot_id,
    event_sequence,
    *,
    status="APPLIED",
    error_code="NONE",
):
    application = store.list_native_device_entry_url_applications()
    journal = (
        application[0]["journal"]
        if application
        else store.get_native_device_entry_url_reload()
    )
    while True:
        before = len(sent)
        runtime._device_entry_url_poll(0)
        if len(sent) == before:
            break
        accept_last_command(store, runtime, sent, boot_id)
    attempt = journal["attempt"]
    commit = store.get_native_command(attempt["commandUids"][-1])
    assert commit["decision_outcome"] == "ACCEPTED"
    result = uart.encode_payload(
        "DEVICE_ENTRY_URL_APPLY_RESULT",
        {
            "mcuBootId": boot_id,
            "mcuEventSequence": event_sequence,
            "uptimeMs": 100,
            "mcuCommandUid": attempt["commandUids"][-1],
            "applicationUid": attempt["applicationUid"],
            "urlLength": len(URL),
            "urlSha256": URL_SHA256,
            "status": status,
            "errorCode": error_code,
        },
    )
    assert runtime._accept_device_entry_url_result(result)
    return journal


def test_sync_waits_for_apply_result_and_uart_reconnect_builds_reload(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    sent = []
    runtime = owner(store, boot_id, sent)
    cloud = command()
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"

    assert CommandProcessor(store, runtime).process_next()
    assert store.get_command(cloud["commandUid"])["state"] == "WAITING_MCU_RESULT"
    assert store.get_native_device_entry_url_applied_evidence() is None

    first = finish_application(store, runtime, sent, boot_id, 1)
    completed = store.get_command(cloud["commandUid"])
    assert completed["state"] == "COMPLETED"
    evidence = store.get_native_device_entry_url_applied_evidence()
    assert evidence == {
        "deviceEntryUrlSha256": URL_SHA256,
        "applicationUid": first["attempt"]["applicationUid"],
        "mcuCommandUid": first["attempt"]["commandUids"][-1],
        "mcuBootId": boot_id,
        "mcuEventSequence": 1,
        "status": "APPLIED",
        "faultCode": None,
        "basis": "UART3_COMMAND_ATOMICALLY_QUEUED",
    }
    result = store.get_native_device_entry_url_application_result(
        cloud["commandUid"]
    )
    assert result["commandUid"] == cloud["commandUid"]
    assert result["mcuCommandUid"] == first["attempt"]["commandUids"][-1]
    assert result["displayBasis"] == "UART3_COMMAND_ATOMICALLY_QUEUED"
    assert result["faultCode"] is None
    events = [
        row
        for row in store.list_pending_events(100)
        if row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
    ]
    assert len(events) == 1
    event = json.loads(events[0]["payload_json"])
    assert event["commandUid"] == cloud["commandUid"]
    assert event["payload"] == {
        "applicationUid": first["attempt"]["applicationUid"],
        "status": "APPLIED",
        "deviceEntryUrlSha256": URL_SHA256,
        "mcuCommandUid": first["attempt"]["commandUids"][-1],
        "mcuBootId": boot_id,
        "displayBasis": "UART3_COMMAND_ATOMICALLY_QUEUED",
        "faultCode": None,
    }
    assert encode_event_post("DEVICE_ENTRY_URL_APPLICATION_RESULT", event)
    event_uid = event["eventUid"]
    store.close()
    store.initialize()
    assert store.get_event(event_uid)["state"] == "PENDING"

    # A fresh UART owner must not treat the old link's evidence as proof that
    # the QR command traversed this connection. The original cloud command
    # stays completed while a distinct local reload attempt is persisted.
    reconnect_sent = []
    reconnected = owner(store, boot_id, reconnect_sent)
    reconnected._device_entry_url_poll(0)
    reload = store.get_native_device_entry_url_reload()
    assert reload["state"] == "WAITING"
    assert store.get_native_device_entry_url_applied_evidence() is None
    assert reload["sourceCommandUid"] == cloud["commandUid"]
    assert reload["attempt"]["applicationUid"] != first["attempt"]["applicationUid"]
    assert uart.decode_frame(
        reconnect_sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    assert store.get_command(cloud["commandUid"])["state"] == "COMPLETED"


def test_enrolled_url_is_applied_without_a_cloud_sync_command(tmp_path):
    """The encrypted enrollment result is enough to start the UART write."""

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    assert store.save_device_entry_url(
        URL,
        URL_SHA256,
        "2030-01-01T00:00:00.000Z",
    )["disposition"] == "SAVED"
    sent = []
    runtime = owner(store, boot_id, sent, enrolled_url=True)

    runtime._device_entry_url_poll(0)

    assert uart.decode_frame(
        sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    reload = store.get_native_device_entry_url_reload()
    assert reload["continuation"] == "LOCAL_RELOAD"
    assert reload["sourceCommandUid"] \
        == runtime._enrolled_device_entry_url["sourceUid"]
    assert store.get_command(reload["sourceCommandUid"]) is None
    accept_last_command(store, runtime, sent, boot_id)

    finish_application(store, runtime, sent, boot_id, 1)

    evidence = store.get_native_device_entry_url_applied_evidence()
    assert evidence["deviceEntryUrlSha256"] == URL_SHA256
    assert evidence["mcuBootId"] == boot_id
    assert not any(
        row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
        for row in store.list_pending_events(100)
    )
    store.close()


def test_enrolled_url_reloads_after_uart_reconnect_without_cloud_command(
    tmp_path,
):
    """A new UART owner must write the enrolled URL on the new link."""

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    store.save_device_entry_url(
        URL,
        URL_SHA256,
        "2030-01-01T00:00:00.000Z",
    )
    first_sent = []
    first = owner(store, boot_id, first_sent, enrolled_url=True)
    first._device_entry_url_poll(0)
    accept_last_command(store, first, first_sent, boot_id)
    first_journal = finish_application(
        store,
        first,
        first_sent,
        boot_id,
        1,
    )
    assert store.get_native_device_entry_url_applied_evidence() is not None

    second_sent = []
    second = owner(store, boot_id, second_sent, enrolled_url=True)
    second._device_entry_url_poll(0)

    reload = store.get_native_device_entry_url_reload()
    assert reload["state"] == "WAITING"
    assert reload["sourceCommandUid"] \
        == second._enrolled_device_entry_url["sourceUid"]
    assert reload["attempt"]["applicationUid"] \
        != first_journal["attempt"]["applicationUid"]
    assert store.get_native_device_entry_url_applied_evidence() is None
    assert uart.decode_frame(
        second_sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    store.close()


def test_enrolled_url_reloads_after_mcu_restart_without_cloud_command(
    tmp_path,
):
    """The enrollment authority remains usable after MCU RAM is reset."""

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    first_boot = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(first_boot)
    store.save_device_entry_url(
        URL,
        URL_SHA256,
        "2030-01-01T00:00:00.000Z",
    )
    first_sent = []
    first = owner(store, first_boot, first_sent, enrolled_url=True)
    first._device_entry_url_poll(0)
    accept_last_command(store, first, first_sent, first_boot)
    finish_application(store, first, first_sent, first_boot, 1)

    second_boot = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert second_boot > first_boot
    assert store.recognize_native_boot_id(second_boot)
    second_sent = []
    second = owner(store, second_boot, second_sent, enrolled_url=True)
    second._device_entry_url_poll(0)

    reload = store.get_native_device_entry_url_reload()
    assert reload["state"] == "WAITING"
    assert reload["sourceCommandUid"] \
        == second._enrolled_device_entry_url["sourceUid"]
    assert reload["attempt"]["targetMcuBootId"] == second_boot
    assert store.get_native_device_entry_url_applied_evidence() is None
    assert uart.decode_frame(
        second_sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    store.close()


def test_enrolled_url_waits_until_the_device_is_idle(tmp_path, monkeypatch):
    """Registration does not bypass the existing business-idle guard."""

    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    store.save_device_entry_url(
        URL,
        URL_SHA256,
        "2030-01-01T00:00:00.000Z",
    )
    sent = []
    runtime = owner(store, boot_id, sent, enrolled_url=True)
    original_get_work_slot = store.get_work_slot
    monkeypatch.setattr(
        store,
        "get_work_slot",
        lambda: {"workUid": "busy-delivery"},
    )

    runtime._device_entry_url_poll(0)

    assert sent == []
    assert store.get_native_device_entry_url_reload() is None

    monkeypatch.setattr(store, "get_work_slot", original_get_work_slot)
    runtime._device_entry_url_poll(0)

    assert uart.decode_frame(
        sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    store.close()


def test_a_different_stored_url_does_not_borrow_enrollment_authority(
    tmp_path,
):
    """A failed remote change cannot masquerade as the enrolled URL."""

    changed_url = "https://www.jinshoubao.com/device-entry/remote-change"
    changed_digest = hashlib.sha256(changed_url.encode("ascii")).hexdigest()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    store.save_device_entry_url(
        changed_url,
        changed_digest,
        "2030-01-01T00:00:00.000Z",
    )
    sent = []
    runtime = owner(store, boot_id, sent, enrolled_url=True)

    runtime._device_entry_url_poll(0)

    assert sent == []
    assert store.get_native_device_entry_url_reload() is None
    store.close()


def test_restart_queries_the_original_commit_before_any_new_attempt(tmp_path):
    database = tmp_path / "edge.db"
    store = EdgeStore(str(database))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    cloud = command()
    sent = []
    runtime = owner(store, boot_id, sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    assert CommandProcessor(store, runtime).process_next()

    application = store.list_native_device_entry_url_applications()[0]
    attempt = application["journal"]["attempt"]
    for _ in attempt["commandUids"]:
        before = len(sent)
        runtime._device_entry_url_poll(0)
        assert len(sent) > before
        accept_last_command(store, runtime, sent, boot_id)
    original_commit_uid = attempt["commandUids"][-1]
    original_application_uid = attempt["applicationUid"]
    assert store.get_native_command(original_commit_uid)[
        "decision_outcome"
    ] == "ACCEPTED"
    store.close()

    store = EdgeStore(str(database))
    store.initialize()
    assert store.recover_native_device_entry_url_commands() == {
        "sync_requeued": 0,
        "acceptance_grant_lost": 0,
    }
    restarted_sent = []
    restarted = owner(store, boot_id, restarted_sent)
    restarted._device_entry_url_poll(1_000)
    query = uart.decode_frame(restarted_sent[-1], sender_role="EDGE")
    assert query["messageName"] == "QUERY_COMMAND"
    query_values = uart.decode_payload("QUERY_COMMAND", query["payload"])
    assert query_values["mcuCommandUid"] == original_commit_uid
    resumed = store.list_native_device_entry_url_applications()[0]["journal"]
    assert resumed["attempt"]["applicationUid"] == original_application_uid
    assert (
        store.get_command(cloud["commandUid"])["state"]
        == "WAITING_MCU_RESULT"
    )
    store.close()


def test_acceptance_is_requeued_only_after_applied_result(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    sent = []
    runtime = owner(store, boot_id, sent)
    cloud = command("REQUEST_DEVICE_ACCEPTANCE")
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    claimed = store.claim_next_command()
    stored = store.save_device_entry_url(URL, URL_SHA256, cloud["issuedAt"])
    pending = runtime.send_device_entry_url(
        URL,
        command=claimed["payload"],
        stored_record={key: stored[key] for key in (
            "deviceEntryUrl", "deviceEntryUrlSha256", "issuedAt"
        )},
    )
    assert pending["native_pending"] is True
    assert store.get_command(cloud["commandUid"])["state"] == "WAITING_MCU_RESULT"

    finish_application(store, runtime, sent, boot_id, 2)
    resumed = store.get_command(cloud["commandUid"])
    assert resumed["state"] == "PENDING"
    assert resumed["result"]["deviceEntryUrlApplication"]["state"] == "APPLIED"
    assert store.claim_next_command()["command_uid"] == cloud["commandUid"]
    replay = runtime.send_device_entry_url(
        URL,
        command=cloud,
        stored_record=store.get_device_entry_url(),
    )
    assert replay["applied"] is True
    assert not any(
        row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
        for row in store.list_pending_events(100)
    )
    store.close()


def test_native_acceptance_v5_keeps_url_proof_after_final_event(
    tmp_path,
    monkeypatch,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    sent = []
    runtime = owner(store, boot_id, sent)
    cloud = command("REQUEST_DEVICE_ACCEPTANCE")
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    claimed = store.claim_next_command()
    stored = store.save_device_entry_url(URL, URL_SHA256, cloud["issuedAt"])
    assert runtime.send_device_entry_url(
        URL,
        command=claimed["payload"],
        stored_record={
            key: stored[key]
            for key in (
                "deviceEntryUrl",
                "deviceEntryUrlSha256",
                "issuedAt",
            )
        },
    )["native_pending"] is True
    finish_application(store, runtime, sent, boot_id, 2)
    raw_result = store.get_native_device_entry_url_application_result(
        cloud["commandUid"]
    )["rawPayloadHex"]
    assert store.claim_next_command()["command_uid"] == cloud["commandUid"]

    runtime._port = SimpleNamespace(is_open=True)
    runtime._mcu_boot_id = boot_id
    runtime._facts_requested_at = 0
    runtime._facts = {
        "status": "AVAILABLE",
        "portNo": 1,
        "currentMcuBootId": boot_id,
        "capturedUptimeMs": 100,
        "appliedConfigVersion": 1,
        "configStaging": False,
        "scaleReadStatus": "VALID",
        "scaleCapturedUptimeMs": 100,
        "scaleWeightGrams": 0,
        "smokeObservationState": "NORMAL",
        "smokeObservedUptimeMs": 100,
        "fullnessObservationKind": "ULTRASONIC",
        "fullnessReadStatus": "VALID",
        "fullnessCapturedUptimeMs": 100,
    }
    runtime.transport = SimpleNamespace(
        requests=SimpleNamespace(last_matched_ms=0)
    )
    runtime.timeout_ms = 10_000
    runtime._refresh_runtime_observation(0)
    assert runtime.mcu_session_ready
    assert runtime.current_mcu_boot_id == boot_id
    runner = DeviceAcceptanceRunner(
        store,
        runtime,
        None,
        None,
        device_name=DEVICE_NAME,
        mcu_remote_update_capable=True,
    )
    runner._camera_evidence = lambda challenge, grant: {
        "captureHealthy": True,
        "uploadHealthy": True,
        "camerasSimulated": False,
        "verifiedCameraCount": 2,
        "captureSha256": "2" * 64,
        "uploadSha256": "3" * 64,
    }
    monkeypatch.setattr("device_acceptance._clock_state", lambda: "SYNCED")
    runner.run({**cloud, "cosGrant": {"test": "memory-only"}})

    completed = store.get_command(cloud["commandUid"])
    assert completed["state"] == "COMPLETED"
    assert completed["result"]["deviceEntryUrlApplication"]["state"] \
        == "APPLIED"
    assert store.get_native_device_entry_url_application_result(
        cloud["commandUid"]
    )["rawPayloadHex"] == raw_result
    acceptance_rows = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(100)
        if row["event_type"] == "DEVICE_ACCEPTANCE_EVIDENCE"
    ]
    assert len(acceptance_rows) == 1
    evidence = acceptance_rows[0]["payload"]
    assert evidence["evidenceSchemaVersion"] == 5
    assert evidence["deviceEntryUrlMcuApplied"] is True
    assert evidence["deviceEntryUrlAppliedSha256"] == URL_SHA256
    assert evidence["deviceEntryUrlAppliedMcuBootId"] == boot_id
    assert evidence["deviceEntryUrlDisplayBasis"] \
        == "UART3_COMMAND_ATOMICALLY_QUEUED"
    assert encode_event_post("DEVICE_ACCEPTANCE_EVIDENCE", acceptance_rows[0])
    assert not any(
        row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
        for row in store.list_pending_events(100)
    )
    store.close()


def test_acceptance_waits_for_same_boot_reconnect_reload_before_running(
    tmp_path,
):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    cloud = command("REQUEST_DEVICE_ACCEPTANCE")
    first_sent = []
    first = owner(store, boot_id, first_sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    claimed = store.claim_next_command()
    stored = store.save_device_entry_url(URL, URL_SHA256, cloud["issuedAt"])
    assert first.send_device_entry_url(
        URL,
        command=claimed["payload"],
        stored_record=stored,
    )["native_pending"]
    finish_application(store, first, first_sent, boot_id, 2)
    assert store.get_command(cloud["commandUid"])["state"] == "PENDING"
    old_result = bytes.fromhex(
        store.get_native_device_entry_url_application_result(
            cloud["commandUid"]
        )["rawPayloadHex"]
    )

    # A new Pi process owns a new UART connection even though the MCU boot
    # number is unchanged. Starting LOCAL_RELOAD invalidates the old proof.
    reconnect_sent = []
    reconnected = owner(store, boot_id, reconnect_sent)
    reconnected._device_entry_url_poll(0)
    assert store.get_native_device_entry_url_applied_evidence() is None
    assert store.get_native_device_entry_url_reload()["state"] == "WAITING"

    acceptance = AcceptanceProbe()
    processor = CommandProcessor(
        store,
        reconnected,
        acceptance_runner=acceptance,
    )
    claimed = store.claim_next_command()
    processor._request_device_acceptance(
        {**claimed["payload"], "cosGrant": {"memoryOnly": True}}
    )
    parked = store.get_command(cloud["commandUid"])
    assert parked["state"] == "WAITING_MCU_RESULT"
    assert parked["last_error"] == "WAITING_DEVICE_ENTRY_URL_RELOAD"
    assert acceptance.commands == []
    assert reconnected._accept_device_entry_url_result(old_result)
    assert store.get_command(cloud["commandUid"])["state"] \
        == "WAITING_MCU_RESULT"

    # Only the current connection's terminal APPLIED result resumes the
    # original acceptance. It then observes current proof, never NOT_APPLIED.
    accept_last_command(store, reconnected, reconnect_sent, boot_id)
    finish_application(store, reconnected, reconnect_sent, boot_id, 3)
    assert store.get_command(cloud["commandUid"])["state"] == "PENDING"
    resumed = store.claim_next_command()
    processor._request_device_acceptance(
        {**resumed["payload"], "cosGrant": {"memoryOnly": True}}
    )
    assert len(acceptance.commands) == 1
    assert store.get_native_device_entry_url_applied_evidence()[
        "mcuBootId"
    ] == boot_id
    store.close()


def test_acceptance_before_boot_bind_is_durably_parked_not_left_processing(
    tmp_path,
):
    database = tmp_path / "edge.db"
    store = EdgeStore(str(database))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    cloud = command("REQUEST_DEVICE_ACCEPTANCE")
    first_sent = []
    first = owner(store, boot_id, first_sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    claimed = store.claim_next_command()
    stored = store.save_device_entry_url(URL, URL_SHA256, cloud["issuedAt"])
    assert first.send_device_entry_url(
        URL,
        command=claimed["payload"],
        stored_record=stored,
    )["native_pending"]
    finish_application(store, first, first_sent, boot_id, 2)

    unbound = owner(store, None, [])
    acceptance = AcceptanceProbe()
    processor = CommandProcessor(
        store,
        unbound,
        acceptance_runner=acceptance,
    )
    claimed = store.claim_next_command()
    processor._request_device_acceptance(
        {**claimed["payload"], "cosGrant": {"memoryOnly": True}}
    )
    parked = store.get_command(cloud["commandUid"])
    assert parked["state"] == "WAITING_MCU_RESULT"
    assert parked["last_error"] == "WAITING_DEVICE_ENTRY_URL_RELOAD"
    assert acceptance.commands == []

    # If the process dies before boot/reload becomes ready, the in-memory COS
    # grant is deliberately lost and the command asks the backend for a fresh
    # grant instead of remaining forever in generic PROCESSING.
    store.close()
    store = EdgeStore(str(database))
    store.initialize()
    assert store.recover_native_device_entry_url_commands() == {
        "sync_requeued": 0,
        "acceptance_grant_lost": 1,
    }
    failed = store.get_command(cloud["commandUid"])
    assert failed["state"] == "FAILED"
    assert failed["last_error"] == "ACCEPTANCE_GRANT_NOT_AVAILABLE"
    store.close()


def test_new_mcu_boot_uses_new_attempt_without_reopening_completed_sync(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    first_boot = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(first_boot)
    cloud = command()
    sent = []
    first_owner = owner(store, first_boot, sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    assert CommandProcessor(store, first_owner).process_next()
    first = finish_application(store, first_owner, sent, first_boot, 1)
    assert store.get_command(cloud["commandUid"])["state"] == "COMPLETED"

    second_boot = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert second_boot > first_boot
    assert store.recognize_native_boot_id(second_boot)
    second_sent = []
    second_owner = owner(store, second_boot, second_sent)
    second_owner._device_entry_url_poll(0)
    reload = store.get_native_device_entry_url_reload()
    assert reload["attempt"]["targetMcuBootId"] == second_boot
    assert reload["attempt"]["applicationUid"] != first["attempt"]["applicationUid"]
    assert store.get_native_device_entry_url_applied_evidence() is None

    accept_last_command(store, second_owner, second_sent, second_boot)
    finish_application(store, second_owner, second_sent, second_boot, 1)
    assert store.get_native_device_entry_url_applied_evidence()["mcuBootId"] == second_boot
    assert store.get_command(cloud["commandUid"])["state"] == "COMPLETED"
    assert sum(
        row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
        for row in store.list_pending_events(100)
    ) == 1
    store.close()


def test_failed_link_reload_invalidates_old_proof_until_a_new_reconnect(
        tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    cloud = command()
    first_sent = []
    first = owner(store, boot_id, first_sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    assert CommandProcessor(store, first).process_next()
    finish_application(store, first, first_sent, boot_id, 1)
    assert store.get_native_device_entry_url_applied_evidence() is not None

    reload_sent = []
    reloaded = owner(store, boot_id, reload_sent)
    reloaded._device_entry_url_poll(0)
    assert store.get_native_device_entry_url_applied_evidence() is None
    accept_last_command(store, reloaded, reload_sent, boot_id)
    failed_attempt = finish_application(
        store,
        reloaded,
        reload_sent,
        boot_id,
        2,
        status="FAILED",
        error_code="BUSY",
    )
    assert store.get_native_device_entry_url_reload()["state"] == "FAILED"
    assert store.get_native_device_entry_url_applied_evidence() is None
    sent_after_failure = len(reload_sent)
    reloaded._device_entry_url_poll(0)
    assert len(reload_sent) == sent_after_failure

    reconnect_sent = []
    reconnected = owner(store, boot_id, reconnect_sent)
    reconnected._device_entry_url_poll(0)
    retry = store.get_native_device_entry_url_reload()
    assert retry["state"] == "WAITING"
    assert retry["attempt"]["applicationUid"] \
        != failed_attempt["attempt"]["applicationUid"]
    assert uart.decode_frame(
        reconnect_sent[0], sender_role="EDGE"
    )["messageName"] == "DEVICE_ENTRY_URL_BEGIN"
    store.close()


def test_failed_apply_result_is_persisted_before_sync_fails(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    boot_id = store.reserve_native_boot_id(store.reserve_native_query_id())
    assert store.recognize_native_boot_id(boot_id)
    cloud = command()
    sent = []
    runtime = owner(store, boot_id, sent)
    assert store.receive_command(
        cloud["commandUid"], cloud["commandType"], cloud
    ) == "ACCEPTED"
    assert CommandProcessor(store, runtime).process_next()

    finish_application(
        store,
        runtime,
        sent,
        boot_id,
        1,
        status="FAILED",
        error_code="BUSY",
    )
    failed = store.get_command(cloud["commandUid"])
    assert failed["state"] == "FAILED"
    assert failed["last_error"] == "MCU_DEVICE_ENTRY_URL_BUSY"
    result = store.get_native_device_entry_url_application_result(
        cloud["commandUid"]
    )
    assert result["commandUid"] == cloud["commandUid"]
    assert result["mcuCommandUid"] == failed["mcu_command_uid"]
    assert result["status"] == "FAILED"
    assert result["displayBasis"] == "NOT_APPLIED"
    assert result["faultCode"] == "BUSY"
    assert store.get_native_device_entry_url_applied_evidence() is None
    events = [
        json.loads(row["payload_json"])
        for row in store.list_pending_events(100)
        if row["event_type"] == "DEVICE_ENTRY_URL_APPLICATION_RESULT"
    ]
    assert len(events) == 1
    assert events[0]["commandUid"] == cloud["commandUid"]
    assert events[0]["payload"]["displayBasis"] == "NOT_APPLIED"
    assert events[0]["payload"]["faultCode"] == "BUSY"
    assert encode_event_post(
        "DEVICE_ENTRY_URL_APPLICATION_RESULT",
        events[0],
    )
    store.close()
