from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from first_boot.cellular_config import (
    CellularBatchConfig,
    CellularConfigurationError,
    parse_cellular_config,
)
from first_boot.cellular_probe import (
    CellularProbe,
    SimState,
    UsbNetworkDevice,
)
from first_boot.command import CommandResult, redact_command_output
from first_boot.network_manager import (
    NetworkManagerActivator,
    install_network_manager_profile,
    render_network_manager_profile,
)


def _config_text(**overrides: str) -> str:
    values = {
        "ECOBIN_CELLULAR_SCHEMA_VERSION": "2",
        "ECOBIN_CELLULAR_HIL_APPROVED": "true",
        "ECOBIN_CELLULAR_CONNECTION_ID": "ecobin-air780e-rndis",
        "ECOBIN_CELLULAR_USB_DRIVER": "rndis_host",
        "ECOBIN_CELLULAR_USB_PROFILE": "RNDIS",
        "ECOBIN_CELLULAR_AUTO_APN": "true",
        "ECOBIN_CELLULAR_PROBE_IPV4": "203.0.113.10",
        "ECOBIN_CELLULAR_HTTPS_PROBE_URL": "https://probe.example.test/health",
    }
    values.update(overrides)
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _config() -> CellularBatchConfig:
    return parse_cellular_config(_config_text())


class _Inventory:
    def __init__(self, *devices: UsbNetworkDevice) -> None:
        self._devices = devices

    def devices(self) -> tuple[UsbNetworkDevice, ...]:
        return self._devices


class _Runner:
    def __init__(self, *, selected_interface: str = "enxcell0") -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.selected_interface = selected_interface

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, timeout_seconds))
        if command[1:6] == ("-j", "-4", "address", "show", "dev"):
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "ifname": command[-1],
                            "addr_info": [
                                {"local": "192.168.5.2", "scope": "global"}
                            ],
                        }
                    ]
                ),
            )
        if command[1:] == ("-j", "-4", "route", "show", "default"):
            return CommandResult(0, json.dumps([{"dst": "default", "dev": "enxcell0"}]))
        if command[1:6] == ("-j", "-4", "route", "get", "203.0.113.10"):
            return CommandResult(0, json.dumps([{"dev": self.selected_interface}]))
        if command[0] == "/usr/bin/resolvectl":
            return CommandResult(0, "probe.example.test: 203.0.113.10\n")
        if command[0] == "/usr/bin/curl":
            return CommandResult(0, "")
        if command[0] == "/usr/bin/nmcli":
            return CommandResult(0, "")
        raise AssertionError(f"unexpected command shape: {command[0:3]}")


def _device(**overrides: object) -> UsbNetworkDevice:
    values: dict[str, object] = {
        "interface": "enxcell0",
        "usb_vid": "19d1",
        "usb_pid": "0001",
        "driver": "rndis_host",
        "usb_parent_verified": True,
    }
    values.update(overrides)
    return UsbNetworkDevice(**values)


def test_repository_template_is_explicitly_hil_locked() -> None:
    template = (
        Path(__file__).parents[1]
        / "first_boot"
        / "config"
        / "cellular.env.example"
    ).read_text(encoding="utf-8")

    with pytest.raises(CellularConfigurationError) as captured:
        parse_cellular_config(template)

    assert captured.value.code == "CELLULAR_HIL_LOCKED"
    assert "HIL_REQUIRED" in template
    assert "ECOBIN_CELLULAR_USB_VID" not in template
    assert "ECOBIN_CELLULAR_USB_PID" not in template


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"ECOBIN_CELLULAR_HIL_APPROVED": "false"}, "CELLULAR_HIL_LOCKED"),
        ({"ECOBIN_CELLULAR_USB_DRIVER": "cdc_ether"}, "CELLULAR_USB_DRIVER_UNKNOWN"),
        ({"ECOBIN_CELLULAR_USB_PROFILE": "QMI"}, "CELLULAR_USB_PROFILE_UNKNOWN"),
        ({"ECOBIN_CELLULAR_AUTO_APN": "false"}, "CELLULAR_APN_MODE_UNSUPPORTED"),
        (
            {"ECOBIN_CELLULAR_HTTPS_PROBE_URL": "https://probe.example.test/?token=secret"},
            "CELLULAR_HTTPS_PROBE_INVALID",
        ),
    ],
)
def test_batch_schema_fails_closed_on_unapproved_or_unknown_facts(
    overrides: dict[str, str], code: str
) -> None:
    with pytest.raises(CellularConfigurationError) as captured:
        parse_cellular_config(_config_text(**overrides))
    assert captured.value.code == code


