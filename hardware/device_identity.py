"""Stable device identity exposed to edge business components."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Immutable identity of the physical device running this process.

    The value is deliberately separate from cloud credentials and transport
    objects.  Business components can therefore address their own device
    without gaining access to a device key or to transport implementation
    details.
    """

    device_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.device_name, str) or not self.device_name.strip():
            raise ValueError("device_name must be a non-blank string")


__all__ = ["DeviceIdentity"]
