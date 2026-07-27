from fixed_frame_mcu_adapter import (
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


def test_parser_handles_partial_joined_and_payload_marker_bytes():
    parser = FixedFrameParser()
    delivery = bytes.fromhex("DD 00 DD 10 00 EF 20 01 DD")
    clean = bytes.fromhex("EF 01 86 A0 00 C3 50 00 EF")

    assert parser.feed(b"\x99\x88" + delivery[:4]) == []
    results = parser.feed(delivery[4:] + clean)

    assert results == [
        {
            "result_type": "DELIVERY",
            "pre_weight_grams": 0x00DD10,
            "post_weight_grams": 0x00EF20,
            "infrared_blocked": True,
            "raw_frame_hex": delivery.hex(),
        },
        {
            "result_type": "CLEAN",
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
            "result_type": "DELIVERY",
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
    assert fake.reset_count == 1


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
