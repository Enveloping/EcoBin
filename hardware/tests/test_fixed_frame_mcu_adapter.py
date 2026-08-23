import threading
import time
from types import SimpleNamespace

import pytest

import fixed_frame_mcu_adapter as fixed_frame_module

from fixed_frame_mcu_adapter import (
    DEVICE_ENTRY_URL_FRAME_LENGTH,
    FIRMWARE_STATUS_FRAME_LENGTH,
    FixedFrameMcuAdapter,
    FixedFrameParser,
    price_digit_from_ten_thousandths,
)


class FakeSerial:
    def __init__(self, **kwargs):
        self.is_open = True
        self.timeout = kwargs.get("timeout", 0.5)
        self.received = bytearray()
        self.writes = []
        self.flush_count = 0
        self.reset_count = 0

    @property
    def in_waiting(self):
        return len(self.received)

    def inject(self, data):
        self.received.extend(data)

    def read(self, size):
        if not self.received:
            return b""
        chunk = bytes(self.received[:size])
        del self.received[:size]
        return chunk

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        self.flush_count += 1

    def reset_input_buffer(self):
        self.received.clear()
        self.reset_count += 1

    def close(self):
        self.is_open = False


class ShortWriteSerial(FakeSerial):
    def write(self, data):
        self.writes.append(bytes(data))
        return len(data) - 1


class RespondingSerial(FakeSerial):
    def __init__(self, response):
        super().__init__()
        self.response = bytes(response)

    def write(self, data):
        written = super().write(data)
        if bytes(data) == bytes.fromhex("F0 01 F0"):
            self.inject(self.response)
        return written


class FirmwareRespondingSerial(FakeSerial):
    def __init__(self, responses):
        super().__init__()
        self.responses = {
            int(mode): bytes(response)
            for mode, response in responses.items()
        }

    def write(self, data):
        written = super().write(data)
        wire = bytes(data)
        if len(wire) == 3 and wire[0] == 0xF2 and wire[2] == 0xF2:
            response = self.responses.get(wire[1])
            if response is not None:
                self.inject(response)
        return written


def firmware_status_frame(
    *,
    mode=1,
    status=0,
    revision=2,
    version_code=10203,
    version="1.2.3",
    identity=bytes.fromhex("0102030405060708"),
    safe_flags=0x0F,
):
    encoded = version.encode("ascii")
    frame = b"".join(
        (
            bytes((0xF3, mode, status, revision)),
            version_code.to_bytes(4, "big"),
            bytes((len(encoded),)),
            encoded.ljust(32, b"\x00"),
            identity,
            bytes((safe_flags, 0xF3)),
        )
    )
    assert len(frame) == FIRMWARE_STATUS_FRAME_LENGTH
    return frame


class ContendedSerial(FakeSerial):
    """Serial double that exposes reads started while a write is pending."""

    def __init__(self, read_delay_s=0.01):
        super().__init__(timeout=read_delay_s)
        self.read_started = threading.Event()
        self._state_lock = threading.Lock()
        self._foreground_pending = False
        self._reads_during_foreground = 0
        self.completed_foreground_read_counts = []

    def begin_foreground(self):
        with self._state_lock:
            self._foreground_pending = True
            self._reads_during_foreground = 0

    def read(self, size):
        self.read_started.set()
        with self._state_lock:
            if self._foreground_pending:
                self._reads_during_foreground += 1
        time.sleep(self.timeout)
        return super().read(size)

    def write(self, data):
        with self._state_lock:
            if self._foreground_pending:
                self.completed_foreground_read_counts.append(
                    self._reads_during_foreground
                )
                self._foreground_pending = False
        return super().write(data)


class BlockingWriteSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.write_started = threading.Event()
        self.release_write = threading.Event()

    def write(self, data):
        self.write_started.set()
        if not self.release_write.wait(timeout=1):
            raise TimeoutError("test did not release the UART write")
        return super().write(data)


