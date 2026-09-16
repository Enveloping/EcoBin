"""edge_boot.py -- startup recovery: SQLite -> HELLO -> QUERY_STATE -> reconcile."""
from __future__ import annotations
import json
import logging
import os
import shutil
import time
import uuid as _uuid
from cloud_transport import CloudEvent, CloudTransport
from device_identity import DeviceIdentity
from edge_identity import is_valid_edge_boot_id, new_edge_boot_id
from edge_store import EdgeStore, WORK_TYPE_NONE
from mcu_configuration import NATIVE_PORT_CONSTANTS
from onenet_wire import canonical_payload_sha256
from trusted_clock import sample_clock
logger = logging.getLogger("edge-boot")
NATIVE_FACTS_MAXIMUM_AGE_MS = NATIVE_PORT_CONSTANTS["weightMaximumSampleAgeMs"]


def _verified_firmware_identity(result):
    """Normalize only a successful revision-2 F3 identity observation."""
    if not isinstance(result, dict):
        return None
    identity = result.get("firmwareIdentityHex")
    version = result.get("firmwareVersion")
    version_code = result.get("firmwareVersionCode")
    if (
        result.get("queryStatus") != "OK"
        or result.get("statusCode") != 0
        or result.get("protocolRevision") != 2
        or not isinstance(version, str)
        or len(version) < 5
        or len(version) > 32
        or not isinstance(version_code, int)
        or isinstance(version_code, bool)
        or not 1 <= version_code <= 4_294_967_295
        or not isinstance(identity, str)
        or len(identity) != 16
        or any(character not in "0123456789abcdef" for character in identity)
    ):
        return None
    return {
        "queryStatus": "OK",
        "statusCode": 0,
        "fixedFrameRevision": 2,
        "firmwareVersionCode": version_code,
        "firmwareVersion": version,
        "firmwareIdentityHex": identity,
    }


def _observe_edge_fault(
    store,
    device_identity: DeviceIdentity,
    component,
    fault_code,
    severity,
    detail=None,
):
    try:
        return store.observe_fault_and_create_event(
            device_name=device_identity.device_name,
            component=component,
            fault_code=fault_code,
            severity=severity,
            detail=detail,
        )
    except Exception:
        logger.exception(
            "failed to persist reliable fault: %s/%s",
            component,
            fault_code,
        )
        return "REJECTED"


def _recover_edge_fault(
    store,
    device_identity: DeviceIdentity,
    component,
    fault_code,
    recovery_evidence,
):
    fault = store.get_active_edge_fault(component, fault_code)
    if fault is None:
        return "UNKNOWN"
    return store.recover_fault_and_create_event(
        device_name=device_identity.device_name,
        fault_uid=fault["fault_uid"],
        component=component,
        fault_code=fault_code,
        port_no=fault["port_no"],
        recovery_evidence=recovery_evidence,
    )


def boot_sequence(
    store,
    uart_link,
    device_identity: DeviceIdentity,
):
    """Recover SQLite, UART and MCU local facts without starting networking."""
    if not store.integrity_check():
        logger.critical("BOOT: SQLite integrity FAILED")
        _observe_edge_fault(
            store,
            device_identity,
            "EDGE_STORAGE",
            "EDGE_STORAGE",
            "BLOCK_DEVICE",
            {"reasonCode": "SQLITE_INTEGRITY_FAILED"},
        )
        return {"status": "SAFETY_LOCKED", "reason": "sqlite_integrity_failed"}
    restart_result = store.abort_interrupted_work()
    if restart_result["outcome"] != "NO_ACTIVE_WORK":
        logger.warning(
            "BOOT: previous physical work resolved without replay: %s",
            restart_result,
        )
    boot_id = store.get_edge_boot_id()
    if not is_valid_edge_boot_id(boot_id):
        boot_id = str(new_edge_boot_id())
        store.set_edge_boot_id(boot_id)
        logger.info("BOOT: new edge boot ID: %s", boot_id)
    else:
        logger.info("BOOT: resume edge boot ID: %s", boot_id)
    if not uart_link.open():
        logger.error("BOOT: UART open failed")
        _observe_edge_fault(
            store,
            device_identity,
            "UART",
            "UART_PROTOCOL",
            "BLOCK_DEVICE",
            {"reasonCode": "UART_OPEN_FAILED"},
        )
        return {"status": "SAFETY_LOCKED", "reason": "uart_open_failed"}
    try:
        mcu_info = uart_link.handshake()
        mcu_info["mcu_receive_generation"] = (
            store.begin_mcu_receive_generation(mcu_info["mcu_boot_id"])
        )
        logger.info(
            "BOOT: HELLO complete: mcu_boot=%d receive_generation=%d",
            mcu_info["mcu_boot_id"],
            mcu_info["mcu_receive_generation"],
        )
    except Exception as e:
        logger.error("BOOT: HELLO failed: %s", e)
        _observe_edge_fault(
            store,
            device_identity,
            "UART",
            "UART_PROTOCOL",
            "BLOCK_DEVICE",
            {"reasonCode": "UART_HANDSHAKE_FAILED"},
        )
        uart_link.close()
        return {"status": "SAFETY_LOCKED", "reason": str(e)}
    if getattr(uart_link, "compatibility_mode", False):
        return _boot_fixed_frame_compatibility(
            store,
            uart_link,
            device_identity,
            mcu_info,
        )
    try:
        snapshots = uart_link.query_state(
            on_segment=lambda frame: _persist_and_ack_mcu_frame(
                store, uart_link, frame
            )
        )
        logger.info("BOOT: QUERY_STATE %d segments", len(snapshots))
    except Exception as e:
        logger.error("BOOT: QUERY_STATE failed: %s", e)
        _observe_edge_fault(
            store,
            device_identity,
            "UART",
            "UART_PROTOCOL",
            "BLOCK_DEVICE",
            {"reasonCode": "UART_QUERY_STATE_FAILED"},
        )
        uart_link.close()
        return {"status": "SAFETY_LOCKED", "reason": f"query_state_failed: {e}"}
    snapshot_begin = _snapshot_begin(snapshots)
    if _is_boot_recovery(snapshot_begin):
        try:
            _restore_configuration_after_mcu_restart(
                store,
                uart_link,
                snapshot_begin,
            )
            if (
                restart_result["outcome"]
                != "JOB_SAFETY_RECONCILIATION_REQUIRED"
            ):
                _reconcile_mcu_boot_work(
                    store,
                    uart_link,
                    snapshot_begin,
                )
            else:
                logger.critical(
                    "BOOT: retained permanent job safety context; "
                    "not confirming an empty MCU work state"
                )
        except Exception as error:
            logger.error("BOOT: MCU recovery failed: %s", error)
            store.record_fault(
                "MCU_INTERNAL",
                2048,
                "BLOCK_DEVICE",
                {"reason": str(error)},
            )
            uart_link.close()
            return {
                "status": "SAFETY_LOCKED",
                "reason": f"mcu_recovery_failed: {error}",
            }
    active_work = _extract_active_work(snapshots)
    slot = store.get_work_slot()
    if slot and active_work and str(active_work["work_uid"]) == str(slot["work_uid"]):
        logger.info("BOOT: SQLite and MCU agree, resuming")
    elif slot and not active_work and not _is_boot_recovery(snapshot_begin):
        logger.warning("BOOT: SQLite work exists but MCU is idle")
        store.record_fault(
            "MCU_INTERNAL",
            2048,
            "BLOCK_DEVICE",
            {"work_uid": slot["work_uid"], "reason": "MCU_WORK_MISSING"},
        )
    elif active_work and not slot:
        logger.warning("BOOT: MCU has active work but SQLite has none")
        store.record_fault(
            "MCU_INTERNAL",
            2048,
            "WARNING",
            {"mcu_work": active_work},
        )
    _recover_edge_fault(
        store,
        device_identity,
        "UART",
        "UART_PROTOCOL",
        "BOOT_UART_READY",
    )
    logger.info("BOOT: local recovery complete, READY")
    return {
        "status": "READY",
        "mcu_info": mcu_info,
        "snapshots": snapshots,
        "snapshot_count": len(snapshots),
    }


