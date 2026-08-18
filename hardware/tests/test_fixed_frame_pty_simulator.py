from __future__ import annotations

import sys
import threading
import time

import pytest

from fixed_frame_mcu_adapter import FixedFrameMcuAdapter
from tools.fixed_frame_pty_simulator import (
    CLEAN_RESULT_HEADER,
    DELIVERY_RESULT_HEADER,
    DEVICE_ENTRY_URL_FIELD_LENGTH,
    SELF_TEST_RESPONSE_HEADER,
    DownstreamFrameParser,
    LinuxPtyFixedFrameSimulator,
    SimulatorConfig,
    VirtualFixedFrameMcu,
    encode_result_frame,
    encode_self_test_frame,
    encode_smoke_frame,
    parse_args,
)


def _device_entry_url_frame(url: str) -> bytes:
    encoded = url.encode("ascii")
    return b"".join((
        bytes((0xA0, len(encoded))),
        encoded.ljust(DEVICE_ENTRY_URL_FIELD_LENGTH, b"\x00"),
        b"\xA0",
    ))


def test_downstream_parser_handles_fragmented_joined_and_noisy_frames():
    parser = DownstreamFrameParser()

    assert parser.feed(bytes.fromhex("99 BB 04")) == []
    frames = parser.feed(
        bytes.fromhex(
            "BB AA 01 AA "
            "BB FF BB "
            "EE 01 EE "
            "F0 01 F0"
        )
    )

    assert frames == [
        bytes.fromhex("BB 04 BB"),
        bytes.fromhex("AA 01 AA"),
        bytes.fromhex("EE 01 EE"),
        bytes.fromhex("F0 01 F0"),
    ]


def test_virtual_mcu_retains_fragmented_device_entry_url_without_response():
    parser = DownstreamFrameParser()
    model = VirtualFixedFrameMcu(SimulatorConfig())
    url = "https://www.jinshoubao.com/device-entry/public-code-1"
    frame = _device_entry_url_frame(url)

    assert parser.feed(frame[:50]) == []
    parsed = parser.feed(frame[50:] + bytes.fromhex("F0 01 F0"))
    assert parsed == [frame, bytes.fromhex("F0 01 F0")]
    assert model.handle_frame(parsed[0]) == ("DEVICE_ENTRY_URL", None)
    assert model.device_entry_url == url
    assert model.device_entry_url_count == 1


def test_virtual_mcu_remembers_price_and_builds_delivery_result():
    model = VirtualFixedFrameMcu(
        SimulatorConfig(
            delivery_pre_weight_grams=10_000,
            delivery_post_weight_grams=12_500,
            delivery_infrared_blocked=1,
        )
    )

    assert model.handle_frame(bytes.fromhex("BB 09 BB")) == ("PRICE", None)
    name, response = model.handle_frame(bytes.fromhex("AA 01 AA"))

    assert model.last_price_digit == 9
    assert model.delivery_start_count == 1
    assert name == "DELIVERY_START"
    assert response == bytes.fromhex(
        "DD 00 27 10 00 30 D4 01 DD"
    )


def test_virtual_mcu_builds_clean_result_with_uint24_weights():
    model = VirtualFixedFrameMcu(
        SimulatorConfig(
            clean_pre_weight_grams=350_000,
            clean_post_weight_grams=800,
            clean_infrared_blocked=0,
        )
    )

    name, response = model.handle_frame(bytes.fromhex("EE 01 EE"))

    assert model.clean_start_count == 1
    assert name == "CLEAN_START"
    assert response == bytes.fromhex(
        "EF 05 57 30 00 03 20 00 EF"
    )


def test_virtual_mcu_returns_self_test_and_reports_smoke_changes():
    model = VirtualFixedFrameMcu(
        SimulatorConfig(
            self_test_weight_grams=12_000,
            self_test_infrared_blocked=1,
            smoke_state=0,
        )
    )

    name, response = model.handle_frame(bytes.fromhex("F0 01 F0"))

    assert name == "SELF_TEST"
    assert response == bytes.fromhex("F1 03 00 2E E0 01 00 F1")
    assert model.change_smoke_state(2) == bytes.fromhex("CC 02 CC")
    assert model.change_smoke_state(2) is None
    assert model.change_smoke_state(0) == bytes.fromhex("CC 00 CC")


def test_self_test_and_smoke_encoders_reject_invalid_values():
    assert encode_self_test_frame(3, 1000, 0, 1)[0] == (
        SELF_TEST_RESPONSE_HEADER
    )
    assert encode_smoke_frame(2) == bytes.fromhex("CC 02 CC")
    with pytest.raises(ValueError, match="valid_flags"):
        encode_self_test_frame(4, 1000, 0, 0)
    with pytest.raises(ValueError, match="smoke_code"):
        encode_smoke_frame(3)


