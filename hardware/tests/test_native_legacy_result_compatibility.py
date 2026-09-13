"""Read actual checkpoint v1 data without compiling or running old C in tests.

The JSON was produced by the real C host execution and old Pi report creator
at 3178a994, not by changing a v2 report's version. All data are synthetic.

To regenerate, export ``git archive 3178a994 hardware hardware_mcu contracts``
into a NEW temporary directory, then run this file with Python 3.11 and
``--export-checkpoint <extracted-source>``. Its stdout is an apply_patch patch.
That explicit maintenance command uses cached Clang and emits no binary DB.
Ordinary pytest runs only load the checked-in JSON and current Python code.
"""
from contextlib import contextmanager
import json
from pathlib import Path

import pytest


FIXTURE = Path(__file__).with_name("fixtures") / "native-v1-clean-timeout.json"
CHECKPOINT = "3178a99455ecbaf936d4468ede81e395afe94fcf"


@contextmanager
def loaded_checkpoint(tmp_path, *, reported):
    from edge_store import EdgeStore

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["sourceCommit"] == CHECKPOINT
    tables = fixture["pendingTables"] | (fixture["reportedOverrides"] if reported else {})
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        # Load old facts into today's schema, then really close/open the DB.
        # Disable FK enforcement only during fixture import, not validation.
        store._conn.execute("PRAGMA foreign_keys=OFF")
        with store.transaction() as conn:
            for name, table in tables.items():
                conn.execute(f'DELETE FROM "{name}"')
                columns = ",".join(f'"{column}"' for column in table["columns"])
                placeholders = ",".join("?" for _ in table["columns"])
                rows = [[bytes.fromhex(value["hex"]) if isinstance(value, dict) else value
                    for value in row] for row in table["rows"]]
                conn.executemany(f'INSERT INTO "{name}" ({columns}) VALUES ({placeholders})', rows)
        store._conn.execute("PRAGMA foreign_keys=ON")
        assert not store._conn.execute("PRAGMA foreign_key_check").fetchall()
        store.close()
        store.initialize()
        yield store, fixture
    finally:
        store.close()


def test_checkpoint_final_bytes_keep_real_timeout_and_manual_confirmation(tmp_path):
    import uart2_protocol as uart

    with loaded_checkpoint(tmp_path, reported=False) as (store, fixture):
        expected = fixture["expected"]
        row = store.get_native_mcu_result(expected["mcuBootId"], expected["resultSequence"])
        result = uart.decode_payload("WORK_RESULT", row["payload"])
        assert result["resultDigestSha256"] == expected["resultDigestSha256"]
        assert result["finishReason"] == "CLEAN_CONFIRMED"
        assert result["physicalCloseConfirmed"] is True
        assert result["initialWeightGrams"] == 500
        assert result["finalKind"] == "UNAVAILABLE" and result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        assert result["finalElapsedMs"] == 5000 and result["finalSampleCount"] == 0


def test_frozen_v1_report_survives_cold_start_without_rewriting_payload(tmp_path):
    from job_safety import JobPermit
    from native_result_report import NativeResultReporter
    from hardware.tests.test_command_processor import make_real_job_safety

    updater, safety = make_real_job_safety(tmp_path)
    try:
        with loaded_checkpoint(tmp_path, reported=True) as (store, fixture):
            permit = JobPermit(**fixture["permit"])
            expected = fixture["expected"]
            before = store.get_event(expected["eventUid"])
            payload = json.loads(before["payload_json"])
            slot = store.get_work_slot()
            reporter = NativeResultReporter(store, safety, device_name=fixture["deviceName"])
            for _ in range(2):
                assert reporter.prepare(permit, fixture["startCommandUid"]) == expected["report"]
                assert store.get_event(expected["eventUid"]) == before
                assert store.get_work_slot() == slot
                assert len(store.list_pending_events()) == 1
                report = store.list_native_result_report_tasks()[0]
                assert json.loads(report["report_json"])["version"] == "ecobin-native-result-report-v1"
                store.close()
                store.initialize()
            assert payload["payloadSha256"] == expected["payloadSha256"]
            assert payload["payload"]["cleanerCompletionConfirmed"] is True
            assert payload["payload"]["removedNetWeightGrams"] is None
            assert payload["payload"]["newBaselineWeightGrams"] is None
            assert payload["payload"]["cleanerConfirmedFinalMeasurement"]["reportedWeightGrams"] is None
    finally:
        updater.close()


