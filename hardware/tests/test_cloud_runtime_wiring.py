from device_identity import DeviceIdentity
from main import EcoBinEdge


class FakeOutbox:
    def __init__(self, trace=None):
        self.trace = trace if trace is not None else []
        self.connections = []
        self.disconnects = 0

    def on_connected(self):
        self.trace.append("outbox-connected")
        self.connections.append("connected")

    def on_disconnected(self):
        self.disconnects += 1


class FakeStore:
    def __init__(self, *, fault=None, trace=None):
        self.fault = fault
        self.trace = trace if trace is not None else []
        self.recoveries = []
        self.observations = []
        self.mqtt_states = []

    def get_active_edge_fault(self, component, fault_code):
        assert (component, fault_code) == (
            "NETWORK",
            "NETWORK_CONNECTIVITY",
        )
        return self.fault

    def recover_fault_and_create_event(self, **values):
        self.trace.append("network-recovered")
        self.recoveries.append(values)
        return "ACCEPTED"

    def observe_fault_and_create_event(self, **values):
        self.observations.append(values)
        return "ACCEPTED"

    def save_mqtt_persistent_state(self, session_present, reason_code):
        self.mqtt_states.append((session_present, reason_code))


class FakeTransport:
    def __init__(self, connect_result):
        self.connect_result = connect_result
        self.connected = False

    def connect(self):
        self.connected = self.connect_result
        return self.connect_result


def bare_edge():
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge.device_identity = DeviceIdentity("SN-DEMO-0001")
    edge._runtime_ready = False
    edge._exit_flag = type(
        "ExitFlag",
        (),
        {"is_set": lambda _self: False},
    )()
    edge._can_clear_runtime_error = lambda: False
    edge._report_factory_progress = lambda **_values: None
    return edge


def test_connected_callback_recovers_network_fact_before_relaying_outbox():
    trace = []
    edge = bare_edge()
    edge.store = FakeStore(
        fault={"fault_uid": "fault-1", "port_no": None},
        trace=trace,
    )
    edge.business_outbox = FakeOutbox(trace)

    edge._on_cloud_connected()

    assert trace == ["network-recovered", "outbox-connected"]
    assert edge.business_outbox.connections == ["connected"]
    assert edge.store.recoveries[0]["device_name"] == "SN-DEMO-0001"


def test_disconnect_always_recovers_inflight_events_idempotently():
    edge = bare_edge()
    edge.business_outbox = FakeOutbox()

    edge._on_cloud_disconnected()

    assert edge.business_outbox.disconnects == 1


def test_direct_mqtt_diagnostics_preserve_session_and_reason_code():
    edge = bare_edge()
    edge.store = FakeStore()

    edge._on_direct_mqtt_state_observed(True, 0)
    edge._on_direct_mqtt_state_observed(False, 7)

    assert edge.store.mqtt_states == [(True, 0), (False, 7)]


def test_local_boot_connection_failure_degrades_and_records_network_fault():
    edge = bare_edge()
    edge.cloud_transport = FakeTransport(False)
    edge.store = FakeStore()
    local_result = {
        "status": "READY",
        "mcu_info": {"mcu_boot_id": 42},
        "snapshots": [],
    }

    result = edge._connect_cloud_after_local_boot(local_result)

    assert result == {
        **local_result,
        "status": "DEGRADED",
        "reason": "mqtt_connect_failed",
    }
    assert edge.store.observations[0] == {
        "device_name": "SN-DEMO-0001",
        "component": "NETWORK",
        "fault_code": "NETWORK_CONNECTIVITY",
        "severity": "WARNING",
        "detail": {"reasonCode": "MQTT_CONNECT_FAILED"},
    }


def test_local_boot_success_publishes_initial_snapshot_from_boot_facts(
    monkeypatch,
):
    edge = bare_edge()
    edge.cloud_transport = FakeTransport(True)
    edge.store = FakeStore()
    published = []
    monkeypatch.setattr("main.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "edge_boot._publish_runtime_snapshot",
        lambda *args: published.append(args),
    )
    result = {
        "status": "READY",
        "mcu_info": {"mcu_boot_id": 42},
        "snapshots": [{"message_name": "STATE_SNAPSHOT_BEGIN"}],
    }

    assert edge._connect_cloud_after_local_boot(result) is result
    assert published == [(
        edge.store,
        edge.cloud_transport,
        edge.device_identity,
        result["mcu_info"],
        result["snapshots"],
    )]
