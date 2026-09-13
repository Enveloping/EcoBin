"""Actual C UART endpoint and Python custody, no mechanical command execution."""
import uart2_protocol as uart
from hardware.tests.test_mcu_control_endpoint import endpoint, bind, exchange, request
from hardware.tests.test_mcu_actuator_event_credits import reserve, publish, next_held
from edge_store import EdgeStore
import pytest


def query(endpoint, query_id=1, boot=42):
    return exchange(endpoint, request("QUERY_ACTUATOR_EVENT", dict(queryId=query_id,
        targetMcuBootId=boot, afterMcuEventSequence=0)))


def test_uart_query_replays_original_on_and_exact_saved_only_releases_that_record(endpoint):
    bind(endpoint)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == 1
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED", uptimeMs=2000) == 2
    first = next_held(endpoint)[1]
    frames = query(endpoint)
    assert [uart.decode_frame(frame)["messageName"] for frame in frames] == [
        "ACTUATOR_EVENT_QUERY_REPLY", "CLEAN_LOCK_POWER_CHANGED"]
    assert uart.decode_frame(frames[1])["payload"] == first
    reference = uart.decode_payload("ACTUATOR_EVENT_QUERY_REPLY", uart.decode_frame(frames[0])["payload"])
    assert reference["eventDigestSha256"] == uart.compute_actuator_event_digest("CLEAN_LOCK_POWER_CHANGED", first)
    receipt = dict(mcuBootId=42, **{key: reference[key] for key in ("mcuEventSequence", "eventMessageType", "eventDigestSha256")})
    saved = exchange(endpoint, request("ACTUATOR_EVENT_SAVED", receipt))
    assert uart.decode_payload("ACTUATOR_EVENT_SAVED_REPLY", uart.decode_frame(saved[0])["payload"])["status"] == "RELEASED"
    frames = query(endpoint, 2)
    assert uart.decode_payload("CLEAN_LOCK_POWER_CHANGED", uart.decode_frame(frames[1])["payload"])["lockPowerState"] == "DEENERGIZED"


def test_pi_handoff_commits_each_original_before_wire_confirmation(endpoint, tmp_path):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    bind(endpoint)
    token = reserve(endpoint)
    assert publish(endpoint, token, 0) == 1
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == 2
    lib, memory, replies, _ = endpoint
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    writes = []
    def write(frame):
        decoded = uart.decode_frame(frame)
        writes.append(decoded["messageName"])
        if decoded["messageName"] == "ACTUATOR_EVENT_SAVED":
            values = uart.decode_payload(decoded["messageName"], decoded["payload"])
            # A separate connection already sees the committed row when UART is written.
            reader = EdgeStore(store.db_path)
            reader.initialize()
            try:
                assert reader.get_native_actuator_event(42, values["mcuEventSequence"])["saved_payload"] == decoded["payload"]
            finally:
                reader.close()
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    try:
        client = McuActuatorEventHandoff(store, write, 42)
        for now in (0, 1000, 2000):
            client.poll(now)
            while replies:
                client.accept_frame(replies.pop(0), now)
        assert writes == ["QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED",
                          "QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED", "QUERY_ACTUATOR_EVENT"]
        assert client.observation(2000)["status"] == "NOT_FOUND"
        assert next_held(endpoint) is None
        assert store.get_work_slot() is None and store.list_native_result_report_tasks() == []
    finally:
        store.close()


def test_query_body_contradiction_is_durable_and_cannot_be_laundered_by_a_later_query(endpoint, tmp_path):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    bind(endpoint)
    token = reserve(endpoint, 1)
    publish(endpoint, token, 0)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    sent = []
    client = McuActuatorEventHandoff(store, lambda frame: sent.append(frame) or len(frame), 42)
    try:
        query_id = client.poll(0)
        frames = query(endpoint, query_id)
        assert client.accept_frame(frames[0], 0)
        decoded = uart.decode_frame(frames[1])
        values = uart.decode_payload(decoded["messageName"], decoded["payload"])
        bad = uart.encode_frame(decoded["messageName"], 1,
            uart.encode_payload(decoded["messageName"], values | {"lockPowerState": "DEENERGIZED"}))
        assert not client.accept_frame(bad, 0)
        assert len(store.list_native_actuator_event_conflicts()) == 2
        with pytest.raises(ValueError, match="conflict"):
            client.accept_frame(frames[1], 0)
        assert len(sent) == 1  # only a read query, never a saved confirmation
        assert next_held(endpoint) is not None
    finally:
        store.close()


@pytest.mark.parametrize("changes,status", [({"mcuBootId": 41}, "BOOT_MISMATCH"),
    ({"mcuEventSequence": 99}, "NOT_FOUND"), ({"eventDigestSha256": "ff" * 32}, "IDENTITY_CONFLICT"),
    ({"eventMessageType": "SAFE_CLOSE_RESULT"}, "IDENTITY_CONFLICT")])