def _boot_fixed_frame_compatibility(
    store,
    uart_link,
    device_identity: DeviceIdentity,
    mcu_info,
):
    """Boot with the small F0/F1 sensor query, but no MCU work recovery."""
    try:
        self_test = uart_link.query_self_test(
            timeout_ms=3_000,
            on_result=store.save_fixed_frame_self_test,
        )
    except Exception as error:
        logger.error("BOOT: fixed-frame self-test failed: %s", error)
        self_test = {
            "queryStatus": "ADAPTER_ERROR",
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
            "smokeSensorHealth": "PROTOCOL_ERROR",
            "faultCode": "SMOKE_SENSOR",
            "rawFrameHex": None,
        }
        store.save_fixed_frame_self_test(self_test)
    _resend_device_entry_url_to_fixed_frame_mcu(store, uart_link)
    communication_healthy = bool(
        self_test.get("communicationHealthy") is True
    )
    firmware_identity = None
    identity_query = getattr(
        uart_link,
        "query_firmware_identity",
        None,
    )
    if communication_healthy and callable(identity_query):
        try:
            firmware_identity = _verified_firmware_identity(
                identity_query(timeout_ms=3_000)
            )
        except Exception as error:
            logger.warning(
                "BOOT: fixed-frame firmware identity query failed: %s",
                error,
            )
    mcu_info["mcu_firmware_identity"] = firmware_identity
    if firmware_identity is not None:
        mcu_info["mcu_firmware_version"] = firmware_identity[
            "firmwareVersion"
        ]
        mcu_info["mcu_firmware_version_code"] = firmware_identity[
            "firmwareVersionCode"
        ]
        mcu_info["fixed_frame_revision"] = 2
    sensors_healthy = bool(
        communication_healthy
        and self_test.get("queryStatus") == "OK"
        and self_test.get("validFlags") == 3
        and self_test.get("weightValid") is True
        and self_test.get("infraredValid") is True
        and self_test.get("smokeCode") == 0
    )
    if communication_healthy:
        _recover_edge_fault(
            store,
            device_identity,
            "UART",
            "UART_PROTOCOL",
            "FIXED_FRAME_SELF_TEST_SUCCEEDED",
        )
        mcu_info["uart_state"] = "READY"
    else:
        _observe_edge_fault(
            store,
            device_identity,
            "UART",
            "UART_PROTOCOL",
            "BLOCK_DEVICE",
            {"reasonCode": self_test.get("queryStatus", "SELF_TEST_FAILED")},
        )
        mcu_info["uart_state"] = "FAULT"
    status = "READY" if sensors_healthy else "DEGRADED"
    logger.info(
        "BOOT: fixed-frame sensor query complete: status=%s query=%s",
        status,
        self_test.get("queryStatus"),
    )
    return {
        "status": status,
        "reason": (
            None if sensors_healthy else "fixed_frame_sensor_self_test_failed"
        ),
        "mcu_info": mcu_info,
        "snapshots": [],
        "snapshot_count": 1 if communication_healthy else 0,
    }


def _resend_device_entry_url_to_fixed_frame_mcu(store, uart_link) -> bool:
    """Best-effort replay after edge restart; it never changes boot health."""
    try:
        record = store.get_device_entry_url()
        if record is None:
            logger.info("BOOT: no stored device entry URL to resend")
            return False
        uart_link.send_device_entry_url(record["deviceEntryUrl"])
        logger.info(
            "BOOT: stored device entry URL resent to MCU: sha256=%s",
            record["deviceEntryUrlSha256"],
        )
        return True
    except Exception as error:
        logger.warning(
            "BOOT: stored device entry URL could not be resent: %s",
            error,
        )
        return False