def test_unreported_old_final_is_present_even_after_confirmed_mcu_reboot(tmp_path):
    import uart2_protocol as uart
    from job_safety import JobPermit
    from native_result_report import NativeResultReporter
    from hardware.tests.test_command_processor import make_real_job_safety

    updater, safety = make_real_job_safety(tmp_path)
    try:
        with loaded_checkpoint(tmp_path, reported=False) as (store, fixture):
            permit = JobPermit(**fixture["permit"])
            expected = fixture["expected"]
            before = store.get_native_mcu_result(expected["mcuBootId"], expected["resultSequence"])
            probe = store.reserve_native_query_id()
            boot = store.reserve_native_boot_id(probe)
            assert boot > expected["mcuBootId"]
            assert store.save_native_boot_observation("BIND_BOOT_REPLY", uart.encode_payload("BIND_BOOT_REPLY",
                dict(probeId=probe, proposedMcuBootId=boot, mcuBootId=boot, status="BOUND")))
            store.close()
            store.initialize()
            decision = store.evaluate_native_work_recovery(permit, fixture["startCommandUid"], current_boot=lambda: boot)
            assert decision["status"] == "COMPLETE_RESULT_AVAILABLE"
            assert decision["evidence"]["state"] == "MATCHED"
            assert decision["result"]["payload"] == before["payload"]
            assert not store.list_native_work_recovery_intents(permit.work_uid)
            assert not store.get_native_delivery_issue(permit.work_uid)
            # Today's producer uses FAILED for a missing clean final weight.
            # Historical complete data stay available for explicit classification,
            # but must not silently become a successful v2 clean report.
            reporter = NativeResultReporter(store, safety, device_name=fixture["deviceName"])
            assert reporter.prepare(permit, fixture["startCommandUid"]) == {"state": "WAITING_FOR_RESULT_POLICY"}
            assert not store.list_pending_events()
            assert store.list_native_result_report_tasks()[0]["state"] == "PENDING_CLASSIFICATION"
    finally:
        updater.close()


def test_v1_verification_still_requires_its_original_process_custody(tmp_path):
    from job_safety import JobPermit

    with loaded_checkpoint(tmp_path, reported=True) as (store, fixture):
        with store.transaction() as conn:
            conn.execute("DELETE FROM native_process_receipt")
        with pytest.raises(ValueError, match="original result evidence"):
            store.get_native_result_report(JobPermit(**fixture["permit"]), fixture["startCommandUid"],
                device_name=fixture["deviceName"])


def _export_checkpoint(source):
    """Explicit fixture maintenance only; never called by pytest."""
    from dataclasses import asdict
    import sys
    import tempfile

    source = Path(source).resolve(strict=True)
    sys.path[:0] = [str(source), str(source / "hardware")]
    from hardware.tests.test_mcu_work_preparation import library, runtime
    from hardware.tests.test_mcu_delivery_execution import executed_action_case
    from hardware.tests.test_native_result_report import finish_with_samples, original_command, validate_event
    from native_result_report import NativeResultReporter
    import uart2_protocol as uart

    # Clang's loaded DLL cannot be deleted on Windows before Python exits.
    # Leave only this uniquely named, clearly reported generation directory.
    base = Path(tempfile.mkdtemp(prefix="ecobin-v1-fixture-generate-"))
    print(f"Fixture generation files: {base}", file=sys.stderr)

    class Factory:
        def mktemp(self, name):
            path = base / name
            path.mkdir()
            return path

    def snapshot(store):
        tables = {}
        names = [row[0] for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            if name == "schema_version":
                continue
            cursor = store._conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')
            rows = [[{"hex": value.hex()} if isinstance(value, bytes) else value for value in row] for row in cursor]
            if rows:
                tables[name] = {"columns": [column[0] for column in cursor.description], "rows": rows}
        return tables

    native_generator = runtime.__wrapped__(library.__wrapped__(Factory()))
    native = next(native_generator)
    case_path = base / "clean-timeout"
    case_path.mkdir()
    try:
        with executed_action_case(native, case_path, clean_work=True, cloud_command_factory=original_command) as case:
            saved = finish_with_samples(case, native, [])
            value = uart.decode_payload("WORK_RESULT", saved["payload"])
            assert value["finishReason"] == "CLEAN_CONFIRMED"
            assert value["finalKind"] == "UNAVAILABLE" and value["finalFaultCode"] == "WEIGHT_TIMEOUT"
            pending = snapshot(case.store)
            report = NativeResultReporter(case.store, case.safety, device_name="device-1").prepare(
                case.permit, case.start["mcuCommandUid"])
            event = json.loads(case.store.get_event(report["eventUid"])["payload_json"])
            validate_event(event)
            reported = snapshot(case.store)
            task = reported["native_result_report_outbox"]
            binding = json.loads(task["rows"][0][task["columns"].index("report_json")])
            assert binding["version"] == "ecobin-native-result-report-v1", "source must be the v1 checkpoint"
            fixture = dict(
                description="Synthetic host execution of checkpoint C clean flow: manual confirmation after real five-second scale timeout; no hardware or real credentials.",
                sourceCommit=CHECKPOINT,
                producer="test_mcu_work_preparation.library/runtime + executed_action_case(clean_work=True) + finish_with_samples([]) + NativeResultReporter.prepare",
                deviceName="device-1", permit=asdict(case.permit), startCommandUid=case.start["mcuCommandUid"],
                expected=dict(report=report, eventUid=event["eventUid"], payloadSha256=event["payloadSha256"],
                    mcuBootId=value["mcuBootId"], resultSequence=value["resultSequence"], resultDigestSha256=value["resultDigestSha256"]),
                pendingTables=pending,
                reportedOverrides={name: rows for name, rows in reported.items() if pending.get(name) != rows})
            text = json.dumps(fixture, ensure_ascii=True, indent=2, sort_keys=True)
            print("*** Begin Patch\n*** Add File: hardware/tests/fixtures/native-v1-clean-timeout.json")
            print("\n".join("+" + line for line in text.splitlines()))
            print("*** End Patch")
    finally:
        native_generator.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-checkpoint", required=True, metavar="EXTRACTED_SOURCE")
    _export_checkpoint(parser.parse_args().export_checkpoint)
