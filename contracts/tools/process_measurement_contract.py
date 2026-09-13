"""Native process measurement rules, also embedded in the generated Python codec."""


def validate_process_measurement_fields(registry, message_name, values, error):
    if message_name not in registry["sessionPolicy"]["processMeasurementMessages"]:
        return
    kind = registry["enums"]["ResultMeasurementKind"]["values"].get(
        values["measurementKind"], values["measurementKind"])
    fault = registry["enums"]["FaultCode"]["values"].get(values["faultCode"], values["faultCode"])
    interrupted = registry["enums"]["ResultMeasurementKind"]["values"]["INTERRUPTED"]
    cause = registry["enums"]["FaultCode"]["values"]["MEASUREMENT_INTERRUPTED"]
    if (kind == interrupted) != (fault == cause):
        raise error("measurement interruption kind and cause must agree")
    if kind < 2:
        raise error("process event must describe an actual terminal measurement, not a missing slot")
    if values["measurementElapsedMs"] > values["uptimeMs"]:
        raise error("measurement cannot start before this MCU boot")
    if kind in (2, 3) and (values["sampleCount"] < 5 or fault != 0):
        raise error("available measurement needs five samples and no fault")
    if kind == 2 and values["sampleSpanGrams"] > 100:
        raise error("stable window span exceeds 100 grams")
    if kind == 3 and values["measurementElapsedMs"] != 5000:
        raise error("median is the five-second timeout fallback")
    if kind > 3 and values["reportedWeightGrams"] != 0:
        raise error("unavailable measurement cannot contain a business weight")
    validate_work_fullness_fields(registry, values, error)


def validate_work_fullness_fields(registry, values, error):
    if "workFullnessStatus" not in values:
        return

    def enum(key, name):
        return registry["enums"][name]["values"].get(values[key], values[key])

    def nonzero_digest(key):
        raw = values[key]
        return any(bytes.fromhex(raw) if isinstance(raw, str) else raw)

    status = enum("workFullnessStatus", "WorkFullnessStatus")
    reason = enum("fullnessStopReason", "FullnessStopReason")
    kind = enum("workFullnessSensorKind", "FullnessObservationKind")
    value = enum("workFullnessSensorValue", "WorkFullnessValue")
    basis = enum("workFullnessBasis", "WorkFullnessBasis")
    if status == 0:
        for field in registry["fieldGroups"]["workFullnessEvidence"]:
            key = field["name"]
            occupied = (enum(key, field["enum"]) if "enum" in field else
                        nonzero_digest(key) if field["type"] == "sha256" else values[key])
            if occupied:
                raise error("unobserved work fullness must have an empty evidence slot")
        return
    requested, completed, valid, minimum = (values[key] for key in (
        "fullnessRequestedSampleCount", "fullnessCompletedSampleCount", "fullnessValidSampleCount", "fullnessMinimumValidSampleCount"))
    if (values["fullnessGroupSequence"] == 0 or kind != 1
            or not nonzero_digest("fullnessConfigContentSha256") or not nonzero_digest("fullnessMcuPayloadSha256")
            or requested < 3 or minimum == 0 or minimum > requested or completed > requested or valid > completed
            or values["fullnessDistanceThresholdMm"] == 0):
        raise error("work fullness identity, original policy or sample counts are inconsistent")
    start, end, last = (values[key] for key in (
        "fullnessStartedUptimeMs", "fullnessCompletedUptimeMs", "fullnessLastCapturedUptimeMs"))
    if end < start or (completed == 0 and last != 0) or (completed > 0 and not start <= last <= end):
        raise error("work fullness observation times are inconsistent")
    present, distance = values["fullnessDistancePresent"], values["fullnessDistanceMm"]
    if status == 2:
        if reason == 0 or value != 0 or basis != 0 or present or distance != 0:
            raise error("interrupted work fullness cannot claim a sensor decision or fallback")
        return
    if reason != 0 or completed != requested:
        raise error("complete work fullness requires all original attempts and no stop reason")
    if valid >= minimum:
        expected_value = 2 if distance < values["fullnessDistanceThresholdMm"] else 1
        if basis != 1 or not present or value != expected_value:
            raise error("measured fullness median must match original threshold and valid count")
    elif basis != (2 if valid == 0 else 3) or value != 1 or present or distance != 0:
        raise error("work fullness clear fallback must preserve missing/insufficient samples without a distance")
