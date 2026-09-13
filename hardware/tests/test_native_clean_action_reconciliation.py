"""Read-only compatibility for the checkpoint-v1 clean action/result chain.

The frozen sample contains one original unlock pulse, one finish request and
one physical-close confirmation.  It contains no historical reopen action.
"""
import json
import sqlite3

import pytest

from edge_store import EdgeStore
from mcu_action_evidence import canonical, executed_bundle
from mcu_process_handoff import process_event_receipt
from native_result_evidence import reconcile_legacy
import uart2_protocol as uart
from hardware.tests.native_confirmation_fixture import legacy_result_case


ACTION_UID = "77777777-7777-4777-8777-777777777777"
OTHER_UUID = "33333333-3333-4333-8333-333333333333"
CLEAN_CORRUPTION_ERRORS = {
    "finish-payload": "clean confirmation differs from its saved final candidate or order",
    "finish-saved-payload": "native clean intent custody is corrupt",
    "final-weight-receipt": "native process receipt is corrupt",
    "confirmation-uuid": "clean confirmation differs from its saved final candidate or order",
    "confirmation-chronology": "clean confirmation differs from its saved final candidate or order",
    "confirmation-saved-payload": "native clean confirmation custody is corrupt",
}


def _snapshot_path(path):
    with sqlite3.connect(path) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: tuple(conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')) for name in names}


def _clean_result_evidence(case):
    finish = case.store.get_native_process_event("CLEAN_FINISH_REQUESTED", 1, 4)
    confirmation = case.store.get_native_process_event("CLEAN_COMPLETION_CONFIRMED", 1, 6)
    if finish is None or confirmation is None:
        raise ValueError("checkpoint clean completion custody is missing")
    binding = case.store.get_native_action_binding(ACTION_UID)
    proof = case.store.get_native_action_confirmation(ACTION_UID)
    bundle = executed_bundle(case.store, ACTION_UID, json.loads(proof["bundle_json"]))
    if binding is None or bundle is None or canonical(bundle) != proof["bundle_json"]:
        raise ValueError("checkpoint clean unlock evidence is incomplete")

    start_record = case.store.get_native_command(case.start["mcuCommandUid"])
    start = uart.decode_payload(start_record["message_name"], start_record["payload"])
    expected = case.fixture["expected"]
    result_record = case.store.get_native_mcu_result(expected["mcuBootId"], expected["resultSequence"])
    result = uart.decode_payload("WORK_RESULT", result_record["payload"])
    evidence = reconcile_legacy(case.store, start_record, start, result, case.permit)
    if evidence["state"] != "MATCHED":
        raise ValueError("checkpoint clean result evidence is incomplete")
    report = case.store.get_native_result_report(
        case.permit, case.start["mcuCommandUid"], device_name=case.fixture["deviceName"])
    if report != expected["report"]:
        raise ValueError("checkpoint clean report differs from its frozen report")
    return finish, confirmation, binding, proof, bundle, evidence


def test_clean_checkpoint_reads_only_its_actual_first_unlock_and_completion(tmp_path):
    with legacy_result_case(tmp_path, clean=True) as case:
        finish, confirmation, binding, proof, bundle, evidence = _clean_result_evidence(case)
        unlocks = [record for record in case.store.list_native_commands()
            if record["message_name"] == "UNLOCK_CLEAN_DOOR"]
        assert len(unlocks) == 1
        grant = uart.decode_payload("UNLOCK_CLEAN_DOOR", unlocks[0]["payload"])
        assert grant["cleanActionSequence"] == 0
        assert grant["recoveryGeneration"] == 0
        assert binding["action"].action_key == "clean:first-unlock"
        assert binding["action"].action_kind == "UNLOCK_CLEAN_DOOR"
        assert proof["state"] == "CONFIRMED"
        assert bundle["version"] == "ecobin-native-output-effect-v1"
        assert "reopenIntent" not in bundle

        finish_value = uart.decode_payload(finish["message_name"], finish["payload"])
        confirmed_value = uart.decode_payload(confirmation["message_name"], confirmation["payload"])
        assert finish_value["cleanActionSequence"] == confirmed_value["cleanActionSequence"] == 1
        assert finish_value["mcuEventSequence"] < confirmation["final_event_sequence"] < confirmed_value["mcuEventSequence"]
        assert confirmed_value["cleanerPhysicalCloseConfirmed"] is True
        assert confirmed_value["lockPowerState"] == "DEENERGIZED"
        assert evidence["completion"]["payload"] == confirmation["payload"]
        assert evidence["execution"]["firstAction"] == bundle


def test_clean_typed_readers_and_legacy_reconcile_are_stable_after_reopen(tmp_path):
    with legacy_result_case(tmp_path, clean=True) as case:
        before = _snapshot_path(case.store.db_path)
        first = _clean_result_evidence(case)
        assert _clean_result_evidence(case) == first
        assert _snapshot_path(case.store.db_path) == before

        path = case.store.db_path
        case.store.close()
        case.store = EdgeStore(path)
        case.store.initialize()
        assert _clean_result_evidence(case) == first
        assert _snapshot_path(path) == before


def _replace_process_payload(case, table, event_sequence, payload):
    row = case.store._conn.execute(
        f'SELECT scope FROM "{table}" WHERE mcu_boot_id=1 AND event_sequence=?',
        (event_sequence,)).fetchone()
    name = "CLEAN_FINISH_REQUESTED" if table == "native_clean_intent" else "CLEAN_COMPLETION_CONFIRMED"
    saved = process_event_receipt(row["scope"], name, payload)
    case.store._conn.execute(
        f'UPDATE "{table}" SET payload=?,saved_payload=? WHERE mcu_boot_id=1 AND event_sequence=?',
        (payload, saved, event_sequence))


def _mutate_clean_checkpoint(case, damage):
    conn = case.store._conn
    if damage == "finish-payload":
        row = case.store.get_native_process_event("CLEAN_FINISH_REQUESTED", 1, 4)
        value = uart.decode_payload(row["message_name"], row["payload"])
        payload = uart.encode_payload(row["message_name"], value | {"uptimeMs": value["uptimeMs"] + 100000})
        _replace_process_payload(case, "native_clean_intent", 4, payload)
    elif damage == "finish-saved-payload":
        raw = conn.execute("""SELECT saved_payload FROM native_clean_intent
            WHERE mcu_boot_id=1 AND event_sequence=4""").fetchone()[0]
        conn.execute("""UPDATE native_clean_intent SET saved_payload=?
            WHERE mcu_boot_id=1 AND event_sequence=4""", (raw[:-1] + bytes([raw[-1] ^ 1]),))
    elif damage == "final-weight-receipt":
        raw = conn.execute("""SELECT saved_payload FROM native_process_receipt
            WHERE mcu_boot_id=1 AND event_sequence=5""").fetchone()[0]
        conn.execute("""UPDATE native_process_receipt SET saved_payload=?
            WHERE mcu_boot_id=1 AND event_sequence=5""", (raw[:-1] + bytes([raw[-1] ^ 1]),))
    elif damage in {"confirmation-uuid", "confirmation-chronology"}:
        row = case.store.get_native_process_event("CLEAN_COMPLETION_CONFIRMED", 1, 6)
        value = uart.decode_payload(row["message_name"], row["payload"])
        changes = ({"finalMeasurementUid": OTHER_UUID} if damage == "confirmation-uuid"
            else {"uptimeMs": 1})
        payload = uart.encode_payload(row["message_name"], value | changes)
        _replace_process_payload(case, "native_clean_confirmation", 6, payload)
    elif damage == "confirmation-saved-payload":
        raw = conn.execute("""SELECT saved_payload FROM native_clean_confirmation
            WHERE mcu_boot_id=1 AND event_sequence=6""").fetchone()[0]
        conn.execute("""UPDATE native_clean_confirmation SET saved_payload=?
            WHERE mcu_boot_id=1 AND event_sequence=6""", (raw[:-1] + bytes([raw[-1] ^ 1]),))
    else:
        raise AssertionError(damage)


@pytest.mark.parametrize("damage", [
    "finish-payload",
    "finish-saved-payload",
    "final-weight-receipt",
    "confirmation-uuid",
    "confirmation-chronology",
    "confirmation-saved-payload",
])
def test_corrupt_clean_process_or_confirmation_is_rejected_after_reopen_without_repair(
        tmp_path, damage):
    with legacy_result_case(tmp_path, clean=True) as case:
        with case.store.transaction():
            _mutate_clean_checkpoint(case, damage)
        path = case.store.db_path
        corrupt = _snapshot_path(path)
        case.store.close()
        case.store = EdgeStore(path)
        with pytest.raises(ValueError, match=CLEAN_CORRUPTION_ERRORS[damage]):
            case.store.initialize()
            _clean_result_evidence(case)
        case.store.close()
        assert _snapshot_path(path) == corrupt
