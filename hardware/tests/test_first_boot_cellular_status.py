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
        "schemaVersion": 2,
        "resultCode": "CHRONY_ONLINE_FAILED",
        "consecutiveFailureCount": 0,
        "nextRetryAtMonotonicMs": None,
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


def test_cellular_status_exposes_bounded_retry_progress(tmp_path: Path) -> None:
    path = tmp_path / "cellular-uplink" / "status.json"
    store = CellularStatusStore(path)

    store.publish(
        "CELLULAR_DNS_UNAVAILABLE",
        consecutive_failure_count=3,
        next_retry_at_monotonic_ms=115_000,
    )

    status = store.read_status()
    assert status is not None
    assert status.result_code == "CELLULAR_DNS_UNAVAILABLE"
    assert status.consecutive_failure_count == 3
    assert status.next_retry_at_monotonic_ms == 115_000


def test_cellular_status_keeps_schema_v1_read_compatibility(tmp_path: Path) -> None:
    path = tmp_path / "cellular-uplink" / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resultCode": "CELLULAR_DNS_UNAVAILABLE",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    store = CellularStatusStore(path)

    status = store.read_status()

    assert status is not None
    assert status.result_code == "CELLULAR_DNS_UNAVAILABLE"
    assert status.consecutive_failure_count == 0
    assert status.next_retry_at_monotonic_ms is None