def test_production_serial_open_requests_posix_exclusive_ownership(monkeypatch):
    captured = {}
    fake = FakeSerial()

    def open_serial(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(
        fixed_frame_module,
        "serial",
        SimpleNamespace(Serial=open_serial),
    )
    adapter = FixedFrameMcuAdapter("/dev/ttyS5", edge_boot_id=1)

    assert adapter.open()
    assert captured["exclusive"] is True


def test_parser_handles_partial_joined_and_payload_marker_bytes():
    parser = FixedFrameParser()
    delivery = bytes.fromhex("DD 00 DD 10 00 EF 20 01 DD")
    clean = bytes.fromhex("EF 01 86 A0 00 C3 50 00 EF")

    assert parser.feed(b"\x99\x88" + delivery[:4]) == []
    results = parser.feed(delivery[4:] + clean)

    assert results == [
        {
            "frame_type": "DELIVERY",
            "pre_weight_grams": 0x00DD10,
            "post_weight_grams": 0x00EF20,
            "infrared_blocked": True,
            "raw_frame_hex": delivery.hex(),
        },
        {
            "frame_type": "CLEAN",
            "pre_weight_grams": 100_000,
            "post_weight_grams": 50_000,
            "infrared_blocked": False,
            "raw_frame_hex": clean.hex(),
        },
    ]


def test_parser_rejects_invalid_tail_flag_and_weight_then_resynchronizes():
    parser = FixedFrameParser()
    overweight = bytes.fromhex("DD 05 57 31 00 00 01 00 DD")
    bad_flag = bytes.fromhex("EF 00 00 01 00 00 02 02 EF")
    valid = bytes.fromhex("DD 00 2E E0 00 2F FC 01 DD")

    assert parser.feed(overweight + bad_flag + valid) == [
        {
            "frame_type": "DELIVERY",
            "pre_weight_grams": 12_000,
            "post_weight_grams": 12_284,
            "infrared_blocked": True,
            "raw_frame_hex": valid.hex(),
        }
    ]


def test_price_is_floored_to_one_decimal_and_saturated_at_nine():
    assert price_digit_from_ten_thousandths(1) == 0
    assert price_digit_from_ten_thousandths(4_500) == 4
    assert price_digit_from_ten_thousandths(9_999) == 9
    assert price_digit_from_ten_thousandths(12_000) == 9


def test_delivery_start_writes_price_and_start_once_without_retry():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.send_command(
        "START_DELIVERY_SESSION",
        {"unitPriceTenThousandths": 12_000},
        mcu_command_uid="10000000-0000-4000-8000-000000000001",
    )

    assert result["acked"] is True
    assert result["disposition"] == "LOCALLY_DISPATCHED"
    assert fake.writes == [bytes.fromhex("BB 09 BB AA 01 AA")]
    assert fake.flush_count == 1
    assert fake.reset_count == 0


def test_foreground_operations_are_not_starved_by_continuous_background_reads():
    fake = ContendedSerial(read_delay_s=0.01)
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        timeout_s=0.01,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    stop = threading.Event()

    def read_forever():
        while not stop.is_set():
            adapter.read_mcu_event(timeout_ms=10)

    reader = threading.Thread(target=read_forever, daemon=True)
    reader.start()
    assert fake.read_started.wait(timeout=1)

    try:
        url = "https://www.jinshoubao.com/device-entry/public-code-1"
        for index in range(60):
            fake.begin_foreground()
            if index % 3 == 0:
                result = adapter.send_command(
                    "START_DELIVERY_SESSION",
                    {"unitPriceTenThousandths": 4_500},
                )
                assert result["acked"] is True
            elif index % 3 == 1:
                result = adapter.send_command(
                    "START_CLEAN_OPERATION",
                    {},
                )
                assert result["acked"] is True
            else:
                result = adapter.send_device_entry_url(url)
                assert result["responseExpected"] is False
    finally:
        stop.set()
        reader.join(timeout=2)
        adapter.close()

    assert not reader.is_alive()
    assert len(fake.completed_foreground_read_counts) == 60
    assert max(fake.completed_foreground_read_counts) <= 1
    assert len(fake.writes) == 60


def test_background_read_timeout_includes_waiting_for_foreground_io():
    fake = BlockingWriteSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    url = "https://www.jinshoubao.com/device-entry/public-code-1"
    writer = threading.Thread(
        target=lambda: adapter.send_device_entry_url(url),
        daemon=True,
    )
    writer.start()
    assert fake.write_started.wait(timeout=1)
    delayed_release = threading.Timer(0.1, fake.release_write.set)
    delayed_release.start()

    started_at = time.monotonic()
    result = adapter.read_mcu_event(timeout_ms=20)
    elapsed = time.monotonic() - started_at

    fake.release_write.set()
    writer.join(timeout=1)
    delayed_release.cancel()
    adapter.close()
    assert not writer.is_alive()
    assert result is None
    assert elapsed < 0.08


@pytest.mark.parametrize(
    ("message_name", "values"),
    [
        (
            "START_DELIVERY_SESSION",
            {
                "unitPriceTenThousandths": 4_500,
                "startExecutionWindowMs": 5,
            },
        ),
        (
            "START_CLEAN_OPERATION",
            {"startExecutionWindowMs": 5},
        ),
    ],
)
def test_start_does_not_write_after_execution_window_expires_while_waiting(
    message_name,
    values,
):
    fake = ContendedSerial(read_delay_s=0.05)
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        timeout_s=0.05,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    reader = threading.Thread(
        target=lambda: adapter.read_mcu_event(timeout_ms=50),
        daemon=True,
    )
    reader.start()
    assert fake.read_started.wait(timeout=1)

    result = adapter.send_command(message_name, values)

    reader.join(timeout=1)
    adapter.close()
    assert not reader.is_alive()
    assert result["acked"] is False
    assert result["error"] == "COMMAND_EXPIRED"
    assert fake.writes == []


def test_device_entry_url_writes_fixed_frame_without_waiting_for_response():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    url = "https://www.jinshoubao.com/device-entry/public-code-1"

    result = adapter.send_device_entry_url(url)

    assert result == {
        "disposition": "LOCALLY_DISPATCHED",
        "frameLength": DEVICE_ENTRY_URL_FRAME_LENGTH,
        "responseExpected": False,
    }
    frame = fake.writes[0]
    assert len(frame) == DEVICE_ENTRY_URL_FRAME_LENGTH
    assert frame[0] == 0xA0
    assert frame[1] == len(url)
    assert frame[2 : 2 + len(url)] == url.encode("ascii")
    assert frame[2 + len(url) : -1] == b"\x00" * (192 - len(url))
    assert frame[-1] == 0xA0
    assert fake.flush_count == 1


def test_device_entry_url_rejects_space_before_writing_uart():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    with pytest.raises(ValueError, match="printable ASCII HTTPS"):
        adapter.send_device_entry_url(
            "https://www.jinshoubao.com/device entry/invalid"
        )

    assert fake.writes == []


def test_uart_reopen_resends_the_locally_stored_device_entry_url_once():
    first = FakeSerial()
    second = FakeSerial()
    serials = iter((first, second))
    url = "https://www.jinshoubao.com/device-entry/?deviceCode=public-1"
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: next(serials),
        device_entry_url_provider=lambda: {"deviceEntryUrl": url},
    )

    assert adapter.open()
    assert first.writes == []
    adapter.close()
    assert adapter.open()

    assert len(second.writes) == 1
    assert second.writes[0][2 : 2 + len(url)] == url.encode("ascii")


