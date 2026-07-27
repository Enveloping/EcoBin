from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edge_store import EdgeStore
from onenet_wire import canonical_payload_sha256
from tools.edge_local_command import (
    LocalCommandError,
    build_sample_configuration_command,
    build_start_delivery_command,
    queue_sample_configuration,
    queue_start_delivery,
)
from tools.uart_hil_probe import _sample_configuration


EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "examples"
    / "onenet"
    / "apply-configuration.command.json"
)


def _applied_store(tmp_path: Path) -> EdgeStore:
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    command = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    part_uids = [
        f"40000000-0000-4000-8000-{index:012d}"
        for index in range(len(command["payload"]["ports"]) + 3)
    ]
    assert store.save_configuration_edge(command, part_uids) == "ACCEPTED"
    config = command["payload"]["config"]
    assert store.apply_configuration_result(
        {
            "mcuCommandUid": part_uids[-1],
            "applicationUid": command["payload"]["applicationUid"],
            "status": "APPLIED",
            "configVersion": config["version"],
            "contentSha256": config["contentSha256"],
            "mcuPayloadSha256": config["mcuPayloadSha256"],
            "faultCode": "NONE",
        }
    ) == "ACCEPTED"
    return store


def test_build_start_delivery_uses_applied_device_and_port_values(tmp_path):
    store = _applied_store(tmp_path)
    applied = store.get_latest_applied_configuration()

    command = build_start_delivery_command(
        applied,
        port_no=2,
        command_uid="50000000-0000-4000-8000-000000000001",
        session_uid="50000000-0000-4000-8000-000000000002",
        bag_uid="50000000-0000-4000-8000-000000000003",
        ttl_seconds=30,
        now=datetime(2099, 7, 27, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert command["deploymentCode"] == "Dp_demo_01"
    assert command["issuedAt"] == "2099-07-27T01:02:03.000Z"
    assert command["expiresAt"] == "2099-07-27T01:02:33.000Z"
    assert command["payload"] == {
        "bagUid": "50000000-0000-4000-8000-000000000003",
        "config": {
            "contentSha256": "a" * 64,
            "mcuPayloadSha256": "b" * 64,
            "version": 8,
        },
        "continueDeliveryWaitMs": 30000,
        "deliveryAutoCloseMs": 120000,
        "negativeWeightThresholdGrams": 500,
        "portNo": 2,
        "sessionUid": "50000000-0000-4000-8000-000000000002",
        "unitPriceTenThousandths": 4500,
    }
    assert command["payloadSha256"] == canonical_payload_sha256(command["payload"])
    store.close()


def test_queue_start_delivery_writes_normal_command_inbox_row(tmp_path):
    store = _applied_store(tmp_path)

    disposition, command = queue_start_delivery(store, port_no=1)

    assert disposition == "ACCEPTED"
    row = store.get_command(command["commandUid"])
    assert row["state"] == "PENDING"
    assert row["command_type"] == "START_DELIVERY_SESSION"
    assert row["payload"] == command
    store.close()


def test_sample_configuration_matches_uart_hil_payload():
    command = build_sample_configuration_command(
        deployment_code="Dp_demo_01",
        config_version=24,
        command_uid="51000000-0000-4000-8000-000000000001",
        application_uid="51000000-0000-4000-8000-000000000002",
        now=datetime(2099, 7, 27, 1, 2, 3, tzinfo=timezone.utc),
    )

    expected = _sample_configuration(24)["payload"]
    expected["applicationUid"] = command["payload"]["applicationUid"]
    assert command["payload"] == expected
    assert command["target"] == {
        "type": "CONFIGURATION_APPLICATION",
        "uid": "51000000-0000-4000-8000-000000000002",
    }
    assert command["payloadSha256"] == canonical_payload_sha256(
        command["payload"]
    )


def test_queue_sample_configuration_does_not_require_applied_config(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()

    disposition, command = queue_sample_configuration(
        store,
        deployment_code="Dp_demo_01",
        config_version=24,
    )

    assert disposition == "ACCEPTED"
    row = store.get_command(command["commandUid"])
    assert row["state"] == "PENDING"
    assert row["command_type"] == "APPLY_CONFIGURATION"
    store.close()


def test_dry_run_does_not_write_command(tmp_path):
    store = _applied_store(tmp_path)

    disposition, command = queue_start_delivery(
        store,
        port_no=1,
        dry_run=True,
    )

    assert disposition == "DRY_RUN"
    assert store.get_command(command["commandUid"]) is None
    store.close()


def test_queue_refuses_when_work_slot_is_busy(tmp_path):
    store = _applied_store(tmp_path)
    assert store.acquire_work_slot(
        "DELIVERY",
        "60000000-0000-4000-8000-000000000001",
        1,
        {"phase": "WAITING_PREOPEN_WEIGHT"},
    )

    with pytest.raises(LocalCommandError, match="device is busy"):
        queue_start_delivery(store, port_no=1)
    store.close()


def test_build_refuses_unknown_or_disabled_port(tmp_path):
    store = _applied_store(tmp_path)
    applied = store.get_latest_applied_configuration()

    with pytest.raises(LocalCommandError, match="port 9 is absent"):
        build_start_delivery_command(applied, port_no=9)

    applied["payload"]["ports"][0]["enabled"] = False
    with pytest.raises(LocalCommandError, match="port 1 is disabled"):
        build_start_delivery_command(applied, port_no=1)
    store.close()
