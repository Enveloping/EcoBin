"""MCU-local clean unlock/finish buttons under the rc.23 autonomous flow."""
import uuid

import uart2_protocol as uart
from edge_store import EdgeStore
from hardware.tests.test_mcu_delivery_execution import facts
from hardware.tests.test_mcu_simplified_execution import (
    final,
    library,
    runtime,
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import exchange, original_scope, take_samples
from hardware.tests.test_native_configuration import inputs


def active_clean(runtime, tmp_path, *, saved_edges=2):
    """Compatibility tuple for remaining consumers, now created by START alone."""
    del tmp_path, saved_edges
    _, clean, start, now = setup(runtime, clean=True)
    pulse_ms = inputs()["device"]["cleanSolenoidPulseMs"]
    now = tick(runtime, now, pulse_ms)
    scope = original_scope(start, clean=True) | {
        "eventMessageType": "WORK_PREUNLOCK_WEIGHT_READY",
        "stepSequence": 0,
        "configVersion": start["configVersion"],
    }
    frames = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)
    assert frames[0][1]["status"] == "HELD"
    initial = frames[1][1]
    command = {"mcuCommandUid": start["mcuCommandUid"], "unlockPulseMs": pulse_ms}
    return clean, start, initial, command, now


def request(runtime, clean, start, message, now, after=0):
    """Inject a real local button event; zero means the current action sequence."""
    lib, endpoint, *_ = runtime
    sequence = after or lib.TestSimple_CleanSequence(clean)
    return lib.McuCleanExecution_Request(
        clean,
        endpoint,
        uuid.UUID(start["operationUid"]).bytes,
        uart.MESSAGE_SPECS[message]["id"],
        sequence,
        now,
    )


def save_intent(runtime, start, message, sequence, now, store):
    """Historical process-event custody helper retained until its consumers migrate."""
    scope = original_scope(start, clean=True) | {
        "eventMessageType": message,
        "stepSequence": sequence,
        "configVersion": start["configVersion"],
    }
    event = exchange(runtime, "QUERY_PROCESS_EVENT", scope, now=now)[1][1]
    receipt = store.save_native_process_receipt(
        uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:],
        message,
        uart.encode_payload(message, event),
    )
    assert exchange(runtime, "PROCESS_EVENT_SAVED", payload=receipt, now=now)[0][1]["status"] == "RELEASED"
    return event


def test_local_unlock_request_reopens_once_without_waiting_for_pi(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    assert facts(runtime, now)["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_UNLOCK_PULSE"
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)

    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert not facts(runtime, now)["cleanLockPowered"]
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"


def test_local_finish_request_measures_and_completes_without_a_second_pi_confirmation(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    assert not request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=1)
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["physicalCloseConfirmed"]
    assert result["cleanActionSequence"] == 1
    assert result["initialWeightGrams"] == 500
    assert result["finalWeightGrams"] == 100


def test_unlock_then_finish_preserves_button_order_and_deduplicates_old_sequences(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    assert not request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=1)
    now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now, after=1)
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["cleanActionSequence"] == 2


def test_button_from_another_operation_or_stale_sequence_cannot_change_current_work(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    lib, endpoint, *_ = runtime
    finish_id = uart.MESSAGE_SPECS["CLEAN_FINISH_REQUESTED"]["id"]
    assert not lib.McuCleanExecution_Request(
        clean,
        endpoint,
        uuid.uuid4().bytes,
        finish_id,
        lib.TestSimple_CleanSequence(clean),
        now,
    )
    assert not request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now, after=999)
    assert state(runtime, start, now, clean=True)["phase"] == "CLEAN_ACTIVE"


def test_update_or_expiry_rejects_late_buttons_without_reenergizing_the_lock(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    runtime[0].ActuatorRuntime_StopForUpdate()
    now = tick(runtime, now, 0)
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    result = final(runtime, start, now, clean=True)
    assert result["finishReason"] == "CANCELLED"
    assert not facts(runtime, now)["cleanLockPowered"]


def test_completed_clean_result_is_immutable_under_late_buttons(runtime, tmp_path):
    clean, start, _, _, now = active_clean(runtime, tmp_path)
    assert request(runtime, clean, start, "CLEAN_FINISH_REQUESTED", now)
    now = take_samples(runtime, [100] * 5, start=now, measurement=2)
    result = final(runtime, start, now, clean=True)
    assert not request(runtime, clean, start, "CLEAN_UNLOCK_REQUESTED", now)
    now = tick(runtime, now, 60000)
    assert final(runtime, start, now, clean=True) == result
    assert not facts(runtime, now)["cleanLockPowered"]