def test_parser_handles_self_test_and_smoke_frames_without_changing_lengths():
    parser = FixedFrameParser()
    self_test = bytes.fromhex("F1 03 00 2E E0 01 02 F1")
    smoke = bytes.fromhex("CC 01 CC")

    assert parser.feed(self_test[:5]) == []
    assert parser.feed(self_test[5:] + smoke) == [
        {
            "frame_type": "SELF_TEST",
            "valid_flags": 3,
            "weight_valid": True,
            "weight_grams": 12_000,
            "infrared_valid": True,
            "infrared_blocked": True,
            "smoke_code": 2,
            "raw_frame_hex": self_test.hex(),
        },
        {
            "frame_type": "SMOKE",
            "smoke_code": 1,
            "raw_frame_hex": smoke.hex(),
        },
    ]


def test_parser_decodes_revision_two_firmware_status():
    parser = FixedFrameParser()
    frame = firmware_status_frame(safe_flags=0x1F)

    assert parser.feed(frame[:19]) == []
    assert parser.feed(frame[19:]) == [
        {
            "frame_type": "FIRMWARE_STATUS",
            "mode": 1,
            "status_code": 0,
            "status": "OK",
            "protocol_revision": 2,
            "firmware_version_code": 10203,
            "firmware_version": "1.2.3",
            "firmware_identity_hex": "0102030405060708",
            "safe_flags": 0x1F,
            "raw_frame_hex": frame.hex(),
        }
    ]


