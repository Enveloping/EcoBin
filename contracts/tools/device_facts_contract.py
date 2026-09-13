"""Device-facts semantics embedded in the generated Python candidate."""


def validate_device_facts_fields(registry, name, values, error):
    if name != "DEVICE_FACTS_REPLY":
        return
    v = dict(values)
    spec = next(item for item in registry["messages"] if item["name"] == name)
    for field in spec["payload"]:
        key = field["name"]
        if field.get("enum") and isinstance(v[key], str):
            v[key] = registry["enums"][field["enum"]]["values"][v[key]]
        if field["type"] in ("sha256", "uuid"):
            value = v[key]
            v[key] = any(value) if isinstance(value, bytes) else any(bytes.fromhex(value.replace("-", "")))
    def require(condition):
        if not condition:
            raise error("inconsistent device facts")
    require((v["status"] == 2) == (v["currentMcuBootId"] != v["targetMcuBootId"]))
    body = [field["name"] for field in spec["payload"]][5:]
    if v["status"] != 1:
        require(not any(v[key] for key in body))
        return
    require(v["controlUptimeMs"] <= v["capturedUptimeMs"])
    if v["appliedConfigVersion"] == 0:
        require(not v["appliedContentSha256"] and not v["appliedMcuPayloadSha256"])
    else:
        require(v["appliedContentSha256"] and v["appliedMcuPayloadSha256"])
    target, active = v["lastDeliveryDoorCommand"], v["doorActionActive"]
    require(not active or target != 0)
    expected_pause = active and target == 2 and v["pb5Active"]
    require(v["pinchPaused"] == expected_pause)
    require(v["pb6Output"] == (active and target == 1))
    require(v["pb7Output"] == (active and target == 2 and not v["pb5Active"]))
    require(not v["updateLatched"] or not (active or v["cleanLockPowered"]))
    scale_fields = ("scaleAttemptSequence", "scaleCapturedUptimeMs", "scaleWeightGrams", "scaleCalibrationVersion")
    if v["scaleReadStatus"] == 0:
        require(not any(v[key] for key in scale_fields))
    else:
        require(v["scaleAttemptSequence"] > 0 and v["scaleCapturedUptimeMs"] <= v["capturedUptimeMs"])
        require(v["scaleReadStatus"] == 1 or v["scaleWeightGrams"] == 0)
    measurement = v["measurementState"]
    measurement_fields = [key for key in v if key.startswith("measurement") and key != "measurementState"]
    if measurement == 0:
        require(not any(v[key] for key in measurement_fields))
    else:
        require(v["measurementSequence"] > 0 and v["measurementConfigVersion"] > 0)
        require(v["measurementElapsedMs"] <= v["measurementObservedUptimeMs"] <= v["capturedUptimeMs"])
        if measurement in (2, 3):
            require(v["measurementSampleCount"] >= 5)
            require(measurement != 2 or v["measurementSpanGrams"] <= 100)
            require(measurement != 3 or v["measurementElapsedMs"] == 5000)
        else:
            require(v["measurementWeightGrams"] == 0 and v["measurementSpanGrams"] == 0)
            require(measurement != 1 or v["measurementElapsedMs"] == 0)
    require(v["smokeObservedUptimeMs"] <= v["capturedUptimeMs"])
    require(v["smokeObservationState"] != 0 or v["smokeObservedUptimeMs"] == 0)
    fullness = v["fullnessReadStatus"]
    kind = v["fullnessObservationKind"]
    require(v["fullnessCapturedUptimeMs"] <= v["capturedUptimeMs"])
    if fullness == 0:
        require(kind == 0 and v["fullnessCapturedUptimeMs"] == 0)
    else:
        require(kind in (1, 2))
    require((fullness == 1 and kind == 2) or not v["fullnessInfraredBlocked"])
    require((fullness == 1 and kind == 1) or v["fullnessDistanceMm"] == 0)
    retained = v["retainedWorkState"]
    if retained == 0:
        require(not any(v[key] for key in v if key.startswith("retained") and key != "retainedWorkState"))
    else:
        require(v["retainedWorkUid"] and v["retainedOriginCommandSequence"] > 0 and 1 <= v["retainedPortNo"] <= 6)
        require(v["retainedWorkType"] in (2, 3))
        prefix = "DELIVERY_" if v["retainedWorkType"] == 2 else "CLEAN_"
        phases = registry["enums"]["McuWorkPhase"]["values"]
        if retained == 1:
            require(v["retainedWorkPhase"] in [number for key, number in phases.items() if key.startswith(prefix) or key == "SAFETY_LOCKED"])
            require(v["retainedResultSequence"] == 0)
        else:
            require(v["retainedWorkPhase"] == phases[prefix + "FINALIZING"] and v["retainedResultSequence"] > 0)
