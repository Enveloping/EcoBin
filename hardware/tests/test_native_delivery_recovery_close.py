"""Frozen recovery-close rows remain readable but can never regain send authority."""
from pathlib import Path

import pytest

from hardware.tests.native_recovery_runtime_fixture import (
    RecoverySerial,
    assert_recovery_only,
    candidate_loop,
    cold_reopen_runtime_history,
    load_native_recovery_runtime_history,
    poll_candidate,
)
import uart2_protocol as uart


@pytest.mark.parametrize(
    "variant",
    ["prepared-unclaimed", "armed-unclaimed", "claimed-unknown", "claimed-with-output"],
)
def test_frozen_recovery_close_history_is_decodable_without_dispatch(tmp_path, variant):
    case = load_native_recovery_runtime_history(tmp_path, variant)
    try:
        uid = case.variant["targetActionUid"]
        command = case.store.get_native_command(uid)
        values = uart.decode_payload(command["message_name"], command["payload"])
        assert command["message_name"] == "SAFE_CLOSE"
        assert values["mcuCommandUid"] == uid
        assert case.binding["action"].action_uid == uid
        assert case.store.get_native_delivery_issue(case.permit.work_uid) == case.issue
        assert case.issue["settlementAllowed"] is False

        serial = RecoverySerial(mcu_boot_id=None)
        status = candidate_loop(case, serial).poll()
        assert status["admissionAllowed"] is False
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_uart_contract_keeps_old_layouts_only_in_non_business_groups():
    lifecycle = uart.REGISTRY["messageLifecyclePolicy"]
    deprecated = set(lifecycle["deprecatedRejectedMessages"])
    frozen = set(lifecycle["frozenHistoryMessages"])
    current = set(lifecycle["currentBusinessMessages"])

    assert {"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR", "SAFE_CLOSE"} <= deprecated
    assert "SAFE_CLOSE_RESULT" in frozen
    assert not ({"AUTHORIZE_DELIVERY_FIRST_OPEN", "UNLOCK_CLEAN_DOOR", "SAFE_CLOSE"} & current)
    assert {"START_DELIVERY_SESSION", "START_CLEAN_OPERATION"} <= current


def test_runtime_retires_prepared_history_without_creating_a_successor(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "prepared-unclaimed")
    try:
        original_ids = {row["action"].action_uid
                        for row in case.store.snapshot_native_recovery_close_startup()["bindings"]}
        serial = RecoverySerial()
        status = poll_candidate(candidate_loop(case, serial), serial)
        after = case.store.snapshot_native_recovery_close_startup()
        assert {row["action"].action_uid for row in after["bindings"]} == original_ids
        assert case.store.get_native_recovery_close_retirement(next(iter(original_ids)))["state"] == "RETIRED"
        assert status["state"] == "NEW_CLOSE_REQUIRED"
        assert_recovery_only(status, serial)
    finally:
        case.close()


def test_cold_reopen_preserves_history_and_never_replays_it(tmp_path):
    case = load_native_recovery_runtime_history(tmp_path, "claimed-unknown")
    try:
        uid = case.variant["targetActionUid"]
        before = case.store.get_native_command(uid)
        cold_reopen_runtime_history(case)
        assert case.store.get_native_command(uid) == before
        serial = RecoverySerial(mcu_boot_id=None)
        status = poll_candidate(candidate_loop(case, serial), serial, count=3)
        assert status["state"] == "WAITING_BOOT"
        assert_recovery_only(status, serial)
    finally:
        case.close()


@pytest.mark.parametrize("kind", ["business", "runtime"])
def test_release_inventory_contains_readers_but_no_legacy_dispatch_entry(tmp_path, kind):
    from hardware.tests.test_native_release_custody import stage_app
    from install.runtime_payload_manifest import BUSINESS_APP_FILES, RUNTIME_APP_FILES

    files = BUSINESS_APP_FILES if kind == "business" else RUNTIME_APP_FILES
    app = stage_app(tmp_path, files)
    assert (Path(app) / "edge_store.py").is_file()
    assert (Path(app) / "native_delivery_recovery_close.py").is_file()
    main = (Path(app) / "main.py").read_text(encoding="utf-8")
    assert "NativeDeliveryRecoveryClose(" not in main