def recover_after_online_mcu_hello(store, uart_link, hello_frame):
    """Re-negotiate an MCU restart without replaying physical work."""
    restart_result = store.abort_interrupted_work()
    if restart_result["outcome"] != "NO_ACTIVE_WORK":
        logger.warning(
            "MCU restart cancelled active physical work: %s",
            restart_result,
        )
    mcu_info = uart_link.renegotiate_from_mcu_hello(hello_frame)
    mcu_info["mcu_receive_generation"] = (
        store.begin_mcu_receive_generation(mcu_info["mcu_boot_id"])
    )
    snapshots = uart_link.query_state(
        on_segment=lambda frame: _persist_and_ack_mcu_frame(
            store,
            uart_link,
            frame,
        )
    )
    snapshot_begin = _snapshot_begin(snapshots)
    if _is_boot_recovery(snapshot_begin):
        _restore_configuration_after_mcu_restart(
            store,
            uart_link,
            snapshot_begin,
        )
        if (
            restart_result["outcome"]
            != "JOB_SAFETY_RECONCILIATION_REQUIRED"
        ):
            _reconcile_mcu_boot_work(store, uart_link, snapshot_begin)
        else:
            logger.critical(
                "MCU restart retained permanent job safety context; "
                "not confirming an empty MCU work state"
            )
    else:
        slot = store.get_work_slot()
        active_work = _extract_active_work(snapshots)
        if slot and (
            not active_work
            or str(active_work["work_uid"]) != str(slot["work_uid"])
        ):
            raise ValueError("online MCU state conflicts with SQLite work slot")
        if active_work and not slot:
            raise ValueError("online MCU reports unknown active work")
    return {
        "status": "READY",
        "mcu_info": mcu_info,
        "snapshot_count": len(snapshots),
    }


def _snapshot_begin(snapshots):
    for segment in snapshots:
        if segment.get("message_name") == "STATE_SNAPSHOT_BEGIN":
            return segment.get("payload", {})
    raise ValueError("STATE_SNAPSHOT_BEGIN missing")


def _is_boot_recovery(snapshot_begin):
    return snapshot_begin.get("activeWorkPhase") == "BOOT_RECOVERY"


def _restore_configuration_after_mcu_restart(
    store,
    uart_link,
    snapshot_begin,
):
    configuration = store.get_latest_applied_configuration()
    if not configuration:
        raise ValueError("no applied configuration in SQLite")
    if (
        snapshot_begin.get("appliedConfigVersion")
        == configuration["config_version"]
        and snapshot_begin.get("appliedContentSha256")
        == configuration["content_sha256"]
        and snapshot_begin.get("appliedMcuPayloadSha256")
        == configuration["mcu_payload_sha256"]
    ):
        return
    result = uart_link.apply_configuration(
        {"payload": configuration["payload"]},
        configuration["part_command_uids"],
    )
    if not result.get("acked"):
        raise ValueError(
            "configuration restore ACK failed: "
            + str(result.get("error") or "UART_FAILURE")
        )
    commit_uid = configuration["part_command_uids"][-1]
    frame = _wait_for_mcu_event(
        store,
        uart_link,
        "CONFIG_APPLY_RESULT",
        lambda payload: payload.get("mcuCommandUid") == commit_uid,
    )
    applied = store.apply_configuration_result(frame["payload"])
    if applied not in ("ACCEPTED", "DUPLICATE"):
        raise ValueError(f"configuration restore result {applied.lower()}")
    _mark_boot_event_processed(store, frame)


def _reconcile_mcu_boot_work(store, uart_link, snapshot_begin):
    configuration = store.get_latest_applied_configuration()
    if not configuration:
        raise ValueError("no applied configuration in SQLite")
    command_uid = str(_uuid.uuid4())
    result = uart_link.send_command(
        "CONFIRM_NO_ACTIVE_WORK",
        {
            "configVersion": configuration["config_version"],
            "configContentSha256": configuration["content_sha256"],
        },
        mcu_command_uid=command_uid,
    )
    if not result.get("acked"):
        raise ValueError(
            "boot reconciliation ACK failed: "
            + str(result.get("error") or "UART_FAILURE")
        )
    frame = _wait_for_mcu_event(
        store,
        uart_link,
        "BOOT_RECONCILIATION_RESULT",
        lambda payload: (
            payload.get("mcuCommandUid") == command_uid
            and payload.get("decision") == "CONFIRM_NO_ACTIVE_WORK"
        ),
    )
    payload = frame["payload"]
    if payload.get("status") != "ACCEPTED":
        raise ValueError(
            "boot reconciliation rejected: "
            + str(payload.get("faultCode") or "MCU_INTERNAL")
        )
    _mark_boot_event_processed(store, frame)


def _wait_for_mcu_event(
    store,
    uart_link,
    message_name,
    predicate,
    timeout_ms=10000,
):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        frame = uart_link.read_mcu_event(timeout_ms=min(remaining_ms, 500))
        if frame is None:
            continue
        payload = frame.get("payload") or {}
        if "mcuBootId" in payload and "mcuEventSequence" in payload:
            _persist_and_ack_mcu_frame(store, uart_link, frame)
        if frame.get("message_name") == message_name and predicate(payload):
            return frame
    raise TimeoutError(f"{message_name} timeout")


def _mark_boot_event_processed(store, frame):
    payload = frame["payload"]
    store.mark_mcu_event_processed(
        payload["mcuBootId"],
        payload["mcuEventSequence"],
    )


def _extract_active_work(snapshots):
    for seg in snapshots:
        p = seg.get("payload", {})
        if seg.get("message_name") == "STATE_SNAPSHOT_BEGIN":
            wt = p.get("activeWorkType", 0)
            wu = p.get("activeWorkUid", "0")
            pn = p.get("activePortNo", 0)
            if wt != 0 and wu != "00000000-0000-0000-0000-000000000000":
                return {"work_type": wt, "work_uid": wu, "port_no": pn}
    return None


