"""edge_boot.py -- startup recovery: SQLite -> HELLO -> QUERY_STATE -> reconcile."""
from __future__ import annotations
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
        logger.info("BOOT: HELLO complete: mcu_boot=%d", mcu_info["mcu_boot_id"])
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
    active_work = _extract_active_work(snapshots)
    slot = store.get_work_slot()
    if slot and active_work and str(active_work["work_uid"]) == str(slot["work_uid"]):
        logger.info("BOOT: SQLite and MCU agree, resuming")
    elif slot and not active_work:
        logger.info("BOOT: stale work slot, releasing")
        store.release_work_slot(slot["work_uid"])
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
    payload = {
        "edgeBootId": int(store.get_edge_boot_id() or 0),
        "edgeVersion": "1.0.0-rc.1",
        "mcuBootId": mcu_info.get("mcu_boot_id"),
        "mcuFirmwareVersion": mcu_info.get("mcu_firmware_version", "unknown"),
        "uartProtocolMajor": 1,
        "uartProtocolMinor": 0,
        "uartState": "READY" if not faults else "DEGRADED",
        "localStorageState": "OK",
        "clockState": "SYNCED",
        "appliedConfig": None,
        "pendingReliableEventCount": len(store.list_pending_events(limit=1000)),
        "capabilityBitmapHex": f"{int(mcu_info.get('mcu_capability', 0)):016x}",
        "ports": _runtime_ports_from_snapshots(snapshots),
    }
    payload = {
        "schemaVersion": 1,
        "eventUid": str(_uuid.uuid4()),
        "deploymentCode": getattr(mqtt_client, "deployment_code", "") or "UNKNOWN_DEPLOYMENT",
        "edgeEventSequence": store.get_edge_event_sequence(),
        "eventType": "DEVICE_RUNTIME_SNAPSHOT",
        "deliveryClass": "TELEMETRY_SNAPSHOT",
        "target": {
            "type": "DEVICE_DEPLOYMENT",
            "uid": getattr(mqtt_client, "deployment_code", "") or "UNKNOWN_DEPLOYMENT",
        },
        "commandUid": "",
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
            "deliveryDoorState": p.get("deliveryDoorState", "UNKNOWN"),
            "deliveryDoorHealth": p.get("deliveryDoorHealth", "UNKNOWN"),
            "cleanLockAndInferredDoor": p.get("cleanLockAndInferredDoor", {
                "lockPower": "DEENERGIZED",
                "solenoidState": "UNKNOWN",
                "inferredDoorState": "UNKNOWN",
                "stateBasis": "INFERRED_FROM_LOCK_POWER",
            }),
            "weightSensorHealth": p.get("weightSensorHealth", "UNKNOWN"),
            "infraredValue": p.get("infraredValue", "UNKNOWN"),
            "infraredHealth": p.get("infraredHealth", "UNKNOWN"),
            "smokeState": p.get("smokeState", "UNKNOWN"),
            "smokeSensorHealth": p.get("smokeSensorHealth", "UNKNOWN"),
        })
    return ports
