from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from first_boot.model import FactoryTestStatus, FirstBootFacts, FirstBootStage
from first_boot.cellular_status import CellularStatus, cellular_check_states
from first_boot.status_projection import (
    AccessPointAuthorizationProjector,
    PortalStatusProjector,
    validate_ap_authorization_projection,
    validate_public_projection,
)


def test_projection_is_exact_public_field_whitelist(tmp_path: Path) -> None:
    path = tmp_path / "run" / "status.json"
    owned: list[Path] = []
    projector = PortalStatusProjector(
        path,
        owner=owned.append,
        cellular_status_reader=lambda: CellularStatus(
            result_code="CELLULAR_DNS_UNAVAILABLE",
            consecutive_failure_count=3,
            next_retry_at_monotonic_ms=115_000,
        ),
        monotonic=lambda: 100.0,
    )
    facts = FirstBootFacts(
        factory_test_status=FactoryTestStatus.RUNNING,
        time_trusted=True,
    )

    projector.publish(
        FirstBootStage.FACTORY_TEST_RUNNING,
        facts,
        error_code="NONE",
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {
        "stage": "FACTORY_TEST_RUNNING",
        "lastErrorCode": "NONE",
        "timeTrusted": True,
        "factoryTestStatus": "RUNNING",
        "cellular": {
            "resultCode": "CELLULAR_DNS_UNAVAILABLE",
            "consecutiveFailureCount": 3,
            "retryScheduled": True,
            "retryInSeconds": 15,
            "checks": cellular_check_states("CELLULAR_DNS_UNAVAILABLE"),
        },
    }
    assert validate_public_projection(document) == document
    assert owned
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.parametrize(
    "extra",
    [
        {"deviceKey": "secret"},
        {"enrollmentKey": "K1"},
        {"state": {"raw": "private"}},
    ],
)
def test_projection_validator_rejects_any_extra_field(extra: dict[str, object]) -> None:
    document: dict[str, object] = {
        "stage": "FACTORY_TEST_REQUIRED",
        "lastErrorCode": "NONE",
        "timeTrusted": False,
        "factoryTestStatus": "NOT_RUN",
        "cellular": {
            "resultCode": "STATUS_UNAVAILABLE",
            "consecutiveFailureCount": 0,
            "retryScheduled": False,
            "retryInSeconds": None,
            "checks": cellular_check_states("STATUS_UNAVAILABLE"),
        },
        **extra,
    }
    with pytest.raises(ValueError, match="fields"):
        validate_public_projection(document)


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        (
            FirstBootFacts(),
            {"schemaVersion": 2, "allowed": True, "statusCode": "UNSEALED"},
        ),
        (
            FirstBootFacts(sealed_exists=True, sealed_valid=True),
            {"schemaVersion": 2, "allowed": False, "statusCode": "SEALED"},
        ),
        (
            FirstBootFacts(sealed_exists=True, sealed_valid=False),
            {
                "schemaVersion": 2,
                "allowed": False,
                "statusCode": "SEALED_FACT_INVALID",
            },
        ),
    ),
)
def test_ap_projection_is_a_three_field_one_way_gate(
    tmp_path: Path,
    facts: FirstBootFacts,
    expected: dict[str, object],
) -> None:
    path = tmp_path / "factory-network" / "ap-allowed.json"
    projector = AccessPointAuthorizationProjector(
        path, owner=lambda _path: None
    )

    projector.publish(facts)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == expected
    assert validate_ap_authorization_projection(document) == document
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_ap_projection_keeps_the_boot_scoped_seal_response_window(
    tmp_path: Path,
) -> None:
    path = tmp_path / "factory-network" / "ap-allowed.json"
    projector = AccessPointAuthorizationProjector(
        path,
        owner=lambda _path: None,
    )

    projector.publish(
        FirstBootFacts(sealed_exists=True, sealed_valid=False),
        allow_sealed_response=True,
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 2,
        "allowed": True,
        "statusCode": "SEALED_RESPONSE_PENDING",
    }
