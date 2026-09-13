"""Pi process restart must recover the original configuration, never new IDs/writes."""
import hashlib
import json
from datetime import datetime, timezone
import uuid

import pytest

from hardware.tests.test_native_business_runtime import (
    library, runtime, completed_first_work, configuration_command, open_owner, poll_until,
)
from job_safety import JobSafetyError
from mcu_configuration import NativeMcuConfiguration
from onenet_wire import canonical_payload_sha256
from uart_link import compute_mcu_payload_sha256
import uart2_protocol as uart


def receive(case, command):
    assert case.store.receive_command(command["commandUid"], command["commandType"], command) == "ACCEPTED"
    assert case.store.claim_next_command()["command_uid"] == command["commandUid"]


def restart(case, owner):
    owner.close()
    case.store.close()
    case.store.initialize()
    return open_owner(case, case.clock)


def newer(command):
    candidate = NativeMcuConfiguration.from_cloud_payload(command["payload"])
    offset = len(b"ECOBIN:UART:MCU-CONFIG:v2\0")
    command["payload"]["config"]["version"] += 1
    preimage = (candidate.digest_preimage[:offset]
                + command["payload"]["config"]["version"].to_bytes(8, "big")
                + candidate.digest_preimage[offset + 8:])
    command["payload"]["config"]["mcuPayloadSha256"] = hashlib.sha256(preimage).hexdigest()
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def test_restart_finds_saved_configuration_when_crash_preceded_pointer(runtime, tmp_path, monkeypatch):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        parts = [str(uuid.uuid4()) for _ in range(NativeMcuConfiguration.from_cloud_payload(command["payload"]).part_count)]
        assert case.store.save_configuration_edge(command, parts) == "ACCEPTED"
        assert not case.store.get_state("native_configuration_application")
        monkeypatch.setattr(case.store, "recover_interrupted_commands",
                            lambda: pytest.fail("native startup must not invoke legacy physical recovery"))
        owner = restart(case, owner)
        try:
            assert case.store.get_state("native_configuration_application") == command["payload"]["applicationUid"]
            poll_until(owner, case.clock, lambda: case.store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED")
            assert case.store.get_configuration(command["payload"]["applicationUid"])["part_command_uids"] == parts
            assert case.store.get_command(command["commandUid"])["state"] == "COMPLETED"
        finally:
            owner.close()


def test_new_configuration_cannot_replace_an_unfinished_original(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        first = configuration_command()
        receive(case, first)
        owner.apply_configuration_command(first)
        second = newer(configuration_command())
        receive(case, second)
        with pytest.raises(JobSafetyError, match="configuration"):
            owner.apply_configuration_command(second)
        assert case.store.get_state("native_configuration_application") == first["payload"]["applicationUid"]
        assert case.store.get_configuration(second["payload"]["applicationUid"]) is None


def test_claimed_part_after_restart_is_queried_without_resending(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        parts = case.store.get_configuration(command["payload"]["applicationUid"])["part_command_uids"]
        poll_until(owner, case.clock, lambda: (case.store.get_native_command(parts[0]) or {}).get("write_claimed", False))
        before = case.store.get_native_command(parts[0])
        assert before["decision_outcome"] is None
        case.wire.take()  # MCU accepted; simulate the reply being lost before Pi restart.
        owner = restart(case, owner)
        try:
            poll_until(owner, case.clock, lambda: case.store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED")
            after = case.store.get_native_command(parts[0])
            for key in ("payload", "mcu_boot_id", "command_sequence", "command_uid"):
                assert after[key] == before[key]
            assert after["decision_outcome"] == "ACCEPTED"
            assert case.store.get_configuration(command["payload"]["applicationUid"])["part_command_uids"] == parts
            assert sum(frame["messageName"] == "CONFIG_BEGIN" and frame["payload"] == before["payload"]
                       for frame in case.wire.sent) == 1
            assert any(frame["messageName"] == "QUERY_COMMAND"
                       and uart.decode_payload("QUERY_COMMAND", frame["payload"])["mcuCommandUid"] == parts[0]
                       for frame in case.wire.sent)
        finally:
            owner.close()


def test_completed_original_configuration_is_not_reopened_by_duplicate(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        poll_until(owner, case.clock, lambda: case.store.get_command(command["commandUid"])["state"] == "COMPLETED")
        before = case.store.get_configuration(command["payload"]["applicationUid"])
        sent = len(case.wire.sent)
        owner.apply_configuration_command(command)
        assert case.store.get_command(command["commandUid"])["state"] == "COMPLETED"
        assert not case.store.get_state("native_configuration_application")
        assert case.store.get_configuration(command["payload"]["applicationUid"]) == before
        assert len(case.wire.sent) == sent


def test_same_application_cannot_be_rebound_to_another_cloud_command(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        before = case.store.get_configuration(command["payload"]["applicationUid"])
        second = command | {"commandUid": str(uuid.uuid4())}
        receive(case, second)
        with pytest.raises(ValueError, match="original cloud authority"):
            owner.apply_configuration_command(second)
        assert case.store.get_configuration(command["payload"]["applicationUid"]) == before
        assert case.store.get_state("native_configuration_application") == before["application_uid"]


def test_restart_refuses_ambiguous_old_multiple_pending_configurations(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        first = configuration_command()
        receive(case, first)
        owner.apply_configuration_command(first)
        second = newer(configuration_command())
        receive(case, second)
        parts = [str(uuid.uuid4()) for _ in range(NativeMcuConfiguration.from_cloud_payload(second["payload"]).part_count)]
        assert case.store.save_configuration_edge(second, parts) == "ACCEPTED"
        owner.close()
        case.store.close()
        case.store.initialize()
        with pytest.raises(JobSafetyError, match="multiple original native configurations"):
            open_owner(case, case.clock)
        assert case.store.get_state("native_configuration_application") == first["payload"]["applicationUid"]
        assert all(row["state"] == "EDGE_SAVED" for row in case.store.list_pending_configurations())


def test_restart_rejects_configuration_without_original_cloud_authority(runtime, tmp_path):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        # Simulate local corruption, not a backend cancellation or a new command.
        with case.store.transaction():
            case.store._conn.execute("DELETE FROM command_inbox WHERE command_uid=?", (command["commandUid"],))
        with pytest.raises(ValueError, match="nonterminal original command"):
            owner._restore_configuration()
        assert case.store.get_configuration(command["payload"]["applicationUid"])["state"] == "EDGE_SAVED"


def test_expired_configuration_may_query_claimed_part_but_not_send_another(runtime, tmp_path, monkeypatch):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        parts = case.store.get_configuration(command["payload"]["applicationUid"])["part_command_uids"]
        poll_until(owner, case.clock, lambda: (case.store.get_native_command(parts[0]) or {}).get("write_claimed", False))
        first = case.store.get_native_command(parts[0])
        case.wire.take()
        monkeypatch.setattr("onenet_wire.local_deadline_reference", lambda: datetime(2031, 1, 1, tzinfo=timezone.utc))
        owner = restart(case, owner)  # Restoring custody itself does not refresh execution authority.
        try:
            with pytest.raises(ValueError, match="command expired"):
                poll_until(owner, case.clock, lambda: case.store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED")
            assert case.store.get_native_command(parts[0])["decision_outcome"] == "ACCEPTED"
            second = case.store.get_native_command(parts[1])
            assert second is not None and not second["write_claimed"]
            assert not any(frame["messageName"] == "CONFIG_DEVICE_BLOCK" and frame["payload"] == second["payload"]
                           for frame in case.wire.sent)
            assert sum(frame["messageName"] == "CONFIG_BEGIN" and frame["payload"] == first["payload"]
                       for frame in case.wire.sent) == 1
        finally:
            owner.close()


@pytest.mark.parametrize("points_to_legacy", [False, True])
def test_legacy_pending_configuration_is_never_silently_upgraded(runtime, tmp_path, points_to_legacy):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        command["payload"].pop("mcuConfigurationProfile")
        command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(command["payload"])
        command["payloadSha256"] = canonical_payload_sha256(command["payload"])
        receive(case, command)
        parts = [str(uuid.uuid4()) for _ in range(5)]
        assert case.store.save_configuration_edge(command, parts) == "ACCEPTED"
        if points_to_legacy:
            case.store.set_state("native_configuration_application", command["payload"]["applicationUid"])
        original = case.store.get_configuration(command["payload"]["applicationUid"])
        sent = len(case.wire.sent)
        if points_to_legacy:
            with pytest.raises(ValueError, match="pointer differs"):
                restart(case, owner)
        else:
            owner = restart(case, owner)
            try:
                assert not case.store.get_state("native_configuration_application")
            finally:
                owner.close()
        assert case.store.get_configuration(command["payload"]["applicationUid"]) == original
        assert case.store.get_command(command["commandUid"])["state"] == "PROCESSING"
        assert len(case.wire.sent) == sent


@pytest.mark.parametrize("profile", [None, "UART_V2_UNKNOWN"])
def test_explicit_invalid_profile_cannot_be_treated_as_absent_legacy(runtime, tmp_path, profile):
    with completed_first_work(runtime, tmp_path) as (case, owner):
        command = configuration_command()
        receive(case, command)
        owner.apply_configuration_command(command)
        payload = dict(command["payload"], mcuConfigurationProfile=profile)
        with case.store.transaction():
            case.store._conn.execute("UPDATE configuration_state SET payload_json=? WHERE application_uid=?",
                (json.dumps(payload), payload["applicationUid"]))
        with pytest.raises(ValueError, match="unsupported explicit profile"):
            owner._restore_configuration()