@pytest.mark.parametrize(
    "frame",
    [
        firmware_status_frame(revision=1),
        firmware_status_frame(version="1.2.3")[:-2]
        + b"\x80"
        + firmware_status_frame(version="1.2.3")[-1:],
        firmware_status_frame(identity=b"\x00" * 8),
        firmware_status_frame(safe_flags=0x80),
    ],
)
def test_parser_rejects_invalid_firmware_status(frame):
    parser = FixedFrameParser()

    assert parser.feed(frame) == []
    assert parser.invalid_count(0xF3) == 1


def test_firmware_identity_query_updates_handshake_identity():
    response = firmware_status_frame(mode=1, safe_flags=0x0F)
    fake = FirmwareRespondingSerial({1: response})
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.query_firmware_identity(timeout_ms=20)
    handshake = adapter.handshake()

    assert fake.writes == [bytes.fromhex("F2 01 F2")]
    assert result == {
        "queryStatus": "OK",
        "mode": 1,
        "statusCode": 0,
        "status": "OK",
        "protocolRevision": 2,
        "firmwareVersionCode": 10203,
        "firmwareVersion": "1.2.3",
        "firmwareIdentityHex": "0102030405060708",
        "safeFlags": 0x0F,
        "rawFrameHex": response.hex(),
    }
    assert handshake["mcu_firmware_identity"] == "0102030405060708"
    assert handshake["mcu_firmware_version"] == "1.2.3"
    assert handshake["mcu_firmware_version_code"] == 10203
    assert handshake["fixed_frame_revision"] == 2
    assert adapter.verified_firmware_identity == {
        "queryStatus": "OK",
        "statusCode": 0,
        "fixedFrameRevision": 2,
        "firmwareVersionCode": 10203,
        "firmwareVersion": "1.2.3",
        "firmwareIdentityHex": "0102030405060708",
    }


def test_short_firmware_version_is_not_exposed_as_registration_fact():
    response = firmware_status_frame(mode=1, version="1")
    fake = FirmwareRespondingSerial({1: response})
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.query_firmware_identity(timeout_ms=20)

    assert result["queryStatus"] == "OK"
    assert adapter.verified_firmware_identity is None


def test_execute_firmware_update_prepare_requires_execution_confirmation():
    ready = firmware_status_frame(mode=2, safe_flags=0x1F)
    fake = FirmwareRespondingSerial({2: ready})
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.execute_firmware_update_prepare(timeout_ms=20)

    assert fake.writes == [bytes.fromhex("F2 02 F2")]
    assert result["executed"] is True


def test_legacy_mcu_policy_status_is_only_an_execution_failure():
    busy = firmware_status_frame(mode=2, status=1, safe_flags=0x0F)
    fake = FirmwareRespondingSerial({2: busy})
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.execute_firmware_update_prepare(timeout_ms=20)

    assert result["queryStatus"] == "OK"
    assert result["status"] == "LEGACY_BUSY"
    assert result["executed"] is False
    assert adapter.verified_firmware_identity is None


