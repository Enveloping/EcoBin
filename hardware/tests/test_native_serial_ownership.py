"""Native candidate and legacy UART clients share POSIX advisory ownership."""
import os
from types import SimpleNamespace

import pytest
import serial

import uart_link


def test_uart_v1_requests_exclusive_ownership_on_posix(monkeypatch):
    opened = []

    def factory(**kwargs):
        opened.append(kwargs)
        return SimpleNamespace(is_open=True, close=lambda: None)

    monkeypatch.setattr(uart_link, "os", SimpleNamespace(name="posix"), raising=False)
    monkeypatch.setattr(serial, "Serial", factory)
    link = uart_link.UartLink("/dev/fake", 7)
    try:
        assert link.open()
        assert opened == [dict(port="/dev/fake", baudrate=115200, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.5, exclusive=True)]
    finally:
        link.close()


def test_uart_v1_does_not_pass_posix_exclusive_option_on_windows(monkeypatch):
    opened = []
    def factory(**kwargs):
        opened.append(kwargs)
        return SimpleNamespace(is_open=True, close=lambda: None)
    monkeypatch.setattr(uart_link, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(serial, "Serial", factory)
    link = uart_link.UartLink("COM999", 7)
    try:
        assert link.open()
        assert "exclusive" not in opened[0]
        assert opened[0]["timeout"] == 0.5
    finally:
        link.close()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX pseudo-TTY advisory locks")
@pytest.mark.parametrize("first", ["legacy", "native"])
@pytest.mark.parametrize("legacy_kind", ["uart-v1", "fixed-frame"])
def test_actual_pseudo_tty_rejects_second_owner_in_both_directions(first, legacy_kind):
    import pty
    from native_recovery_entry import _serial_port
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    if legacy_kind == "uart-v1":
        legacy = uart_link.UartLink(name, 7)
    else:
        from fixed_frame_mcu_adapter import FixedFrameMcuAdapter
        legacy = FixedFrameMcuAdapter(port=name, edge_boot_id=7)
    native = None
    try:
        if first == "legacy":
            assert legacy.open()
            with pytest.raises(serial.SerialException):
                _serial_port(port=name, baudrate=115200, timeout=0, write_timeout=1, exclusive=True)
            legacy.close()
            native = _serial_port(port=name, baudrate=115200, timeout=0, write_timeout=1, exclusive=True)
            assert native.is_open
        else:
            native = _serial_port(port=name, baudrate=115200, timeout=0, write_timeout=1, exclusive=True)
            assert not legacy.open()
            assert not legacy.is_open
            native.close()
            assert legacy.open()
    finally:
        legacy.close()
        if native is not None:
            native.close()
        os.close(slave)
        os.close(master)
