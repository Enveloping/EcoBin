"""Idempotent terminal MCU-fact replay for the standard UART-v1 flow."""

from command_processor import CommandProcessor
from tests.test_command_processor import (
    FakePhotoManager,
    FakeUart,
    make_real_job_safety,
    make_store,
    mark_configuration_applied,
    valid_service_command,
)
from work_manager import WorkManager


def _runtime(tmp_path):
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
    return store, updater, uart, work, processor


def _event(message_name, message_type, sequence, payload):
    return {
        "message_name": message_name,
        "message_type": message_type,
        "source_tx_sequence": sequence,
        "payload": payload,
    }


def _persist_terminal_fact(store, event):
    frame = {
        "message_name": event["message_name"],
        "message_type": event["message_type"],
        "tx_sequence": event["source_tx_sequence"],
        "payload": event["payload"],
    }
    assert store.receive_mcu_frame(frame) == "ACCEPTED"
    pending = store.list_pending_mcu_events()
    assert len(pending) == 1
    terminal = pending[0]
    assert terminal["mcu_receive_generation"] > 0
    assert terminal["mcu_boot_id"] == event["payload"]["mcuBootId"]
    assert terminal["mcu_event_sequence"] == event["payload"][
        "mcuEventSequence"
    ]
    return terminal


def _completion_rows(store, event_type):
    return store._conn.execute(
        """SELECT event_uid, payload_json FROM event_outbox
           WHERE event_type=? ORDER BY edge_event_sequence""",
        (event_type,),
    ).fetchall()


def _assert_terminal_inbox_processed(store, terminal):
    row = store._conn.execute(
        """SELECT state, processed_at, last_error
           FROM mcu_event_inbox
           WHERE mcu_receive_generation=?
             AND mcu_boot_id=? AND mcu_event_sequence=?""",
        (
            terminal["mcu_receive_generation"],
            terminal["mcu_boot_id"],
            terminal["mcu_event_sequence"],
        ),
    ).fetchone()
    assert row["state"] == "PROCESSED"
    assert row["processed_at"] is not None
    assert row["last_error"] is None
    assert store.list_pending_mcu_events() == []


def test_delivery_selection_replay_keeps_one_completion_event_and_uid(
    tmp_path,
):
    store, updater, uart, work, processor = _runtime(tmp_path)
    command = valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert processor.process_next() is True
    session_uid = command["payload"]["sessionUid"]
    port_no = command["payload"]["portNo"]
    start_uid = uart.calls[-1][2]
    preopen_uid = "52000000-0000-4000-8000-0000000003a1"
    processor.process_mcu_event(
        _event(
            "WORK_PREOPEN_WEIGHT_READY",
            48,
            1,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "measurementUid": preopen_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        )
    )
    authorize_uid = uart.calls[-1][2]
    processor.process_mcu_event(
        _event(
            "DELIVERY_DOOR_COMMAND_RESULT",
            49,
            2,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 2,
                "uptimeMs": 2_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "command": "OPEN",
                "outputStatus": "COMMAND_DISPATCHED",
                "physicalDoorStateBasis": "NOT_OBSERVABLE",
                "faultCode": "NONE",
            },
        )
    )
    processor.process_mcu_event(
        _event(
            "DELIVERY_DOOR_COMMAND_RESULT",
            49,
            3,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 3,
                "uptimeMs": 3_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "command": "CLOSE",
                "outputStatus": "COMMAND_DISPATCHED",
                "physicalDoorStateBasis": "NOT_OBSERVABLE",
                "faultCode": "NONE",
            },
        )
    )
    postclose_uid = "52000000-0000-4000-8000-0000000003a2"
    processor.process_mcu_event(
        _event(
            "WORK_POSTCLOSE_WEIGHT_READY",
            50,
            4,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 4,
                "uptimeMs": 4_000,
                "mcuCommandUid": authorize_uid,
                "sessionUid": session_uid,
                "portNo": port_no,
                "roundIndex": 1,
                "measurementUid": postclose_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_500,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        )
    )

    generation = store.begin_mcu_receive_generation(42)
    assert generation > 0
    selection = _event(
        "DELIVERY_SELECTION",
        51,
        5,
        {
            "mcuBootId": 42,
            "mcuEventSequence": 5,
            "uptimeMs": 5_000,
            "sessionUid": session_uid,
            "portNo": port_no,
            "roundIndex": 1,
            "postCloseMeasurementUid": postclose_uid,
            "selection": "END",
        },
    )
    terminal = _persist_terminal_fact(store, selection)

    processor.process_mcu_event(terminal)
    rows_after_first = _completion_rows(store, "DELIVERY_COMPLETE")
    assert len(rows_after_first) == 1
    first_event_uid = rows_after_first[0]["event_uid"]
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETING"
    assert slot["context"]["phase"] == "COMPLETING"

    # Simulate a crash after the atomic completion/inbox transaction while a
    # stale in-memory copy of the same fact is still available to be replayed.
    safety = work._job_safety
    store.close()
    store = make_store(tmp_path)
    restarted_work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, restarted_work)
    processor.process_mcu_event(terminal)
    rows_after_replay = _completion_rows(store, "DELIVERY_COMPLETE")
    assert len(rows_after_replay) == 1
    assert rows_after_replay[0]["event_uid"] == first_event_uid
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETING"
    assert slot["context"]["phase"] == "COMPLETING"
    updater.close()
    store.close()


