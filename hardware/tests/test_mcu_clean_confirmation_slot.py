"""The real C process slot binds confirmation to a saved final candidate."""
import ctypes as c
import pytest
import uart2_protocol as uart
from contracts.tests.test_uart_v2_clean_confirmation import NAME, confirmation_values, final_values
from contracts.tests.test_uart_v2_clean_intent import intent_scope
from hardware.tests.test_mcu_process_event_slot import slot_lib, freeze, saved, query


def test_confirmation_cannot_exist_without_exact_saved_final_weight(slot_lib):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    scope = intent_scope(NAME)
    assert not freeze(slot_lib, slot, confirmation_values(), scope, NAME)
    final = final_values()
    assert freeze(slot_lib, slot, final, intent_scope("CLEAN_FINAL_WEIGHT_READY"), "CLEAN_FINAL_WEIGHT_READY")
    assert not freeze(slot_lib, slot, confirmation_values(), scope, NAME)
    receipt = saved(final, "CLEAN_FINAL_WEIGHT_READY")
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert freeze(slot_lib, slot, confirmation_values(), scope, NAME)
    assert query(slot_lib, slot, **scope)["status"] == "HELD"
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 3
    receipt = saved(confirmation_values(), NAME)
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert query(slot_lib, slot, **scope)["status"] == "RELEASED"


@pytest.mark.parametrize("changes,scope_changes", [
    ({"finalMeasurementUid": "99999999-9999-4999-8999-999999999999"}, {}),
    ({"uptimeMs": 12019}, {}), ({"mcuEventSequence": 3}, {}),
    ({"cleanActionSequence": 2}, {"stepSequence": 2}), ({"configVersion": 8}, {"configVersion": 8}),
    ({}, {"commandDigestSha256": "55" * 32}),
    ({"mcuCommandUid": "99999999-9999-4999-8999-999999999999"},
        {"mcuCommandUid": "99999999-9999-4999-8999-999999999999"}),
])
def test_other_candidate_or_original_scope_cannot_follow_saved_weight(slot_lib, changes, scope_changes):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    final = final_values()
    final_scope = intent_scope("CLEAN_FINAL_WEIGHT_READY")
    assert freeze(slot_lib, slot, final, final_scope, "CLEAN_FINAL_WEIGHT_READY")
    receipt = saved(final, "CLEAN_FINAL_WEIGHT_READY")
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert not freeze(slot_lib, slot, confirmation_values() | changes, intent_scope(NAME) | scope_changes, NAME)
    assert query(slot_lib, slot, **final_scope)["status"] == "RELEASED"
    assert freeze(slot_lib, slot, confirmation_values(), intent_scope(NAME), NAME)


def test_wrong_digest_and_old_boot_cannot_release_confirmation(slot_lib):
    slot = (c.c_uint64 * 52)()
    slot_lib.McuProcessEventSlot_Init(slot, 42)
    assert freeze(slot_lib, slot, final_values(), intent_scope("CLEAN_FINAL_WEIGHT_READY"), "CLEAN_FINAL_WEIGHT_READY")
    receipt = saved(final_values(), "CLEAN_FINAL_WEIGHT_READY")
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 1
    assert freeze(slot_lib, slot, confirmation_values(), intent_scope(NAME), NAME)
    receipt = saved(confirmation_values(), NAME)
    wrong = receipt[:-1] + bytes([receipt[-1] ^ 1])
    assert slot_lib.McuProcessEventSlot_Saved(slot, wrong, len(wrong)) == 4
    assert query(slot_lib, slot, **intent_scope(NAME))["status"] == "HELD"
    slot_lib.McuProcessEventSlot_Init(slot, 43)
    assert slot_lib.McuProcessEventSlot_Saved(slot, receipt, len(receipt)) == 5
    assert query(slot_lib, slot, **intent_scope(NAME))["status"] == "BOOT_MISMATCH"