def _persist_and_ack_mcu_frame(store, uart_link, frame):
    result = store.receive_mcu_frame(frame)
    payload = frame["payload"]
    if result in ("ACCEPTED", "DUPLICATE"):
        uart_link.send_ack(
            payload["mcuBootId"],
            frame["tx_sequence"],
            frame["message_type"],
            "DUPLICATE_ACCEPTED" if result == "DUPLICATE" else "ACCEPTED",
        )
        return
    if result == "CONFLICT":
        uart_link.send_nack(
            payload["mcuBootId"],
            frame["tx_sequence"],
            frame["message_type"],
            "IDEMPOTENCY_CONFLICT",
        )
    raise ValueError(f"MCU event persistence rejected: {result}")


def _build_runtime_snapshot_payload(
    store,
    mcu_info,
    snapshots,
    *,
    device_facts=None,
    clock_sample=None,
):
    faults = store.list_active_faults()
    compatibility_mode = bool(mcu_info.get("compatibility_mode"))
    native_mode = bool(
        not compatibility_mode
        and mcu_info.get("uart_protocol_major") == 2
    )
    applied = store.get_latest_applied_configuration()
    ports = (
        _fixed_frame_runtime_ports(
            store,
            applied,
            faults,
        )
        if compatibility_mode
        else (
            _native_runtime_ports(
                store,
                mcu_info,
                device_facts,
                applied,
                faults,
            )
            if native_mode
            else _runtime_ports_from_snapshots(snapshots)
        )
    )
    if ports:
        store.set_state(
            "latest_runtime_ports_json",
            json.dumps(ports, ensure_ascii=False),
        )
    elif not compatibility_mode and not native_mode:
        try:
            ports = json.loads(
                store.get_state("latest_runtime_ports_json", "[]")
            )
        except (TypeError, ValueError):
            ports = []
    if not ports and not compatibility_mode:
        port_count = int(mcu_info.get("mcu_port_count") or 1)
        fullness_sensor_kind = mcu_info.get(
            "fullness_sensor_kind",
            "ULTRASONIC",
        )
        ports = [
            _unknown_runtime_port(port_no, fullness_sensor_kind)
            for port_no in range(1, port_count + 1)
        ]
    applied_config = None
    if applied:
        applied_config = {
            "version": applied["config_version"],
            "contentSha256": applied["content_sha256"],
            "mcuPayloadSha256": applied["mcu_payload_sha256"],
        }
    mcu_boot_id = mcu_info.get("mcu_boot_id")
    edge_boot_id = int(store.get_edge_boot_id() or 0)
    if compatibility_mode:
        mcu_boot_id = edge_boot_id
    if not isinstance(mcu_boot_id, int) or mcu_boot_id <= 0:
        mcu_boot_id = None
    firmware_identity = mcu_info.get("mcu_firmware_identity")
    if not isinstance(firmware_identity, dict):
        firmware_identity = None
    firmware_version = (
        firmware_identity.get("firmwareVersion")
        if firmware_identity is not None
        else mcu_info.get("mcu_firmware_version")
    )
    if compatibility_mode and not firmware_version:
        firmware_version = "fixed-frame-compat"
    if not firmware_version:
        firmware_version = None
    uart_state = mcu_info.get("uart_state")
    if uart_state == "STARTING":
        uart_state = "NEGOTIATING"
    if uart_state not in {
        "DISCONNECTED",
        "NEGOTIATING",
        "READY",
        "INCOMPATIBLE",
        "FAULT",
    }:
        uart_state = "READY" if mcu_boot_id is not None else "DISCONNECTED"
    sampled_clock = clock_sample or sample_clock()
    payload = {
        "edgeBootId": edge_boot_id,
        "edgeVersion": (
            mcu_info.get("edge_version")
            or os.getenv("ECOBIN_EDGE_VERSION", "0.1.0")
        ),
        "mcuBootId": mcu_boot_id,
        "mcuFirmwareVersion": firmware_version,
        "uartProtocolMajor": mcu_info.get("uart_protocol_major", 1),
        "uartProtocolMinor": mcu_info.get("uart_protocol_minor", 0),
        "uartState": uart_state,
        "localStorageState": _local_storage_state(store),
        "clockState": sampled_clock.quality,
        "appliedConfig": applied_config,
        "pendingReliableEventCount": (
            store.count_pending_reliable_events()
        ),
        "capabilityBitmapHex": f"{int(mcu_info.get('mcu_capability', 0)):016x}",
        "ports": ports,
    }
    if firmware_identity is not None:
        payload["mcuFirmwareIdentity"] = firmware_identity
    if sampled_clock.offset_millis is not None:
        payload["clockOffsetMillis"] = sampled_clock.offset_millis
    repair_state = store.get_state("clock_repair_state")
    if repair_state:
        payload["clockRepairState"] = repair_state
    return payload


def _publish_runtime_snapshot(
    store,
    cloud_transport: CloudTransport,
    device_identity: DeviceIdentity,
    mcu_info,
    snapshots,
    *,
    device_facts=None,
    force=True,
    previous_payload_sha256=None,
):
    sampled_clock = sample_clock()
    payload = _build_runtime_snapshot_payload(
        store,
        mcu_info,
        snapshots,
        device_facts=device_facts,
        clock_sample=sampled_clock,
    )
    payload_sha256 = canonical_payload_sha256(payload)
    if not force and payload_sha256 == previous_payload_sha256:
        logger.debug(
            "runtime snapshot skipped because semantic state is unchanged"
        )
        return {
            "published": False,
            "skipped_unchanged": True,
            "payload_sha256": payload_sha256,
        }
    envelope = {
        "schemaVersion": 2,
        "eventUid": str(_uuid.uuid4()),
        "edgeEventSequence": store.reserve_edge_event_sequence(),
        "eventType": "DEVICE_RUNTIME_SNAPSHOT",
        "deliveryClass": "TELEMETRY_SNAPSHOT",
        "target": {
            "type": "DEVICE_ASSET",
            "uid": device_identity.device_name,
        },
        "commandUid": None,
        "occurredAt": sampled_clock.occurred_at,
        "clockQuality": sampled_clock.quality,
        "payloadSha256": payload_sha256,
        "payload": payload,
    }
    queued = cloud_transport.send_event(
        CloudEvent(
            event_uid=envelope["eventUid"],
            event_type="DEVICE_RUNTIME_SNAPSHOT",
            params=envelope,
        )
    )
    if not queued:
        logger.warning("DEVICE_RUNTIME_SNAPSHOT could not be queued")
        return {
            "published": False,
            "skipped_unchanged": False,
            "payload_sha256": payload_sha256,
        }
    logger.info("published DEVICE_RUNTIME_SNAPSHOT")
    return {
        "published": True,
        "skipped_unchanged": False,
        "payload_sha256": payload_sha256,
    }


