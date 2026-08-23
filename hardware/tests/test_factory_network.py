from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from factory.network import (
    FactoryNetworkConfig,
    FactoryNetworkConfigurationError,
    derive_factory_id,
    read_setup_ap_passphrase,
    render_dnsmasq,
    render_hostapd,
    validate_wpa2_passphrase,
)


def test_factory_id_is_stable_public_station_label() -> None:
    machine_id = "0123456789abcdef0123456789abcdef"
    release_id = "2026.08.22-p4"

    expected = hashlib.sha256(f"{machine_id}\n{release_id}".encode()).hexdigest()

    assert derive_factory_id(machine_id, release_id) == expected[:8].upper()


@pytest.mark.parametrize(
    ("machine_id", "release_id"),
    [
        ("", "release"),
        ("not-a-machine-id", "release"),
        ("0123456789abcdef0123456789abcdef", ""),
        ("0123456789abcdef0123456789abcdef", "x" * 129),
    ],
)
def test_factory_id_rejects_uninitialized_or_unbounded_inputs(
    machine_id: str, release_id: str
) -> None:
    with pytest.raises(FactoryNetworkConfigurationError):
        derive_factory_id(machine_id, release_id)


def test_hostapd_is_wpa2_ccmp_only_and_dnsmasq_has_no_upstream() -> None:
    config = FactoryNetworkConfig(factory_id="12AB34CD")
    password = "Factory-Only-42"

    hostapd = render_hostapd(config, password)
    dnsmasq = render_dnsmasq(config)

    assert "interface=wlan0" in hostapd
    assert "ssid=EcoBin-Factory-12AB34CD" in hostapd
    assert "wpa=2" in hostapd
    assert "wpa_key_mgmt=WPA-PSK" in hostapd
    assert "rsn_pairwise=CCMP" in hostapd
    assert "TKIP" not in hostapd
    assert f"wpa_passphrase={password}" in hostapd

    assert "listen-address=10.42.0.1" in dnsmasq
    assert "bind-interfaces" in dnsmasq
    assert "bind-dynamic" not in dnsmasq
    assert "dhcp-range=10.42.0.20,10.42.0.100,255.255.255.0,10m" in dnsmasq
    assert "address=/#/10.42.0.1" in dnsmasq
    assert "no-resolv" in dnsmasq
    assert "except-interface=lo" in dnsmasq
    assert "pid-file=" not in dnsmasq
    assert "no-dhcpv6-interface=wlan0" in dnsmasq
    assert "dhcp-broadcast" in dnsmasq
    assert "no-ping" in dnsmasq
    assert "dhcp-option=option:router\n" in dnsmasq
    assert "dhcp-leasefile=/run/ecobin/factory-network/dnsmasq-state/dnsmasq.leases" in dnsmasq
    assert password not in dnsmasq


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "x" * 64,
        " leading-space",
        "trailing-space ",
        "line\nbreak",
        "中文密码123456",
    ],
)
def test_wpa2_password_rejects_unsafe_values(password: str) -> None:
    with pytest.raises(FactoryNetworkConfigurationError):
        validate_wpa2_passphrase(password)


def test_secret_reader_accepts_one_bounded_private_line(tmp_path: Path) -> None:
    secret = tmp_path / "setup-ap.key"
    secret.write_bytes(b"Factory-Only-42\n")
    os.chmod(secret, 0o600)

    assert read_setup_ap_passphrase(secret) == "Factory-Only-42"


def test_secret_reader_rejects_multiple_lines(tmp_path: Path) -> None:
    secret = tmp_path / "setup-ap.key"
    secret.write_bytes(b"Factory-Only-42\n\n")
    os.chmod(secret, 0o600)

    with pytest.raises(FactoryNetworkConfigurationError, match="one line"):
        read_setup_ap_passphrase(secret)


def test_network_shape_cannot_be_redirected_to_another_interface() -> None:
    with pytest.raises(FactoryNetworkConfigurationError):
        FactoryNetworkConfig(factory_id="12AB34CD", interface="eth0").validate()
    with pytest.raises(FactoryNetworkConfigurationError):
        FactoryNetworkConfig(factory_id="12AB34CD", address="0.0.0.0").validate()
    with pytest.raises(FactoryNetworkConfigurationError):
        FactoryNetworkConfig(factory_id="not-safe").validate()