@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        (
            (DELIVERY_RESULT_HEADER, 350_001, 0, 0),
            "pre_weight_grams",
        ),
        (
            (CLEAN_RESULT_HEADER, 0, 0, 2),
            "infrared_blocked",
        ),
        ((0xAA, 0, 0, 0), "header"),
    ],
)
def test_result_encoder_rejects_invalid_wire_values(
    arguments,
    expected_fragment,
):
    with pytest.raises(ValueError, match=expected_fragment):
        encode_result_frame(*arguments)


def test_command_line_defaults_expose_a_stable_linux_serial_link():
    args = parse_args([])

    assert args.link.as_posix() == "/tmp/ecobin-fixed-frame-mcu"
    assert args.response_delay_ms == 500
    assert args.delivery_result_delay_ms == 40_000
    assert args.clean_result_delay_ms == 40_000
    assert args.delivery_post_grams - args.delivery_pre_grams == 1_200
    assert args.clean_pre_grams == args.delivery_post_grams
    assert args.clean_post_grams < args.clean_pre_grams
    assert args.self_test_weight_valid == 1
    assert args.self_test_full_valid == 1
    assert args.smoke_state == 0


def test_business_result_delays_are_independent_from_self_test_delay():
    config = SimulatorConfig(
        response_delay_ms=500,
        delivery_result_delay_ms=40_000,
        clean_result_delay_ms=40_000,
    )

    assert config.response_delay_for("SELF_TEST") == 500
    assert config.response_delay_for("DELIVERY_START") == 40_000
    assert config.response_delay_for("CLEAN_START") == 40_000

    args = parse_args([
        "--delivery-result-delay-ms",
        "40000",
        "--clean-result-delay-ms",
        "40000",
    ])
    assert args.delivery_result_delay_ms == 40_000
    assert args.clean_result_delay_ms == 40_000