def test_clean_completion_replay_keeps_one_completion_event_and_uid(
    tmp_path,
):
    store, updater, uart, work, processor = _runtime(tmp_path)
    command = valid_service_command(
        "start-clean-operation.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert processor.process_next() is True
    operation_uid = command["payload"]["operationUid"]
    port_no = command["payload"]["portNo"]
    start_uid = uart.calls[-1][2]
    preunlock_uid = "53000000-0000-4000-8000-0000000003a1"
    processor.process_mcu_event(
        _event(
            "WORK_PREUNLOCK_WEIGHT_READY",
            52,
            1,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 1,
                "uptimeMs": 1_000,
                "mcuCommandUid": start_uid,
                "operationUid": operation_uid,
                "portNo": port_no,
                "measurementUid": preunlock_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 50_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        )
    )
    unlock_uid = uart.calls[-1][2]
    for sequence, lock_state in ((2, "ENERGIZED"), (3, "DEENERGIZED")):
        processor.process_mcu_event(
            _event(
                "CLEAN_LOCK_POWER_CHANGED",
                54,
                sequence,
                {
                    "mcuBootId": 42,
                    "mcuEventSequence": sequence,
                    "uptimeMs": sequence * 1_000,
                    "mcuCommandUid": unlock_uid,
                    "operationUid": operation_uid,
                    "portNo": port_no,
                    "cleanActionSequence": 0,
                    "lockPowerState": lock_state,
                    "solenoidHealth": "OK",
                    "faultCode": "NONE",
                },
            )
        )
    processor.process_mcu_event(
        _event(
            "CLEAN_FINISH_REQUESTED",
            55,
            4,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 4,
                "uptimeMs": 4_000,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 1,
            },
        )
    )
    final_uid = "54000000-0000-4000-8000-0000000003a1"
    processor.process_mcu_event(
        _event(
            "CLEAN_FINAL_WEIGHT_READY",
            56,
            5,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 5,
                "uptimeMs": 5_000,
                "operationUid": operation_uid,
                "portNo": port_no,
                "cleanActionSequence": 1,
                "measurementUid": final_uid,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 2_000,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1_000,
                "sampleCount": 10,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
            },
        )
    )

    generation = store.begin_mcu_receive_generation(42)
    assert generation > 0
    completion = _event(
        "CLEAN_COMPLETION_CONFIRMED",
        62,
        6,
        {
            "mcuBootId": 42,
            "mcuEventSequence": 6,
            "uptimeMs": 6_000,
            "operationUid": operation_uid,
            "portNo": port_no,
            "cleanActionSequence": 1,
            "finalMeasurementUid": final_uid,
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "OK",
            "cleanDoorStateBasis": "CLEANER_CONFIRMATION",
            "cleanerPhysicalCloseConfirmed": True,
        },
    )
    terminal = _persist_terminal_fact(store, completion)

    processor.process_mcu_event(terminal)
    rows_after_first = _completion_rows(store, "CLEAN_COMPLETE")
    assert len(rows_after_first) == 1
    first_event_uid = rows_after_first[0]["event_uid"]
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETING"
    assert slot["context"]["phase"] == "COMPLETING"

    # Re-open edge.db before replay so the test covers the real crash window,
    # not merely two calls within one WorkManager instance.
    safety = work._job_safety
    store.close()
    store = make_store(tmp_path)
    restarted_work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    processor = CommandProcessor(store, uart, restarted_work)
    processor.process_mcu_event(terminal)
    rows_after_replay = _completion_rows(store, "CLEAN_COMPLETE")
    assert len(rows_after_replay) == 1
    assert rows_after_replay[0]["event_uid"] == first_event_uid
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETING"
    assert slot["context"]["phase"] == "COMPLETING"
    updater.close()
    store.close()


