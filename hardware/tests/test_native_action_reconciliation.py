"""Read-only compatibility for checkpoint-v1 native action evidence.

These fixtures are historical database bytes.  Tests in this module must not
compile an old MCU or dispatch its retired action commands to the current MCU.
"""
import json
import sqlite3

import pytest

from edge_store import EdgeStore
from mcu_action_evidence import canonical, executed_bundle
from native_result_evidence import reconcile_legacy
import uart2_protocol as uart
from hardware.tests.native_confirmation_fixture import (
    CHECKPOINT,
    legacy_result_case,
    legacy_result_tables,
)


ACTION_UID = "77777777-7777-4777-8777-777777777777"
OTHER_UUID = "33333333-3333-4333-8333-333333333333"
CORRUPTION_ERRORS = {
    "uuid": "native command and permanent action binding mismatch",
    "boot": "native actuator evidence is corrupt",
    "port": "native actuator evidence is corrupt",
    "config": "native complete result does not match the original START",
    "digest": "native action confirmation is corrupt",
    "payload": "native action binding is corrupt",
    "saved-payload": "native actuator evidence is corrupt",
    "receipt": "native action binding is corrupt",
    "acceptance": "native report lost its original result evidence",
    "chronology": "native output chronology contradicts original measurement",
}


def _fixture_value(value):
    return bytes.fromhex(value["hex"]) if isinstance(value, dict) else value


def _snapshot_connection(conn):
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {
        name: tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid'))
        for name in names
    }


def _snapshot_path(path):
    with sqlite3.connect(path) as conn:
        return _snapshot_connection(conn)


def _read_legacy_evidence(case):
    """Run only historical readers; this function never creates new evidence."""
    binding = case.store.get_native_action_binding(ACTION_UID)
    proof = case.store.get_native_action_confirmation(ACTION_UID)
    if binding is None or proof is None or proof["state"] != "CONFIRMED":
        raise ValueError("checkpoint action binding/confirmation is missing")
    bundle = executed_bundle(case.store, ACTION_UID, json.loads(proof["bundle_json"]))
    if bundle is None or canonical(bundle) != proof["bundle_json"]:
        raise ValueError("checkpoint action execution evidence is incomplete")

    start_record = case.store.get_native_command(case.start["mcuCommandUid"])
    start = uart.decode_payload(start_record["message_name"], start_record["payload"])
    expected = case.fixture["expected"]
    result_record = case.store.get_native_mcu_result(
        expected["mcuBootId"], expected["resultSequence"])
    result = uart.decode_payload("WORK_RESULT", result_record["payload"])
    evidence = reconcile_legacy(case.store, start_record, start, result, case.permit)
    if evidence["state"] != "MATCHED":
        raise ValueError("checkpoint result evidence is incomplete")
    report = case.store.get_native_result_report(
        case.permit, case.start["mcuCommandUid"], device_name=case.fixture["deviceName"])
    if report != case.fixture["expected"]["report"]:
        raise ValueError("checkpoint report differs from its frozen report")
    return binding, proof, bundle, evidence, report


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
def test_checkpoint_source_and_every_committed_fixture_row_load_exactly(tmp_path, clean):
    with legacy_result_case(tmp_path, clean=clean) as case:
        assert case.fixture["sourceCommit"] == CHECKPOINT
        assert "executed_action_case" in case.fixture["producer"]
        for name, table in legacy_result_tables(case.fixture).items():
            columns = ",".join(f'"{column}"' for column in table["columns"])
            actual = [tuple(row) for row in case.store._conn.execute(
                f'SELECT {columns} FROM "{name}" ORDER BY rowid')]
            expected = [tuple(_fixture_value(value) for value in row) for row in table["rows"]]
            assert actual == expected, name


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
def test_frozen_action_acceptance_outputs_process_and_report_are_readable(tmp_path, clean):
    with legacy_result_case(tmp_path, clean=clean) as case:
        binding, proof, bundle, evidence, report = _read_legacy_evidence(case)
        expected_kind = "UNLOCK_CLEAN_DOOR" if clean else "AUTHORIZE_DELIVERY_FIRST_OPEN"
        expected_key = "clean:first-unlock" if clean else "delivery:first-open"
        assert binding["action"].action_kind == expected_kind
        assert binding["action"].action_key == expected_key
        assert binding["action"].receipt_uid == "66666666-6666-4666-8666-666666666666"
        command = case.store.get_native_command(ACTION_UID)
        assert command["payload"] == binding["command_payload"]
        assert command["write_claimed"] == 1
        assert command["decision_outcome"] == "ACCEPTED"
        assert bundle["command"]["payloadHex"] == command["payload"].hex()
        assert all(uart.decode_payload(item["messageName"], bytes.fromhex(item["payloadHex"]))["outcome"] == "ACCEPTED"
            for item in bundle["acceptance"].values())

        output_values = [uart.decode_payload(item["messageName"], bytes.fromhex(item["payloadHex"]))
            for item in bundle["outputs"]]
        states = ([item["lockPowerState"] for item in output_values] if clean
            else [item["command"] for item in output_values])
        assert states == (["ENERGIZED", "DEENERGIZED"] if clean else ["OPEN", "CLOSE"])
        initial = case.store.get_native_process_receipt(bytes.fromhex(bundle["initial"]["scopeHex"]))
        assert initial["payload"].hex() == bundle["initial"]["payloadHex"]
        assert initial["saved_payload"].hex() == bundle["initial"]["savedHex"]
        assert proof["bundle_json"] == canonical(bundle)
        assert evidence["state"] == "MATCHED"
        assert report == case.fixture["expected"]["report"]


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
def test_repeated_reads_and_sqlite_reopen_never_change_checkpoint_tables(tmp_path, clean):
    with legacy_result_case(tmp_path, clean=clean) as case:
        before = _snapshot_connection(case.store._conn)
        assert _read_legacy_evidence(case) == _read_legacy_evidence(case)
        assert _snapshot_connection(case.store._conn) == before

        path = case.store.db_path
        case.store.close()
        case.store = EdgeStore(path)
        case.store.initialize()
        _read_legacy_evidence(case)
        assert _snapshot_connection(case.store._conn) == before