@pytest.mark.parametrize(
    "field_name",
    ("delivery_result_delay_ms", "clean_result_delay_ms"),
)
def test_business_result_delay_rejects_negative_values(field_name):
    with pytest.raises(ValueError, match=field_name):
        SimulatorConfig(**{field_name: -1})


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux PTY integration requires Linux",
)
def test_real_fixed_frame_adapter_round_trips_over_linux_pty(tmp_path):
    link_path = tmp_path / "virtual-mcu"
    simulator = LinuxPtyFixedFrameSimulator(
        SimulatorConfig(
            response_delay_ms=10,
            delivery_result_delay_ms=10,
            clean_result_delay_ms=10,
        ),
        link_path,
        exit_after_responses=3,
        log=lambda message: None,
    )
    simulator.open()
    thread = threading.Thread(target=simulator.run, daemon=True)
    thread.start()
    adapter = FixedFrameMcuAdapter(
        str(link_path),
        edge_boot_id=77,
        timeout_s=0.1,
    )
    try:
        assert adapter.open()
        self_test = adapter.query_self_test(timeout_ms=2000)
        safety = adapter.read_mcu_event(timeout_ms=2000)
        delivery_command = adapter.send_command(
            "START_DELIVERY_SESSION",
            {"unitPriceTenThousandths": 4500},
        )
        delivery = adapter.read_mcu_event(timeout_ms=2000)

        assert self_test["queryStatus"] == "OK"
        assert self_test["weightGrams"] == 10_000
        assert safety["message_name"] == "SAFETY_SENSOR_EVENT"
        assert safety["payload"]["smokeState"] == "NORMAL"
        assert delivery_command["acked"] is True
        assert delivery["message_name"] == "COMPAT_DELIVERY_RESULT"
        assert delivery["payload"]["preWeightGrams"] == 10_000
        assert delivery["payload"]["postWeightGrams"] == 11_200
        assert simulator.model.last_price_digit == 4

        clean_command = adapter.send_command(
            "START_CLEAN_OPERATION",
            {},
        )
        clean = adapter.read_mcu_event(timeout_ms=2000)

        assert clean_command["acked"] is True
        assert clean["message_name"] == "COMPAT_CLEAN_RESULT"
        assert clean["payload"]["preWeightGrams"] == 11_200
        assert clean["payload"]["postWeightGrams"] == 800
    finally:
        adapter.close()
        simulator.stop()
        thread.join(timeout=2)
        simulator.close()

    assert not link_path.exists()
    assert not link_path.is_symlink()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux PTY integration requires Linux",
)
def test_real_pty_background_reads_cannot_starve_physical_starts(tmp_path):
    link_path = tmp_path / "virtual-mcu-contended"
    simulator = LinuxPtyFixedFrameSimulator(
        SimulatorConfig(
            response_delay_ms=10,
            delivery_result_delay_ms=10,
            clean_result_delay_ms=10,
        ),
        link_path,
        log=lambda message: None,
    )
    simulator.open()
    simulator_thread = threading.Thread(target=simulator.run, daemon=True)
    simulator_thread.start()
    adapter = FixedFrameMcuAdapter(
        str(link_path),
        edge_boot_id=77,
        timeout_s=0.1,
    )
    stop_reader = threading.Event()

    def read_forever():
        while not stop_reader.is_set():
            adapter.read_mcu_event(timeout_ms=100)

    reader = threading.Thread(target=read_forever, daemon=True)
    elapsed_seconds = []
    try:
        assert adapter.open()
        reader.start()
        for index in range(20):
            started_at = time.monotonic()
            if index % 2 == 0:
                result = adapter.send_command(
                    "START_DELIVERY_SESSION",
                    {
                        "unitPriceTenThousandths": 4_500,
                        "startExecutionWindowMs": 1_000,
                    },
                )
            else:
                result = adapter.send_command(
                    "START_CLEAN_OPERATION",
                    {"startExecutionWindowMs": 1_000},
                )
            elapsed_seconds.append(time.monotonic() - started_at)
            assert result["acked"] is True

        deadline = time.monotonic() + 2
        while (
            (
                simulator.model.delivery_start_count < 10
                or simulator.model.clean_start_count < 10
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        assert max(elapsed_seconds) < 0.5
        assert simulator.model.delivery_start_count == 10
        assert simulator.model.clean_start_count == 10
    finally:
        stop_reader.set()
        reader.join(timeout=1)
        adapter.close()
        simulator.stop()
        simulator_thread.join(timeout=2)
        simulator.close()

    assert not reader.is_alive()
    assert not link_path.exists()
    assert not link_path.is_symlink()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux PTY integration requires Linux",
)
def test_real_device_entry_url_is_fire_and_forget_and_replayed_on_reopen(
    tmp_path,
):
    link_path = tmp_path / "virtual-mcu-device-entry-url"
    simulator = LinuxPtyFixedFrameSimulator(
        SimulatorConfig(
            response_delay_ms=10,
            delivery_result_delay_ms=10,
            clean_result_delay_ms=10,
        ),
        link_path,
        log=lambda message: None,
    )
    simulator.open()
    thread = threading.Thread(target=simulator.run, daemon=True)
    thread.start()
    url = (
        "https://www.jinshoubao.com/device-entry/"
        "?deviceCode=Dv_0123456789abcdefghijklmn"
    )
    adapter = FixedFrameMcuAdapter(
        str(link_path),
        edge_boot_id=77,
        timeout_s=0.1,
        device_entry_url_provider=lambda: {"deviceEntryUrl": url},
    )
    try:
        assert adapter.open()
        result = adapter.send_device_entry_url(url)
        deadline = time.monotonic() + 2
        while (
            simulator.model.device_entry_url_count < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        assert result["responseExpected"] is False
        assert simulator.model.device_entry_url == url
        assert simulator.model.device_entry_url_count == 1
        assert adapter.read_mcu_event(timeout_ms=100) is None

        adapter.close()
        assert adapter.open()
        deadline = time.monotonic() + 2
        while (
            simulator.model.device_entry_url_count < 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        assert simulator.model.device_entry_url == url
        assert simulator.model.device_entry_url_count == 2
        assert adapter.read_mcu_event(timeout_ms=100) is None
    finally:
        adapter.close()
        simulator.stop()
        thread.join(timeout=2)
        simulator.close()

    assert not link_path.exists()
    assert not link_path.is_symlink()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux PTY integration requires Linux",
)
def test_real_smoke_change_round_trips_over_linux_pty(tmp_path):
    link_path = tmp_path / "virtual-mcu-smoke"
    simulator = LinuxPtyFixedFrameSimulator(
        SimulatorConfig(
            smoke_state=0,
            smoke_change_to=2,
            response_delay_ms=10,
        ),
        link_path,
        exit_after_responses=2,
        log=lambda message: None,
    )
    simulator.open()
    thread = threading.Thread(target=simulator.run, daemon=True)
    thread.start()
    adapter = FixedFrameMcuAdapter(
        str(link_path),
        edge_boot_id=77,
        timeout_s=0.1,
    )
    try:
        assert adapter.open()
        self_test = adapter.query_self_test(timeout_ms=2000)
        initial = adapter.read_mcu_event(timeout_ms=2000)
        changed = adapter.read_mcu_event(timeout_ms=2000)

        assert self_test["smokeState"] == "NORMAL"
        assert initial["payload"]["smokeState"] == "NORMAL"
        assert changed["payload"]["smokeState"] == "UNKNOWN"
        assert changed["payload"]["smokeSensorHealth"] == "SENSOR_FAULT"
        assert changed["payload"]["rawFrameHex"] == "cc02cc"
    finally:
        adapter.close()
        simulator.stop()
        thread.join(timeout=2)
        simulator.close()

    assert not link_path.exists()
    assert not link_path.is_symlink()
