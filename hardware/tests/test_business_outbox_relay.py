"""BusinessOutboxRelay 的真实 SQLite 边界测试。"""

from __future__ import annotations

import threading

import pytest

from business_outbox_relay import BusinessOutboxRelay
from cloud_transport import CloudEvent, CloudEventPlatformResult
from edge_store import EVENT_CONFIRMED, EVENT_PENDING, EVENT_SENDING, EdgeStore


class FakeCloudTransport:
    """Only expose the transport-neutral event API used by the relay."""

    def __init__(self) -> None:
        self.connected = True
        self.send_result = True
        self.send_error: Exception | None = None
        self.sent_events: list[CloudEvent] = []
        self.send_started: threading.Event | None = None
        self.release_send: threading.Event | None = None

    def send_event(self, event: CloudEvent) -> bool:
        self.sent_events.append(event)
        if self.send_started is not None:
            self.send_started.set()
        if self.release_send is not None:
            assert self.release_send.wait(timeout=2.0)
        if self.send_error is not None:
            raise self.send_error
        return self.send_result


@pytest.fixture
def store(tmp_path):
    edge_store = EdgeStore(str(tmp_path / "edge.db"))
    edge_store.initialize()
    try:
        yield edge_store
    finally:
        edge_store.close()


def _create_event(
    store: EdgeStore,
    *,
    event_uid: str = "event-1",
    event_type: str = "DELIVERY_COMPLETE",
) -> dict:
    assert store.receive_mcu_event(
        event_uid,
        event_type,
        {"workUid": "work-1", "weightGrams": 1200},
        work_uid="work-1",
    ) == "ACCEPTED"
    event = store.get_event(event_uid)
    assert event is not None
    return event


def test_successful_submit_claims_event_before_transport_returns(store):
    event = _create_event(store)
    transport = FakeCloudTransport()
    observed_state: list[str] = []

    def observe_claim(cloud_event: CloudEvent) -> bool:
        observed_state.append(store.get_event(cloud_event.event_uid)["state"])
        transport.sent_events.append(cloud_event)
        return True

    transport.send_event = observe_claim
    relay = BusinessOutboxRelay(store, transport)

    relay.relay_pending_events()

    assert observed_state == [EVENT_SENDING]
    assert transport.sent_events == [
        CloudEvent(
            event_uid="event-1",
            event_type="DELIVERY_COMPLETE",
            params={"workUid": "work-1", "weightGrams": 1200},
        )
    ]
    stored = store.get_event(event["event_uid"])
    assert stored["state"] == EVENT_SENDING


@pytest.mark.parametrize("failure", [False, RuntimeError("network unavailable")])
def test_rejected_or_failed_transport_submit_returns_event_to_pending(
    store,
    failure,
):
    _create_event(store)
    transport = FakeCloudTransport()
    if failure is False:
        transport.send_result = False
    else:
        transport.send_error = failure
    relay = BusinessOutboxRelay(store, transport)

    relay.relay_pending_events()

    event = store.get_event("event-1")
    assert event["state"] == EVENT_PENDING
    assert event["retry_count"] == 1
    assert event["next_retry_at"] is not None


def test_transport_ack_for_business_event_only_schedules_business_retry(store):
    _create_event(store)
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()

    relay.handle_transport_ack("event-1")

    event = store.get_event("event-1")
    assert event["state"] == EVENT_PENDING
    assert event["confirmed_at"] is None
    assert event["retry_count"] == 1
    assert event["next_retry_at"] is not None


def test_transport_ack_completes_business_confirmation_receipt(store):
    _create_event(
        store,
        event_uid="receipt-1",
        event_type="BUSINESS_CONFIRMATION_RECEIPT",
    )
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()

    relay.handle_transport_ack("receipt-1")

    receipt = store.get_event("receipt-1")
    assert receipt["state"] == EVENT_CONFIRMED
    assert receipt["confirmed_at"] is not None
    assert receipt["next_retry_at"] is None