def test_execute_firmware_update_prepare_requires_all_result_flags():
    incomplete = firmware_status_frame(mode=2, status=0, safe_flags=0x0F)
    fake = FirmwareRespondingSerial({2: incomplete})
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.execute_firmware_update_prepare(timeout_ms=20)

    assert result["status"] == "OK"
    assert result["executed"] is False


def test_unsolicited_firmware_status_is_not_projected_as_business_event():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    fake.inject(firmware_status_frame())

    assert adapter.read_mcu_event(timeout_ms=20) is None


def test_self_test_query_returns_fresh_snapshot_and_queues_safety_event():
    fake = RespondingSerial(
        bytes.fromhex("F1 03 00 2E E0 00 02 F1")
    )
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.query_self_test(timeout_ms=20)
    event = adapter.read_mcu_event(timeout_ms=20)

    assert fake.writes == [bytes.fromhex("F0 01 F0")]
    assert result["queryStatus"] == "OK"
    assert result["communicationHealthy"] is True
    assert result["weightGrams"] == 12_000
    assert result["infraredBlocked"] is False
    assert result["smokeCode"] == 2
    assert result["smokeState"] == "UNKNOWN"
    assert result["smokeSensorHealth"] == "SENSOR_FAULT"
    assert event["message_name"] == "SAFETY_SENSOR_EVENT"
    assert event["payload"]["smokeState"] == "UNKNOWN"
    assert event["payload"]["smokeSensorHealth"] == "SENSOR_FAULT"
    assert event["payload"]["compatibilityMode"] is True


def test_self_test_discards_partial_response_that_started_before_query():
    stale = bytes.fromhex("F1 03 00 00 64 00 00 F1")
    fake = RespondingSerial(stale[4:])
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    fake.inject(stale[:4])
    assert adapter.read_mcu_event(timeout_ms=5) is None

    persisted = []
    result = adapter.query_self_test(
        timeout_ms=10,
        on_result=persisted.append,
    )

    assert result["queryStatus"] == "TIMEOUT"
    assert persisted == [result]


def test_self_test_query_preserves_interleaved_business_and_smoke_events():
    fake = RespondingSerial(
        bytes.fromhex(
            "DD 00 2E E0 00 2F FC 01 DD "
            "F1 03 00 2F FC 01 00 F1 "
            "CC 01 CC"
        )
    )
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.query_self_test(timeout_ms=20)
    queued = [
        adapter.read_mcu_event(timeout_ms=20),
        adapter.read_mcu_event(timeout_ms=20),
        adapter.read_mcu_event(timeout_ms=20),
    ]

    assert result["queryStatus"] == "OK"
    assert [item["message_name"] for item in queued] == [
        "COMPAT_DELIVERY_RESULT",
        "SAFETY_SENSOR_EVENT",
        "SAFETY_SENSOR_EVENT",
    ]
    assert queued[1]["payload"]["smokeState"] == "NORMAL"
    assert queued[2]["payload"]["smokeState"] == "ALARM"


def test_self_test_retry_suppresses_unchanged_safety_projection():
    fake = RespondingSerial(
        bytes.fromhex("F1 03 00 2E E0 00 00 F1")
    )
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    adapter.query_self_test(
        timeout_ms=20,
        on_result=lambda _: False,
        queue_unchanged_safety_event=False,
    )

    assert adapter.read_mcu_event(timeout_ms=10) is None


def test_self_test_retry_queues_one_changed_projection_before_following_cc():
    fake = RespondingSerial(
        bytes.fromhex(
            "F1 03 00 2E E0 00 00 F1 "
            "CC 01 CC"
        )
    )
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    adapter.query_self_test(
        timeout_ms=20,
        on_result=lambda _: True,
        queue_unchanged_safety_event=False,
    )
    queued = [
        adapter.read_mcu_event(timeout_ms=20),
        adapter.read_mcu_event(timeout_ms=20),
    ]

    assert [item["payload"]["smokeState"] for item in queued] == [
        "NORMAL",
        "ALARM",
    ]


