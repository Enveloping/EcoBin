"""Reference rules embedded in generated Python; no runtime dependencies."""
import uuid


def validate_result_fields(registry, values, error):
    def number(key, enum):
        value = values[key]
        return registry["enums"][enum]["values"].get(value, value)

    work = number("workType", "WorkType")
    reason = number("finishReason", "WorkResultFinishReason")
    delivery = registry["enums"]["WorkType"]["values"]["DELIVERY_SESSION"]
    clean = registry["enums"]["WorkType"]["values"]["CLEAN_OPERATION"]
    if work not in (delivery, clean):
        raise error("WORK_RESULT supports delivery/clean only")
    if work == delivery and (reason == 3 or values["cleanActionSequence"] != 0 or values["physicalCloseConfirmed"]):
        raise error("delivery result contains clean-only facts")
    if work == clean and (reason in (1, 2) or values["deliveryRoundCount"] != 0 or values["negativeWeightAnomaly"]):
        raise error("clean result contains delivery-only facts")
    if reason in (1, 2) and values["deliveryRoundCount"] == 0:
        raise error("completed delivery needs a round")
    if reason == 3 and (not values["physicalCloseConfirmed"] or values["cleanActionSequence"] == 0):
        raise error("clean completion needs cleaner confirmation and action sequence")
    for prefix in ("initial", "final"):
        kind = number(prefix + "Kind", "ResultMeasurementKind")
        interrupted = registry["enums"]["ResultMeasurementKind"]["values"]["INTERRUPTED"]
        cause = registry["enums"]["FaultCode"]["values"]["MEASUREMENT_INTERRUPTED"]
        if (kind == interrupted) != (number(prefix + "FaultCode", "FaultCode") == cause):
            raise error("measurement interruption kind and cause must agree")
        if kind == interrupted and reason not in (4, 5):
            raise error("interrupted measurement requires a failed or cancelled result")
        uid = uuid.UUID(str(values[prefix + "MeasurementUid"])).int
        if kind in (0, 1):
            keys = ("SourceMcuBootId", "McuEventSequence", "WeightGrams", "ElapsedMs", "SampleCount", "SpanGrams", "CalibrationVersion")
            if uid != 0 or any(values[prefix + key] != 0 for key in keys) or number(prefix + "FaultCode", "FaultCode") != 0:
                raise error("missing measurement must use zero slots, not a measured zero")
        else:
            if uid == 0 or values[prefix + "SourceMcuBootId"] != values["mcuBootId"] or values[prefix + "McuEventSequence"] == 0:
                raise error("measurement identity must name this MCU boot")
            valid = kind in (2, 3)
            if valid and (values[prefix + "SampleCount"] < 5 or number(prefix + "FaultCode", "FaultCode") != 0):
                raise error("valid measurement needs five samples and no fault")
            if kind == 2 and values[prefix + "SpanGrams"] > 100:
                raise error("stable window span exceeds 100 grams")
            if kind == 3 and values[prefix + "ElapsedMs"] != 5000:
                raise error("median is the five-second timeout fallback")
            if not valid and values[prefix + "WeightGrams"] != 0:
                raise error("unavailable measurement cannot contain a business weight")
    if number("initialKind", "ResultMeasurementKind") > 1 and number("finalKind", "ResultMeasurementKind") > 1:
        if uuid.UUID(str(values["initialMeasurementUid"])) == uuid.UUID(str(values["finalMeasurementUid"])) or values["initialMcuEventSequence"] >= values["finalMcuEventSequence"]:
            raise error("initial/final measurements must be distinct and ordered")