def _valid_actuator_change(case, event_sequence, **changes):
    row = case.store.get_native_actuator_event(1, event_sequence)
    value = uart.decode_payload(row["message_name"], row["payload"]) | changes
    payload = uart.encode_payload(row["message_name"], value)
    saved = EdgeStore._native_actuator_record(row["message_name"], payload)["saved_payload"]
    case.store._conn.execute("""UPDATE native_actuator_event SET payload=?,saved_payload=?
        WHERE mcu_boot_id=1 AND event_sequence=?""", (payload, saved, event_sequence))


def _mutate_checkpoint(case, damage):
    conn = case.store._conn
    if damage == "uuid":
        permit = json.loads(conn.execute(
            "SELECT permit_json FROM native_action_binding WHERE action_uid=?", (ACTION_UID,)).fetchone()[0])
        permit["work_uid"] = OTHER_UUID
        conn.execute("UPDATE native_action_binding SET permit_json=? WHERE action_uid=?",
            (canonical(permit), ACTION_UID))
    elif damage == "boot":
        _valid_actuator_change(case, 2, mcuBootId=2)
    elif damage == "port":
        _valid_actuator_change(case, 2, portNo=2)
    elif damage == "config":
        uid = case.start["mcuCommandUid"]
        row = case.store.get_native_command(uid)
        value = uart.decode_payload(row["message_name"], row["payload"])
        value["configVersion"] += 1
        value["commandDigestSha256"] = uart.compute_command_digest(row["message_name"], value)
        conn.execute("UPDATE native_mcu_command SET payload=? WHERE command_uid=?",
            (uart.encode_payload(row["message_name"], value), uid))
    elif damage == "digest":
        conn.execute("UPDATE native_action_confirmation SET evidence_sha256=? WHERE action_uid=?",
            ("0" * 64, ACTION_UID))
    elif damage == "payload":
        raw = conn.execute("SELECT command_payload FROM native_action_binding WHERE action_uid=?",
            (ACTION_UID,)).fetchone()[0]
        conn.execute("UPDATE native_action_binding SET command_payload=? WHERE action_uid=?",
            (raw[:-1] + bytes([raw[-1] ^ 1]), ACTION_UID))
    elif damage == "saved-payload":
        raw = conn.execute("""SELECT saved_payload FROM native_actuator_event
            WHERE mcu_boot_id=1 AND event_sequence=2""").fetchone()[0]
        conn.execute("""UPDATE native_actuator_event SET saved_payload=?
            WHERE mcu_boot_id=1 AND event_sequence=2""", (raw[:-1] + bytes([raw[-1] ^ 1]),))
    elif damage == "receipt":
        conn.execute("UPDATE native_action_binding SET receipt_uid=? WHERE action_uid=?",
            (OTHER_UUID, ACTION_UID))
    elif damage == "acceptance":
        conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (ACTION_UID,))
    elif damage == "chronology":
        first = case.store.get_native_actuator_event(1, 2)
        uptime = uart.decode_payload(first["message_name"], first["payload"])["uptimeMs"] - 1
        _valid_actuator_change(case, 3, uptimeMs=uptime)
    else:
        raise AssertionError(damage)


@pytest.mark.parametrize("clean", [False, True], ids=["delivery", "clean"])
@pytest.mark.parametrize("damage", [
    "uuid", "boot", "port", "config", "digest", "payload", "saved-payload",
    "receipt", "acceptance", "chronology",
])
def test_corrupt_checkpoint_identity_or_custody_is_rejected_after_reopen_without_repair(
        tmp_path, clean, damage):
    with legacy_result_case(tmp_path, clean=clean) as case:
        with case.store.transaction():
            _mutate_checkpoint(case, damage)
        path = case.store.db_path
        corrupt = _snapshot_path(path)
        case.store.close()
        case.store = EdgeStore(path)
        with pytest.raises(ValueError, match=CORRUPTION_ERRORS[damage]):
            case.store.initialize()
            _read_legacy_evidence(case)
        case.store.close()
        assert _snapshot_path(path) == corrupt
