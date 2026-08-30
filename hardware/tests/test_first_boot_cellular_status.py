from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from first_boot.cellular_status import CellularStatusStore


def test_cellular_status_round_trips_an_exact_stable_code(tmp_path: Path) -> None:
    path = tmp_path / "cellular-uplink" / "status.json"
    store = CellularStatusStore(path)

    store.publish("CHRONY_ONLINE_FAILED")

    assert store.read() == "CHRONY_ONLINE_FAILED"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "resultCode": "CHRONY_ONLINE_FAILED",
    }
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_cellular_status_rejects_a_boolean_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "cellular-uplink" / "status.json"
    store = CellularStatusStore(path)
    store.publish("CHRONY_ONLINE_FAILED")
    path.write_text(
        json.dumps(
            {
                "schemaVersion": True,
                "resultCode": "CHRONY_ONLINE_FAILED",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fields are invalid"):
        store.read()