def _assert_measurement_terminal_replay_is_idempotent(
    *,
    tmp_path,
    command_example,
    event_type,
    terminal_event,
):
    store, updater, uart, work, processor = _runtime(tmp_path)
    command = valid_service_command(command_example)
    assert store.receive_command(
        command["commandUid"], command["commandType"], command
    ) == "ACCEPTED"
    assert processor.process_next() is True
    mcu_command_uid = store.get_command(command["commandUid"])[
        "mcu_command_uid"
    ]

    event = terminal_event(command, mcu_command_uid)
    generation = store.begin_mcu_receive_generation(
        event["payload"]["mcuBootId"]
    )
    assert generation > 0
    terminal = _persist_terminal_fact(store, event)

    # The permanent updater ledger deliberately cannot finish. The business
    # slot must therefore remain available for convergence/retry even though
    # the terminal MCU fact and its cloud event are already durable.
    work._complete_job_safety = lambda *args, **kwargs: False
    processor.process_mcu_event(terminal)
    rows_after_first = _completion_rows(store, event_type)
    assert len(rows_after_first) == 1
    first_event_uid = rows_after_first[0]["event_uid"]
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETED"

    # Model a crash after EdgeStore atomically persisted the event, projection,
    # work context and processed inbox marker. A stale in-memory copy of the
    # identical MCU fact must resolve to the original event UID.
    safety = work._job_safety
    store.close()
    store = make_store(tmp_path)
    restarted_work = WorkManager(
        store,
        uart,
        None,
        FakePhotoManager(),
        job_safety=safety,
    )
    restarted_work._complete_job_safety = lambda *args, **kwargs: False
    processor = CommandProcessor(store, uart, restarted_work)
    processor.process_mcu_event(terminal)

    rows_after_replay = _completion_rows(store, event_type)
    assert len(rows_after_replay) == 1
    assert rows_after_replay[0]["event_uid"] == first_event_uid
    _assert_terminal_inbox_processed(store, terminal)
    slot = store.get_work_slot()
    assert slot is not None
    assert slot["work_state"] == "COMPLETED"
    updater.close()
    store.close()


def test_fullness_result_replay_while_complete_job_unavailable_keeps_one_event(
    tmp_path,
):
    def terminal_event(command, mcu_command_uid):
        return _event(
            "FULLNESS_SAMPLE_RESULT",
            57,
            14,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 6,
                "uptimeMs": 12_000,
                "mcuCommandUid": mcu_command_uid,
                "detectionUid": command["payload"]["detectionUid"],
                "portNo": command["payload"]["portNo"],
                "sampleRole": "INITIAL",
                "fullnessSensorKind": "ULTRASONIC",
                "fullnessSensorValue": "CLEAR",
                "fullnessSampleBasis": "NO_ECHO_CLEAR_FALLBACK",
                "representativeDistancePresent": False,
                "representativeDistanceMm": 0,
                "requestedSampleCount": 5,
                "validSampleCount": 0,
                "measurementUid": (
                    "55000000-0000-4000-8000-000000000091"
                ),
                "measurementStatus": "DISCONNECTED",
                "weightValuePresent": False,
                "reportedWeightGrams": 0,
                "weightValueKind": "NONE",
                "measurementElapsedMs": 0,
                "sampleCount": 0,
                "calibrationVersion": 1,
                "weightSensorHealth": "DISCONNECTED",
                "faultCode": "WEIGHT_DISCONNECTED",
            },
        )

    _assert_measurement_terminal_replay_is_idempotent(
        tmp_path=tmp_path,
        command_example="sample-fullness.service-wire.json",
        event_type="FULLNESS_SAMPLE_COMPLETE",
        terminal_event=terminal_event,
    )


def test_baseline_result_replay_while_complete_job_unavailable_keeps_one_event(
    tmp_path,
):
    def terminal_event(command, mcu_command_uid):
        return _event(
            "BASELINE_MEASUREMENT_RESULT",
            58,
            15,
            {
                "mcuBootId": 42,
                "mcuEventSequence": 7,
                "uptimeMs": 18_000,
                "mcuCommandUid": mcu_command_uid,
                "measurementUid": command["payload"]["measurementUid"],
                "portNo": command["payload"]["portNo"],
                "measurementStatus": "UNSTABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": 1_180,
                "weightValueKind": "LAST_FOUR_MEAN",
                "measurementElapsedMs": 6_000,
                "sampleCount": 60,
                "calibrationVersion": 1,
                "weightSensorHealth": "OK",
                "faultCode": "WEIGHT_UNSTABLE",
            },
        )

    _assert_measurement_terminal_replay_is_idempotent(
        tmp_path=tmp_path,
        command_example="measure-empty-bag-baseline.service-wire.json",
        event_type="BASELINE_MEASUREMENT_COMPLETE",
        terminal_event=terminal_event,
    )
