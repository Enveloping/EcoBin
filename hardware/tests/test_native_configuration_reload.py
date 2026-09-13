"""Standing settings reload uses real SQLite and simulated, explicit wire facts.

No UART, updater, network or physical device is opened by this component.
"""
import json
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from edge_store import EdgeStore
from hardware.tests.test_native_business_runtime import configuration_command
from hardware.tests.test_native_simplified_result import bind_boot, IDENTITY
from mcu_configuration import NativeMcuConfiguration
import native_configuration_reload as reload
import uart2_protocol as uart


def register_part(store, candidate, application, uid, boot, index, *, accept=True):
    name, raw = candidate.encode_part(index, application_uid=application,
        mcu_command_uid=uid, target_mcu_boot_id=boot, command_sequence=index)
    values = uart.decode_payload(name, raw)
    record = store.prepare_native_command(name, uid, boot,
        {key: value for key, value in values.items() if key not in IDENTITY})
    if accept:
        accept_part(store, record)
    return store.get_native_command(uid)


def accept_part(store, record):
    assert store.claim_native_command_write(record["command_uid"])
    value = uart.decode_payload(record["message_name"], record["payload"])
    assert store.save_native_command_observation("COMMAND_DECISION", uart.encode_payload("COMMAND_DECISION",
        {key: value[key] for key in IDENTITY} | dict(currentMcuBootId=record["mcu_boot_id"],
                                                  outcome="ACCEPTED", errorCode="NONE")))


def install_configuration(store, command, boot):
    candidate = NativeMcuConfiguration.from_cloud_payload(command["payload"])
    application = command["payload"]["applicationUid"]
    assert store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert store.claim_next_command()["command_uid"] == command["commandUid"]
    parts = [str(uuid.uuid4()) for _ in range(candidate.part_count)]
    assert store.save_configuration_edge(command, parts) == "ACCEPTED"
    for index, uid in enumerate(parts, 1):
        register_part(store, candidate, application, uid, boot, index)
    config = command["payload"]["config"]
    assert store.apply_configuration_result(dict(applicationUid=application, configVersion=config["version"],
        contentSha256=config["contentSha256"], mcuPayloadSha256=config["mcuPayloadSha256"],
        mcuCommandUid=parts[-1], status="APPLIED")) == "ACCEPTED"
    return candidate, application, parts