def _fixed_frame_runtime_ports(store, applied, faults):
    del applied
    port_count = 1
    self_test_record = store.get_state_record(
        "fixed_frame_latest_self_test_json"
    )
    business_record = store.get_state_record(
        "fixed_frame_latest_observation_json"
    )
    try:
        self_test = json.loads(
            self_test_record["state_value"] if self_test_record else ""
        )
    except (TypeError, ValueError):
        self_test = None
    try:
        observation = json.loads(
            business_record["state_value"] if business_record else ""
        )
    except (TypeError, ValueError):
        observation = None
    if not isinstance(self_test, dict):
        self_test = None
    if not isinstance(observation, dict):
        observation = None
    self_test_is_latest = bool(
        self_test_record
        and (
            not business_record
            or self_test_record["updated_at"] >= business_record["updated_at"]
        )
    )
    ports = []
    for port_no in range(1, port_count + 1):
        port_observation = (
            observation
            if observation
            and observation.get("portNo") == port_no
            else None
        )
        if self_test_is_latest and self_test:
            weight = self_test.get("weightGrams")
            has_weight = bool(
                self_test.get("weightValid") is True
                and isinstance(weight, int)
                and not isinstance(weight, bool)
                and 0 <= weight <= 350_000
            )
            has_infrared = bool(
                self_test.get("infraredValid") is True
                and isinstance(self_test.get("infraredBlocked"), bool)
            )
            infrared_blocked = self_test.get("infraredBlocked") is True
            measurement_uid = self_test.get("weightMeasurementUid")
            query_status = self_test.get("queryStatus")
            if has_weight:
                weight_health = "OK"
            elif query_status == "TIMEOUT":
                weight_health = "TIMEOUT"
            elif query_status == "OK":
                weight_health = "SENSOR_FAULT"
            else:
                weight_health = "PROTOCOL_ERROR"
        elif port_observation:
            weight = port_observation.get("postWeightGrams")
            has_weight = (
                isinstance(weight, int)
                and not isinstance(weight, bool)
                and 0 <= weight <= 350_000
            )
            has_infrared = isinstance(
                port_observation.get("infraredBlocked"),
                bool,
            )
            infrared_blocked = (
                port_observation.get("infraredBlocked") is True
            )
            measurement_uid = port_observation.get("measurementUid")
            weight_health = "OK" if has_weight else "SENSOR_FAULT"
        else:
            weight = None
            has_weight = False
            has_infrared = False
            infrared_blocked = False
            measurement_uid = None
            query_status = self_test.get("queryStatus") if self_test else None
            weight_health = (
                "TIMEOUT"
                if query_status == "TIMEOUT"
                else "PROTOCOL_ERROR"
            )
        if has_weight and not measurement_uid:
            state_key = (
                f"port_{port_no}_fixed_frame_zero_weight_uid"
            )
            measurement_uid = store.get_state(state_key)
            try:
                _uuid.UUID(str(measurement_uid), version=4)
            except (ValueError, AttributeError):
                measurement_uid = str(_uuid.uuid4())
                store.set_state(state_key, measurement_uid)
        if not has_weight:
            measurement_uid = None
        delivery_context = _state_json(
            store,
            f"port_{port_no}_delivery_runtime_context_json",
        )
        clean_context = _state_json(
            store,
            f"port_{port_no}_clean_runtime_context_json",
        )
        cleaner_close_confirmed = bool(
            clean_context.get(
                "cleaner_physical_close_confirmed",
                False,
            )
        )
        ports.append({
            "portNo": port_no,
            "lastDeliveryDoorCommand": delivery_context.get(
                "last_delivery_door_command",
                "NONE",
            ),
            "lastDeliveryDoorOutputStatus": delivery_context.get(
                "last_delivery_door_output_status",
                "NOT_DISPATCHED",
            ),
            "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
            "cleanLockPowerState": "DEENERGIZED",
            "solenoidHealth": (
                "UNKNOWN"
                if _port_has_active_fault(
                    faults,
                    port_no,
                    {"UART", "CLEAN_SOLENOID"},
                )
                else "OK"
            ),
            "cleanDoorStateBasis": (
                "CLEANER_CONFIRMATION"
                if cleaner_close_confirmed
                else "NOT_OBSERVABLE"
            ),
            "cleanerPhysicalCloseConfirmed": cleaner_close_confirmed,
            "weightMeasurementUid": measurement_uid,
            "weightMeasurementStatus": (
                "STABLE" if has_weight else weight_health
            ),
            "weightValueAvailable": has_weight,
            "reportedWeightGrams": weight if has_weight else None,
            "weightValueKind": (
                "LAST_OBSERVED" if has_weight else "NONE"
            ),
            "measurementElapsedMs": 0,
            "weightSampleCount": 1 if has_weight else 0,
            "calibrationVersion": 0,
            "weightSensorHealth": weight_health,
            "weightFaultCode": (
                None if weight_health == "OK" else "WEIGHT_SENSOR"
            ),
            "weightMcuBootId": None,
            "weightMcuEventSequence": None,
            "fullnessSensorKind": "DIGITAL_INFRARED",
            "fullnessSensorValue": (
                "BLOCKED"
                if has_infrared and infrared_blocked
                else "CLEAR"
            ),
            "fullnessSampleBasis": "NOT_SAMPLED",
            "representativeDistanceMm": None,
            "fullnessValidSampleCount": (
                1 if has_infrared else 0
            ),
            "smokeState": store.get_state(
                f"port_{port_no}_smoke_state",
                store.get_state("smoke_state", "UNKNOWN"),
            ),
            "smokeSensorHealth": store.get_state(
                f"port_{port_no}_smoke_sensor_health",
                store.get_state("smoke_sensor_health", "UNKNOWN"),
            ),
            "faultBitmap": _fault_bitmap(faults, port_no),
        })
    return ports


