"""edge_boot.py -- startup recovery: SQLite -> HELLO -> QUERY_STATE -> reconcile."""
from __future__ import annotations
import json
import logging
import time
import uuid as _uuid
from edge_identity import is_valid_edge_boot_id, new_edge_boot_id
from edge_store import EdgeStore, WORK_TYPE_NONE
from onenet_wire import canonical_payload_sha256, utc_now_rfc3339
logger = logging.getLogger("edge-boot")


def boot_sequence(store, uart_link, mqtt_client, work_manager, photo_manager, test_mode=False):
    """Execute the full boot sequence. Returns status dict."""
    if not store.integrity_check():
        logger.critical("BOOT: SQLite integrity FAILED")
        store.record_fault("MCU_STORAGE", 1792, "BLOCK_DEVICE")
        return {"status": "SAFETY_LOCKED", "reason": "sqlite_integrity_failed"}
    boot_id = store.get_edge_boot_id()
    if not is_valid_edge_boot_id(boot_id):
        boot_id = str(new_edge_boot_id())
        store.set_edge_boot_id(boot_id)
        logger.info("BOOT: new edge boot ID: %s", boot_id)
    else:
        logger.info("BOOT: resume edge boot ID: %s", boot_id)
    if not uart_link.open():
        logger.error("BOOT: UART open failed")
        store.record_fault("UART", 256, "BLOCK_DEVICE")
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
        store.record_fault("UART", 256, "BLOCK_DEVICE")
        uart_link.close()
        return {"status": "SAFETY_LOCKED", "reason": str(e)}
    try:
        snapshots = uart_link.query_state(
            on_segment=lambda frame: _persist_and_ack_mcu_frame(
                store, uart_link, frame
            )
        )
        logger.info("BOOT: QUERY_STATE %d segments", len(snapshots))
    except Exception as e:
        logger.error("BOOT: QUERY_STATE failed: %s", e)
        store.record_fault("UART", 256, "BLOCK_DEVICE", {"reason": str(e)})
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
            _reconcile_mcu_boot_work(
                store,
                uart_link,
                snapshot_begin,
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
        store.record_fault("MCU_INTERNAL", 2048, "WARNING", {"mcu_work": active_work})
    if not mqtt_client.connect():
        logger.error("BOOT: MQTT connect failed")
        return {"status": "DEGRADED", "reason": "mqtt_connect_failed", "mcu_info": mcu_info}
    time.sleep(0.5)
    _publish_runtime_snapshot(store, mqtt_client, mcu_info, snapshots)
    logger.info("BOOT: sequence complete, READY")
    return {"status": "READY", "mcu_info": mcu_info, "snapshot_count": len(snapshots)}


def recover_after_online_mcu_hello(store, uart_link, hello_frame):
    """Re-negotiate and reconcile an MCU that restarted while edge stays up."""
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
        _reconcile_mcu_boot_work(store, uart_link, snapshot_begin)
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
    slot = store.get_work_slot()
    if slot and slot["work_type"] == "CLEAN":
        _resume_clean_after_restart(
            store,
            uart_link,
            configuration,
            slot,
        )
        return
    if slot and slot["work_type"] == "DELIVERY":
        _record_interrupted_delivery(store, slot)
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


def _record_interrupted_delivery(store, slot):
    ctx = slot["context"]
    event_uid = ctx.get("interruption_event_uid")
    if event_uid and store.get_event(event_uid):
        return
    event_uid = event_uid or str(_uuid.uuid4())
    ctx["interruption_event_uid"] = event_uid
    ctx["phase"] = "DEVICE_INTERRUPTED"
    ctx["manual_review_required"] = True
    configuration = store.get_latest_applied_configuration()
    frozen_config = ctx.get("config")
    if not frozen_config and configuration:
        frozen_config = {
            "version": configuration["config_version"],
            "contentSha256": configuration["content_sha256"],
            "mcuPayloadSha256": configuration["mcu_payload_sha256"],
        }
    first_measurement = _measurement_fact(ctx.get("first_measurement"))
    final_measurement = _measurement_fact(ctx.get("final_measurement"))
    first_weight = _usable_weight(first_measurement)
    final_weight = _usable_weight(final_measurement)
    event_payload = {
        "sessionUid": slot["work_uid"],
        "portNo": slot["port_no"],
        "firstPreOpenMeasurement": first_measurement,
        "finalPostCloseMeasurement": final_measurement,
        "deliveryNetWeightGrams": (
            final_weight - first_weight
            if first_weight is not None and final_weight is not None
            else None
        ),
        "finalDoorCommand": None,
        "completionReason": "DEVICE_INTERRUPTED",
        "manualReviewRequired": True,
        "negativeWeightAnomaly": ctx.get(
            "negative_weight_anomaly",
            False,
        ),
        "frozenConfig": frozen_config,
        "unitPriceTenThousandths": ctx.get(
            "unit_price_ten_thousandths",
            0,
        ),
        "photos": [
            _pending_photo(slot_name)
            for slot_name in (
                "BEFORE_INNER",
                "BEFORE_OUTER",
                "AFTER_INNER",
                "AFTER_OUTER",
            )
        ],
    }
    created = store.create_edge_event(
        event_uid=event_uid,
        event_type="DELIVERY_COMPLETE",
        payload=event_payload,
        work_uid=slot["work_uid"],
        work_state_update={
            "state": "RECOVERY_REQUIRED",
            "context": ctx,
        },
        deployment_code=ctx.get("deployment_code") or "Dp_unknown",
        target_type="DELIVERY_SESSION",
        command_uid=ctx.get("start_command_uid"),
    )
    if created not in ("ACCEPTED", "DUPLICATE"):
        raise ValueError(f"interrupted delivery persistence {created.lower()}")


def _resume_clean_after_restart(
    store,
    uart_link,
    configuration,
    slot,
):
    ctx = slot["context"]
    recovery_generation = int(ctx.get("recovery_generation", 0)) + 1
    next_action_sequence = int(ctx.get("action_sequence", 0)) + 1
    if next_action_sequence > 65535:
        raise ValueError("clean action sequence exhausted")
    command_uid = str(_uuid.uuid4())
    ctx["recovery_generation"] = recovery_generation
    ctx["resume_mcu_command_uid"] = command_uid
    ctx["phase"] = "RESUMING_AFTER_MCU_RESTART"
    store.update_work_context(slot["work_uid"], ctx)
    result = uart_link.send_command(
        "RESUME_CLEAN_OPERATION",
        {
            "operationUid": slot["work_uid"],
            "portNo": slot["port_no"],
            "recoveryGeneration": recovery_generation,
            "nextCleanActionSequence": next_action_sequence,
            "configVersion": configuration["config_version"],
            "configContentSha256": configuration["content_sha256"],
        },
        mcu_command_uid=command_uid,
    )
    if not result.get("acked"):
        raise ValueError(
            "clean resume ACK failed: "
            + str(result.get("error") or "UART_FAILURE")
        )
    frame = _wait_for_mcu_event(
        store,
        uart_link,
        "BOOT_RECONCILIATION_RESULT",
        lambda payload: (
            payload.get("mcuCommandUid") == command_uid
            and payload.get("decision") == "RESUME_CLEAN_OPERATION"
        ),
    )
    payload = frame["payload"]
    if (
        payload.get("status") != "ACCEPTED"
        or payload.get("activeWorkUid") != slot["work_uid"]
        or payload.get("activePortNo") != slot["port_no"]
        or payload.get("recoveryGeneration") != recovery_generation
        or payload.get("nextCleanActionSequence") != next_action_sequence
    ):
        raise ValueError(
            "clean resume rejected: "
            + str(payload.get("faultCode") or "MCU_INTERNAL")
        )
    ctx["phase"] = "CLEAN_RECOVERY_REQUIRED"
    store.update_work_context(slot["work_uid"], ctx)
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


def _publish_runtime_snapshot(store, mqtt_client, mcu_info, snapshots):
    faults = store.list_active_faults()
    ports = _runtime_ports_from_snapshots(snapshots)
    if ports:
        store.set_state(
            "latest_runtime_ports_json",
            json.dumps(ports, ensure_ascii=False),
        )
    else:
        try:
            ports = json.loads(
                store.get_state("latest_runtime_ports_json", "[]")
            )
        except (TypeError, ValueError):
            ports = []
    if not ports:
        port_count = int(mcu_info.get("mcu_port_count") or 1)
        ports = [_unknown_runtime_port(port_no) for port_no in range(1, port_count + 1)]
    applied = store.get_latest_applied_configuration()
    applied_config = None
    if applied:
        applied_config = {
            "version": applied["config_version"],
            "contentSha256": applied["content_sha256"],
            "mcuPayloadSha256": applied["mcu_payload_sha256"],
        }
    mcu_boot_id = mcu_info.get("mcu_boot_id")
    if not isinstance(mcu_boot_id, int) or mcu_boot_id <= 0:
        mcu_boot_id = None
    firmware_version = mcu_info.get("mcu_firmware_version")
    if not firmware_version:
        firmware_version = None
    uart_state = mcu_info.get("uart_state")
    if uart_state not in {
        "DISCONNECTED",
        "NEGOTIATING",
        "READY",
        "INCOMPATIBLE",
        "FAULT",
    }:
        uart_state = "READY" if mcu_boot_id is not None else "DISCONNECTED"
    payload = {
        "edgeBootId": int(store.get_edge_boot_id() or 0),
        "edgeVersion": "1.0.0-rc.3",
        "mcuBootId": mcu_boot_id,
        "mcuFirmwareVersion": firmware_version,
        "uartProtocolMajor": 1,
        "uartProtocolMinor": 0,
        "uartState": uart_state,
        "localStorageState": "HEALTHY",
        "clockState": "SYNCED",
        "appliedConfig": applied_config,
        "pendingReliableEventCount": len(store.list_pending_events(limit=1000)),
        "capabilityBitmapHex": f"{int(mcu_info.get('mcu_capability', 0)):016x}",
        "ports": ports,
    }
    deployment_code = (
        getattr(mqtt_client, "deployment_code", "") or "Dp_unknown"
    )
    payload = {
        "schemaVersion": 1,
        "eventUid": str(_uuid.uuid4()),
        "deploymentCode": deployment_code,
        "edgeEventSequence": store.reserve_edge_event_sequence(),
        "eventType": "DEVICE_RUNTIME_SNAPSHOT",
        "deliveryClass": "TELEMETRY_SNAPSHOT",
        "target": {
            "type": "DEVICE_DEPLOYMENT",
            "uid": deployment_code,
        },
        "commandUid": None,
        "occurredAt": utc_now_rfc3339(),
        "clockQuality": "SYNCED",
        "payloadSha256": canonical_payload_sha256(payload),
        "payload": payload,
    }
    mqtt_client.publish_event("DEVICE_RUNTIME_SNAPSHOT", payload)
    logger.info("BOOT: published DEVICE_RUNTIME_SNAPSHOT (%d faults)", len(faults))


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


def _unknown_runtime_port(port_no):
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
        "fullnessSensorKind": "ULTRASONIC",
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


def _measurement_fact(payload):
    if not payload:
        return None
    value_present = bool(payload.get("weightValuePresent"))
    fault_code = payload.get("faultCode")
    return {
        "measurementUid": payload.get("measurementUid"),
        "status": payload.get("measurementStatus"),
        "weightValueAvailable": value_present,
        "reportedWeightGrams": (
            payload.get("reportedWeightGrams")
            if value_present
            else None
        ),
        "weightValueKind": payload.get("weightValueKind", "NONE"),
        "measurementElapsedMs": payload.get("measurementElapsedMs", 0),
        "sampleCount": payload.get("sampleCount", 0),
        "calibrationVersion": payload.get("calibrationVersion", 0),
        "sensorHealth": payload.get("weightSensorHealth", "UNKNOWN"),
        "faultCode": None if fault_code in (None, "NONE") else fault_code,
        "mcuBootId": payload.get("mcuBootId"),
        "mcuEventSequence": payload.get("mcuEventSequence"),
    }


def _usable_weight(measurement):
    if (
        not measurement
        or not measurement.get("weightValueAvailable")
        or measurement.get("status") not in ("STABLE", "UNSTABLE")
        or measurement.get("sensorHealth") != "OK"
    ):
        return None
    return measurement.get("reportedWeightGrams")


def _pending_photo(slot):
    return {
        "slot": slot,
        "status": "UPLOAD_PENDING",
        "photoUid": None,
        "url": None,
        "sha256": None,
        "sizeBytes": None,
        "capturedAt": None,
        "missingReason": "DEVICE_INTERRUPTED",
    }
