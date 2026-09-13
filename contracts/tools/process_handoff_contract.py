"""Process custody rules. Shape validation is not freshness or authorization."""


def validate_process_handoff_fields(registry, name, values, error):
    if name == "PROCESS_EVENT_SAVED_REPLY":
        status = registry["enums"]["ResultSavedStatus"]["values"].get(values["status"], values["status"])
        if (status == 5) != (values["currentMcuBootId"] != values["mcuBootId"]):
            raise error("process saved status/current boot mismatch")
    if name not in ("QUERY_PROCESS_EVENT", "PROCESS_EVENT_QUERY_REPLY"):
        return
    def number(field, enum):
        return registry["enums"][enum]["values"].get(values[field], values[field])
    event = number("eventMessageType", "ProcessEventMessageType")
    work = number("workType", "WorkType")
    expected_work = {48: 2, 50: 2, 51: 2, 52: 3, 54: 3, 55: 3, 56: 3, 57: 4, 58: 5, 62: 3}[event]
    if work != expected_work or (values["stepSequence"] > 0) != (event in (48, 50, 51, 54, 55, 56, 62)):
        raise error("process query work type or measurement step mismatch")
    if name == "QUERY_PROCESS_EVENT":
        return
    status = number("status", "ResultQueryStatus")
    if (status == 5) != (values["currentMcuBootId"] != values["targetMcuBootId"]):
        raise error("process query status/current boot mismatch")
    if status in (1, 2):
        if values["mcuEventSequence"] == 0:
            raise error("retained process event requires its event sequence")
    else:
        digest = values["eventDigestSha256"]
        if values["mcuEventSequence"] != 0 or (digest if isinstance(digest, bytes) else bytes.fromhex(digest)) != bytes(32):
            raise error("missing process event must clear the reference")
