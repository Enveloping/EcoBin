from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


class CellularConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_FIELDS = {
    "ECOBIN_CELLULAR_SCHEMA_VERSION",
    "ECOBIN_CELLULAR_HIL_APPROVED",
    "ECOBIN_CELLULAR_CONNECTION_ID",
    "ECOBIN_CELLULAR_USB_DRIVER",
    "ECOBIN_CELLULAR_USB_PROFILE",
    "ECOBIN_CELLULAR_AUTO_APN",
    "ECOBIN_CELLULAR_PROBE_IPV4",
    "ECOBIN_CELLULAR_HTTPS_PROBE_URL",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SUPPORTED_DRIVERS = {"rndis_host"}


@dataclass(frozen=True)
class CellularBatchConfig:
    connection_id: str
    usb_driver: str
    usb_profile: str
    probe_ipv4: str
    https_probe_url: str
    https_probe_host: str


def load_cellular_config(
    path: Path = Path("/etc/ecobin/cellular.env"),
) -> CellularBatchConfig:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise CellularConfigurationError("CELLULAR_CONFIG_MISSING") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CellularConfigurationError("CELLULAR_CONFIG_NOT_REGULAR")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
        raise CellularConfigurationError("CELLULAR_CONFIG_PERMISSIONS")
    if not 1 <= info.st_size <= 16 * 1024:
        raise CellularConfigurationError("CELLULAR_CONFIG_SIZE")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise CellularConfigurationError("CELLULAR_CONFIG_ENCODING") from error
    return parse_cellular_config(text)


def parse_cellular_config(text: str) -> CellularBatchConfig:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise CellularConfigurationError("CELLULAR_CONFIG_SYNTAX")
        key, value = line.split("=", 1)
        if key not in _FIELDS or key in values or key != key.strip():
            raise CellularConfigurationError("CELLULAR_CONFIG_FIELDS")
        if any(character in value for character in "\r\n\x00"):
            raise CellularConfigurationError("CELLULAR_CONFIG_VALUE")
        values[key] = value.strip()
    if set(values) != _FIELDS:
        raise CellularConfigurationError("CELLULAR_CONFIG_FIELDS")
    if values["ECOBIN_CELLULAR_SCHEMA_VERSION"] != "2":
        raise CellularConfigurationError("CELLULAR_CONFIG_SCHEMA")
    if values["ECOBIN_CELLULAR_HIL_APPROVED"] != "true":
        raise CellularConfigurationError("CELLULAR_HIL_LOCKED")
    if values["ECOBIN_CELLULAR_AUTO_APN"] != "true":
        raise CellularConfigurationError("CELLULAR_APN_MODE_UNSUPPORTED")

    connection_id = values["ECOBIN_CELLULAR_CONNECTION_ID"]
    if not _SAFE_NAME.fullmatch(connection_id):
        raise CellularConfigurationError("CELLULAR_CONNECTION_ID_INVALID")
    driver = values["ECOBIN_CELLULAR_USB_DRIVER"]
    if driver not in _SUPPORTED_DRIVERS:
        raise CellularConfigurationError("CELLULAR_USB_DRIVER_UNKNOWN")
    profile = values["ECOBIN_CELLULAR_USB_PROFILE"]
    if profile != "RNDIS":
        raise CellularConfigurationError("CELLULAR_USB_PROFILE_UNKNOWN")
    try:
        probe_ip = str(ipaddress.IPv4Address(values["ECOBIN_CELLULAR_PROBE_IPV4"]))
    except ipaddress.AddressValueError as error:
        raise CellularConfigurationError("CELLULAR_PROBE_IPV4_INVALID") from error
    if ipaddress.IPv4Address(probe_ip).is_unspecified or ipaddress.IPv4Address(probe_ip).is_loopback:
        raise CellularConfigurationError("CELLULAR_PROBE_IPV4_INVALID")

    probe_url = values["ECOBIN_CELLULAR_HTTPS_PROBE_URL"]
    parsed = urlsplit(probe_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(probe_url) > 512
    ):
        raise CellularConfigurationError("CELLULAR_HTTPS_PROBE_INVALID")
    try:
        probe_port = parsed.port
    except ValueError as error:
        raise CellularConfigurationError("CELLULAR_HTTPS_PROBE_INVALID") from error
    if probe_port not in (None, 443):
        raise CellularConfigurationError("CELLULAR_HTTPS_PROBE_INVALID")

    return CellularBatchConfig(
        connection_id=connection_id,
        usb_driver=driver,
        usb_profile=profile,
        probe_ipv4=probe_ip,
        https_probe_url=probe_url,
        https_probe_host=parsed.hostname,
    )