def test_self_test_retry_suppresses_repeated_timeout_projection():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.query_self_test(
        timeout_ms=10,
        on_result=lambda _: False,
        queue_unchanged_safety_event=False,
    )

    assert result["queryStatus"] == "TIMEOUT"
    assert adapter.read_mcu_event(timeout_ms=10) is None


def test_self_test_timeout_and_invalid_response_are_distinct():
    timeout_serial = FakeSerial()
    timeout_adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: timeout_serial,
    )
    assert timeout_adapter.open()
    timeout = timeout_adapter.query_self_test(timeout_ms=10)

    invalid_serial = RespondingSerial(
        bytes.fromhex("F1 03 00 00 64 00 03 F1")
    )
    invalid_adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=78,
        serial_factory=lambda **kwargs: invalid_serial,
    )
    assert invalid_adapter.open()
    invalid = invalid_adapter.query_self_test(timeout_ms=10)

    assert timeout["queryStatus"] == "TIMEOUT"
    assert timeout["smokeSensorHealth"] == "TIMEOUT"
    assert invalid["queryStatus"] == "PROTOCOL_ERROR"
    assert invalid["smokeSensorHealth"] == "PROTOCOL_ERROR"


def test_start_command_discards_stale_work_but_keeps_smoke_alarm():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    fake.inject(
        bytes.fromhex(
            "DD 00 00 01 00 00 02 00 DD CC 01 CC"
        )
    )

    result = adapter.send_command("START_CLEAN_OPERATION", {})
    event = adapter.read_mcu_event(timeout_ms=20)

    assert result["acked"] is True
    assert event["message_name"] == "SAFETY_SENSOR_EVENT"
    assert event["payload"]["smokeState"] == "ALARM"
    assert adapter.read_mcu_event(timeout_ms=10) is None


def test_adapter_reads_aggregate_delivery_and_clean_events():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()
    fake.inject(
        bytes.fromhex(
            "DD 00 2E E0 00 2F FC 01 DD "
            "EF 01 86 A0 00 C3 50 00 EF"
        )
    )

    delivery = adapter.read_mcu_event(timeout_ms=20)
    clean = adapter.read_mcu_event(timeout_ms=20)

    assert delivery["message_name"] == "COMPAT_DELIVERY_RESULT"
    assert delivery["payload"]["preWeightGrams"] == 12_000
    assert delivery["payload"]["postWeightGrams"] == 12_284
    assert delivery["payload"]["infraredBlocked"] is True
    assert clean["message_name"] == "COMPAT_CLEAN_RESULT"
    assert clean["payload"]["preWeightGrams"] == 100_000
    assert clean["payload"]["postWeightGrams"] == 50_000
    assert clean["payload"]["infraredBlocked"] is False
    assert (
        clean["payload"]["mcuEventSequence"]
        == delivery["payload"]["mcuEventSequence"] + 1
    )


def test_unsupported_command_returns_failure_without_writing():
    fake = FakeSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.send_command(
        "MEASURE_BASELINE",
        {},
        mcu_command_uid="10000000-0000-4000-8000-000000000002",
    )

    assert result["acked"] is False
    assert result["error"] == "MCU_FEATURE_NOT_SUPPORTED"
    assert fake.writes == []


def test_short_uart_write_is_reported_as_failure():
    fake = ShortWriteSerial()
    adapter = FixedFrameMcuAdapter(
        "/dev/fake",
        edge_boot_id=77,
        serial_factory=lambda **kwargs: fake,
    )
    assert adapter.open()

    result = adapter.send_command(
        "START_CLEAN_OPERATION",
        {},
        mcu_command_uid="10000000-0000-4000-8000-000000000003",
    )

    assert result["acked"] is False
    assert result["error"] == "UART_WRITE_FAILED"
    assert fake.flush_count == 0