def _native_runtime_ports(store, mcu_info, facts, applied, faults):
    """Project one fresh rc.25 DEVICE_FACTS reply without inventing health.

    The current OneNet runtime-port shape has no fields for the literal PB6
    and PB7 booleans, nor a basis enum for a single ultrasonic/infrared read.
    The pin pair is therefore represented only by the existing output-status
    field.  Single environment observations use NOT_SAMPLED as their basis;
    in particular CLEAR + NOT_SAMPLED is unknown, never proof of normal.
    """
    port_count = mcu_info.get("mcu_port_count", 1)
    if type(port_count) is not int or not 1 <= port_count <= 6:
        port_count = 1
    configurations = {}
    if isinstance(applied, dict):
        payload = applied.get("payload")
        if isinstance(payload, dict):
            for candidate in payload.get("ports", []):
                if isinstance(candidate, dict) and type(candidate.get("portNo")) is int:
                    configurations[candidate["portNo"]] = candidate
    result = []
    for port_no in range(1, port_count + 1):
        configuration = configurations.get(port_no, {})
        configured_kind = configuration.get("fullnessSensorKind", "ULTRASONIC")
        if configured_kind not in {"ULTRASONIC", "DIGITAL_INFRARED"}:
            configured_kind = "ULTRASONIC"
        current = facts if (
            isinstance(facts, dict)
            and facts.get("status") == "AVAILABLE"
            and facts.get("currentMcuBootId") == mcu_info.get("mcu_boot_id")
            and facts.get("portNo") == port_no
        ) else None
        if current is None:
            port = _unknown_runtime_port(port_no, configured_kind)
            port["faultBitmap"] = _fault_bitmap(faults, port_no)
            result.append(port)
            continue
        last_command = current.get("lastDeliveryDoorCommand")
        if last_command not in {"NONE", "OPEN", "CLOSE"}:
            last_command = "NONE"
        pb6, pb7 = current.get("pb6Output"), current.get("pb7Output")
        action_active = current.get("doorActionActive") is True
        pinch_paused = current.get("pinchPaused") is True
        expected_pair = {
            "OPEN": (True, False),
            "CLOSE": (False, True),
        }.get(last_command)
        if expected_pair is None and (pb6, pb7) == (False, False):
            output_status = "NOT_DISPATCHED"
        elif action_active and (pb6, pb7) == expected_pair:
            output_status = "COMMAND_DISPATCHED"
        elif (
            last_command == "CLOSE"
            and action_active
            and pinch_paused
            and (pb6, pb7) == (False, False)
        ):
            # PB5 pauses a close output. It is an intentional safety state,
            # not a door fault and not a separate business-admission input.
            output_status = "NOT_DISPATCHED"
        elif not action_active and (pb6, pb7) == (False, False):
            output_status = "NOT_DISPATCHED"
        else:
            output_status = "OUTPUT_REJECTED"
        port = {
            "portNo": port_no,
            "lastDeliveryDoorCommand": last_command,
            "lastDeliveryDoorOutputStatus": output_status,
            "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
            "cleanLockPowerState": (
                "ENERGIZED" if current.get("cleanLockPowered") is True
                else "DEENERGIZED"
            ),
            # The MCU reports its output command, not electrical feedback from
            # the solenoid, so a hardware-health claim would be fabricated.
            "solenoidHealth": "UNKNOWN",
            "cleanDoorStateBasis": "NOT_OBSERVABLE",
            "cleanerPhysicalCloseConfirmed": False,
            **_native_weight_fields(store, port_no, current),
            **_native_fullness_fields(current, configuration, configured_kind),
            **_native_smoke_fields(current),
            "faultBitmap": _fault_bitmap(faults, port_no),
        }
        result.append(port)
    return result


def _native_measurement_uid(store, port_no, kind, facts, sequence, observed_uptime):
    """Return one persisted UUIDv4 for one immutable MCU weight observation."""
    source = "%s:%s:%s:%s" % (
        facts["currentMcuBootId"],
        kind,
        sequence,
        observed_uptime,
    )
    state_key = f"port_{port_no}_native_{kind}_weight_identity_json"
    try:
        stored = json.loads(store.get_state(state_key, "{}"))
        stored_uid = stored.get("uid")
        parsed = _uuid.UUID(stored_uid) if isinstance(stored_uid, str) else None
        if (
            stored.get("source") == source
            and parsed is not None
            and parsed.version == 4
            and parsed.variant == _uuid.RFC_4122
            and str(parsed) == stored_uid
        ):
            return stored_uid
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    measurement_uid = str(_uuid.uuid4())
    store.set_state(
        state_key,
        json.dumps({"source": source, "uid": measurement_uid}, sort_keys=True),
    )
    return measurement_uid


