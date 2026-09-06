from __future__ import annotations

from pathlib import Path

from first_boot.cellular_modem import (
    AtControlPort,
    ModemRegistrationProbe,
    SysfsAtControlPortInventory,
)
from first_boot.cellular_probe import UsbNetworkDevice


class _Ports:
    def __init__(self, *ports: AtControlPort) -> None:
        self._ports = ports

    def ports(self) -> tuple[AtControlPort, ...]:
        return self._ports


class _Responses:
    def __init__(self, **responses: str) -> None:
        self.responses = responses
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def query(self, port: Path, commands: tuple[str, ...]) -> dict[str, str]:
        self.calls.append((port, commands))
        return {command: self.responses[command] for command in commands}


def _device() -> UsbNetworkDevice:
    return UsbNetworkDevice(
        interface="enxcell0",
        usb_vid="19d1",
        usb_pid="0001",
        driver="rndis_host",
        usb_parent_verified=True,
        usb_parent_path="/sys/devices/platform/usb3/3-1",
    )


def _port(**overrides: object) -> AtControlPort:
    values: dict[str, object] = {
        "path": Path("/dev/ttyACM0"),
        "usb_vid": "19d1",
        "usb_pid": "0001",
        "usb_parent_path": "/sys/devices/platform/usb3/3-1",
    }
    values.update(overrides)
    return AtControlPort(**values)


def test_registration_probe_requires_sim_registration_and_packet_attach() -> None:
    responses = _Responses(
        AT="\r\nOK\r\n",
        **{
            "AT+CPIN?": "\r\n+CPIN: READY\r\n\r\nOK\r\n",
            "AT+CEREG?": "\r\n+CEREG: 0,1\r\n\r\nOK\r\n",
            "AT+CGATT?": "\r\n+CGATT: 1\r\n\r\nOK\r\n",
        },
    )

    result = ModemRegistrationProbe(
        inventory=_Ports(_port()),
        client=responses,
    ).probe(_device())

    assert result.ready
    assert result.error_code == "NONE"
    assert responses.calls == [
        (
            Path("/dev/ttyACM0"),
            ("AT", "AT+CPIN?", "AT+CEREG?", "AT+CGATT?"),
        )
    ]


def test_registration_pending_stops_before_packet_attach_is_accepted() -> None:
    responses = _Responses(
        AT="OK",
        **{
            "AT+CPIN?": "+CPIN: READY\r\nOK",
            "AT+CEREG?": "+CEREG: 0,2\r\nOK",
            "AT+CGATT?": "+CGATT: 0\r\nOK",
        },
    )

    result = ModemRegistrationProbe(
        inventory=_Ports(_port()),
        client=responses,
    ).probe(_device())

    assert not result.ready
    assert result.error_code == "CELLULAR_NETWORK_REGISTRATION_PENDING"


def test_registration_probe_rejects_an_at_port_from_another_usb_device() -> None:
    responses = _Responses()

    result = ModemRegistrationProbe(
        inventory=_Ports(
            _port(usb_parent_path="/sys/devices/platform/usb3/3-2")
        ),
        client=responses,
    ).probe(_device())

    assert not result.ready
    assert result.error_code == "CELLULAR_MODEM_CONTROL_UNAVAILABLE"
    assert responses.calls == []


def test_sysfs_inventory_selects_only_the_interface_named_at(
    tmp_path: Path,
) -> None:
    sys_tty = tmp_path / "sys/class/tty"
    usb = tmp_path / "sys/devices/usb3/3-1"
    for name, interface in (("ttyACM0", "at"), ("ttyACM1", "log")):
        device = usb / f"interface-{2 if name == 'ttyACM0' else 4}"
        device.mkdir(parents=True)
        (device / "interface").write_text(interface + "\n", encoding="ascii")
        link = sys_tty / name / "device"
        link.parent.mkdir(parents=True)
        link.symlink_to(device, target_is_directory=True)
    (usb / "idVendor").write_text("19d1\n", encoding="ascii")
    (usb / "idProduct").write_text("0001\n", encoding="ascii")

    ports = SysfsAtControlPortInventory(
        sys_class_tty=sys_tty,
        device_root=Path("/dev"),
    ).ports()

    assert ports == [
        AtControlPort(
            path=Path("/dev/ttyACM0"),
            usb_vid="19d1",
            usb_pid="0001",
            usb_parent_path=str(usb.resolve()),
        )
    ]
