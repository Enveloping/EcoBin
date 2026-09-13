"""Output-result shape, never actuator execution or observable door position."""


def validate_door_output_fields(registry, name, values, error):
    if name == "CLEAN_OPERATION_INTERRUPTED":
        phases = registry["enums"]["McuWorkPhase"]["values"]
        phase = phases.get(values["interruptedPhase"], values["interruptedPhase"])
        reasons = registry["enums"]["CleanInterruptionReason"]["values"]
        reason = reasons.get(values["interruptionReason"], values["interruptionReason"])
        if reason == reasons["UNLOCK_DISPATCH_REJECTED"]:
            valid = (phase == phases["CLEAN_WAIT_FIRST_UNLOCK"] and values["cleanActionSequence"] == 0
                or phase == phases["CLEAN_ACTIVE"] and values["cleanActionSequence"] > 0)
        else:
            valid = phase in [phases[key] for key in ("CLEAN_UNLOCK_PULSE", "CLEAN_ACTIVE",
                "CLEAN_FINAL_MEASURING", "CLEAN_RESULT_CONFIRMATION")]
        if not valid:
            raise error("clean interruption must retain its original eligible phase and action")
        final_phase = phase in (phases["CLEAN_FINAL_MEASURING"], phases["CLEAN_RESULT_CONFIRMATION"])
        measurement = values["finalMeasurementEventSequence"]
        if (final_phase != (measurement > 0) or final_phase and values["cleanActionSequence"] == 0
            or measurement >= values["mcuEventSequence"]):
            raise error("clean interruption must retain only its actual preceding final measurement")
        return
    if name == "DELIVERY_POSTCLOSE_INTERRUPTED":
        phases = registry["enums"]["McuWorkPhase"]["values"]
        phase = phases.get(values["interruptedPhase"], values["interruptedPhase"])
        allowed = [phases[key] for key in ("DELIVERY_CLOSE_TRAVEL_WAIT", "DELIVERY_POSTCLOSE_MEASURING",
            "DELIVERY_WAIT_SELECTION", "DELIVERY_FINALIZING")]
        if phase not in allowed:
            raise error("interruption must belong to a post-close phase")
        measurement = values["postCloseMeasurementEventSequence"]
        if (phase == phases["DELIVERY_CLOSE_TRAVEL_WAIT"]) != (measurement == 0) or measurement >= values["mcuEventSequence"]:
            raise error("post-close interruption must retain the actual preceding measurement reference")
        return
    if name == "DELIVERY_CYCLE_ABORTED":
        reason = registry["enums"]["DeliveryCycleAbortReason"]["values"].get(values["abortReason"], values["abortReason"])
        if reason != registry["enums"]["DeliveryCycleAbortReason"]["values"]["UPDATE_STOPPED"] and values["openDispatched"]:
            raise error("a rejected/expired opening cannot have been dispatched")
        if (values["roundIndex"] == 1) != (values["selectionEventSequence"] == 0):
            raise error("only a first-round abort has no local selection cause")
        if values["selectionEventSequence"] >= values["mcuEventSequence"]:
            raise error("abort must follow its original selection cause")
        return
    if name not in ("DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT", "DELIVERY_LOCAL_DOOR_RESULT"):
        return
    if name == "DELIVERY_LOCAL_DOOR_RESULT" and values["selectionEventSequence"] >= values["mcuEventSequence"]:
        raise error("local door action must follow its saved selection event")

    def number(field, enum):
        return registry["enums"][enum]["values"].get(values[field], values[field])

    commands = registry["enums"]["DeliveryDoorCommand"]["values"]
    statuses = registry["enums"]["DoorCommandOutputStatus"]["values"]
    faults = registry["enums"]["FaultCode"]["values"]
    command = number("command", "DeliveryDoorCommand")
    status = number("outputStatus", "DoorCommandOutputStatus")
    fault = number("faultCode", "FaultCode")
    if number("physicalDoorStateBasis", "DoorPhysicalStateBasis") != registry["enums"]["DoorPhysicalStateBasis"]["values"]["NOT_OBSERVABLE"]:
        raise error("delivery-door physical state is not observable")
    if command == commands["NONE"] or status == statuses["NOT_DISPATCHED"]:
        raise error("NONE/NOT_DISPATCHED belong to snapshots, not output results")
    if status == statuses["OUTPUT_REJECTED"]:
        if fault not in (faults["DELIVERY_DOOR_OUTPUT_REJECTED"], faults["DELIVERY_DOOR_HIL_NOT_QUALIFIED"]):
            raise error("rejected door output has the wrong fault")
    elif fault != faults["NONE"]:
        raise error("dispatched/superseded/coalesced output must have no fault")
    if (name == "SAFE_CLOSE_RESULT" or status == statuses["COALESCED_WITH_EXISTING_CLOSE"]) and command != commands["CLOSE"]:
        raise error("safe/coalesced close must report CLOSE, not OPEN")