def _native_weight_fields(store, port_no, facts):
    state = facts.get("measurementState")
    measurement_sequence = facts.get("measurementSequence")
    measurement_uptime = facts.get("measurementObservedUptimeMs")
    scale_uptime = facts.get("scaleCapturedUptimeMs")
    use_measurement = bool(
        state in {
            "STABLE_MEAN",
            "TIMEOUT_MEDIAN",
            "UNAVAILABLE",
            "CONFIG_ERROR",
            "BUFFER_FULL",
            "INTERRUPTED",
        }
        and type(measurement_sequence) is int
        and measurement_sequence > 0
        and type(measurement_uptime) is int
        and type(scale_uptime) is int
        and measurement_uptime >= scale_uptime
    )
    if use_measurement:
        uid = _native_measurement_uid(
            store,
            port_no,
            "measurement",
            facts,
            measurement_sequence,
            measurement_uptime,
        )
        common = {
            "weightMeasurementUid": uid,
            "measurementElapsedMs": facts.get("measurementElapsedMs", 0),
            "weightSampleCount": facts.get("measurementSampleCount", 0),
            "calibrationVersion": facts.get("scaleCalibrationVersion", 0),
            "weightMcuBootId": facts["currentMcuBootId"],
            # DEVICE_FACTS.measurementSequence is not the global reliable
            # MCU event sequence required by this OneNet field.
            "weightMcuEventSequence": None,
        }
        if state == "STABLE_MEAN":
            return common | {
                "weightMeasurementStatus": "STABLE",
                "weightValueAvailable": True,
                "reportedWeightGrams": facts["measurementWeightGrams"],
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "weightSensorHealth": "OK",
                "weightFaultCode": None,
            }
        if state == "TIMEOUT_MEDIAN":
            return common | {
                "weightMeasurementStatus": "UNSTABLE",
                "weightValueAvailable": True,
                "reportedWeightGrams": facts["measurementWeightGrams"],
                "weightValueKind": "TIMEOUT_MEDIAN",
                "weightSensorHealth": "OK",
                "weightFaultCode": None,
            }
        status, health = {
            "CONFIG_ERROR": ("CONFIG_ERROR", "CONFIG_ERROR"),
            "UNAVAILABLE": ("TIMEOUT", "TIMEOUT"),
            "BUFFER_FULL": ("SENSOR_FAULT", "SENSOR_FAULT"),
            "INTERRUPTED": ("SENSOR_FAULT", "UNKNOWN"),
        }.get(state, ("PROTOCOL_ERROR", "PROTOCOL_ERROR"))
        return common | {
            "weightMeasurementStatus": status,
            "weightValueAvailable": False,
            "reportedWeightGrams": None,
            "weightValueKind": "NONE",
            "weightSensorHealth": health,
            "weightFaultCode": "WEIGHT_SENSOR",
        }
    scale_status = facts.get("scaleReadStatus")
    scale_sequence = facts.get("scaleAttemptSequence")
    scale_is_fresh = bool(
        scale_status == "VALID"
        and type(scale_sequence) is int
        and scale_sequence > 0
        and type(scale_uptime) is int
        and type(facts.get("capturedUptimeMs")) is int
        and 0 <= facts["capturedUptimeMs"] - scale_uptime <= NATIVE_FACTS_MAXIMUM_AGE_MS
    )
    if scale_is_fresh:
        return {
            "weightMeasurementUid": _native_measurement_uid(
                store,
                port_no,
                "scale",
                facts,
                scale_sequence,
                scale_uptime,
            ),
            "weightMeasurementStatus": "UNSTABLE",
            "weightValueAvailable": True,
            "reportedWeightGrams": facts["scaleWeightGrams"],
            "weightValueKind": "LAST_OBSERVED",
            "measurementElapsedMs": 0,
            "weightSampleCount": 1,
            "calibrationVersion": facts.get("scaleCalibrationVersion", 0),
            "weightSensorHealth": "OK",
            "weightFaultCode": None,
            "weightMcuBootId": facts["currentMcuBootId"],
            "weightMcuEventSequence": None,
        }
    status, health = {
        "TIMEOUT": ("TIMEOUT", "TIMEOUT"),
        "CRC_ERROR": ("PROTOCOL_ERROR", "PROTOCOL_ERROR"),
        "PROTOCOL_ERROR": ("PROTOCOL_ERROR", "PROTOCOL_ERROR"),
        "RANGE_ERROR": ("OVERLOAD", "OVERLOAD"),
    }.get(scale_status, ("SENSOR_FAULT", "UNKNOWN"))
    return {
        **_snapshot_measurement_fields({}),
        "weightMeasurementStatus": status,
        "weightSensorHealth": health,
        "weightFaultCode": (
            "WEIGHT_SENSOR" if scale_status != "NOT_OBSERVED" else None
        ),
        "calibrationVersion": facts.get("scaleCalibrationVersion", 0),
    }


def _native_fullness_fields(facts, configuration, configured_kind):
    kind = facts.get("fullnessObservationKind")
    if kind not in {"ULTRASONIC", "DIGITAL_INFRARED"}:
        kind = configured_kind
    if facts.get("fullnessReadStatus") != "VALID":
        return {
            "fullnessSensorKind": kind,
            "fullnessSensorValue": "CLEAR",
            "fullnessSampleBasis": "NOT_SAMPLED",
            "representativeDistanceMm": None,
            "fullnessValidSampleCount": 0,
        }
    if kind == "DIGITAL_INFRARED":
        blocked = facts.get("fullnessInfraredBlocked") is True
        distance = None
    else:
        distance = facts.get("fullnessDistanceMm")
        threshold = configuration.get("fullnessDistanceThresholdMm")
        blocked = bool(
            type(distance) is int
            and type(threshold) is int
            and distance <= threshold
        )
    return {
        "fullnessSensorKind": kind,
        "fullnessSensorValue": "BLOCKED" if blocked else "CLEAR",
        # rc.25 reports one observation; claiming MEASURED_MEDIAN would lie.
        "fullnessSampleBasis": "NOT_SAMPLED",
        "representativeDistanceMm": distance,
        "fullnessValidSampleCount": 1,
    }


def _native_smoke_fields(facts):
    state = facts.get("smokeObservationState")
    if state == "NORMAL":
        return {"smokeState": "NORMAL", "smokeSensorHealth": "OK"}
    if state == "ALARM":
        return {"smokeState": "ALARM", "smokeSensorHealth": "OK"}
    if state == "UNAVAILABLE":
        return {"smokeState": "UNKNOWN", "smokeSensorHealth": "SENSOR_FAULT"}
    return {"smokeState": "UNKNOWN", "smokeSensorHealth": "UNKNOWN"}