def test_wrong_saved_identity_keeps_original_and_pending_off(endpoint, changes, status):
    bind(endpoint)
    token = reserve(endpoint)
    publish(endpoint, token, 0)
    held = next_held(endpoint)
    receipt = dict(mcuBootId=42, mcuEventSequence=1, eventMessageType="CLEAN_LOCK_POWER_CHANGED",
        eventDigestSha256=uart.compute_actuator_event_digest("CLEAN_LOCK_POWER_CHANGED", held[1]))
    response = exchange(endpoint, request("ACTUATOR_EVENT_SAVED", receipt | changes))
    assert uart.decode_payload("ACTUATOR_EVENT_SAVED_REPLY", uart.decode_frame(response[0])["payload"])["status"] == status
    assert next_held(endpoint) == held
    assert publish(endpoint, token, 1, lockPowerState="DEENERGIZED") == 2


@pytest.mark.parametrize("failure", ["short", "exception", "lost", "reply_lost"])
def test_pi_restart_after_confirmation_loss_only_queries_original_and_preserves_saved_bytes(endpoint, tmp_path, failure):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    bind(endpoint)
    token = reserve(endpoint, 1)
    publish(endpoint, token, 0)
    lib, memory, replies, _ = endpoint
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    writes, broken = [], [True]
    def write(frame):
        name = uart.decode_frame(frame)["messageName"]
        writes.append(name)
        if name == "ACTUATOR_EVENT_SAVED" and broken[0]:
            if failure == "exception":
                raise OSError("injected serial failure")
            if failure == "short":
                return 1
            if failure == "lost":
                return len(frame)
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        if name == "ACTUATOR_EVENT_SAVED" and broken[0]:
            replies.clear()  # only saved reply lost; MCU already released data
        return len(frame)
    try:
        client = McuActuatorEventHandoff(store, write, 42)
        client.poll(0)
        frames = list(replies)
        while replies:
            client.accept_frame(replies.pop(0), 0)
        assert store.get_native_actuator_event(42, 1) is not None
        assert (next_held(endpoint) is None) == (failure == "reply_lost")
        client.accept_frame(frames[-1], 0)  # duplicate cannot retry a failed save write
        assert writes.count("ACTUATOR_EVENT_SAVED") == 1
        store.close()
        store = EdgeStore(store.db_path)
        store.initialize()
        broken[0] = False
        restarted = McuActuatorEventHandoff(store, write, 42)
        restarted.poll(0)
        while replies:
            restarted.accept_frame(replies.pop(0), 0)
        assert next_held(endpoint) is None
        assert set(writes) <= {"QUERY_ACTUATOR_EVENT", "ACTUATOR_EVENT_SAVED"}
    finally:
        store.close()


def test_sqlite_failure_never_confirms_and_next_query_recovers_without_reexecution(endpoint, tmp_path):
    import sqlite3
    from mcu_actuator_handoff import McuActuatorEventHandoff
    bind(endpoint)
    token = reserve(endpoint, 1)
    publish(endpoint, token, 0)
    lib, memory, replies, _ = endpoint
    replies.clear()
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    names = []
    def write(frame):
        names.append(uart.decode_frame(frame)["messageName"])
        lib.McuControlEndpoint_Feed(memory, frame, len(frame), 0)
        return len(frame)
    client = McuActuatorEventHandoff(store, write, 42)
    try:
        store._conn.execute("CREATE TEMP TRIGGER fail_actuator BEFORE INSERT ON native_actuator_event BEGIN SELECT RAISE(ABORT, 'injected disk failure'); END")
        client.poll(0)
        client.accept_frame(replies.pop(0), 0)
        with pytest.raises(sqlite3.DatabaseError):
            client.accept_frame(replies.pop(0), 0)
        assert names == ["QUERY_ACTUATOR_EVENT"] and next_held(endpoint) is not None
        assert store.get_native_actuator_event(42, 1) is None
        store._conn.execute("DROP TRIGGER fail_actuator")
        client.poll(1000)
        while replies:
            client.accept_frame(replies.pop(0), 1000)
        assert next_held(endpoint) is None
    finally:
        store.close()


@pytest.mark.parametrize("when", ["before_query", "expired", "old_query", "wrong_boot"])
def test_unassociated_or_stale_event_never_gets_saved_confirmation(endpoint, tmp_path, when):
    from mcu_actuator_handoff import McuActuatorEventHandoff
    bind(endpoint)
    token = reserve(endpoint, 1)
    publish(endpoint, token, 0)
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    writes = []
    client = McuActuatorEventHandoff(store, lambda raw: writes.append(raw) or len(raw), 42)
    try:
        now = 0
        query_id = 1 if when == "before_query" else client.poll(0)
        frames = query(endpoint, query_id)
        if when != "before_query":
            assert client.accept_frame(frames[0], 0)
        if when == "expired":
            now = 1000
        elif when == "old_query":
            client.poll(1000)
            now = 1000
            assert not client.accept_frame(frames[0], now)
        elif when == "wrong_boot":
            values = uart.decode_payload("CLEAN_LOCK_POWER_CHANGED", uart.decode_frame(frames[1])["payload"])
            frames[1] = uart.encode_frame("CLEAN_LOCK_POWER_CHANGED", 2, uart.encode_payload("CLEAN_LOCK_POWER_CHANGED", values | {"mcuBootId": 41}))
        assert not client.accept_frame(frames[1], now)
        assert all(uart.decode_frame(raw)["messageName"] == "QUERY_ACTUATOR_EVENT" for raw in writes)
        assert store.get_native_actuator_event(42, 1) is None
    finally:
        store.close()
