from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from first_boot.cellular_status import (
    CELLULAR_CHECK_IDS,
    CellularStatusStore,
    cellular_check_states,
)


def test_cellular_status_round_trips_an_exact_stable_code(tmp_path: Path) -> None:
    path = tmp_path / "cellular-uplink" / "status.json"
    store = CellularStatusStore(path)

    store.publish("CHRONY_ONLINE_FAILED")

    assert store.read() == "CHRONY_ONLINE_FAILED"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 3,
        "resultCode": "CHRONY_ONLINE_FAILED",
        "consecutiveFailureCount": 0,
        "nextRetryAtMonotonicMs": None,
        "checks": cellular_check_states("CHRONY_ONLINE_FAILED"),
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
    assert status.checks == cellular_check_states("CELLULAR_DNS_UNAVAILABLE")


def test_cellular_status_explains_the_registration_window_before_dns() -> None:
    checks = cellular_check_states("CELLULAR_NETWORK_REGISTRATION_PENDING")

    assert tuple(checks) == CELLULAR_CHECK_IDS
    assert checks == {
        "MODEM_INTERFACE": "PASSED",
        "MODEM_CONTROL": "PASSED",
        "SIM_READY": "PASSED",
        "NETWORK_REGISTERED": "WAITING",
        "PACKET_ATTACHED": "UNKNOWN",
        "IP_ADDRESS": "UNKNOWN",
        "DEFAULT_ROUTE": "UNKNOWN",
        "DNS_RESOLUTION": "UNKNOWN",
        "BACKEND_HTTPS": "UNKNOWN",
    }


def test_cellular_status_marks_all_prerequisites_passed_at_dns_failure() -> None:
    checks = cellular_check_states("CELLULAR_DNS_UNAVAILABLE")

    assert checks["NETWORK_REGISTERED"] == "PASSED"
    assert checks["PACKET_ATTACHED"] == "PASSED"
    assert checks["DEFAULT_ROUTE"] == "PASSED"
    assert checks["DNS_RESOLUTION"] == "WAITING"
    assert checks["BACKEND_HTTPS"] == "UNKNOWN"


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
