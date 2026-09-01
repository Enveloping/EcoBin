from dataclasses import FrozenInstanceError

import pytest

from device_identity import DeviceIdentity


def test_device_identity_is_immutable() -> None:
    identity = DeviceIdentity("ecobin-001")

    with pytest.raises(FrozenInstanceError):
        identity.device_name = "ecobin-002"  # type: ignore[misc]

    assert identity.device_name == "ecobin-001"


@pytest.mark.parametrize("device_name", ["", " ", "\t\r\n"])
def test_device_identity_rejects_blank_names(device_name: str) -> None:
    with pytest.raises(ValueError, match="non-blank"):
        DeviceIdentity(device_name)
