"""Render the fixed factory access-point configuration.

The functions in this module are intentionally pure apart from the narrowly
scoped secret-file reader.  They make the AP configuration reviewable without
requiring hostapd, dnsmasq, or Orange Pi hardware on the development machine.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import stat


FACTORY_INTERFACE = "wlan0"
FACTORY_ADDRESS = "10.42.0.1"
FACTORY_PREFIX_LENGTH = 24
FACTORY_PORT = 80
FACTORY_DHCP_START = "10.42.0.20"
FACTORY_DHCP_END = "10.42.0.100"

_FACTORY_ID_RE = re.compile(r"^[0-9A-F]{8}$")
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


class FactoryNetworkConfigurationError(ValueError):
    """Raised when an AP input would weaken or corrupt the fixed setup."""


@dataclass(frozen=True, slots=True)
class FactoryNetworkConfig:
    """The single network shape supported by the production factory image."""

    factory_id: str
    interface: str = FACTORY_INTERFACE
    address: str = FACTORY_ADDRESS
    prefix_length: int = FACTORY_PREFIX_LENGTH
    dhcp_start: str = FACTORY_DHCP_START
    dhcp_end: str = FACTORY_DHCP_END
    channel: int = 6
    country_code: str = "CN"

    @property
    def ssid(self) -> str:
        return f"EcoBin-Factory-{self.factory_id}"

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(
            f"{self.address}/{self.prefix_length}", strict=False
        )

    def validate(self) -> None:
        if not _FACTORY_ID_RE.fullmatch(self.factory_id):
            raise FactoryNetworkConfigurationError(
                "factory_id must be eight uppercase hexadecimal characters"
            )
        if not _INTERFACE_RE.fullmatch(self.interface):
            raise FactoryNetworkConfigurationError("invalid AP interface name")
        if self.interface != FACTORY_INTERFACE:
            raise FactoryNetworkConfigurationError(
                f"factory AP must use {FACTORY_INTERFACE}"
            )
        if self.address != FACTORY_ADDRESS or self.prefix_length != 24:
            raise FactoryNetworkConfigurationError(
                "factory AP must use 10.42.0.1/24"
            )
        if self.dhcp_start != FACTORY_DHCP_START or self.dhcp_end != FACTORY_DHCP_END:
            raise FactoryNetworkConfigurationError(
                "factory DHCP range must be 10.42.0.20-10.42.0.100"
            )
        start = ipaddress.ip_address(self.dhcp_start)
        end = ipaddress.ip_address(self.dhcp_end)
        gateway = ipaddress.ip_address(self.address)
        if start not in self.network or end not in self.network or start > end:
            raise FactoryNetworkConfigurationError("invalid factory DHCP range")
        if start <= gateway <= end:
            raise FactoryNetworkConfigurationError("DHCP range includes AP address")
        if self.channel not in {1, 6, 11}:
            raise FactoryNetworkConfigurationError(
                "factory AP channel must be one of 1, 6, or 11"
            )
        if not re.fullmatch(r"[A-Z]{2}", self.country_code):
            raise FactoryNetworkConfigurationError("invalid regulatory country code")
        if len(self.ssid.encode("utf-8")) > 32:
            raise FactoryNetworkConfigurationError("factory SSID exceeds 32 bytes")


def derive_factory_id(machine_id: str, image_release_id: str) -> str:
    """Derive the non-identity eight-character station label from public facts."""

    normalized_machine_id = machine_id.strip().lower()
    normalized_release_id = image_release_id.strip()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized_machine_id):
        raise FactoryNetworkConfigurationError("machine-id is not initialized")
    if not normalized_release_id or len(normalized_release_id) > 128:
        raise FactoryNetworkConfigurationError("image release id is missing or too long")
    digest = hashlib.sha256(
        f"{normalized_machine_id}\n{normalized_release_id}".encode("utf-8")
    ).hexdigest()
    return digest[:8].upper()


def validate_wpa2_passphrase(passphrase: str) -> str:
    """Validate a hostapd WPA2 passphrase without normalizing its value."""

    if not 8 <= len(passphrase) <= 63:
        raise FactoryNetworkConfigurationError(
            "factory WPA2 passphrase must contain 8-63 characters"
        )
    try:
        encoded = passphrase.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FactoryNetworkConfigurationError(
            "factory WPA2 passphrase must be printable ASCII"
        ) from exc
    if any(byte < 0x20 or byte > 0x7E for byte in encoded):
        raise FactoryNetworkConfigurationError(
            "factory WPA2 passphrase contains a control character"
        )
    if passphrase != passphrase.strip():
        raise FactoryNetworkConfigurationError(
            "factory WPA2 passphrase cannot start or end with whitespace"
        )
    return passphrase


def read_setup_ap_passphrase(path: Path) -> str:
    """Read the AP secret through a no-follow, bounded file descriptor.

    The caller must never log the returned value.  Root ownership is enforced
    when this process itself runs as root; group/other permission bits are
    always rejected on POSIX.
    """

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FactoryNetworkConfigurationError(
            "factory AP secret is unavailable"
        ) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise FactoryNetworkConfigurationError(
                "factory AP secret must be a regular file"
            )
        if details.st_size <= 0 or details.st_size > 128:
            raise FactoryNetworkConfigurationError(
                "factory AP secret has an invalid size"
            )
        if os.name == "posix":
            if details.st_mode & 0o077:
                raise FactoryNetworkConfigurationError(
                    "factory AP secret must not be group/world accessible"
                )
            if os.geteuid() == 0 and details.st_uid != 0:
                raise FactoryNetworkConfigurationError(
                    "factory AP secret must be owned by root"
                )
        raw = os.read(descriptor, 129)
    finally:
        os.close(descriptor)
    if len(raw) > 128:
        raise FactoryNetworkConfigurationError("factory AP secret is too large")
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if b"\n" in raw or b"\r" in raw:
        raise FactoryNetworkConfigurationError(
            "factory AP secret must contain one line"
        )
    try:
        passphrase = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FactoryNetworkConfigurationError(
            "factory AP secret must be printable ASCII"
        ) from exc
    return validate_wpa2_passphrase(passphrase)


def render_hostapd(config: FactoryNetworkConfig, passphrase: str) -> str:
    """Render a WPA2/CCMP-only 2.4 GHz hostapd configuration."""

    config.validate()
    validate_wpa2_passphrase(passphrase)
    return "\n".join(
        (
            f"interface={config.interface}",
            "driver=nl80211",
            f"ssid={config.ssid}",
            f"country_code={config.country_code}",
            "ieee80211d=1",
            "hw_mode=g",
            f"channel={config.channel}",
            "ieee80211n=1",
            "wmm_enabled=1",
            "auth_algs=1",
            "ignore_broadcast_ssid=0",
            "wpa=2",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
            f"wpa_passphrase={passphrase}",
            "ap_isolate=1",
            "max_num_sta=16",
            "logger_syslog=-1",
            "logger_syslog_level=2",
            "logger_stdout=-1",
            "logger_stdout_level=2",
            "",
        )
    )


def render_dnsmasq(config: FactoryNetworkConfig) -> str:
    """Render DHCP plus captive local DNS with no upstream resolver."""

    config.validate()
    state_path = "/run/ecobin/factory-network/dnsmasq-state"
    lease_path = f"{state_path}/dnsmasq.leases"
    return "\n".join(
        (
            f"interface={config.interface}",
            "except-interface=lo",
            "bind-interfaces",
            f"listen-address={config.address}",
            # IPv6 is disabled on the factory interface by configure_interface().
            # Do not emit no-dhcpv6-interface: Debian Bookworm's dnsmasq 2.89
            # rejects that option and would prevent DHCP/DNS from starting.
            "no-resolv",
            "no-hosts",
            "no-poll",
            "domain-needed",
            "bogus-priv",
            "cache-size=0",
            f"address=/#/{config.address}",
            f"dhcp-range={config.dhcp_start},{config.dhcp_end},255.255.255.0,10m",
            "dhcp-option=option:router",
            f"dhcp-option=option:dns-server,{config.address}",
            "dhcp-authoritative",
            # Always broadcast replies and skip ICMP collision probes.  This
            # lets the dedicated non-root dnsmasq process serve this isolated
            # one-subnet fixture with CAP_NET_BIND_SERVICE only: it never
            # edits ARP state and never opens an ICMP raw socket.
            "dhcp-broadcast",
            "no-ping",
            "quiet-dhcp",
            "quiet-dhcp6",
            "quiet-ra",
            f"dhcp-leasefile={lease_path}",
            "",
        )
    )