@pytest.fixture
def applied(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    try:
        command = configuration_command()
        boot = bind_boot(store)
        candidate, application, parts = install_configuration(store, command, boot)
        yield SimpleNamespace(store=store, command=command, application=application,
                              candidate=candidate, boot=boot, parts=parts)
    finally:
        store.close()


def prepare(case, boot):
    return reload.prepare(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")


def test_new_mcu_boot_prepares_one_local_reload_without_reopening_cloud_application(applied):
    case = applied
    boot = bind_boot(case.store)
    original = case.store.get_configuration(case.application)
    cloud = case.store.get_command(case.command["commandUid"])
    events = case.store.list_pending_events()
    commands = case.store.list_native_commands()
    marker = prepare(case, boot)
    assert marker["state"] == "PREPARED"
    assert marker["application_uid"] == case.application
    assert marker["command_uid"] == case.command["commandUid"]
    assert marker["target_mcu_boot_id"] == boot
    assert marker["payload"] == case.command["payload"]
    assert len(marker["part_command_uids"]) == case.candidate.part_count
    assert not set(marker["part_command_uids"]) & set(case.parts)
    assert prepare(case, boot) == marker
    assert case.store.get_configuration(case.application) == original
    assert case.store.list_pending_events() == events
    assert case.store.list_native_commands() == commands
    after = case.store.get_command(case.command["commandUid"])
    assert {k: v for k, v in after.items() if k not in {"result", "result_json"}} == {
        k: v for k, v in cloud.items() if k not in {"result", "result_json"}}
    assert after["state"] == "COMPLETED"


def test_pi_restarts_keep_claimed_part_and_complete_only_after_all_actual_acceptances(applied):
    case = applied
    boot = bind_boot(case.store)
    assert reload.read(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1") is None
    marker = prepare(case, boot)
    first = register_part(case.store, case.candidate, case.application, marker["part_command_uids"][0],
                          boot, 1, accept=False)
    assert case.store.claim_native_command_write(first["command_uid"])
    first = case.store.get_native_command(first["command_uid"])
    for _ in range(2):
        case.store.close()
        case.store.initialize()
        assert prepare(case, boot) == marker
        assert reload.read(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1") == marker
        assert case.store.get_native_command(first["command_uid"]) == first
        with pytest.raises(ValueError, match="all parts actually ACCEPTED"):
            reload.complete(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    value = uart.decode_payload(first["message_name"], first["payload"])
    assert case.store.save_native_command_observation("COMMAND_DECISION", uart.encode_payload("COMMAND_DECISION",
        {key: value[key] for key in IDENTITY} | dict(currentMcuBootId=boot, outcome="ACCEPTED", errorCode="NONE")))
    for index, uid in enumerate(marker["part_command_uids"][1:], 2):
        register_part(case.store, case.candidate, case.application, uid, boot, index)
    original = case.store.get_configuration(case.application)
    events = case.store.list_pending_events()
    commands = case.store.list_native_commands()
    done = reload.complete(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    assert done == marker | {"state": "APPLIED"}
    assert prepare(case, boot) == done
    assert reload.complete(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1") == done
    assert case.store.get_configuration(case.application) == original
    assert case.store.list_pending_events() == events
    assert case.store.list_native_commands() == commands


@pytest.mark.parametrize("old_complete", [False, True])
def test_another_confirmed_mcu_reboot_allocates_new_ids_but_keeps_old_command_history(applied, old_complete):
    case = applied
    boot = bind_boot(case.store)
    marker = prepare(case, boot)
    for index, uid in enumerate(marker["part_command_uids"] if old_complete else marker["part_command_uids"][:1], 1):
        record = register_part(case.store, case.candidate, case.application, uid, boot, index, accept=old_complete)
        if not old_complete:
            assert case.store.claim_native_command_write(record["command_uid"])
    if old_complete:
        reload.complete(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    newer = bind_boot(case.store)
    old_records = case.store.list_native_commands()
    assert all(record["boot_retired"] for record in old_records)
    next_marker = prepare(case, newer)
    assert next_marker["state"] == "PREPARED"
    assert not set(next_marker["part_command_uids"]) & set(marker["part_command_uids"])
    assert next_marker["evidence_sha256"] != marker["evidence_sha256"]
    assert prepare(case, newer) == next_marker
    assert case.store.list_native_commands() == old_records
    assert case.store.get_configuration(case.application)["part_command_uids"] == case.parts
    with pytest.raises(ValueError, match="current boot"):
        prepare(case, boot)


def test_expired_original_authorization_does_not_expire_already_applied_standing_settings(applied, monkeypatch):
    from datetime import datetime, timezone
    from onenet_wire import validate_command_envelope
    case = applied
    monkeypatch.setattr("onenet_wire.local_deadline_reference", lambda: datetime(2031, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="command expired"):
        validate_command_envelope(case.command)
    boot = bind_boot(case.store)
    assert prepare(case, boot)["state"] == "PREPARED"
    assert case.store.get_command(case.command["commandUid"])["payload"]["expiresAt"] == case.command["expiresAt"]


def test_pi_only_restart_cannot_create_a_reload_in_the_original_mcu_boot(applied):
    case = applied
    case.store.close()
    case.store.initialize()
    with pytest.raises(ValueError):
        prepare(case, case.boot)
    assert case.store.get_command(case.command["commandUid"])["result"] is None


@pytest.mark.parametrize("changed", ["device", "application", "pending", "cloud_state", "cloud_hash",
                                     "config_payload", "commit", "original_acceptance", "original_bytes"])
def test_reload_rejects_a_different_or_unapplied_original_source(applied, changed):
    case = applied
    boot = bind_boot(case.store)
    device, application = "device-1", case.application
    with case.store.transaction() as conn:
        if changed == "device":
            device = "other-device"
        elif changed == "application":
            application = str(uuid.uuid4())
        elif changed == "pending":
            conn.execute("UPDATE configuration_state SET state='EDGE_SAVED' WHERE application_uid=?", (application,))
        elif changed == "cloud_state":
            conn.execute("UPDATE command_inbox SET state='FAILED' WHERE command_uid=?", (case.command["commandUid"],))
        elif changed == "cloud_hash":
            conn.execute("UPDATE command_inbox SET canonical_sha256=? WHERE command_uid=?", ("0" * 64, case.command["commandUid"]))
        elif changed == "config_payload":
            payload = json.loads(json.dumps(case.command["payload"]))
            payload["ports"][0]["displayName"] = "changed after application"
            conn.execute("UPDATE configuration_state SET payload_json=? WHERE application_uid=?", (json.dumps(payload), application))
        elif changed == "commit":
            conn.execute("UPDATE configuration_state SET commit_mcu_command_uid=? WHERE application_uid=?", (case.parts[0], application))
        elif changed == "original_acceptance":
            conn.execute("DELETE FROM native_mcu_command_observation WHERE command_uid=?", (case.parts[0],))
        elif changed == "original_bytes":
            first = case.store.get_native_command(case.parts[0])
            name, raw = case.candidate.encode_part(1, application_uid=str(uuid.uuid4()),
                mcu_command_uid=first["command_uid"], target_mcu_boot_id=case.boot, command_sequence=first["command_sequence"])
            conn.execute("UPDATE native_mcu_command SET payload=? WHERE command_uid=?", (raw, first["command_uid"]))
    cloud = case.store.get_command(case.command["commandUid"])
    with pytest.raises(ValueError):
        reload.prepare(case.store, application, target_mcu_boot_id=boot, device_name=device)
    assert case.store.get_command(case.command["commandUid"]) == cloud


@pytest.mark.parametrize("witness", ["reserved_only", "recognized_without_reply", "historical_only"])
def test_reserved_or_historical_boot_identity_is_not_current_reboot_evidence(applied, witness):
    case = applied
    probe = case.store.reserve_native_query_id()
    boot = case.store.reserve_native_boot_id(probe)
    if witness == "recognized_without_reply":
        assert case.store.recognize_native_boot_id(boot)
    elif witness == "historical_only":
        boot = bind_boot(case.store)
        bind_boot(case.store)
    with pytest.raises(ValueError, match="current boot"):
        prepare(case, boot)
    assert case.store.get_command(case.command["commandUid"])["result"] is None


@pytest.mark.parametrize("changed", ["gap", "foreign_application", "rejected", "unwitnessed_acceptance", "unclaimed_acceptance"])
def test_registered_reload_part_must_be_exact_ordered_and_actually_accepted(applied, changed):
    case = applied
    boot = bind_boot(case.store)
    marker = prepare(case, boot)
    index = 2 if changed == "gap" else 1
    record = register_part(case.store, case.candidate,
        str(uuid.uuid4()) if changed == "foreign_application" else case.application,
        marker["part_command_uids"][index - 1], boot, index, accept=False)
    if changed == "rejected":
        assert case.store.claim_native_command_write(record["command_uid"])
        value = uart.decode_payload(record["message_name"], record["payload"])
        assert case.store.save_native_command_observation("COMMAND_DECISION", uart.encode_payload("COMMAND_DECISION",
            {key: value[key] for key in IDENTITY} | dict(currentMcuBootId=boot, outcome="REJECTED", errorCode="BUSY")))
    elif changed in {"unwitnessed_acceptance", "unclaimed_acceptance"}:
        if changed == "unwitnessed_acceptance":
            assert case.store.claim_native_command_write(record["command_uid"])
        with case.store.transaction() as conn:
            conn.execute("UPDATE native_mcu_command SET decision_outcome='ACCEPTED', decision_error='NONE' WHERE command_uid=?",
                         (record["command_uid"],))
    for action in (reload.prepare, reload.read, reload.complete):
        with pytest.raises(ValueError):
            action(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    assert case.store.get_command(case.command["commandUid"])["result"][reload.MARKER]["state"] == "PREPARED"


@pytest.mark.parametrize("changed", ["null", "array", "digest", "state", "part_uid", "premature_applied"])
def test_bad_reload_marker_is_not_silently_replaced(applied, changed):
    case = applied
    boot = bind_boot(case.store)
    prepare(case, boot)
    result = case.store.get_command(case.command["commandUid"])["result"]
    if changed == "null":
        result[reload.MARKER] = None
    elif changed == "array":
        result = []
    elif changed == "digest":
        result[reload.MARKER]["evidenceSha256"] = "0" * 64
    elif changed == "state":
        result[reload.MARKER]["state"] = "FAILED"
    elif changed == "part_uid":
        result[reload.MARKER]["evidence"]["partCommandUids"][0] = str(uuid.uuid4())
    else:
        result[reload.MARKER]["state"] = "APPLIED"
    with case.store.transaction() as conn:
        conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?", (json.dumps(result), case.command["commandUid"]))
    for action in (reload.prepare, reload.read, reload.complete):
        with pytest.raises(ValueError):
            action(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    assert case.store.get_command(case.command["commandUid"])["result"] == result


@pytest.mark.parametrize("stage", ["prepare", "complete"])
def test_failed_marker_commit_rolls_back_without_changing_original_history(applied, stage):
    case = applied
    boot = bind_boot(case.store)
    if stage == "complete":
        marker = prepare(case, boot)
        for index, uid in enumerate(marker["part_command_uids"], 1):
            register_part(case.store, case.candidate, case.application, uid, boot, index)
    cloud = case.store.get_command(case.command["commandUid"])
    source = case.store.get_configuration(case.application)
    commands = case.store.list_native_commands()
    with case.store.transaction() as conn:
        conn.execute("""CREATE TRIGGER fail_configuration_reload BEFORE UPDATE OF result_json ON command_inbox
            BEGIN SELECT RAISE(ABORT, 'injected reload marker write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        getattr(reload, stage)(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    assert case.store.get_command(case.command["commandUid"]) == cloud
    assert case.store.get_configuration(case.application) == source
    assert case.store.list_native_commands() == commands
    with case.store.transaction() as conn:
        conn.execute("DROP TRIGGER fail_configuration_reload")
    case.store.close()
    case.store.initialize()
    assert getattr(reload, stage)(case.store, case.application,
        target_mcu_boot_id=boot, device_name="device-1")["state"] == ("PREPARED" if stage == "prepare" else "APPLIED")


def test_latest_applied_configuration_change_invalidates_old_reload_source(applied):
    from hardware.tests.test_native_configuration_resume import newer
    case = applied
    boot = bind_boot(case.store)
    prepare(case, boot)
    command = newer(configuration_command())
    _, application, parts = install_configuration(case.store, command, boot)
    boot = bind_boot(case.store)
    for action in (reload.prepare, reload.read, reload.complete):
        with pytest.raises(ValueError, match="latest APPLIED"):
            action(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    marker = reload.prepare(case.store, application, target_mcu_boot_id=boot, device_name="device-1")
    assert marker["payload"] == command["payload"]
    assert marker["command_uid"] == command["commandUid"]
    assert not set(marker["part_command_uids"]) & set(parts)


def test_read_and_duplicate_completion_never_touch_new_business_bag_or_unrelated_result(applied):
    case = applied
    boot = bind_boot(case.store)
    with case.store.transaction() as conn:
        conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=?",
                     (json.dumps({"native_pending": True, "anotherLocalReceipt": {"saved": True}}), case.command["commandUid"]))
    marker = prepare(case, boot)
    for index, uid in enumerate(marker["part_command_uids"], 1):
        register_part(case.store, case.candidate, case.application, uid, boot, index)
    done = reload.complete(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1")
    bag = str(uuid.uuid4())
    case.store.set_state("current_bag_uid", bag)
    case.store.set_state("current_tare_weight_grams", "123")
    case.store.set_state("native_blocking_fault", "SOME_UNRELATED_FAULT")
    assert case.store.acquire_work_slot("DELIVERY", str(uuid.uuid4()), 1, {"phase": "NATIVE_RUNNING", "bagUid": bag})
    slot = case.store.get_work_slot()
    events = case.store.list_pending_events()
    for action in (reload.prepare, reload.read, reload.complete):
        assert action(case.store, case.application, target_mcu_boot_id=boot, device_name="device-1") == done
    assert case.store.get_work_slot() == slot
    assert case.store.get_state("current_bag_uid") == bag
    assert case.store.get_state("current_tare_weight_grams") == "123"
    assert case.store.get_state("native_blocking_fault") == "SOME_UNRELATED_FAULT"
    assert case.store.list_pending_events() == events
    result = case.store.get_command(case.command["commandUid"])["result"]
    assert result["native_pending"] is True
    assert result["anotherLocalReceipt"] == {"saved": True}


def test_new_marker_cannot_itself_retire_old_boot_commands(applied):
    case = applied
    boot = bind_boot(case.store)
    marker = prepare(case, boot)
    first = register_part(case.store, case.candidate, case.application, marker["part_command_uids"][0], boot, 1, accept=False)
    newer = bind_boot(case.store)
    # Simulate missing/corrupt retirement. Only save_native_boot_observation owns
    # that transition; preparing a reload must not silently repair or invent it.
    with case.store.transaction() as conn:
        conn.execute("UPDATE native_mcu_command SET boot_retired=0 WHERE command_uid=?", (first["command_uid"],))
    original = case.store.get_command(case.command["commandUid"])
    with pytest.raises(ValueError, match="retired"):
        prepare(case, newer)
    assert case.store.get_command(case.command["commandUid"]) == original
    assert not case.store.get_native_command(first["command_uid"])["boot_retired"]