def test_onenet_200_records_platform_acceptance_without_business_confirmation(
    store,
):
    original = _create_event(store)
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()

    relay.handle_platform_result(
        CloudEventPlatformResult(
            edge_event_sequence=original["edge_event_sequence"],
            code=200,
        )
    )

    event = store.get_event("event-1")
    assert event["state"] == EVENT_SENDING
    assert event["confirmed_at"] is None
    assert event["last_platform_code"] == 200
    assert event["platform_accepted_at"] is not None
    assert event["last_platform_reply_at"] is not None


def test_onenet_24xx_permanently_rejects_event(store):
    original = _create_event(store)
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()

    relay.handle_platform_result(
        CloudEventPlatformResult(
            edge_event_sequence=original["edge_event_sequence"],
            code=2402,
        )
    )

    event = store.get_event("event-1")
    assert event["state"] == "DEAD"
    assert event["confirmed_at"] is None
    assert event["last_platform_code"] == 2402
    assert event["next_retry_at"] is None


def test_other_onenet_error_schedules_retry(store):
    original = _create_event(store)
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()

    relay.handle_platform_result(
        CloudEventPlatformResult(
            edge_event_sequence=original["edge_event_sequence"],
            code=500,
        )
    )

    event = store.get_event("event-1")
    assert event["state"] == EVENT_PENDING
    assert event["confirmed_at"] is None
    assert event["last_platform_code"] == 500
    assert event["retry_count"] == 1
    assert event["next_retry_at"] is not None


def test_disconnect_recovers_all_in_flight_events(store):
    _create_event(store, event_uid="event-1")
    _create_event(store, event_uid="event-2")
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)
    relay.relay_pending_events()
    assert store.get_event("event-1")["state"] == EVENT_SENDING
    assert store.get_event("event-2")["state"] == EVENT_SENDING

    relay.on_disconnected()

    for event_uid in ("event-1", "event-2"):
        event = store.get_event(event_uid)
        assert event["state"] == EVENT_PENDING
        assert event["next_retry_at"] is None


def test_concurrent_relay_calls_do_not_claim_or_send_event_twice(store):
    _create_event(store)
    transport = FakeCloudTransport()
    transport.send_started = threading.Event()
    transport.release_send = threading.Event()
    relay = BusinessOutboxRelay(store, transport)

    first = threading.Thread(target=relay.relay_pending_events)
    second = threading.Thread(target=relay.relay_pending_events)
    first.start()
    assert transport.send_started.wait(timeout=2.0)
    second.start()
    transport.release_send.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert [event.event_uid for event in transport.sent_events] == ["event-1"]
    assert store.get_event("event-1")["state"] == EVENT_SENDING


def test_repeated_concurrent_wake_coalesces_without_duplicate_send(store):
    _create_event(store)
    transport = FakeCloudTransport()
    transport.send_started = threading.Event()
    transport.release_send = threading.Event()
    relay = BusinessOutboxRelay(
        store,
        transport,
        poll_interval_seconds=60.0,
    )
    original_relay = relay.relay_pending_events
    relay_runs = 0
    second_relay_run = threading.Event()

    def count_relay_runs() -> None:
        nonlocal relay_runs
        original_relay()
        relay_runs += 1
        if relay_runs >= 2:
            second_relay_run.set()

    relay.relay_pending_events = count_relay_runs
    relay.start()
    try:
        wake_threads = [threading.Thread(target=relay.wake) for _ in range(20)]
        for thread in wake_threads:
            thread.start()
        for thread in wake_threads:
            thread.join(timeout=1.0)
        assert transport.send_started.wait(timeout=2.0)
        for _ in range(20):
            relay.wake()
        transport.release_send.set()
        assert second_relay_run.wait(timeout=2.0)
    finally:
        transport.release_send.set()
        relay.stop()

    assert [event.event_uid for event in transport.sent_events] == ["event-1"]
    assert store.get_event("event-1")["state"] == EVENT_SENDING


def test_transport_boundary_does_not_expose_mqtt_packet_identifier(store):
    _create_event(store)
    transport = FakeCloudTransport()
    relay = BusinessOutboxRelay(store, transport)

    relay.relay_pending_events()

    cloud_event = transport.sent_events[0]
    assert set(cloud_event.__slots__) == {"event_uid", "event_type", "params"}
    assert not hasattr(cloud_event, "mqtt_msg_id")
    assert store.get_event("event-1")["mqtt_msg_id"] is None
