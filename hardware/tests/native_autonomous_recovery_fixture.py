"""START-only rc.23 work fixture for native recovery/issue tests."""
from contextlib import contextmanager
import uuid

import uart2_protocol as uart
from mcu_process_handoff import McuProcessEventHandoff
from mcu_result_handoff import McuResultHandoff
from mcu_session import McuBootSession
from mcu_work_query import McuWorkQuery
from hardware.tests.test_mcu_work_preparation import original_scope, take_samples


class AutonomousRecoveryWire:
    """Serial, clock, process, and result boundaries for autonomous START tests."""

    def __init__(self, case, runtime):
        self.case, self.runtime, self.now = case, runtime, case.now
        self.sent = []

    def write(self, frame):
        lib, endpoint, _, _, *_ = self.runtime
        decoded = uart.decode_frame(frame, sender_role="EDGE")
        values = uart.decode_payload(decoded["messageName"], decoded["payload"])
        self.sent.append((decoded["messageName"], values))
        assert decoded["messageName"] not in {
            "AUTHORIZE_DELIVERY_FIRST_OPEN",
            "SAFE_CLOSE",
            "UNLOCK_CLEAN_DOOR",
        }
        assert lib.McuControlEndpoint_Feed(endpoint, frame, len(frame), self.now)
        return len(frame)

    def pump(self, client):
        while self.runtime[3]:
            client.accept_frame(self.runtime[3].pop(0), self.now)

    def advance(self, elapsed):
        lib, endpoint, preparation, *_ = self.runtime
        lib.RuntimeClock_Advance(elapsed)
        lib.ActuatorRuntime_Tick()
        self.now += elapsed
        assert lib.McuWorkPreparation_Poll(preparation, endpoint, self.now)

    def boot(self):
        boot = McuBootSession(self.case.store, self.write)
        boot.poll(self.now)
        self.pump(boot)
        assert boot.current_boot(self.now) == 1
        return boot

    def intent(self, after, message="CLEAN_UNLOCK_REQUESTED"):
        case = self.case
        lib, endpoint, *_ = self.runtime
        assert lib.McuCleanExecution_Request(
            case.execution,
            endpoint,
            uuid.UUID(case.permit.work_uid).bytes,
            uart.MESSAGE_SPECS[message]["id"],
            after,
            self.now,
        )
        return self.custody(message, after + 1)

    def custody(self, message, step):
        scope = original_scope(self.case.start, clean=self.case.clean) | dict(
            eventMessageType=message,
            stepSequence=step,
            configVersion=self.case.start["configVersion"],
        )
        del scope["queryId"]
        client = McuProcessEventHandoff(self.case.store, self.write, scope)
        client.poll(self.now)
        self.pump(client)
        payload = uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"queryId": 1})[8:]
        return self.case.store.get_native_process_receipt(payload)

    def handshake(self):
        boot = McuBootSession(self.case.store, self.write)
        boot.poll(self.now)
        self.pump(boot)
        return boot

    def reset_mcu(self):
        lib, endpoint, preparation, replies, _, sink, guard = self.runtime
        replies.clear()
        lib.TestFacts_InitHardware()
        lib.McuControlEndpoint_Init(endpoint, 1, sink, None)
        assert lib.McuWorkPreparation_Attach(preparation, endpoint, 2, guard, None)
        self.now += 1  # Pi's monotonic clock does not reset with the MCU
        return self.handshake()

    def finish_delivery(self, *, unavailable=False):
        from hardware.tests.test_native_configuration import inputs

        self.advance(inputs()["device"]["deliveryDoorTravelWaitMs"])
        if unavailable:
            self.advance(5000)
        else:
            self.now = take_samples(self.runtime, [700] * 5, start=self.now, measurement=2)
        row = self.custody("WORK_POSTCLOSE_WEIGHT_READY", 1)
        value = uart.decode_payload(row["message_name"], row["payload"])
        self.advance(0)
        if not unavailable:
            lib, endpoint, *_ = self.runtime
            assert lib.McuDeliveryExecution_Select(
                self.case.execution,
                endpoint,
                uuid.UUID(value["measurementUid"]).bytes,
                2,
                self.now,
            )
            self.custody("DELIVERY_SELECTION", 1)
            self.advance(0)
        saved = self.handoff_result()
        result = uart.decode_payload("WORK_RESULT", saved["payload"])
        assert result["initialWeightGrams"] == 500
        if unavailable:
            assert result["finishReason"] == "FAILED" and result["finalKind"] == "UNAVAILABLE"
        else:
            assert result["finalWeightGrams"] == 700
        return saved

    def finish_clean(self):
        self.intent(0, "CLEAN_FINISH_REQUESTED")
        self.advance(0)
        self.now = take_samples(self.runtime, [100] * 5, start=self.now, measurement=2)
        self.custody("CLEAN_FINAL_WEIGHT_READY", 1)
        # rc.23 turns the local FINISH request plus terminal weight into the
        # authoritative result. No second Pi-side/historical confirmation is
        # required to make the result exist.
        self.advance(0)
        return self.handoff_result()

    def handoff_result(self):
        original = original_scope(self.case.start, clean=self.case.clean)
        del original["queryId"]
        query = McuWorkQuery(self.case.store, self.write, original)
        query.poll(self.now)
        self.pump(query)
        observed = query.observation(self.now)
        assert observed["status"] == "RESULT_HELD"
        identity = dict(
            mcuBootId=1,
            workUid=self.case.permit.work_uid,
            resultSequence=observed["resultSequence"],
            resultDigestSha256=observed["resultDigestSha256"],
        )
        handoff = McuResultHandoff(self.case.store, self.write, identity)
        handoff.poll(self.now)
        self.pump(handoff)
        return self.case.store.get_native_mcu_result(1, identity["resultSequence"])


@contextmanager
def autonomous_active_case(runtime, tmp_path, *, clean=None, clean_work=None,
                           cloud_command_factory=None):
    """Pause current autonomous work after its first door/lock action.

    The only cloud/native control command is START. Process queries below
    retain facts the MCU already produced and never create an old action
    binding or send a deprecated per-action command.
    """
    from hardware.tests.test_mcu_simplified_execution import tick
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_simplified_mcu_pi_business import real_work

    if clean is None:
        clean = clean_work
    if not isinstance(clean, bool):
        raise ValueError("autonomous active case requires its work kind")
    if cloud_command_factory is not None and cloud_command_factory.__name__ != "original_command":
        raise ValueError("autonomous active case only accepts the production cloud command")
    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        case.occupancy = case.store.get_work_slot()
        case.execution = case.cleanup if clean else case.delivery
        case.now = case.wire.now
        wire = AutonomousRecoveryWire(case, runtime)
        case.wire = wire
        initial_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
        initial_step = 0 if clean else 1
        saved = wire.custody(initial_name, initial_step)
        case.initial = uart.decode_payload(saved["message_name"], saved["payload"])
        case.scope = saved["scope"]
        if clean:
            wire.now = tick(runtime, wire.now, inputs()["device"]["cleanSolenoidPulseMs"])
        else:
            for duration in (100, case.start["deliveryAutoCloseMs"], 100):
                wire.now = tick(runtime, wire.now, duration)
        case.now = wire.now
        yield case
