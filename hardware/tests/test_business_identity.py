from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from business_identity import (
    load_business_identity,
    validate_business_identity_document,
)


def valid_identity() -> dict:
    return {
        "schemaVersion": 1,
        "assetUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "deviceName": "ECM0-0123456789ABCDEFGHJKMNPQ",
        "modelCode": "EC-M0",
        "expectedPortCount": 1,
        "deviceEntryUrl": "https://www.jinshoubao.com/d/asset",
    }


def test_business_identity_contains_no_transport_secret(tmp_path: Path) -> None:
    path = tmp_path / "device-identity.json"
    path.write_text(json.dumps(valid_identity()), encoding="utf-8")
    path.chmod(0o600)

    identity = load_business_identity(path)

    assert identity.device_name == "ECM0-0123456789ABCDEFGHJKMNPQ"
    assert "deviceKey" not in path.read_text(encoding="utf-8")
    assert "productId" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(unexpected=True),
        lambda value: value.update(schemaVersion=True),
        lambda value: value.update(assetUid="not-a-uuid"),
        lambda value: value.update(deviceName=""),
        lambda value: value.update(expectedPortCount=True),
        lambda value: value.update(deviceEntryUrl="http://unsafe.example"),
    ],
)
def test_business_identity_rejects_ambiguous_documents(mutation) -> None:
    document = valid_identity()
    mutation(document)

    with pytest.raises(ValueError):
        validate_business_identity_document(document)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode boundary")
def test_business_identity_rejects_group_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "device-identity.json"
    path.write_text(json.dumps(valid_identity()), encoding="utf-8")
    path.chmod(0o640)

    with pytest.raises(ValueError, match="0600"):
        load_business_identity(path)
