"""Fixtures dedicated to native confirmation tests, not an execution adapter.

New results are produced by the autonomous production C executor. Historical
upgrade scenarios load original checkpoint-v1 bytes and never relabel them v2.
"""
from contextlib import contextmanager
import json
from pathlib import Path


CHECKPOINT = "3178a99455ecbaf936d4468ede81e395afe94fcf"


def legacy_result_fixture(*, clean):
    """Read the committed checkpoint snapshot without regenerating old UART data."""
    path = Path(__file__).with_name("fixtures") / (
        "native-v1-clean-timeout.json" if clean else "native-v1-confirmation-delivery.json")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["sourceCommit"] == CHECKPOINT
    return fixture


def legacy_result_tables(fixture):
    """Return the exact reported checkpoint state, preserving frozen row bytes."""
    if "tables" in fixture:
        return fixture["tables"]
    return fixture["pendingTables"] | fixture["reportedOverrides"]


@contextmanager
def autonomous_result_case(runtime, tmp_path, *, clean, samples=(700,) * 5):
    from mcu_result_handoff import McuResultHandoff
    from hardware.tests.test_mcu_simplified_execution import tick, request, select
    from hardware.tests.test_mcu_work_preparation import take_samples
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_simplified_mcu_pi_business import real_work, restored_work_query

    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        case.occupancy = case.store.get_work_slot()
        now = case.wire.now
        if clean:
            now = tick(runtime, now, inputs()["device"]["cleanSolenoidPulseMs"])
            assert request(runtime, case.cleanup, case.start, now, "CLEAN_FINISH_REQUESTED")
        else:
            for duration in (100, case.start["deliveryAutoCloseMs"], 100,
                             inputs()["device"]["deliveryDoorTravelWaitMs"]):
                now = tick(runtime, now, duration)
        began = now
        now = take_samples(runtime, samples, start=began, measurement=2)
        if len(samples) != 5:
            now = tick(runtime, now, began + 5000 - now)
        if not clean and samples:
            assert select(runtime, case.delivery, now, "END")
        case.wire.now = now
        observed = restored_work_query(case, clean, 0)
        assert observed["status"] == "RESULT_HELD"
        handoff = McuResultHandoff(case.store, case.wire.write, dict(mcuBootId=case.boot,
            resultSequence=observed["resultSequence"], resultDigestSha256=observed["resultDigestSha256"],
            workUid=case.permit.work_uid))
        assert handoff.poll(0)
        assert case.wire.deliver(handoff, 0) == [
            ("RESULT_QUERY_REPLY", True), ("WORK_RESULT", True), ("RESULT_SAVED_REPLY", False)]
        try:
            yield case
        finally:
            # Some crash/restart cases replace case.store with a new connection.
            case.store.close()


@contextmanager
def legacy_result_case(tmp_path, *, clean):
    """Exact v1 report custody, with a real test updater for its original permit."""
    from types import SimpleNamespace
    from edge_store import EdgeStore
    from job_safety import JobPermit
    from hardware.tests.test_command_processor import make_real_job_safety

    fixture = legacy_result_fixture(clean=clean)
    tables = legacy_result_tables(fixture)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    updater, safety = make_real_job_safety(tmp_path)
    case = None
    try:
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
        permit = JobPermit(**fixture["permit"])
        command = store.get_command(permit.command_uid)["payload"]
        assert safety.request_job(command, work_type=permit.work_type, work_uid=permit.work_uid) == permit
        safety.begin_job(permit, begin_uid=permit.work_uid, digest=permit.request_digest_sha256)
        report = store.get_native_result_report(permit, fixture["startCommandUid"], device_name=fixture["deviceName"])
        assert report == fixture["expected"]["report"]
        tasks = store.list_native_result_report_tasks()
        assert json.loads(tasks[0]["report_json"])["version"] == "ecobin-native-result-report-v1"
        case = SimpleNamespace(store=store, safety=safety, permit=permit, clean=clean, fixture=fixture,
            start={"mcuCommandUid": fixture["startCommandUid"]}, occupancy=store.get_work_slot())
        yield case
    finally:
        if case is not None:
            case.store.close()
        store.close()
        updater.close()
