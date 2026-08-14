import pytest

from device_entry_url_refresh import DeviceEntryUrlRefreshController


URL_1 = "https://www.jinshoubao.com/device-entry/?deviceCode=public-1"
URL_2 = "https://www.jinshoubao.com/device-entry/?deviceCode=public-2"


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeStore:
    def __init__(self, url=URL_1):
        self.url = url
        self.work_slot = None
        self.url_reads = 0

    def get_device_entry_url(self):
        self.url_reads += 1
        if self.url is None:
            return None
        return {"deviceEntryUrl": self.url}

    def get_work_slot(self):
        return self.work_slot


class FakeUart:
    compatibility_mode = True
    is_open = True

    def __init__(self):
        self.sent_urls = []
        self.failures_remaining = 0

    def send_device_entry_url(self, url):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("write failed")
        self.sent_urls.append(url)
        return {"disposition": "LOCALLY_DISPATCHED"}


def make_controller(store, uart, clock, **kwargs):
    return DeviceEntryUrlRefreshController(
        store,
        uart,
        interval_seconds=60,
        retry_seconds=5,
        monotonic=clock,
        **kwargs,
    )


def test_sends_latest_url_every_configured_interval():
    store = FakeStore()
    uart = FakeUart()
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    assert refresh.poll()["status"] == "ARMED"
    assert refresh.next_send_at == 160.0
    clock.advance(59.999)
    assert refresh.poll()["status"] == "WAITING"
    assert uart.sent_urls == []

    store.url = URL_2
    clock.advance(0.001)
    assert refresh.poll() == {"attempted": True, "status": "SENT"}
    assert uart.sent_urls == [URL_2]
    assert refresh.next_send_at == 220.0

    clock.advance(60)
    assert refresh.poll()["status"] == "SENT"
    assert uart.sent_urls == [URL_2, URL_2]


def test_active_work_defers_send_until_the_mcu_is_idle():
    store = FakeStore()
    store.work_slot = {"work_type": "DELIVERY"}
    uart = FakeUart()
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    refresh.poll()
    clock.advance(60)
    assert refresh.poll() == {"attempted": False, "status": "BUSY"}
    assert refresh.next_send_at == 165.0
    assert uart.sent_urls == []

    store.work_slot = None
    clock.advance(5)
    assert refresh.poll()["status"] == "SENT"
    assert uart.sent_urls == [URL_1]


def test_closed_uart_retries_without_consuming_the_full_interval():
    store = FakeStore()
    uart = FakeUart()
    uart.is_open = False
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    refresh.poll()
    clock.advance(60)
    assert refresh.poll() == {
        "attempted": False,
        "status": "DISCONNECTED",
    }
    assert refresh.next_send_at == 165.0

    uart.is_open = True
    clock.advance(5)
    assert refresh.poll()["status"] == "SENT"
    assert uart.sent_urls == [URL_1]


def test_failed_write_retries_after_short_delay():
    store = FakeStore()
    uart = FakeUart()
    uart.failures_remaining = 1
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    refresh.poll()
    clock.advance(60)
    assert refresh.poll() == {
        "attempted": True,
        "status": "RETRY_SCHEDULED",
    }
    assert refresh.next_send_at == 165.0

    clock.advance(5)
    assert refresh.poll()["status"] == "SENT"
    assert uart.sent_urls == [URL_1]


def test_no_stored_url_is_rechecked_on_the_regular_interval():
    store = FakeStore(url=None)
    uart = FakeUart()
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    refresh.poll()
    clock.advance(60)
    assert refresh.poll() == {"attempted": False, "status": "NO_URL"}
    assert refresh.next_send_at == 220.0
    assert uart.sent_urls == []


def test_uart_v1_mode_is_unchanged_and_does_not_read_the_url():
    store = FakeStore()
    uart = FakeUart()
    uart.compatibility_mode = False
    clock = FakeClock()
    refresh = make_controller(store, uart, clock)

    assert refresh.poll() == {"attempted": False, "status": "DISABLED"}
    clock.advance(3_600)
    assert refresh.poll()["status"] == "DISABLED"
    assert refresh.next_send_at is None
    assert store.url_reads == 0
    assert uart.sent_urls == []


def test_url_provider_is_replaceable_for_future_signed_dynamic_urls():
    store = FakeStore()
    uart = FakeUart()
    clock = FakeClock()
    generated = iter((URL_1, URL_2))
    refresh = make_controller(
        store,
        uart,
        clock,
        url_provider=lambda: next(generated),
    )

    refresh.poll()
    clock.advance(60)
    refresh.poll()
    clock.advance(60)
    refresh.poll()

    assert uart.sent_urls == [URL_1, URL_2]
    assert store.url_reads == 0


@pytest.mark.parametrize(
    ("interval_seconds", "retry_seconds"),
    (
        (0, 5),
        (-1, 5),
        (float("nan"), 5),
        (float("inf"), 5),
        (60, 0),
        (60, -1),
        (60, float("nan")),
        (60, float("inf")),
    ),
)
def test_intervals_must_be_positive(interval_seconds, retry_seconds):
    with pytest.raises(ValueError, match="positive"):
        DeviceEntryUrlRefreshController(
            FakeStore(),
            FakeUart(),
            interval_seconds=interval_seconds,
            retry_seconds=retry_seconds,
        )
