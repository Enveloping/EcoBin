"""work_manager.py -- delivery session and clean operation state machines."""
from __future__ import annotations
import logging
import uuid as _uuid
from typing import Any, Optional
from edge_store import EdgeStore, WORK_TYPE_DELIVERY, WORK_TYPE_CLEAN, WORK_TYPE_NONE
logger = logging.getLogger("work-manager")
def _new_uid() -> str:
    return str(_uuid.uuid4())


class WorkManager:

    def __init__(self, store: EdgeStore, uart_link, mqtt_client, photo_manager):
        self._store = store
        self._uart = uart_link
        self._mqtt = mqtt_client
        self._photo = photo_manager

    @property
    def active_delivery_session(self) -> Optional[dict]:
        slot = self._store.get_work_slot()
        if slot and slot["work_type"] == WORK_TYPE_DELIVERY:
            return slot
        return None

    def start_delivery_session(self, session_uid, port_no, unit_price_ten_thousandths,
                               bag_qr_code, negative_weight_threshold_grams=500):
        ctx = {"session_uid": session_uid, "port_no": port_no,
               "unit_price_ten_thousandths": unit_price_ten_thousandths,
               "bag_qr_code": bag_qr_code,
               "negative_weight_threshold_grams": negative_weight_threshold_grams,
               "phase": "STARTED", "round_index": 0,
               "negative_weight_anomaly": False, "first_weight_grams": None,
               "first_measurement_uid": None, "final_weight_grams": None,
               "final_measurement_uid": None}
        ok = self._store.acquire_work_slot(WORK_TYPE_DELIVERY, session_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("delivery session started: %s port=%d", session_uid, port_no)
        return {"success": True, "session_uid": session_uid}

    def authorize_first_open(self, session_uid):
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != session_uid:
            return {"success": False, "reason": "SESSION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        preopen_uid = ctx.get("first_measurement_uid") or _new_uid()
        if not ctx.get("first_measurement_uid"):
            ctx["first_measurement_uid"] = preopen_uid
            self._store.update_work_context(session_uid, ctx)
        parent_cmd_uid = _new_uid()
        result = self._uart.send_authorize_delivery_first_open(
            session_uid=session_uid, port_no=port_no,
            preopen_measurement_uid=preopen_uid,
            parent_start_command_uid=parent_cmd_uid, remaining_ms=45000)
        if result["acked"]:
            ctx["phase"] = "WAITING_PREOPEN_WEIGHT"
            self._store.update_work_context(session_uid, ctx)
            return {"success": True, "session_uid": session_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def handle_mcu_event(self, frame):
        msg_name = frame.get("message_name", "")
        payload = frame.get("payload", {})
        slot = self._store.get_work_slot()
        if not slot:
            return
        ctx = slot.get("context", {})
        work_uid = slot["work_uid"]
        work_type = slot["work_type"]
        if msg_name == "WORK_PREOPEN_WEIGHT_READY":
            self._on_preopen_weight(ctx, payload, work_uid)
        elif msg_name == "WORK_POSTCLOSE_WEIGHT_READY":
            self._on_postclose_weight(ctx, payload, work_uid)
        elif msg_name == "DELIVERY_SELECTION":
            self._on_delivery_selection(ctx, payload, work_uid)
        elif msg_name == "CLEAN_FINAL_WEIGHT_READY" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_final_weight(ctx, payload, work_uid)
        elif msg_name == "CLEAN_COMPLETION_CONFIRMED" and work_type == WORK_TYPE_CLEAN:
            self._on_clean_completion(ctx, payload, work_uid)

    def _on_preopen_weight(self, ctx, payload, work_uid):
        measurement_uid = payload.get("measurementUid", "")
        weight_grams = payload.get("stableWeightGrams") if payload.get("stableWeightValid") else None
        status = payload.get("measurementStatus", "STABLE")
        ctx["phase"] = "PREOPEN_MEASURED"
        if status == "STABLE" and weight_grams is not None:
            ctx["first_weight_grams"] = weight_grams
            ctx["first_measurement_uid"] = measurement_uid
        self._store.update_work_context(work_uid, ctx)
        self._photo.capture_open_photos(work_uid)
        logger.info("preopen weight: %d g", weight_grams or 0)

    def _on_postclose_weight(self, ctx, payload, work_uid):
        weight_grams = payload.get("stableWeightGrams") if payload.get("stableWeightValid") else None
        status = payload.get("measurementStatus", "STABLE")
        round_idx = payload.get("roundIndex", 0)
        measurement_uid = payload.get("measurementUid", "")
        ctx["round_index"] = round_idx
        if status == "STABLE" and weight_grams is not None:
            prev_key = "first_weight_grams" if round_idx == 1 else f"round_{round_idx-1}_open_weight"
            prev_weight = ctx.get(prev_key)
            threshold = ctx.get("negative_weight_threshold_grams", 500)
            if prev_weight is not None and (weight_grams - prev_weight) < -threshold:
                ctx["negative_weight_anomaly"] = True
            ctx[f"round_{round_idx}_close_weight"] = weight_grams
            ctx[f"round_{round_idx}_measurement_uid"] = measurement_uid
            ctx["final_weight_grams"] = weight_grams
            ctx["final_measurement_uid"] = measurement_uid
        ctx["phase"] = "WAITING_SELECTION"
        self._store.update_work_context(work_uid, ctx)
        logger.info("postclose weight round=%d wt=%d", round_idx, weight_grams or 0)

    def _on_delivery_selection(self, ctx, payload, work_uid):
        selection = payload.get("selection", "END")
        session_uid = ctx.get("session_uid", work_uid)
        if selection in ("END", "WINDOW_EXPIRED"):
            ctx["phase"] = "FINALIZING"
            self._store.update_work_context(work_uid, ctx)
            self._photo.capture_close_photos(work_uid)
            first_wt = ctx.get("first_weight_grams")
            final_wt = ctx.get("final_weight_grams")
            net = None
            if first_wt is not None and final_wt is not None:
                net = final_wt - first_wt
            payload = {"event_uid": _new_uid(), "session_uid": session_uid,
                       "port_no": ctx["port_no"], "first_weight_grams": first_wt,
                       "final_weight_grams": final_wt, "net_weight_grams": net,
                       "unit_price_ten_thousandths": ctx["unit_price_ten_thousandths"],
                       "bag_qr_code": ctx["bag_qr_code"],
                       "negative_weight_anomaly": ctx.get("negative_weight_anomaly", False),
                       "round_count": ctx.get("round_index", 1),
                       "photos": self._photo.get_slot_urls(work_uid)}
            event_uid = payload["event_uid"]
            self._store.create_edge_event(
                event_uid=event_uid, event_type="DELIVERY_COMPLETE",
                payload=payload, work_uid=work_uid,
                work_state_update={"state": "COMPLETING", "context": ctx})
            logger.info("delivery complete: %s net=%d", session_uid, net or 0)
        else:
            port_no = ctx["port_no"]
            round_idx = ctx.get("round_index", 0) + 1
            postclose_uid = ctx.get(f"round_{round_idx-1}_measurement_uid", "")
            ctx["round_index"] = round_idx
            ctx[f"round_{round_idx}_open_weight"] = ctx.get(f"round_{round_idx-1}_close_weight")
            ctx["phase"] = "CONTINUING"
            self._store.update_work_context(work_uid, ctx)
            self._uart.send_authorize_delivery_local_continue(
                session_uid=session_uid, port_no=port_no,
                round_index=round_idx, postclose_measurement_uid=postclose_uid)
            logger.info("continue delivery round=%d", round_idx)

    def finalize_delivery(self, work_uid):
        self._store.release_work_slot(work_uid)
        logger.info("delivery session ended: %s", work_uid)

    def start_clean_operation(self, operation_uid, port_no, old_bag_qr, new_bag_qr):
        ctx = {"operation_uid": operation_uid, "port_no": port_no,
               "old_bag_qr": old_bag_qr, "new_bag_qr": new_bag_qr,
               "phase": "STARTED", "action_sequence": 0,
               "preunlock_weight_grams": None, "preunlock_measurement_uid": None,
               "final_weight_grams": None, "final_measurement_uid": None,
               "completion_confirmed": False}
        ok = self._store.acquire_work_slot(WORK_TYPE_CLEAN, operation_uid, port_no, ctx)
        if not ok:
            return {"success": False, "reason": "DEVICE_BUSY"}
        logger.info("clean operation started: %s port=%d", operation_uid, port_no)
        return {"success": True, "operation_uid": operation_uid}

    def authorize_clean_unlock(self, operation_uid):
        slot = self._store.get_work_slot()
        if not slot or slot["work_uid"] != operation_uid:
            return {"success": False, "reason": "OPERATION_NOT_ACTIVE"}
        ctx = slot["context"]
        port_no = ctx["port_no"]
        if not ctx.get("preunlock_measurement_uid"):
            ctx["preunlock_measurement_uid"] = _new_uid()
            self._store.update_work_context(operation_uid, ctx)
        ctx["action_sequence"] = ctx.get("action_sequence", 0) + 1
        action_seq = ctx["action_sequence"]
        ctx["phase"] = "UNLOCKING"
        self._store.update_work_context(operation_uid, ctx)
        result = self._uart.send_unlock_clean_door(
            operation_uid=operation_uid, port_no=port_no,
            action_sequence=action_seq,
            preunlock_measurement_uid=ctx["preunlock_measurement_uid"])
        if result["acked"]:
            ctx["phase"] = "ACTIVE"
            self._store.update_work_context(operation_uid, ctx)
            return {"success": True, "operation_uid": operation_uid}
        return {"success": False, "reason": result.get("error", "NACK")}

    def _on_clean_final_weight(self, ctx, payload, work_uid):
        weight_grams = payload.get("stableWeightGrams") if payload.get("stableWeightValid") else None
        ctx["final_weight_grams"] = weight_grams
        ctx["final_measurement_uid"] = payload.get("measurementUid", "")
        ctx["phase"] = "FINAL_WEIGHT_READY"
        self._store.update_work_context(work_uid, ctx)
        self._uart.send_clean_finish(
            operation_uid=ctx["operation_uid"], port_no=ctx["port_no"],
            action_sequence=ctx["action_sequence"])
        logger.info("clean final weight: %d g", weight_grams or 0)

    def _on_clean_completion(self, ctx, payload, work_uid):
        ctx["phase"] = "COMPLETION_CONFIRMED"
        ctx["completion_confirmed"] = True
        self._store.update_work_context(work_uid, ctx)
        self._photo.capture_clean_photos(work_uid)
        payload = {"event_uid": _new_uid(),
                   "operation_uid": ctx["operation_uid"], "port_no": ctx["port_no"],
                   "old_bag_qr": ctx["old_bag_qr"], "new_bag_qr": ctx["new_bag_qr"],
                   "preunlock_weight_grams": ctx.get("preunlock_weight_grams"),
                   "final_weight_grams": ctx.get("final_weight_grams"),
                   "action_sequence": ctx["action_sequence"],
                   "photos": self._photo.get_slot_urls(work_uid)}
        event_uid = payload["event_uid"]
        self._store.create_edge_event(
            event_uid=event_uid, event_type="CLEAN_COMPLETE",
            payload=payload, work_uid=work_uid,
            work_state_update={"state": "COMPLETING", "context": ctx})
        logger.info("clean complete: %s", ctx["operation_uid"])

    def finalize_clean(self, work_uid):
        self._store.release_work_slot(work_uid)
        logger.info("clean operation ended: %s", work_uid)
