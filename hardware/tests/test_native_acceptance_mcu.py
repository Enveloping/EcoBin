"""UART-v2 factory acceptance uses only fresh, read-only MCU facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import uart2_protocol as uart
import factory.native_acceptance_mcu as native_acceptance_mcu

from factory.acceptance_hardware import (
    AcceptanceHardwareError,
    FixedRoleCameraProbe,
    sanitize_firmware_identity,
    sanitize_self_test,
)
from factory.acceptance_core import AcceptanceError, FactoryAcceptanceExecutor
from factory.native_acceptance_mcu import (
    FACTORY_BOOT_ID_NAMESPACE,
    FACTORY_DEVICE_CONFIG,
    FACTORY_PORT_CONFIG,
    NativeAcceptanceMcu,
    _factory_configuration,
)


class ScriptedSerial:
    timeout = 0
    write_timeout = 1
    is_open = True

    def __init__(
        self,
        *,
        state_path,
        initial_boot_id=0,
        facts_changes=None,
        drop_command_decisions=False,
        emit_work_result=True,
        result_changes=None,
        drop_result_saved_reply=False,
        highest_command_sequence=0,
    ):
        self.state_path = state_path
        self.boot_id = initial_boot_id
        self.facts_changes = facts_changes or {}
        self.input = bytearray()
        self.writes = []
        self.state_at_write = []
        self.drop_command_decisions = drop_command_decisions
        self.emit_work_result = emit_work_result
        self.result_changes = result_changes or {}
        self.drop_result_saved_reply = drop_result_saved_reply
        self.highest_command_sequence = highest_command_sequence
        self.applied_config = None
        self.commands = {}
        self.active_work = None
        self.held_result = None
        self.released_result = None

    @property
    def in_waiting(self):
        return len(self.input)

    def read(self, count):
        result = bytes(self.input[:count])
        del self.input[:count]
        return result

    def write(self, frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        values = uart.decode_payload(decoded["messageName"], decoded["payload"])
        self.writes.append((decoded["messageName"], values))
        self.state_at_write.append(
            json.loads(self.state_path.read_text(encoding="utf-8"))
        )
        self._reply(decoded["messageName"], values)
        return len(frame)

    def close(self):
        self.is_open = False

    def _reply(self, name, values):
        if name == "BOOT_PROBE":
            self.enqueue(
                "BOOT_PROBE_REPLY",
                {"probeId": values["probeId"], "mcuBootId": self.boot_id},
            )
        elif name == "BIND_BOOT":
            self.boot_id = values["proposedMcuBootId"]
            # Deliberately lose the bind reply.  A fresh probe must recover it.
        elif name == "QUERY_DEVICE_IDENTITY":
            self.enqueue(
                "DEVICE_IDENTITY_REPLY",
                values
                | {
                    "currentMcuBootId": self.boot_id,
                    "status": "AVAILABLE",
                    "protocolMajor": 2,
                    "protocolMinor": 0,
                    "portCount": 1,
                    "capabilityBitmap": 0x8100,
                    "highestCommandSequence": max(
                        (item["commandSequence"] for item in self.commands.values()),
                        default=self.highest_command_sequence,
                    ),
                    "firmwareVersionCode": 25,
                    "firmwareIdentityHigh": 0x12345678,
                    "firmwareIdentityLow": 0x9ABCDEF0,
                    "firmwareVersion": "2.0.0-rc.25",
                },
            )
        elif name == "QUERY_DEVICE_FACTS":
            facts = _device_facts()
            facts.update(values)
            facts.update(
                {
                    "currentMcuBootId": self.boot_id,
                    "status": "AVAILABLE",
                    "capturedUptimeMs": 10_000,
                    "scaleReadStatus": "VALID",
                    "scaleAttemptSequence": 7,
                    "scaleCapturedUptimeMs": 9_900,
                    "scaleWeightGrams": 486,
                    "smokeObservationState": "NORMAL",
                    "smokeObservedUptimeMs": 9_950,
                    "fullnessObservationKind": "DIGITAL_INFRARED",
                    "fullnessReadStatus": "VALID",
                    "fullnessCapturedUptimeMs": 9_975,
                    "fullnessInfraredBlocked": True,
                }
            )
            if self.applied_config is not None:
                facts.update(self.applied_config)
            facts.update(self.facts_changes)
            self.enqueue("DEVICE_FACTS_REPLY", facts)
        elif name.startswith("CONFIG_"):
            self.commands[values["mcuCommandUid"]] = dict(values)
            if name == "CONFIG_COMMIT":
                self.applied_config = {
                    "appliedConfigVersion": values["configVersion"],
                    "appliedContentSha256": values["contentSha256"],
                    "appliedMcuPayloadSha256": values["mcuPayloadSha256"],
                    "configStaging": False,
                }
            if not self.drop_command_decisions:
                self._decision(values, "COMMAND_DECISION")
        elif name in {"START_DELIVERY_SESSION", "START_CLEAN_OPERATION"}:
            self.commands[values["mcuCommandUid"]] = dict(values)
            work_type = (
                "DELIVERY_SESSION"
                if name == "START_DELIVERY_SESSION"
                else "CLEAN_OPERATION"
            )
            work_uid = values.get("sessionUid", values.get("operationUid"))
            self.active_work = {
                "command": dict(values),
                "workType": work_type,
                "workUid": work_uid,
                "portNo": values["portNo"],
            }
            if not self.drop_command_decisions:
                self._decision(values, "COMMAND_DECISION")
            if self.emit_work_result:
                self.held_result = _work_result_payload(
                    boot_id=self.boot_id,
                    command=values,
                    work_uid=work_uid,
                    work_type=work_type,
                    changes=self.result_changes,
                )
                self.enqueue_raw("WORK_RESULT", self.held_result)
        elif name == "QUERY_COMMAND":
            command = self.commands.get(values["mcuCommandUid"])
            outcome = "ACCEPTED" if command == {
                key: values[key]
                for key in (
                    "mcuCommandUid",
                    "commandDigestSha256",
                    "targetMcuBootId",
                    "commandSequence",
                )
            } | {
                key: command[key]
                for key in command or {}
                if key not in {
                    "mcuCommandUid",
                    "commandDigestSha256",
                    "targetMcuBootId",
                    "commandSequence",
                }
            } else "NOT_SEEN"
            self.enqueue(
                "COMMAND_QUERY_RESULT",
                values
                | {
                    "currentMcuBootId": self.boot_id,
                    "outcome": outcome,
                    "errorCode": "NONE",
                    "highestCommandSequence": max(
                        (item["commandSequence"] for item in self.commands.values()),
                        default=self.highest_command_sequence,
                    ),
                },
            )
        elif name == "QUERY_WORK":
            status = "NOT_FOUND"
            result_sequence = 0
            result_digest = "00" * 32
            if self.active_work is not None and all(
                values[key] == self.active_work["command"][key]
                for key in (
                    "mcuCommandUid",
                    "commandDigestSha256",
                    "targetMcuBootId",
                    "commandSequence",
                )
            ) and values["workUid"] == self.active_work["workUid"]:
                status = "RESULT_HELD" if self.held_result is not None else "RUNNING"
                if self.held_result is not None:
                    decoded = uart.decode_payload("WORK_RESULT", self.held_result)
                    result_sequence = decoded["resultSequence"]
                    result_digest = decoded["resultDigestSha256"]
            self.enqueue(
                "WORK_QUERY_REPLY",
                values
                | {
                    "currentMcuBootId": self.boot_id,
                    "status": status,
                    "phase": "DELIVERY_FINALIZING"
                    if values["workType"] == "DELIVERY_SESSION"
                    else "CLEAN_FINALIZING",
                    "resultSequence": result_sequence,
                    "resultDigestSha256": result_digest,
                },
            )
        elif name == "QUERY_RESULT":
            decoded = (
                uart.decode_payload("WORK_RESULT", self.held_result)
                if self.held_result is not None
                else (
                    uart.decode_payload("WORK_RESULT", self.released_result)
                    if self.released_result is not None
                    else None
                )
            )
            matches = decoded is not None and all(
                values[key] == decoded[key]
                for key in (
                    "mcuBootId",
                    "resultSequence",
                    "workUid",
                    "resultDigestSha256",
                )
            )
            status = (
                "HELD"
                if matches and self.held_result is not None
                else "RELEASED"
                if matches
                else "NOT_FOUND"
            )
            self.enqueue(
                "RESULT_QUERY_REPLY",
                values
                | {
                    "currentMcuBootId": self.boot_id,
                    "status": status,
                },
            )
            if status == "HELD":
                self.enqueue_raw("WORK_RESULT", self.held_result)
        elif name == "RESULT_SAVED":
            decoded = (
                uart.decode_payload("WORK_RESULT", self.held_result)
                if self.held_result is not None
                else None
            )
            boot_mismatch = values["mcuBootId"] != self.boot_id
            matches = decoded is not None and all(
                values[key] == decoded[key] for key in values
            )
            if not self.drop_result_saved_reply:
                self.enqueue(
                    "RESULT_SAVED_REPLY",
                    values
                    | {
                        "currentMcuBootId": self.boot_id,
                        "status": (
                            "BOOT_MISMATCH"
                            if boot_mismatch
                            else "RELEASED"
                            if matches
                            else "NOT_FOUND"
                        ),
                    },
                )
            if matches and not boot_mismatch:
                self.released_result = self.held_result
                self.held_result = None

    def _decision(self, values, name):
        self.enqueue(
            name,
            {
                key: values[key]
                for key in (
                    "mcuCommandUid",
                    "commandDigestSha256",
                    "targetMcuBootId",
                    "commandSequence",
                )
            }
            | {
                "currentMcuBootId": self.boot_id,
                "outcome": "ACCEPTED",
                "errorCode": "NONE",
            },
        )

    def enqueue(self, name, values):
        self.enqueue_raw(name, uart.encode_payload(name, values))

    def enqueue_raw(self, name, payload):
        self.input.extend(uart.encode_frame(name, 1, payload))


class ShortWriteSerial(ScriptedSerial):
    def write(self, frame):
        super().write(frame)
        return len(frame) - 1


class StartWriteFailureSerial(ScriptedSerial):
    def __init__(self, **arguments):
        super().__init__(**arguments)
        self.fail_start_once = True

    def write(self, frame):
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        if decoded["messageName"].startswith("START_") and self.fail_start_once:
            self.fail_start_once = False
            raise OSError("simulated power loss before UART accepted START")
        return super().write(frame)


class DropWorkQuerySerial(ScriptedSerial):
    def _reply(self, name, values):
        if name == "QUERY_WORK":
            return
        super()._reply(name, values)


class ResultWithoutWorkQueryReplySerial(ScriptedSerial):
    def _reply(self, name, values):
        if name != "QUERY_WORK":
            super()._reply(name, values)
            return
        if self.held_result is None:
            work = self.active_work
            assert work is not None
            self.held_result = _work_result_payload(
                boot_id=self.boot_id,
                command=work["command"],
                work_uid=work["workUid"],
                work_type=work["workType"],
                changes=self.result_changes,
            )
            self.enqueue_raw("WORK_RESULT", self.held_result)


class SimulatedPowerLoss(BaseException):
    pass


class UnusedBootloader:
    def enter_system_bootloader(self):
        raise AssertionError("update line must not be used")

    def boot_application(self):
        raise AssertionError("update line must not be used")

    def force_application_selection(self):
        raise AssertionError("update line must not be used")

    def probe_read_only(self):
        raise AssertionError("update line must not be used")


def _native_executor(tmp_path, serial_port, *, fault_hook=None):
    def serial_factory(**arguments):
        del arguments
        serial_port.is_open = True
        return serial_port

    mcu = NativeAcceptanceMcu.for_port(
        state_path=tmp_path / "native-uart-state.json",
        serial_factory=serial_factory,
    )
    cameras = FixedRoleCameraProbe(
        outside_source="simulated://outside",
        inside_source="simulated://inside",
        temporary_directory=tmp_path / "camera-temp",
        capture=lambda source, destination: destination.write_bytes(
            source.encode("ascii")
        ),
        allow_simulated=True,
    )
    return FactoryAcceptanceExecutor(
        mcu=mcu,
        bootloader=UnusedBootloader(),
        cameras=cameras,
        state_path=tmp_path / "acceptance" / "state.json",
        report_path=tmp_path / "acceptance" / "report.json",
        instance_lock_path=tmp_path / "locks" / "acceptance.lock",
        uart_lock_path=tmp_path / "locks" / "uart5.lock",
        fault_hook=fault_hook,
    )


def _seed_native_action_prerequisites(executor):
    executor.begin_run(
        image_release_id="ecobin-zero3-1.0.0",
        boot_id="11111111-2222-3333-4444-555555555555",
        wall_time_trusted=False,
        hardware_config_digest="a" * 64,
        mcu_update_line_installed=False,
    )
    executor.check_mcu()
    state = executor.snapshot()
    state["checks"]["weight"] = {"status": "PASSED"}
    state["checks"]["cameras"] = {"status": "PASSED"}
    executor._save_state(state)


def _device_facts():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "contracts/examples/uart/golden-vectors.json"
    )
    vectors = json.loads(fixture.read_text(encoding="utf-8"))["vectors"]
    vector = next(
        item for item in vectors if item["messageName"] == "DEVICE_FACTS_REPLY"
    )
    return uart.decode_payload(
        "DEVICE_FACTS_REPLY", bytes.fromhex(vector["payloadHex"])
    )


def _work_result_payload(*, boot_id, command, work_uid, work_type, changes=None):
    fixture = (
        Path(__file__).resolve().parents[2]
        / "contracts/examples/uart/golden-vectors.json"
    )
    vectors = json.loads(fixture.read_text(encoding="utf-8"))["vectors"]
    vector = next(item for item in vectors if item["name"] == "work_result_delivery")
    values = uart.decode_payload(
        "WORK_RESULT", bytes.fromhex(vector["payloadHex"])
    )
    values.update(
        {
            "mcuBootId": boot_id,
            "resultSequence": 1,
            "workUid": work_uid,
            "workType": work_type,
            "portNo": 1,
            "configVersion": command["configVersion"],
            "originCommandUid": command["mcuCommandUid"],
            "originCommandSequence": command["commandSequence"],
            "finishReason": "DELIVERY_END"
            if work_type == "DELIVERY_SESSION"
            else "CLEAN_CONFIRMED",
            "physicalCloseConfirmed": work_type == "CLEAN_OPERATION",
            "deliveryRoundCount": 1 if work_type == "DELIVERY_SESSION" else 0,
            "cleanActionSequence": 0 if work_type == "DELIVERY_SESSION" else 1,
            "negativeWeightAnomaly": False,
            "initialKind": "STABLE_MEAN",
            "initialWeightGrams": 100,
            "initialSampleCount": 5,
            "initialSpanGrams": 40,
            "finalKind": "TIMEOUT_MEDIAN",
            "finalWeightGrams": 580 if work_type == "DELIVERY_SESSION" else 20,
            "finalElapsedMs": 5_000,
            "finalSampleCount": 5,
            "finalSpanGrams": 90,
        }
    )
    values.update(changes or {})
    values["resultDigestSha256"] = uart.compute_result_digest(values)
    return uart.encode_payload("WORK_RESULT", values)


def test_identity_recovers_lost_bind_reply_after_persisting_every_number(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path)
    opened_with = {}

    def serial_factory(**arguments):
        opened_with.update(arguments)
        return serial_port

    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=serial_factory,
        # Keep the lost-reply window comfortably above loaded CI scheduling
        # jitter; production uses the independently tested 250 ms default.
        boot_attempt_timeout_ms=100,
    )
    try:
        result = mcu.query_identity(timeout_ms=750)
    finally:
        mcu.close()

    assert sanitize_firmware_identity(result) == {
        "fixedFrameRevision": 2,
        "firmwareVersion": "2.0.0-rc.25",
        "firmwareVersionCode": 25,
        "firmwareIdentityHex": "123456789abcdef0",
        "mcuBootId": FACTORY_BOOT_ID_NAMESPACE + 1,
        "mcuHighestCommandSequence": 0,
        "mcuCapabilityBitmapHex": "0000000000008100",
    }
    assert result["mcuHighestCommandSequence"] == 0
    assert result["mcuBootId"] == FACTORY_BOOT_ID_NAMESPACE + 1
    assert opened_with == {
        "port": "/dev/ttyS5",
        "baudrate": 115200,
        "timeout": 0,
        "write_timeout": 1,
        "exclusive": True,
    }
    assert [name for name, _ in serial_port.writes] == [
        "BOOT_PROBE",
        "BIND_BOOT",
        "BOOT_PROBE",
        "QUERY_DEVICE_IDENTITY",
    ]
    for (name, values), persisted in zip(
        serial_port.writes, serial_port.state_at_write, strict=True
    ):
        if name in {"BOOT_PROBE", "QUERY_DEVICE_IDENTITY"}:
            identifier = values.get("probeId", values.get("queryId"))
            assert persisted["lastQueryId"] >= identifier
        elif name == "BIND_BOOT":
            assert (
                persisted["lastProposedMcuBootId"]
                >= values["proposedMcuBootId"]
            )
    assert serial_port.is_open is False


def test_factory_boot_identity_uses_high_namespace_and_migrates_old_counter(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
        boot_attempt_timeout_ms=100,
    )
    try:
        mcu.query_identity(timeout_ms=750)
    finally:
        mcu.close()
    first = next(
        values["proposedMcuBootId"]
        for name, values in serial_port.writes
        if name == "BIND_BOOT"
    )
    assert first == FACTORY_BOOT_ID_NAMESPACE + 1
    assert first != 1  # an empty production EdgeStore allocates one

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["lastProposedMcuBootId"] = 1  # pre-namespace factory state
    state_path.write_text(json.dumps(state), encoding="utf-8")
    replacement = ScriptedSerial(state_path=state_path)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: replacement,
        boot_attempt_timeout_ms=100,
    )
    try:
        mcu.query_identity(timeout_ms=750)
    finally:
        mcu.close()
    migrated = next(
        values["proposedMcuBootId"]
        for name, values in replacement.writes
        if name == "BIND_BOOT"
    )
    assert migrated == FACTORY_BOOT_ID_NAMESPACE + 1
    assert migrated != 2

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["lastProposedMcuBootId"] = 9_007_199_254_740_991
    state_path.write_text(json.dumps(state), encoding="utf-8")
    exhausted = ScriptedSerial(state_path=state_path)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: exhausted,
        boot_attempt_timeout_ms=100,
    )
    try:
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.query_identity(timeout_ms=750)
    finally:
        mcu.close()
    assert failure.value.code == "MCU_UART_COUNTER_EXHAUSTED"
    assert not any(name == "BIND_BOOT" for name, _ in exhausted.writes)


def test_self_test_maps_only_fresh_actual_device_facts(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        result = mcu.query_self_test(timeout_ms=1_000)
    finally:
        mcu.close()

    assert sanitize_self_test(result) == {
        "weightGrams": 486,
        "mcuBootId": 42,
        "scaleAttemptSequence": 7,
        "scaleCapturedUptimeMs": 9_900,
        "infraredBlocked": True,
        "fullnessSensorKind": "DIGITAL_INFRARED",
        "fullnessReadStatus": "VALID",
        "fullnessDistanceMm": None,
        "fullnessDistanceThresholdMm": None,
        "fullnessBlocked": True,
        "smokeCode": 0,
        "smokeState": "NORMAL",
        "smokeSensorHealth": "OK",
    }
    names = [name for name, _ in serial_port.writes]
    assert names == [
        "BOOT_PROBE",
        "QUERY_DEVICE_IDENTITY",
        "QUERY_DEVICE_FACTS",
        "CONFIG_BEGIN",
        "CONFIG_DEVICE_BLOCK",
        "CONFIG_PORT_BLOCK",
        "CONFIG_COMMIT",
        "QUERY_DEVICE_FACTS",
        "QUERY_DEVICE_FACTS",
    ]
    assert not set(names) & {
        "START_DELIVERY_SESSION",
        "START_CLEAN_OPERATION",
        "START_BASELINE_MEASUREMENT",
        "SAMPLE_FULLNESS",
        "SAFE_CLOSE",
    }


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {
                "scaleReadStatus": "NOT_OBSERVED",
                "scaleAttemptSequence": 0,
                "scaleCapturedUptimeMs": 0,
                "scaleWeightGrams": 0,
                "scaleCalibrationVersion": 0,
            },
            "MCU_WEIGHT_FACT_UNAVAILABLE",
        ),
        (
            {"scaleCapturedUptimeMs": 9_000},
            "MCU_WEIGHT_FACT_STALE",
        ),
        (
            {"smokeObservedUptimeMs": 8_000},
            "MCU_SMOKE_FACT_STALE",
        ),
        (
            {"fullnessCapturedUptimeMs": 8_000},
            "MCU_FULLNESS_FACT_STALE",
        ),
        (
            {
                "fullnessObservationKind": "NONE",
                "fullnessReadStatus": "NOT_OBSERVED",
                "fullnessCapturedUptimeMs": 0,
                "fullnessInfraredBlocked": False,
            },
            "MCU_FULLNESS_FACT_UNAVAILABLE",
        ),
    ],
)
def test_self_test_rejects_missing_unhealthy_or_stale_facts(
    tmp_path, changes, code
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        facts_changes=changes,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.query_self_test(timeout_ms=1_000)
    finally:
        mcu.close()

    assert failure.value.code == code


def test_self_test_accepts_signed_zero_offset_and_records_auxiliary_warning(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=8_000_000_000_000_001,
        facts_changes={
            "scaleWeightGrams": -25_623,
            "smokeObservationState": "ALARM",
            "fullnessObservationKind": "ULTRASONIC",
            "fullnessReadStatus": "UNAVAILABLE",
            # An unavailable auxiliary sensor has no usable observation to
            # age.  MCU boot defaults may therefore leave its timestamp at 0.
            "fullnessCapturedUptimeMs": 0,
            "fullnessDistanceMm": 0,
            "fullnessInfraredBlocked": False,
        },
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        result = sanitize_self_test(mcu.query_self_test(timeout_ms=1_000))
    finally:
        mcu.close()

    assert result == {
        "weightGrams": -25_623,
        "mcuBootId": 8_000_000_000_000_001,
        "scaleAttemptSequence": 7,
        "scaleCapturedUptimeMs": 9_900,
        "infraredBlocked": None,
        "fullnessSensorKind": "ULTRASONIC",
        "fullnessReadStatus": "UNAVAILABLE",
        "fullnessDistanceMm": None,
        "fullnessDistanceThresholdMm": 600,
        "fullnessBlocked": None,
        "smokeCode": 1,
        "smokeState": "ALARM",
        "smokeSensorHealth": "ALARM",
    }


def test_self_test_accepts_stale_unavailable_smoke_warning(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        facts_changes={
            "smokeObservationState": "UNAVAILABLE",
            "smokeObservedUptimeMs": 0,
        },
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        result = sanitize_self_test(mcu.query_self_test(timeout_ms=1_000))
    finally:
        mcu.close()

    assert result["smokeCode"] == 2
    assert result["smokeState"] == "UNAVAILABLE"
    assert result["smokeSensorHealth"] == "UNAVAILABLE"


def test_short_write_fails_without_replaying_the_frame(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ShortWriteSerial(state_path=state_path)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.query_identity(timeout_ms=200)
    finally:
        mcu.close()

    assert failure.value.code == "UART5_SHORT_WRITE"
    assert [name for name, _ in serial_port.writes] == ["BOOT_PROBE"]
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "lastQueryId"
    ] == 1


def test_factory_configuration_is_exact_persistent_and_not_reapplied(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.query_self_test(timeout_ms=1_000)
        mcu.query_self_test(timeout_ms=1_000)
    finally:
        mcu.close()

    config_writes = [
        (name, values)
        for name, values in serial_port.writes
        if name.startswith("CONFIG_")
    ]
    assert [name for name, _ in config_writes] == [
        "CONFIG_BEGIN",
        "CONFIG_DEVICE_BLOCK",
        "CONFIG_PORT_BLOCK",
        "CONFIG_COMMIT",
    ]
    device = config_writes[1][1]
    port = config_writes[2][1]
    assert device["weightPollIntervalMs"] == 250
    assert device["weightResponseTimeoutMs"] == 200
    assert device["weightMeasurementTimeoutMs"] == 5_000
    assert port["weightRequiredSampleCount"] == 5
    assert port["weightMinimumMedianSampleCount"] == 5
    assert port["weightMaximumFluctuationGrams"] == 100
    assert port["weightMeasurementTimeoutMs"] == 5_000
    assert port["weightMinimumGrams"] == -350_000
    assert port["weightMaximumGrams"] == 350_000
    assert port["fullnessSensorKind"] == "ULTRASONIC"
    for name, values in config_writes:
        persisted = next(
            state
            for (written_name, written_values), state in zip(
                serial_port.writes,
                serial_port.state_at_write,
                strict=True,
            )
            if written_name == name
            and written_values["mcuCommandUid"] == values["mcuCommandUid"]
        )
        command = next(
            item
            for item in persisted["configuration"]["commands"]
            if item["mcuCommandUid"] == values["mcuCommandUid"]
        )
        assert command["writeAttempted"] is True
        assert bytes.fromhex(command["payloadHex"]) == uart.encode_payload(
            name, values
        )


def test_factory_continues_current_mcu_command_sequence_without_collision(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        highest_command_sequence=77,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.query_self_test(timeout_ms=1_000)
    finally:
        mcu.close()

    sequences = [
        values["commandSequence"]
        for name, values in serial_port.writes
        if name.startswith("CONFIG_")
    ]
    assert sequences == [78, 79, 80, 81]
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "lastCommandSequence"
    ] == 81


def test_lost_config_decision_queries_identity_without_replaying_config(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        drop_command_decisions=True,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.query_self_test(timeout_ms=1_500)
    finally:
        mcu.close()

    names = [name for name, _ in serial_port.writes]
    assert names.count("CONFIG_BEGIN") == 1
    assert names.count("CONFIG_DEVICE_BLOCK") == 1
    assert names.count("CONFIG_PORT_BLOCK") == 1
    assert names.count("CONFIG_COMMIT") == 1
    assert names.count("QUERY_COMMAND") == 4


def test_lost_start_decision_queries_original_without_replaying_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.query_self_test(timeout_ms=1_000)
        serial_port.drop_command_decisions = True
        mcu.write_action_once("DELIVERY")
    finally:
        mcu.close()

    names = [name for name, _ in serial_port.writes]
    assert names.count("START_DELIVERY_SESSION") == 1
    assert names[-1] == "QUERY_COMMAND"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["activeAction"]["writeAttempted"] is True
    assert state["activeAction"]["accepted"] is True


def test_delivery_result_is_journaled_before_return_and_released_exactly_once(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=500)
        journal = json.loads(state_path.read_text(encoding="utf-8"))
        assert journal["activeAction"]["resultPayloadHex"]
        assert result["preWeightGrams"] == 100
        assert result["postWeightGrams"] == 580
        assert result["weightDeltaGrams"] == 480
        assert result["fullnessSensorKind"] == "DIGITAL_INFRARED"
        assert result["infraredBlocked"] is True
        assert result["finishReason"] == "DELIVERY_END"
        assert result["deliveryDoorCommand"] == "CLOSE"
        assert result["deliveryDoorOutputStatus"] == "COMMAND_DISPATCHED"
        assert result["deliveryDoorPhysicalStateBasis"] == "NOT_OBSERVABLE"
        mcu.confirm_final_result(result)
        mcu.confirm_final_result(result)
    finally:
        mcu.close()

    names = [name for name, _ in serial_port.writes]
    assert names.count("START_DELIVERY_SESSION") == 1
    assert names.count("RESULT_SAVED") == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "activeAction"
    ]["released"] is True


def test_clean_uses_pre_minus_post_and_keeps_raw_ultrasonic_with_legacy_projection(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        facts_changes={
            "fullnessObservationKind": "ULTRASONIC",
            "fullnessReadStatus": "VALID",
            "fullnessDistanceMm": 321,
            "fullnessInfraredBlocked": False,
        },
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        self_test = mcu.query_self_test(timeout_ms=1_000)
        assert self_test["fullnessSensorKind"] == "ULTRASONIC"
        assert self_test["fullnessDistanceMm"] == 321
        assert self_test["fullnessDistanceThresholdMm"] == 600
        assert self_test["fullnessBlocked"] is True
        assert self_test["infraredBlocked"] is True
        assert sanitize_self_test(self_test) == {
            "weightGrams": 486,
            "mcuBootId": 42,
            "scaleAttemptSequence": 7,
            "scaleCapturedUptimeMs": 9_900,
            "infraredBlocked": True,
            "fullnessSensorKind": "ULTRASONIC",
            "fullnessReadStatus": "VALID",
            "fullnessDistanceMm": 321,
            "fullnessDistanceThresholdMm": 600,
            "fullnessBlocked": True,
            "smokeCode": 0,
            "smokeState": "NORMAL",
            "smokeSensorHealth": "OK",
        }
        mcu.write_action_once("CLEAN")
        result = mcu.await_final_result("CLEAN", timeout_ms=500)
    finally:
        mcu.close()

    assert result["preWeightGrams"] == 100
    assert result["postWeightGrams"] == 20
    assert result["weightDeltaGrams"] == 80
    assert result["fullnessSensorKind"] == "ULTRASONIC"
    assert result["fullnessDistanceMm"] == 321
    assert result["fullnessDistanceThresholdMm"] == 600
    assert result["fullnessBlocked"] is True
    assert result["infraredBlocked"] is True
    assert result["finishReason"] == "CLEAN_CONFIRMED"
    assert result["cleanLockPowerState"] == "DEENERGIZED"
    assert result["cleanSolenoidHealth"] == "UNKNOWN"
    assert result["cleanDoorStateBasis"] == "CLEANER_CONFIRMATION"
    assert result["cleanerPhysicalCloseConfirmed"] is True


def test_delivery_keeps_signed_weights_and_stale_auxiliary_unavailable_fact(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        facts_changes={
            "fullnessObservationKind": "ULTRASONIC",
            "fullnessReadStatus": "UNAVAILABLE",
            # The real MCU leaves this at its last/default value when the
            # optional ultrasonic module cannot provide a reading.
            "fullnessCapturedUptimeMs": 0,
            "fullnessDistanceMm": 0,
            "fullnessInfraredBlocked": False,
        },
        result_changes={
            "initialWeightGrams": -25_623,
            "finalWeightGrams": -25_595,
        },
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=500)
    finally:
        mcu.close()

    assert result["preWeightGrams"] == -25_623
    assert result["postWeightGrams"] == -25_595
    assert result["weightDeltaGrams"] == 28
    assert result["fullnessSensorKind"] == "ULTRASONIC"
    assert result["fullnessReadStatus"] == "UNAVAILABLE"
    assert result["fullnessDistanceMm"] is None
    assert result["fullnessDistanceThresholdMm"] == 600
    assert result["fullnessBlocked"] is None
    assert result["infraredBlocked"] is None


def test_failed_work_result_is_journaled_but_never_mapped_to_success(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        result_changes={"finishReason": "FAILED"},
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.await_final_result("DELIVERY", timeout_ms=500)
    finally:
        mcu.close()

    assert failure.value.code == "MCU_FACTORY_WORK_FAILED"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["activeAction"]["resultPayloadHex"]
    assert state["activeAction"]["mappedResult"] is None
    assert not any(name == "RESULT_SAVED" for name, _ in serial_port.writes)


def test_recovery_returns_journaled_result_without_replaying_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        facts_changes={
            "fullnessObservationKind": "ULTRASONIC",
            "fullnessReadStatus": "UNAVAILABLE",
            "fullnessCapturedUptimeMs": 0,
            "fullnessDistanceMm": 0,
            "fullnessInfraredBlocked": False,
        },
    )
    first = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    first.write_action_once("DELIVERY")
    first.close()
    starts_before = sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    )

    serial_port.is_open = True
    recovered = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        result = recovered.recover_final_result("DELIVERY", timeout_ms=500)
    finally:
        recovered.close()

    assert result["weightDeltaGrams"] == 480
    assert result["fullnessReadStatus"] == "UNAVAILABLE"
    assert result["fullnessDistanceMm"] is None
    assert result["fullnessBlocked"] is None
    assert sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    ) == starts_before


def test_recovery_queries_held_original_result_without_replaying_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        command = serial_port.active_work["command"]
        serial_port.held_result = _work_result_payload(
            boot_id=42,
            command=command,
            work_uid=serial_port.active_work["workUid"],
            work_type="DELIVERY_SESSION",
        )
        starts_before = len(
            [name for name, _ in serial_port.writes if name.startswith("START_")]
        )
        result = mcu.recover_final_result("DELIVERY", timeout_ms=500)
    finally:
        mcu.close()

    assert result["weightDeltaGrams"] == 480
    names = [name for name, _ in serial_port.writes]
    assert names.count("START_DELIVERY_SESSION") == starts_before == 1
    assert "QUERY_WORK" in names
    assert "QUERY_RESULT" in names


def test_lost_result_saved_reply_is_proved_released_without_resending(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        drop_result_saved_reply=True,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=500)
        mcu.confirm_final_result(result)
    finally:
        mcu.close()

    names = [name for name, _ in serial_port.writes]
    assert names.count("RESULT_SAVED") == 1
    assert "QUERY_RESULT" in names
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "activeAction"
    ]["released"] is True


def test_recovery_of_running_work_is_stable_error_and_never_sends_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        starts_before = len(
            [name for name, _ in serial_port.writes if name.startswith("START_")]
        )
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.recover_final_result("DELIVERY", timeout_ms=300)
    finally:
        mcu.close()

    assert failure.value.code == "MCU_FACTORY_WORK_STILL_RUNNING"
    assert len(
        [name for name, _ in serial_port.writes if name.startswith("START_")]
    ) == starts_before


def test_waiting_for_running_work_is_bounded_and_reports_overall_timeout(
    tmp_path,
    monkeypatch,
):
    """A responsive RUNNING MCU must not be flooded or mislabeled offline."""

    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    monkeypatch.setattr(
        native_acceptance_mcu,
        "WORK_QUERY_INTERVAL_SECONDS",
        0.03,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.await_final_result("DELIVERY", timeout_ms=120)
    finally:
        mcu.close()

    assert failure.value.code == "FINAL_RESULT_TIMEOUT"
    work_queries = [
        values for name, values in serial_port.writes if name == "QUERY_WORK"
    ]
    # A scheduler waking exactly on the 120 ms boundary may start the fifth
    # paced query before observing the overall deadline.
    assert 2 <= len(work_queries) <= 5


def test_three_missing_work_query_replies_report_control_communication_fault(
    tmp_path,
    monkeypatch,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = DropWorkQuerySerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    monkeypatch.setattr(
        native_acceptance_mcu,
        "WORK_QUERY_INTERVAL_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        native_acceptance_mcu,
        "COMMAND_RESPONSE_TIMEOUT_MS",
        10,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.await_final_result("DELIVERY", timeout_ms=500)
    finally:
        mcu.close()

    assert failure.value.code == "MCU_FACTORY_WORK_QUERY_TIMEOUT"
    assert sum(name == "QUERY_WORK" for name, _ in serial_port.writes) == 3


def test_result_journaled_while_work_query_reply_is_lost_still_completes(
    tmp_path,
    monkeypatch,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ResultWithoutWorkQueryReplySerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    monkeypatch.setattr(
        native_acceptance_mcu,
        "COMMAND_RESPONSE_TIMEOUT_MS",
        10,
    )
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=100)
        mcu.confirm_final_result(result)
    finally:
        mcu.close()

    assert result["finishReason"] == "DELIVERY_END"
    assert sum(name == "QUERY_WORK" for name, _ in serial_port.writes) == 1
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["workResultCount"] == 1
    assert saved["activeAction"]["mappedResult"] == result
    assert saved["activeAction"]["resultSavedWriteAttempted"] is True
    assert saved["activeAction"]["released"] is True


def test_uart_v2_factory_update_prepare_is_explicitly_unsupported(tmp_path):
    mcu = NativeAcceptanceMcu.for_port(
        state_path=tmp_path / "native-uart-state.json",
        serial_factory=lambda **arguments: None,
    )
    with pytest.raises(AcceptanceHardwareError) as failure:
        mcu.execute_update_prepare()
    assert failure.value.code == "MCU_UPDATE_PREPARE_UNSUPPORTED"


def test_exact_action_token_never_creates_a_replacement_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    token = "11111111-2222-3333-4444-555555555555"
    try:
        mcu.prepare_action("DELIVERY", token)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["activeAction"] = None
        state_path.write_text(json.dumps(state), encoding="utf-8")
        starts_before = sum(
            name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
        )

        with pytest.raises(AcceptanceHardwareError) as failure:
            mcu.write_action_once("DELIVERY", token)
    finally:
        mcu.close()

    assert failure.value.code == "MCU_FACTORY_ACTION_IDENTITY_MISMATCH"
    assert sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    ) == starts_before


def test_ambiguous_start_proved_not_seen_is_retired_without_start_replay(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    token = "11111111-2222-3333-4444-555555555555"
    try:
        mcu.prepare_action("DELIVERY", token)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["activeAction"]["writeAttempted"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")

        assert (
            mcu.recover_action_disposition("DELIVERY", token, timeout_ms=500)
            == "NOT_SENT"
        )
        mcu.retire_recovery_action("DELIVERY", token, "NOT_SENT")
        assert mcu.inspect_action("DELIVERY", token) is None
    finally:
        mcu.close()

    names = [name for name, _ in serial_port.writes]
    assert "QUERY_COMMAND" in names
    assert "START_DELIVERY_SESSION" not in names


def test_result_saved_intent_crash_retransmits_exact_acknowledgement(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=500)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw_result = uart.decode_payload(
            "WORK_RESULT",
            bytes.fromhex(state["activeAction"]["resultPayloadHex"]),
        )
        saved_values = {
            key: raw_result[key]
            for key in (
                "mcuBootId",
                "resultSequence",
                "workUid",
                "resultDigestSha256",
            )
        }
        state["activeAction"]["resultSavedPayloadHex"] = uart.encode_payload(
            "RESULT_SAVED", saved_values
        ).hex()
        state["activeAction"]["resultSavedWriteAttempted"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        writes_before = len(serial_port.writes)

        mcu.confirm_final_result(result)
    finally:
        mcu.close()

    new_writes = serial_port.writes[writes_before:]
    assert [name for name, _ in new_writes].count("RESULT_SAVED") == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "activeAction"
    ]["released"] is True


def test_not_seen_configuration_is_replaced_with_fresh_transaction(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.query_self_test(timeout_ms=1_000)
        original = json.loads(state_path.read_text(encoding="utf-8"))
        original_application_uid = original["configuration"]["applicationUid"]
        original_highest_sequence = original["lastCommandSequence"]

        serial_port.applied_config = None
        serial_port.commands.clear()
        original["configuration"]["applied"] = False
        original["configuration"]["commands"][0]["accepted"] = False
        original["configuration"]["commands"][0]["writeAttempted"] = True
        state_path.write_text(json.dumps(original), encoding="utf-8")

        mcu.query_self_test(timeout_ms=1_500)
    finally:
        mcu.close()

    recovered = json.loads(state_path.read_text(encoding="utf-8"))
    assert recovered["configuration"]["applicationUid"] != original_application_uid
    assert recovered["lastCommandSequence"] > original_highest_sequence
    names = [name for name, _ in serial_port.writes]
    assert names.count("CONFIG_BEGIN") == 2
    assert "QUERY_COMMAND" in names
    assert not any(name.startswith("START_") for name in names)


def test_durable_mapped_result_survives_later_mcu_boot_change(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        result = mcu.await_final_result("DELIVERY", timeout_ms=500)
        serial_port.boot_id = 43
        serial_port.active_work = None
        serial_port.held_result = None
        mcu.close()
        serial_port.is_open = True

        assert (
            mcu.recover_action_disposition(
                "DELIVERY",
                mcu._load_state()["activeAction"]["acceptanceActionToken"],
                timeout_ms=500,
            )
            == "RESULT_AVAILABLE"
        )
        assert mcu.recover_final_result("DELIVERY", timeout_ms=500) == result
        mcu.confirm_final_result(result)
    finally:
        mcu.close()

    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "activeAction"
    ]["released"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (("weightDeltaGrams", 999), ("fullnessBlocked", False)),
)
def test_tampered_mapped_result_is_rejected_as_local_state_damage(
    tmp_path,
    field,
    value,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    mcu = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )
    try:
        mcu.write_action_once("DELIVERY")
        mcu.await_final_result("DELIVERY", timeout_ms=500)
    finally:
        mcu.close()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["activeAction"]["mappedResult"][field] = value
    state_path.write_text(json.dumps(state), encoding="utf-8")
    damaged = NativeAcceptanceMcu.for_port(
        state_path=state_path,
        serial_factory=lambda **arguments: serial_port,
    )

    with pytest.raises(AcceptanceHardwareError) as failure:
        damaged.inspect_action(
            "DELIVERY",
            state["activeAction"]["acceptanceActionToken"],
        )

    assert failure.value.code == "MCU_UART_STATE_INVALID"


@pytest.mark.parametrize(
    "fault_point",
    (
        "delivery.after_armed",
        "delivery.after_native_action_prepared",
        "delivery.after_command_journal",
    ),
)
def test_core_and_native_journals_prove_unsent_start_then_allow_exact_retry(
    tmp_path,
    fault_point,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    tripped = False

    def fault(point):
        nonlocal tripped
        if point == fault_point and not tripped:
            tripped = True
            raise SimulatedPowerLoss()

    interrupted = _native_executor(tmp_path, serial_port, fault_hook=fault)
    with pytest.raises(SimulatedPowerLoss):
        with interrupted:
            _seed_native_action_prerequisites(interrupted)
            interrupted.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=500,
                quiet_ms=0,
            )

    assert not any(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    )

    resumed = _native_executor(tmp_path, serial_port)
    with resumed:
        if resumed.snapshot()["status"] == "RECOVERY_REQUIRED":
            resumed.recover(quiet_ms=0)
        recovered = resumed.snapshot()
        assert recovered["status"] == "RUNNING"
        assert recovered["checks"]["delivery"]["resultCode"] == (
            "DELIVERY_INTERRUPTED_BEFORE_COMMAND"
        )

        resumed.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=500,
            quiet_ms=0,
        )
        resumed.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )

    assert sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    ) == 1


def test_core_recovers_start_write_intent_proved_not_seen_without_nrst(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = StartWriteFailureSerial(
        state_path=state_path,
        initial_boot_id=42,
    )
    executor = _native_executor(tmp_path, serial_port)
    with executor:
        _seed_native_action_prerequisites(executor)
        with pytest.raises(AcceptanceError, match="UART5_WRITE_FAILED"):
            executor.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=500,
                quiet_ms=0,
            )
        assert executor.snapshot()["status"] == "RECOVERY_REQUIRED"
        executor.recover(quiet_ms=0)
        assert executor.snapshot()["checks"]["delivery"]["resultCode"] == (
            "DELIVERY_INTERRUPTED_BEFORE_COMMAND"
        )

        executor.run_action(
            "DELIVERY",
            operator_area_safe_confirmed=True,
            timeout_ms=500,
            quiet_ms=0,
        )
        executor.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )

    names = [name for name, _ in serial_port.writes]
    assert "QUERY_COMMAND" in names
    assert names.count("START_DELIVERY_SESSION") == 1


def test_core_recovers_original_held_result_without_replaying_start(tmp_path):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    tripped = False

    def fault(point):
        nonlocal tripped
        if point == "delivery.after_serial_write" and not tripped:
            tripped = True
            raise SimulatedPowerLoss()

    interrupted = _native_executor(tmp_path, serial_port, fault_hook=fault)
    with pytest.raises(SimulatedPowerLoss):
        with interrupted:
            _seed_native_action_prerequisites(interrupted)
            interrupted.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=500,
                quiet_ms=0,
            )

    command = serial_port.active_work["command"]
    serial_port.held_result = _work_result_payload(
        boot_id=42,
        command=command,
        work_uid=serial_port.active_work["workUid"],
        work_type="DELIVERY_SESSION",
    )
    starts_before = sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    )

    resumed = _native_executor(tmp_path, serial_port)
    with resumed:
        recovered = resumed.recover(quiet_ms=0)
        assert recovered["phase"] == "DELIVERY_AWAITING_AREA_CONFIRMATION"
        assert recovered["checks"]["delivery"]["result"][
            "weightDeltaGrams"
        ] == 480
        resumed.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )

    assert sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    ) == starts_before == 1


@pytest.mark.parametrize(
    "recovery_fault_point",
    (
        "delivery.after_boot_changed_journal",
        "delivery.after_native_action_retired",
    ),
)
def test_boot_changed_recovery_is_crash_idempotent_and_never_becomes_not_sent(
    tmp_path,
    recovery_fault_point,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(
        state_path=state_path,
        initial_boot_id=42,
        emit_work_result=False,
    )
    action_tripped = False

    def interrupt_after_start(point):
        nonlocal action_tripped
        if point == "delivery.after_serial_write" and not action_tripped:
            action_tripped = True
            raise SimulatedPowerLoss()

    interrupted = _native_executor(
        tmp_path,
        serial_port,
        fault_hook=interrupt_after_start,
    )
    with pytest.raises(SimulatedPowerLoss):
        with interrupted:
            _seed_native_action_prerequisites(interrupted)
            interrupted.run_action(
                "DELIVERY",
                operator_area_safe_confirmed=True,
                timeout_ms=500,
                quiet_ms=0,
            )

    serial_port.boot_id = 43
    serial_port.commands.clear()
    serial_port.active_work = None
    serial_port.held_result = None
    serial_port.applied_config = None
    recovery_tripped = False

    def interrupt_recovery(point):
        nonlocal recovery_tripped
        if point == recovery_fault_point and not recovery_tripped:
            recovery_tripped = True
            raise SimulatedPowerLoss()

    first_recovery = _native_executor(
        tmp_path,
        serial_port,
        fault_hook=interrupt_recovery,
    )
    with pytest.raises(SimulatedPowerLoss):
        with first_recovery:
            first_recovery.recover(quiet_ms=0)

    persisted = json.loads(
        (tmp_path / "acceptance" / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["recovery"]["nativeActionDisposition"] == "BOOT_CHANGED"

    final_recovery = _native_executor(tmp_path, serial_port)
    with final_recovery:
        recovered = final_recovery.recover(quiet_ms=0)
        assert recovered["phase"] == "DELIVERY_AWAITING_AREA_CONFIRMATION"
        assert recovered["checks"]["delivery"]["resultCode"] == (
            "DELIVERY_AREA_SAFETY_CONFIRMATION_REQUIRED"
        )
        final_recovery.confirm_delivery_area_safe(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert final_recovery.snapshot()["status"] == "FAILED"

    assert sum(
        name == "START_DELIVERY_SESSION" for name, _ in serial_port.writes
    ) == 1


def test_core_native_clean_uses_pre_minus_post_and_waits_for_door_confirmation(
    tmp_path,
):
    state_path = tmp_path / "native-uart-state.json"
    serial_port = ScriptedSerial(state_path=state_path, initial_boot_id=42)
    executor = _native_executor(tmp_path, serial_port)
    with executor:
        _seed_native_action_prerequisites(executor)
        state = executor.snapshot()
        state["checks"]["delivery"] = {
            "status": "PASSED",
            "resultCode": "DELIVERY_SAFE_VERIFIED",
            "sendAttempts": 1,
            "result": None,
            "operatorAreaSafeConfirmed": True,
        }
        executor._save_state(state)

        pending = executor.run_action(
            "CLEAN",
            operator_area_safe_confirmed=True,
            timeout_ms=500,
            quiet_ms=0,
        )
        assert pending["phase"] == "CLEAN_AWAITING_DOOR_CONFIRMATION"
        assert pending["checks"]["clean"]["result"]["weightDeltaGrams"] == 80
        executor.confirm_clean_door_closed(
            operator_confirmed=True,
            quiet_ms=0,
        )
        assert executor.snapshot()["checks"]["clean"]["status"] == "PASSED"

    assert sum(
        name == "START_CLEAN_OPERATION" for name, _ in serial_port.writes
    ) == 1
