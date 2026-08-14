import json

import pytest

from edge_store import EdgeStore
from fixed_frame_health_recovery import (
    FixedFrameHealthRecoveryController,
    runtime_uart_state,
)

DEVICE_NAME = "SN-DEMO-0001"


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeFixedFrameUart:
    compatibility_mode = True
    is_open = True

    def __init__(self, clock, results, *, query_duration=0.0):
        self._clock = clock
        self._results = list(results)
        self._query_duration = query_duration
        self.query_times = []
        self.queued_safety_events = 0

    def query_self_test(
        self,
        *,
        timeout_ms,
        on_result,
        queue_unchanged_safety_event,
    ):
        assert timeout_ms == 3_000
        self.query_times.append(self._clock())
        result = self._results.pop(0)
        self._clock.advance(self._query_duration)
        changed = on_result(result)
        if queue_unchanged_safety_event or changed is not False:
            self.queued_safety_events += 1
        return result


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def self_test(
    *,
    query_status="OK",
    communication_healthy=True,
    valid_flags=3,
    weight_valid=True,
    weight_grams=12_000,
    infrared_valid=True,
    infrared_blocked=False,
    smoke_code=0,
):
    if query_status != "OK":
        smoke_state = "UNKNOWN"
        smoke_health = (
            "TIMEOUT" if query_status == "TIMEOUT" else "PROTOCOL_ERROR"
        )
        fault_code = "SMOKE_SENSOR"
    elif smoke_code == 0:
        smoke_state = "NORMAL"
        smoke_health = "OK"
        fault_code = None
    elif smoke_code == 1:
        smoke_state = "ALARM"
        smoke_health = "OK"
        fault_code = None
    else:
        smoke_state = "UNKNOWN"
        smoke_health = "SENSOR_FAULT"
        fault_code = "SMOKE_SENSOR"
    return {
        "queryStatus": query_status,
        "communicationHealthy": communication_healthy,
        "portNo": 1,
        "validFlags": valid_flags,
        "weightValid": weight_valid,
        "weightGrams": weight_grams,
        "weightMeasurementUid": None,
        "infraredValid": infrared_valid,
        "infraredBlocked": infrared_blocked,
        "smokeCode": smoke_code if query_status == "OK" else None,
        "smokeState": smoke_state,
        "smokeSensorHealth": smoke_health,
        "faultCode": fault_code,
        "rawFrameHex": None,
    }


def make_controller(store, uart, clock):
    return FixedFrameHealthRecoveryController(
        store,
        uart,
        device_name=DEVICE_NAME,
        monotonic=clock,
    )


def observe_uart_fault(store):
    assert store.observe_fault_and_create_event(
        device_name=DEVICE_NAME,
        component="UART",
        fault_code="UART_PROTOCOL",
        severity="BLOCK_DEVICE",
        detail={"reasonCode": "TIMEOUT"},
    ) == "ACCEPTED"


def test_retry_backoff_is_5_10_20_40_60_then_60_seconds(tmp_path):
    store = make_store(tmp_path)
    timeout = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(timeout)
    observe_uart_fault(store)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [timeout] * 6)
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "ARMED"
    for due_time in (105.0, 115.0, 135.0, 175.0, 235.0, 295.0):
        clock.now = due_time - 0.001
        assert recovery.poll()["attempted"] is False
        clock.now = due_time
        assert recovery.poll()["status"] == "RETRY_SCHEDULED"

    assert uart.query_times == [105.0, 115.0, 135.0, 175.0, 235.0, 295.0]
    assert uart.queued_safety_events == 0
    fault = store.get_active_edge_fault("UART", "UART_PROTOCOL")
    assert fault["discovery_count"] == 7
    events = store._conn.execute(
        """SELECT event_type, COUNT(*) AS count FROM event_outbox
           WHERE event_type LIKE 'DEVICE_FAULT_%'
           GROUP BY event_type"""
    ).fetchall()
    assert dict(events) == {"DEVICE_FAULT_OBSERVED": 1}
    store.close()


