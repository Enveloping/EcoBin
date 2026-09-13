"""Compatibility checks for frozen recovery-close custody; never runs an MCU."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from hardware.tests.native_recovery_history_fixture import (
    FIXTURE,
    RecoveryHistoryFixtureError,
    load_native_recovery_history,
    read_native_recovery_history_fixture,
)


def _business_counts(path):
    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        return {table: (connection.execute(
            f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in names else 0)
            for table in ("job_permit", "physical_action_ledger",
                          "native_recovery_close", "native_recovery_close_disposition")}


@pytest.mark.parametrize("variant_name", read_native_recovery_history_fixture()["variantNames"])
def test_every_frozen_variant_loads_and_cold_opens_without_dispatch(tmp_path, variant_name):
    loaded = load_native_recovery_history(tmp_path, variant_name)
    try:
        expected = loaded.variant["expected"]
        action = loaded.store.get_physical_action({"actionUid": loaded.variant["targetActionUid"]})
        assert action["state"] == expected["state"]
        assert action["dispatchMode"] == expected["dispatchMode"]
        assert action["confirmedOutcome"] == expected["confirmedOutcome"]
        # The loader has no writer/dispatcher. Frozen UART payloads stay evidence only.
        assert not hasattr(loaded, "write") and not hasattr(loaded, "dispatcher")
    finally:
        loaded.close()


@pytest.mark.parametrize("variant_name", read_native_recovery_history_fixture()["variantNames"])
def test_repeated_load_and_reads_preserve_custody_without_business_side_effects(tmp_path, variant_name):
    first = load_native_recovery_history(tmp_path, variant_name)
    uid = first.variant["targetActionUid"]
    recovery_before = first.store.get_native_recovery_close({"actionUid": uid})
    action_before = first.store.get_physical_action({"actionUid": uid})
    first.close()
    counts_before = _business_counts(first.path)

    second = load_native_recovery_history(tmp_path, variant_name)
    try:
        for _ in range(3):
            assert second.store.get_native_recovery_close({"actionUid": uid}) == recovery_before
            assert second.store.get_physical_action({"actionUid": uid}) == action_before
        assert _business_counts(second.path) == counts_before
        assert second.store.get_job_permit({
            "permitUid": second.fixture["common"]["permit"]["permitUid"],
        })["state"] == "ACTIVE"
    finally:
        second.close()


def test_original_uart_bytes_and_identities_are_not_rewritten(tmp_path):
    loaded = load_native_recovery_history(tmp_path, "confirmed-with-output")
    try:
        common = loaded.fixture["common"]
        recovery = loaded.store.get_native_recovery_close({
            "actionUid": common["recovery"]["actionUid"],
        })
        assert recovery["sourceCommandPayloadHex"] == common["sourceAction"]["sourceCommandPayloadHex"]
        assert recovery["closeCommandPayloadHex"] == common["recovery"]["closeCommandPayloadHex"]
        raw = bytes.fromhex(common["output"]["payloadHex"])
        assert hashlib.sha256(raw).hexdigest() == common["output"]["payloadSha256"]
        assert loaded.store.get_job_permit({"permitUid": common["permit"]["permitUid"]})["state"] == "ACTIVE"
    finally:
        loaded.close()


def test_fixture_documents_unrepresentable_edge_claim_boundary():
    fixture = read_native_recovery_history_fixture()
    assert fixture["knownGap"]["variant"] == "edge-write-claim-state"
    assert "no durable Edge write-claim fact" in fixture["knownGap"]["reason"]
    assert fixture["provenance"].endswith("exported SQLite image.")
    assert "armed-unclaimed" not in fixture["variantNames"]
    assert "claimed-with-output" not in fixture["variantNames"]
    assert "armed-claim-unknown" in fixture["variantNames"]
    assert "confirmed-with-output" in fixture["variantNames"]


@pytest.mark.parametrize("corruption", ["content", "hash"])
def test_corrupt_or_badly_hashed_fixture_is_rejected(tmp_path, corruption):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if corruption == "content":
        fixture["common"]["recovery"]["portNo"] = 2
    else:
        fixture["fixtureSha256"] = "0" * 64
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(RecoveryHistoryFixtureError, match="hash mismatch"):
        load_native_recovery_history(tmp_path / "load", "prepared-unclaimed", fixture_path=bad)