def _state_json(store, key):
    try:
        value = json.loads(store.get_state(key, "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _fault_bitmap(faults, port_no):
    component_bits = {
        "UART": 1,
        "EDGE_STORAGE": 2,
        "CAMERA": 4,
        "NETWORK": 8,
        "CLOCK": 16,
    }
    bitmap = 0
    for fault in faults:
        fault_port = fault.get("port_no")
        if fault_port not in (None, port_no):
            continue
        bitmap |= component_bits.get(fault.get("component"), 32)
    return bitmap


def _port_has_active_fault(faults, port_no, components):
    return any(
        fault.get("component") in components
        and fault.get("port_no") in (None, port_no)
        for fault in faults
    )


def _local_storage_state(store):
    if not store.integrity_check():
        return "CORRUPT"
    try:
        usage = shutil.disk_usage(
            os.path.dirname(os.path.abspath(store.db_path))
        )
    except OSError:
        return "DEGRADED"
    if usage.free <= 0:
        return "FULL"
    if usage.total and usage.free / usage.total < 0.02:
        return "DEGRADED"
    if not os.access(store.db_path, os.W_OK):
        return "READ_ONLY"
    return "HEALTHY"


def _clock_state():
    return sample_clock().quality


def _runtime_ports_from_snapshots(snapshots):
    ports = []
    for seg in snapshots:
        if seg.get("message_name") != "STATE_SNAPSHOT_PORT":
            continue
        p = seg.get("payload", {})
        ports.append({
            "portNo": p.get("portNo", len(ports) + 1),
            "lastDeliveryDoorCommand": p.get(
                "lastDeliveryDoorCommand",
                "NONE",
            ),
            "lastDeliveryDoorOutputStatus": p.get(
                "lastDeliveryDoorOutputStatus",
                "NOT_DISPATCHED",
            ),
            "deliveryDoorPhysicalStateBasis": p.get(
                "deliveryDoorPhysicalStateBasis",
                "NOT_OBSERVABLE",
            ),
            "cleanLockPowerState": p.get(
                "cleanLockPowerState",
                "UNKNOWN",
            ),
            "solenoidHealth": p.get("solenoidHealth", "UNKNOWN"),
            "cleanDoorStateBasis": p.get(
                "cleanDoorStateBasis",
                "NOT_OBSERVABLE",
            ),
            "cleanerPhysicalCloseConfirmed": bool(
                p.get("cleanerPhysicalCloseConfirmed", False)
            ),
            **_snapshot_measurement_fields(p),
            "fullnessSensorKind": p.get(
                "fullnessSensorKind",
                "ULTRASONIC",
            ),
            "fullnessSensorValue": p.get(
                "fullnessSensorValue",
                "CLEAR",
            ),
            "fullnessSampleBasis": p.get(
                "fullnessSampleBasis",
                "NOT_SAMPLED",
            ),
            "representativeDistanceMm": (
                p.get("representativeDistanceMm")
                if p.get("representativeDistancePresent")
                else None
            ),
            "fullnessValidSampleCount": p.get(
                "fullnessValidSampleCount",
                0,
            ),
            "smokeState": p.get("smokeState", "UNKNOWN"),
            "smokeSensorHealth": p.get("smokeSensorHealth", "UNKNOWN"),
            "faultBitmap": p.get("faultBitmap", 0),
        })
    return ports


def _unknown_runtime_port(port_no, fullness_sensor_kind="ULTRASONIC"):
    return {
        "portNo": port_no,
        "lastDeliveryDoorCommand": "NONE",
        "lastDeliveryDoorOutputStatus": "NOT_DISPATCHED",
        "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
        "cleanLockPowerState": "UNKNOWN",
        "solenoidHealth": "UNKNOWN",
        "cleanDoorStateBasis": "NOT_OBSERVABLE",
        "cleanerPhysicalCloseConfirmed": False,
        **_snapshot_measurement_fields({}),
        "fullnessSensorKind": fullness_sensor_kind,
        "fullnessSensorValue": "CLEAR",
        "fullnessSampleBasis": "NOT_SAMPLED",
        "representativeDistanceMm": None,
        "fullnessValidSampleCount": 0,
        "smokeState": "UNKNOWN",
        "smokeSensorHealth": "UNKNOWN",
        "faultBitmap": 0,
    }


def _snapshot_measurement_fields(payload):
    measurement_uid = payload.get("measurementUid")
    if (
        not measurement_uid
        or measurement_uid
        == "00000000-0000-0000-0000-000000000000"
    ):
        return {
            "weightMeasurementUid": None,
            "weightMeasurementStatus": "SENSOR_FAULT",
            "weightValueAvailable": False,
            "reportedWeightGrams": None,
            "weightValueKind": "NONE",
            "measurementElapsedMs": 0,
            "weightSampleCount": 0,
            "calibrationVersion": 0,
            "weightSensorHealth": "UNKNOWN",
            "weightFaultCode": None,
            "weightMcuBootId": None,
            "weightMcuEventSequence": None,
        }
    value_present = bool(payload.get("weightValuePresent"))
    fault_code = payload.get("faultCode")
    return {
        "weightMeasurementUid": measurement_uid,
        "weightMeasurementStatus": payload.get("measurementStatus"),
        "weightValueAvailable": value_present,
        "reportedWeightGrams": (
            payload.get("reportedWeightGrams")
            if value_present
            else None
        ),
        "weightValueKind": payload.get("weightValueKind", "NONE"),
        "measurementElapsedMs": payload.get("measurementElapsedMs", 0),
        "weightSampleCount": payload.get("sampleCount", 0),
        "calibrationVersion": payload.get("calibrationVersion", 0),
        "weightSensorHealth": payload.get(
            "weightSensorHealth",
            "UNKNOWN",
        ),
        "weightFaultCode": (
            None if fault_code in (None, "NONE") else fault_code
        ),
        "weightMcuBootId": payload.get("mcuBootId"),
        "weightMcuEventSequence": payload.get("mcuEventSequence"),
    }
