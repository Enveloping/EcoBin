"""Regressions for atomic END-versus-clean-unlock arbitration."""

from datetime import datetime, timedelta, timezone

import pytest

from command_processor import CommandProcessor
from tests.test_command_processor import (
    FakePhotoManager,
    FakeUart,
    clean_preunlock_event,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from work_manager import CleanUnlockDecisionDeferred, WorkManager


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _runtime_waiting_for_preunlock(tmp_path):
    store = make_store(tmp_path)
    mark_configuration_applied(store)
    updater, safety = make_real_job_safety(tmp_path)
    uart = FakeUart()
    work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, work)
    start = valid_service_command("start-clean-operation.service-wire.json")
    assert store.receive_command(
        start["commandUid"], start["commandType"], start
    ) == "ACCEPTED"
    assert processor.process_next() is True
    assert [call[0] for call in uart.calls] == ["START_CLEAN_OPERATION"]
    return store, updater, uart, work, processor, start


def _pending_preunlock_fact(store, start, start_action_uid):
    event = clean_preunlock_event(
        start,
        start_action_uid,
        "53000000-0000-4000-8000-0000000002a1",
    )
    frame = {
        "message_name": event["message_name"],
        "message_type": event["message_type"],
        "tx_sequence": event["source_tx_sequence"],
        "payload": event["payload"],
    }
    assert store.receive_mcu_frame(frame) == "ACCEPTED"
    pending = store.list_pending_mcu_events()
    assert len(pending) == 1
    return pending[0]


def _mark_fact_processed(store, event):
    assert store.mark_mcu_event_processed(
        event["mcu_boot_id"],
        event["mcu_event_sequence"],
        event["mcu_receive_generation"],
    )
    assert store.list_pending_mcu_events() == []


def test_expired_pending_end_is_failed_atomically_then_preunlock_can_unlock(
    tmp_path,
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    clock = {"reference": now}
    monkeypatch.setattr(
        "edge_store.local_deadline_reference",
        lambda: clock["reference"],
    )
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: clock["reference"],
    )
    store, updater, uart, _work, processor, start = (
        _runtime_waiting_for_preunlock(tmp_path)
    )
    start_action_uid = uart.calls[-1][2]
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    end["issuedAt"] = _iso(now - timedelta(seconds=10))
    end["expiresAt"] = _iso(now - timedelta(seconds=1))
    assert store.receive_command(
        end["commandUid"], end["commandType"], end
    ) == "ACCEPTED"
    event = _pending_preunlock_fact(store, start, start_action_uid)

    processor.process_mcu_event(event)
    _mark_fact_processed(store, event)

    expired_end = store.get_command(end["commandUid"])
    assert expired_end["state"] == "FAILED"
    assert expired_end["last_error"] == "COMMAND_EXPIRED"
    assert [call[0] for call in uart.calls] == [
        "START_CLEAN_OPERATION",
        "UNLOCK_CLEAN_DOOR",
    ]
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["context"]["phase"] == "WAITING_LOCK_OUTPUT"
    assert "unlock_mcu_command_uid" in slot["context"]
    action_kinds = [
        row["action_kind"]
        for row in updater._connection.execute(
            "SELECT action_kind FROM physical_action_ledger ORDER BY rowid"
        ).fetchall()
    ]
    assert action_kinds == [
        "START_CLEAN_OPERATION",
        "UNLOCK_CLEAN_DOOR",
    ]
    updater.close()
    store.close()


@pytest.mark.parametrize(
    "pending_end_outcome",
    ["COMPLETED", "FAILED_AFTER_CLOCK_RECOVERY"],
)
def test_pending_end_defers_unlock_and_same_fact_replays_after_terminal_end(
    tmp_path,
    monkeypatch,
    pending_end_outcome,
):
    now = datetime.now(timezone.utc)
    clock = {"reference": now}
    monkeypatch.setattr(
        "edge_store.local_deadline_reference",
        lambda: clock["reference"],
    )
    monkeypatch.setattr(
        "onenet_wire.local_deadline_reference",
        lambda: clock["reference"],
    )
    monkeypatch.setattr(
        "work_manager.local_deadline_reference",
        lambda: clock["reference"],
    )
    store, updater, uart, work, processor, start = (
        _runtime_waiting_for_preunlock(tmp_path)
    )
    start_action_uid = uart.calls[-1][2]
    end = valid_service_command("end-clean-before-unlock.service-wire.json")
    end["issuedAt"] = _iso(now)
    end["expiresAt"] = _iso(now + timedelta(minutes=5))
    assert store.receive_command(
        end["commandUid"], end["commandType"], end
    ) == "ACCEPTED"
    event = _pending_preunlock_fact(store, start, start_action_uid)
    claim_results = []
    original_claim = store.claim_clean_unlock_dispatch

    def recording_claim(**kwargs):
        result = original_claim(**kwargs)
        claim_results.append(result)
        return result

    monkeypatch.setattr(store, "claim_clean_unlock_dispatch", recording_claim)
    if pending_end_outcome == "FAILED_AFTER_CLOCK_RECOVERY":
        clock["reference"] = None

    uart_calls_before_fact = list(uart.calls)
    with pytest.raises(CleanUnlockDecisionDeferred):
        processor.process_mcu_event(event)

    assert claim_results == ["DEFERRED_BY_PENDING_END"]
    assert uart.calls == uart_calls_before_fact
    pending_after_defer = store.list_pending_mcu_events()
    assert len(pending_after_defer) == 1
    assert pending_after_defer[0]["mcu_boot_id"] == event["mcu_boot_id"]
    assert pending_after_defer[0]["mcu_event_sequence"] == (
        event["mcu_event_sequence"]
    )
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["context"]["phase"] == "PREUNLOCK_MEASURED"
    assert "unlock_mcu_command_uid" not in slot["context"]
    assert set(slot["context"]["job_safety"]["actions"]) == {
        "CLEAN:START:0"
    }

    if pending_end_outcome == "COMPLETED":
        clock["reference"] = now
        assert processor.process_next() is True
        terminal_end = store.get_command(end["commandUid"])
        assert terminal_end["state"] == "COMPLETED"
        assert store.get_work_slot() is None
        uart_calls_after_end = list(uart.calls)
        assert [call[0] for call in uart_calls_after_end] == [
            "START_CLEAN_OPERATION",
            "END_CLEAN_BEFORE_UNLOCK",
        ]

        processor.process_mcu_event(event)
        assert uart.calls == uart_calls_after_end
    else:
        # Once trusted UTC returns after this END's deadline, normal command
        # processing can terminally reject it. Replaying the exact retained
        # MCU fact must then claim the unlock instead of remaining wedged.
        clock["reference"] = now + timedelta(minutes=5, seconds=1)
        assert processor.process_next() is True
        terminal_end = store.get_command(end["commandUid"])
        assert terminal_end["state"] == "FAILED"
        assert terminal_end["last_error"] == "COMMAND_EXPIRED"
        assert uart.calls == uart_calls_before_fact

        processor.process_mcu_event(event)
        assert [call[0] for call in uart.calls] == [
            "START_CLEAN_OPERATION",
            "UNLOCK_CLEAN_DOOR",
        ]
        replayed_slot = store.get_work_slot()
        assert replayed_slot is not None
        assert replayed_slot["context"]["phase"] == "WAITING_LOCK_OUTPUT"

    _mark_fact_processed(store, event)
    updater.close()
    store.close()