def test_verified_rndis_dhcp_route_dns_and_https_are_all_required() -> None:
    runner = _Runner()
    health = CellularProbe(_config(), _Inventory(_device()), runner=runner).probe()

    assert health.ready
    assert health.interface == "enxcell0"
    assert health.ipv4_address == "192.168.5.2"
    assert health.dhcp_ready
    assert health.default_route_ready
    assert health.dns_ready
    assert health.https_ready
    assert all(call[0][0].startswith("/") for call in runner.calls)
    assert all(0 < call[1] <= 30 for call in runner.calls)
    assert not any("firmware" in " ".join(call[0]).lower() for call in runner.calls)
    assert not any("SETUSB" in " ".join(call[0]) for call in runner.calls)
    assert not any("RNDISCALL" in " ".join(call[0]) for call in runner.calls)


def test_usb_ids_are_diagnostics_and_do_not_require_batch_configuration() -> None:
    runner = _Runner()
    health = CellularProbe(
        _config(),
        _Inventory(_device(usb_vid="2c7c", usb_pid="0901")),
        runner=runner,
    ).probe()

    assert health.ready
    assert health.usb_vid == "2c7c"
    assert health.usb_pid == "0901"


def test_multiple_capable_rndis_interfaces_are_rejected_without_guessing() -> None:
    runner = _Runner()
    health = CellularProbe(
        _config(),
        _Inventory(
            _device(interface="enxcell0"),
            _device(interface="enxcell1", usb_vid="2c7c", usb_pid="0901"),
        ),
        runner=runner,
    ).probe()

    assert not health.ready
    assert health.error_code == "CELLULAR_RNDIS_AMBIGUOUS"
    assert runner.calls == []


def test_legacy_vid_pid_configuration_is_rejected_instead_of_silently_ignored() -> None:
    legacy = _config_text() + "ECOBIN_CELLULAR_USB_VID=19d1\n"

    with pytest.raises(CellularConfigurationError) as captured:
        parse_cellular_config(legacy)

    assert captured.value.code == "CELLULAR_CONFIG_FIELDS"


def test_spoofed_rndis_name_without_verified_usb_parent_is_rejected() -> None:
    runner = _Runner()
    health = CellularProbe(
        _config(),
        _Inventory(_device(usb_parent_verified=False)),
        runner=runner,
    ).probe()

    assert not health.ready
    assert health.error_code == "CELLULAR_USB_PARENT_UNVERIFIED"
    assert runner.calls == []


def test_default_route_selected_on_ethernet_cannot_substitute_for_cellular() -> None:
    runner = _Runner(selected_interface="eth0")
    health = CellularProbe(_config(), _Inventory(_device()), runner=runner).probe()

    assert not health.ready
    assert health.error_code == "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE"
    assert not any(call[0][0] == "/usr/bin/resolvectl" for call in runner.calls)
    assert not any(call[0][0] == "/usr/bin/curl" for call in runner.calls)


def test_known_absent_sim_fails_before_any_ip_command() -> None:
    runner = _Runner()
    health = CellularProbe(
        _config(),
        _Inventory(_device()),
        runner=runner,
        sim_state=lambda _interface: SimState.ABSENT,
    ).probe()

    assert not health.ready
    assert health.error_code == "CELLULAR_SIM_ABSENT"
    assert runner.calls == []


def test_command_output_redacts_full_length_subscriber_identifiers() -> None:
    output = redact_command_output(
        b"ICCID=89860012345678901234 IMSI=460001234567890 ip=192.168.5.2"
    )
    assert "89860012345678901234" not in output
    assert "460001234567890" not in output
    assert output.count("[REDACTED]") == 2
    assert "192.168.5.2" in output


def test_nm_profile_is_deterministic_and_never_autoconnects(tmp_path: Path) -> None:
    profile = render_network_manager_profile(_config(), _device())
    second = render_network_manager_profile(_config(), _device())

    assert profile == second
    assert "autoconnect=false" in profile
    assert "autoconnect=true" not in profile
    assert "interface-name=enxcell0" in profile
    assert "method=auto" in profile
    assert "password" not in profile.lower()
    assert "apn" not in profile.lower()

    target = tmp_path / "system-connections" / "air780e.nmconnection"
    install_network_manager_profile(target, profile)
    assert target.read_text(encoding="utf-8") == profile
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_nm_activation_is_impossible_before_factory_pass() -> None:
    runner = _Runner()
    activator = NetworkManagerActivator(runner)

    with pytest.raises(PermissionError, match="FACTORY_TEST_GATE_CLOSED"):
        activator.activate(
            profile_path=Path("/etc/NetworkManager/system-connections/air780e.nmconnection"),
            connection_id="ecobin-air780e-rndis",
            interface="enxcell0",
            factory_test_passed=False,
            factory_recovery_required=False,
        )
    with pytest.raises(PermissionError, match="FACTORY_TEST_GATE_CLOSED"):
        activator.activate(
            profile_path=Path("/etc/NetworkManager/system-connections/air780e.nmconnection"),
            connection_id="ecobin-air780e-rndis",
            interface="enxcell0",
            factory_test_passed=True,
            factory_recovery_required=True,
        )
    assert runner.calls == []
