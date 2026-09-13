"""Original-work query rules embedded in the generated Python candidate."""


def validate_work_query_fields(registry, name, values, error):
    if name not in ("QUERY_WORK", "WORK_QUERY_REPLY"):
        return
    def symbol(field, enum):
        value = values[field]
        choices = registry["enums"][enum]["values"]
        return next((key for key, number in choices.items() if number == value), value)
    work = symbol("workType", "WorkType")
    if work not in ("DELIVERY_SESSION", "CLEAN_OPERATION"):
        raise error("work query supports delivery/clean only")
    if name == "QUERY_WORK":
        return
    status = symbol("status", "WorkQueryStatus")
    phase = symbol("phase", "McuWorkPhase")
    if (status == "BOOT_MISMATCH") != (values["currentMcuBootId"] != values["targetMcuBootId"]):
        raise error("work query status/current boot mismatch")
    prefix = "DELIVERY_" if work == "DELIVERY_SESSION" else "CLEAN_"
    has_result = status in ("RESULT_HELD", "RESULT_RELEASED")
    if has_result:
        if phase != prefix + "FINALIZING" or values["resultSequence"] == 0:
            raise error("completed work needs finalizing phase and result identity")
    else:
        digest = values["resultDigestSha256"]
        if values["resultSequence"] != 0 or bytes.fromhex(digest.hex() if isinstance(digest, bytes) else digest) != bytes(32):
            raise error("work without a result must clear result reference slots")
        if status == "RUNNING":
            if not phase.startswith(prefix) and phase != "SAFETY_LOCKED":
                raise error("running work phase differs from work type")
        elif phase != "IDLE":
            raise error("unavailable work details must use the empty phase slot")