def test_backoff_starts_after_the_query_finishes(tmp_path):
    store = make_store(tmp_path)
    timeout = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(timeout)
    clock = FakeClock()
    uart = FakeFixedFrameUart(
        clock,
        [timeout],
        query_duration=3.0,
    )
    recovery = make_controller(store, uart, clock)

    recovery.poll()
    clock.advance(5)
    assert recovery.poll()["status"] == "RETRY_SCHEDULED"

    assert uart.query_times == [105.0]
    assert clock.now == 108.0
    assert recovery.next_retry_at == 118.0
    store.close()


def test_first_timeout_then_valid_f1_recovers_without_reboot(tmp_path):
    store = make_store(tmp_path)
    timeout = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(timeout)
    observe_uart_fault(store)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [self_test()])
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "ARMED"
    clock.advance(5)
    outcome = recovery.poll()

    assert outcome["status"] == "HEALTHY"
    assert outcome["attempted"] is True
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
    recovered = store._conn.execute(
        """SELECT recovery_evidence FROM edge_fault_state
           WHERE component='UART' AND fault_code='UART_PROTOCOL'"""
    ).fetchone()
    assert recovered["recovery_evidence"] == (
        "FIXED_FRAME_SELF_TEST_RETRY_SUCCEEDED"
    )
    counts = dict(
        store._conn.execute(
            """SELECT event_type, COUNT(*) AS count FROM event_outbox
               WHERE event_type IN (
                 'DEVICE_FAULT_OBSERVED', 'DEVICE_FAULT_RECOVERED'
               ) GROUP BY event_type"""
        ).fetchall()
    )
    assert counts == {
        "DEVICE_FAULT_OBSERVED": 1,
        "DEVICE_FAULT_RECOVERED": 1,
    }
    assert uart.queued_safety_events == 1
    assert recovery.poll()["status"] == "HEALTHY"
    assert len(uart.query_times) == 1
    store.close()


def test_healthy_state_does_not_poll_or_replay_failed_commands(tmp_path):
    store = make_store(tmp_path)
    store.save_fixed_frame_self_test(self_test())
    command_uid = "10000000-0000-4000-8000-000000000001"
    store.receive_command(command_uid, "START_DELIVERY_SESSION", {})
    store.fail_command(command_uid, "PREVIOUS_FAILURE")
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [])
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "HEALTHY"
    clock.advance(3_600)
    assert recovery.poll()["status"] == "HEALTHY"

    assert uart.query_times == []
    command = store.get_command(command_uid)
    assert command["state"] == "FAILED"
    assert command["last_error"] == "PREVIOUS_FAILURE"
    store.close()


def test_active_uart_fault_requires_fresh_f1_before_recovery(tmp_path):
    store = make_store(tmp_path)
    store.save_fixed_frame_self_test(self_test())
    observe_uart_fault(store)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [self_test()])
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "ARMED"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is not None
    clock.advance(5)
    assert recovery.poll()["status"] == "HEALTHY"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
    assert uart.query_times == [105.0]
    store.close()


def test_closed_uart_is_not_treated_as_healthy_from_stale_f1(tmp_path):
    store = make_store(tmp_path)
    store.save_fixed_frame_self_test(self_test())
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [])
    uart.is_open = False
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "ARMED"
    assert recovery.next_retry_at == 105.0
    store.close()


def test_active_work_defers_retry_without_advancing_backoff(
    tmp_path,
    monkeypatch,
):
    store = make_store(tmp_path)
    timeout = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(timeout)
    busy = [True]
    monkeypatch.setattr(
        store,
        "get_work_slot",
        lambda: {"work_type": "DELIVERY"} if busy[0] else None,
    )
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [timeout])
    recovery = make_controller(store, uart, clock)

    recovery.poll()
    clock.advance(5)
    assert recovery.poll()["status"] == "BUSY"
    assert uart.query_times == []
    busy[0] = False
    clock.advance(4.999)
    assert recovery.poll()["attempted"] is False
    clock.advance(0.001)
    assert recovery.poll()["status"] == "RETRY_SCHEDULED"
    assert uart.query_times == [110.0]
    store.close()


