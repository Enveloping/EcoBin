"""Exact actuator data custody, never physical or business authorization."""


def validate_actuator_handoff_fields(registry, name, values, error):
    if name == "ACTUATOR_EVENT_SAVED_REPLY":
        status = registry["enums"]["ResultSavedStatus"]["values"].get(values["status"], values["status"])
        if (status == 5) != (values["currentMcuBootId"] != values["mcuBootId"]):
            raise error("actuator saved status/current boot mismatch")
    if name != "ACTUATOR_EVENT_QUERY_REPLY":
        return
    status = registry["enums"]["ActuatorEventQueryStatus"]["values"].get(values["status"], values["status"])
    event = registry["enums"]["ActuatorEventReferenceType"]["values"].get(values["eventMessageType"], values["eventMessageType"])
    if (status == 5) != (values["currentMcuBootId"] != values["targetMcuBootId"]):
        raise error("actuator query status/current boot mismatch")
    if status == 1:
        if event == 0 or values["mcuEventSequence"] <= values["afterMcuEventSequence"]:
            raise error("held actuator event must follow cursor and name its type")
    else:
        digest = values["eventDigestSha256"]
        if event != 0 or values["mcuEventSequence"] != 0 or (digest if isinstance(digest, bytes) else bytes.fromhex(digest)) != bytes(32):
            raise error("missing actuator event must clear the reference")
