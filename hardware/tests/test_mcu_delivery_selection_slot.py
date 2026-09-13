"""Real C custody slot enforces saved post-close weight before a choice."""
import ctypes as c
import pytest
import uart2_protocol as uart
from contracts.tests.test_uart_v2_delivery_selection import selection_values
from contracts.tests.test_uart_v2_process_measurement import process_values
from contracts.tests.test_uart_v2_process_handoff import process_scope
from hardware.tests.test_mcu_process_event_slot import slot_lib, freeze, saved, query


def test_choice_cannot_replace_unsaved_weight_and_is_held_until_its_own_exact_save(slot_lib):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    post_scope = process_scope() | {"eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY"}
    choice_scope = process_scope() | {"eventMessageType": "DELIVERY_SELECTION"}
    values = selection_values()
    assert freeze(slot_lib, slot, process_values("WORK_POSTCLOSE_WEIGHT_READY"), post_scope, "WORK_POSTCLOSE_WEIGHT_READY")
    assert not freeze(slot_lib, slot, values, choice_scope, "DELIVERY_SELECTION")
    receipt = saved(process_values("WORK_POSTCLOSE_WEIGHT_READY"), "WORK_POSTCLOSE_WEIGHT_READY")
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert freeze(slot_lib, slot, values, choice_scope, "DELIVERY_SELECTION")
    assert query(slot_lib, slot, eventMessageType="DELIVERY_SELECTION")["status"] == "HELD"
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 3
    exact = saved(values, "DELIVERY_SELECTION")
    wrong = exact[:-1] + bytes([exact[-1] ^ 1])
    assert slot_lib.McuProcessEventSlot_Saved(slot, wrong, len(wrong)) == 4
    assert slot_lib.McuProcessEventSlot_Saved(slot, exact, len(exact)) == 1
    assert freeze(slot_lib, slot, values, choice_scope, "DELIVERY_SELECTION") == 0
    assert query(slot_lib, slot, eventMessageType="DELIVERY_SELECTION")["status"] == "RELEASED"


@pytest.mark.parametrize("changes", [{"roundIndex": 2}, {"configVersion": 8}, {"uptimeMs": 9999},
    {"mcuEventSequence": 3}, {"postCloseMeasurementUid": "44444444-4444-4444-8444-444444444444"},
    {"sessionUid": "44444444-4444-4444-8444-444444444444"},
    {"mcuCommandUid": "44444444-4444-4444-8444-444444444444"}])
def test_selection_cannot_substitute_another_round_weight_or_original_identity(slot_lib, changes):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    post_scope = process_scope() | {"eventMessageType": "WORK_POSTCLOSE_WEIGHT_READY"}
    assert freeze(slot_lib, slot, process_values("WORK_POSTCLOSE_WEIGHT_READY"), post_scope, "WORK_POSTCLOSE_WEIGHT_READY")
    receipt = saved(process_values("WORK_POSTCLOSE_WEIGHT_READY"), "WORK_POSTCLOSE_WEIGHT_READY")
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    values = selection_values() | changes
    scope = process_scope() | {"eventMessageType": "DELIVERY_SELECTION", "stepSequence": values["roundIndex"],
        "configVersion": values["configVersion"], "mcuCommandUid": values["mcuCommandUid"], "workUid": values["sessionUid"]}
    assert not freeze(slot_lib, slot, values, scope, "DELIVERY_SELECTION")
    assert query(slot_lib, slot, eventMessageType="WORK_POSTCLOSE_WEIGHT_READY")["status"] == "RELEASED"


@pytest.mark.parametrize("prior", ["absent", "preopen", "unavailable"])
def test_only_saved_available_postclose_weight_can_support_a_choice(slot_lib, prior):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    if prior != "absent":
        name = "WORK_PREOPEN_WEIGHT_READY" if prior == "preopen" else "WORK_POSTCLOSE_WEIGHT_READY"
        values = process_values(name)
        if prior == "unavailable":
            values.update(measurementKind="UNAVAILABLE", faultCode="WEIGHT_TIMEOUT")
        assert freeze(slot_lib, slot, values, process_scope() | {"eventMessageType": name}, name)
        receipt = saved(values, name)
        assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert not freeze(slot_lib, slot, selection_values(), process_scope() | {"eventMessageType": "DELIVERY_SELECTION"}, "DELIVERY_SELECTION")