def test_uart_v1_mode_is_not_changed(tmp_path):
    store = make_store(tmp_path)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [])
    uart.compatibility_mode = False
    recovery = make_controller(store, uart, clock)

    assert recovery.poll() == {
        "attempted": False,
        "state_changed": False,
        "status": "DISABLED",
    }
    assert uart.query_times == []
    store.close()


@pytest.mark.parametrize(
    "result",
    [
        self_test(
            valid_flags=2,
            weight_valid=False,
            weight_grams=None,
        ),
        self_test(
            valid_flags=1,
            infrared_valid=False,
            infrared_blocked=None,
        ),
        self_test(smoke_code=2),
    ],
    ids=("invalid-weight", "invalid-infrared", "smoke-sensor-fault"),
)
def test_incomplete_sensor_evidence_keeps_retrying_but_recovers_uart(
    tmp_path,
    result,
):
    store = make_store(tmp_path)
    initial = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(initial)
    observe_uart_fault(store)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [result])
    recovery = make_controller(store, uart, clock)

    recovery.poll()
    clock.advance(5)
    outcome = recovery.poll()

    assert outcome["status"] == "RETRY_SCHEDULED"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
    store.close()


def test_smoke_alarm_is_healthy_communication_but_remains_business_alarm(
    tmp_path,
):
    store = make_store(tmp_path)
    timeout = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(timeout)
    observe_uart_fault(store)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [self_test(smoke_code=1)])
    recovery = make_controller(store, uart, clock)

    recovery.poll()
    clock.advance(5)
    assert recovery.poll()["status"] == "HEALTHY"
    assert store.get_active_edge_fault("UART", "UART_PROTOCOL") is None
    assert store.get_state("port_1_smoke_state") == "ALARM"
    clock.advance(60)
    assert recovery.poll()["status"] == "HEALTHY"
    assert len(uart.query_times) == 1
    store.close()


def test_runtime_uart_state_tracks_active_protocol_fault(tmp_path):
    store = make_store(tmp_path)
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [])

    observe_uart_fault(store)
    assert runtime_uart_state(store, uart) == "FAULT"
    fault = store.get_active_edge_fault("UART", "UART_PROTOCOL")
    store.recover_fault_and_create_event(
        device_name=DEVICE_NAME,
        fault_uid=fault["fault_uid"],
        component="UART",
        fault_code="UART_PROTOCOL",
        port_no=None,
        recovery_evidence="TEST",
    )
    assert runtime_uart_state(store, uart) == "READY"
    uart.is_open = False
    assert runtime_uart_state(store, uart) == "DISCONNECTED"
    store.close()


def test_current_cc_smoke_state_cannot_replace_missing_f1_sensor_evidence(
    tmp_path,
):
    store = make_store(tmp_path)
    missing_f1 = self_test(
        query_status="TIMEOUT",
        communication_healthy=False,
        valid_flags=0,
        weight_valid=False,
        weight_grams=None,
        infrared_valid=False,
        infrared_blocked=None,
        smoke_code=None,
    )
    store.save_fixed_frame_self_test(missing_f1)
    store.set_state("smoke_state", "NORMAL")
    store.set_state("smoke_sensor_health", "OK")
    store.set_state("port_1_smoke_state", "NORMAL")
    store.set_state("port_1_smoke_sensor_health", "OK")
    clock = FakeClock()
    uart = FakeFixedFrameUart(clock, [missing_f1])
    recovery = make_controller(store, uart, clock)

    assert recovery.poll()["status"] == "ARMED"
    assert json.loads(
        store.get_state("fixed_frame_latest_self_test_json")
    )["queryStatus"] == "TIMEOUT"
    store.close()
