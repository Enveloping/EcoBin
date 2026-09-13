"""Original result/process/configuration reconciliation, not a business verdict.

Called inside the existing EdgeStore result/recovery transaction. All inputs
are already durable; this neither acknowledges UART nor creates cloud work.
MATCHED means evidence agrees, not measurement success, admission or settlement.
"""
from dataclasses import asdict

import uart2_protocol as uart
from mcu_action_evidence import accepted_command_witness, clean_unlock_action_key, executed_bundle
from mcu_configuration import NativeMcuConfiguration


def original_configuration(store, start_record, start):
    records = [row for row in store.list_native_commands()
        if row["mcu_boot_id"] == start["targetMcuBootId"]
        and row["command_sequence"] < start_record["command_sequence"]
        and row["message_name"] in {"CONFIG_BEGIN", "CONFIG_DEVICE_BLOCK", "CONFIG_PORT_BLOCK", "CONFIG_COMMIT"}]
    commits = [row for row in records if row["message_name"] == "CONFIG_COMMIT"
        and row["write_claimed"] and row["decision_outcome"] != "REJECTED"]
    if not commits:
        return None
    commit = max(commits, key=lambda row: row["command_sequence"])
    last = uart.decode_payload("CONFIG_COMMIT", commit["payload"])
    parts = []
    for record in records:
        value = uart.decode_payload(record["message_name"], record["payload"])
        if record["command_sequence"] > commit["command_sequence"] or value["applicationUid"] != last["applicationUid"]:
            continue
        if accepted_command_witness(store, record) is None:
            return None
        parts.append((record["message_name"], bytes(record["payload"])))
    if len(parts) < last["partCount"]:
        return None
    candidate = NativeMcuConfiguration.from_parts(parts)
    if last["configVersion"] != start["configVersion"] or last["contentSha256"] != start["configContentSha256"]:
        raise ValueError("native result configuration differs from original START")
    port = next((uart.decode_payload(name, raw) for name, raw in parts
        if name == "CONFIG_PORT_BLOCK" and uart.decode_payload(name, raw)["portNo"] == start["portNo"]), None)
    if port is None or not port["enabled"]:
        raise ValueError("native result configuration has no enabled original port")
    return dict(configVersion=last["configVersion"], contentSha256=last["contentSha256"],
        mcuPayloadSha256=candidate.mcu_payload_sha256, applicationUid=last["applicationUid"],
        port=port, parts=tuple(parts))


def measurement(store, result, start, role):
    if result[role + "Kind"] in {"NOT_TAKEN", "MCU_RESET_LOST"}:
        return None, None
    clean = result["workType"] == "CLEAN_OPERATION"
    name = (("WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY") if role == "initial"
        else ("CLEAN_FINAL_WEIGHT_READY" if clean else "WORK_POSTCLOSE_WEIGHT_READY"))
    step = (0 if clean else 1) if role == "initial" else result["cleanActionSequence" if clean else "deliveryRoundCount"]
    scope = {key: start[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence", "portNo")}
    scope.update(queryId=1, workUid=result["workUid"], workType=result["workType"],
        eventMessageType=name, stepSequence=step, configVersion=result["configVersion"])
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    # The original business scope comes first; a matching bare measurement row
    # cannot substitute for custody of this actual work/step.
    row = store.get_native_process_receipt(raw_scope)
    if row is None:
        return None, dict(role=role, messageName=name, scope=raw_scope,
            mcuBootId=result[role + "SourceMcuBootId"], mcuEventSequence=result[role + "McuEventSequence"],
            measurementUid=result[role + "MeasurementUid"])
    value = uart.decode_payload(name, row["payload"])
    fields = {"Kind": "measurementKind", "MeasurementUid": "measurementUid", "SourceMcuBootId": "mcuBootId",
        "McuEventSequence": "mcuEventSequence", "WeightGrams": "reportedWeightGrams", "ElapsedMs": "measurementElapsedMs",
        "SampleCount": "sampleCount", "SpanGrams": "sampleSpanGrams", "CalibrationVersion": "calibrationVersion", "FaultCode": "faultCode"}
    if any(result[role + suffix] != value[field] for suffix, field in fields.items()):
        raise ValueError("native result measurement contradicts original process custody")
    if value["uptimeMs"] > result["completedUptimeMs"]:
        raise ValueError("native result predates its original process measurement")
    return dict(messageName=name, payload=bytes(row["payload"]), scope=raw_scope), None


def completion(store, result, final):
    clean = result["workType"] == "CLEAN_OPERATION"
    required = result["physicalCloseConfirmed"] if clean else result["finishReason"] in {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED"}
    if required and result["finalKind"] in {"NOT_TAKEN", "MCU_RESET_LOST"}:
        raise ValueError("native result completion requires an original final measurement")
    if required and not clean and result["finalKind"] not in {"STABLE_MEAN", "TIMEOUT_MEDIAN"}:
        raise ValueError("native delivery completion requires an available original final measurement")
    # Its typed getter validates predecessor custody too. Defer that dependent
    # read while final measurement custody is missing; never substitute a bare
    # weight or fabricate a terminal event sequence absent from WORK_RESULT.
    if final is None:
        return None, None
    name = "CLEAN_COMPLETION_CONFIRMED" if clean else "DELIVERY_SELECTION"
    scope = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + final["scope"])
    scope["eventMessageType"] = name
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    if clean and required:
        request_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope | {"eventMessageType": "CLEAN_FINISH_REQUESTED"})[8:]
        if store.get_native_process_receipt(request_scope) is None:
            return None, dict(role="completionRequest", messageName="CLEAN_FINISH_REQUESTED",
                scope=request_scope, mcuBootId=result["mcuBootId"])
    row = store.get_native_process_receipt(raw_scope)
    if row is None:
        missing = dict(role="completion", messageName=name, scope=raw_scope,
            mcuBootId=result["mcuBootId"], measurementUid=result["finalMeasurementUid"]) if required else None
        return None, missing
    value = uart.decode_payload(name, row["payload"])
    selected = {"DELIVERY_END": "END", "DELIVERY_WINDOW_EXPIRED": "WINDOW_EXPIRED"}.get(result["finishReason"])
    if ((clean and not result["physicalCloseConfirmed"])
            or (not clean and selected is not None and value["selection"] != selected)):
        raise ValueError("native result completion contradicts original process custody")
    weight = uart.decode_payload(final["messageName"], final["payload"])
    phase_end = max(weight["uptimeMs"], weight["fullnessCompletedUptimeMs"])
    if not phase_end <= value["uptimeMs"] <= result["completedUptimeMs"]:
        raise ValueError("native result completion falls outside its original phase")
    return dict(messageName=name, payload=bytes(row["payload"]), scope=raw_scope), None


def process_scope(start, result, name, step):
    value = {key: start[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence", "portNo", "configVersion")}
    value.update(queryId=1, workUid=result["workUid"], workType=result["workType"],
        eventMessageType=name, stepSequence=step)
    return uart.encode_payload("QUERY_PROCESS_EVENT", value)[8:]


def ordered_clean_intent(previous, row, result):
    value = uart.decode_payload(row["message_name"], row["payload"])
    if (value["uptimeMs"] > result["completedUptimeMs"] or (previous is not None and (
            value["mcuEventSequence"] <= previous["mcuEventSequence"] or value["uptimeMs"] < previous["uptimeMs"]))):
        raise ValueError("native clean button chronology contradicts original work")
    return value


def clean_reopen_outputs(store, permit, start, result, initial):
    """Expected pulse identities come from the original contiguous button steps."""
    expected, missing = [], []
    for record in store.list_native_commands():
        if record["message_name"] != "UNLOCK_CLEAN_DOOR" or not record["write_claimed"] or record["decision_outcome"] == "REJECTED":
            continue
        grant = uart.decode_payload("UNLOCK_CLEAN_DOOR", record["payload"])
        if grant["operationUid"] != result["workUid"] or grant["parentCommandUid"] != start["mcuCommandUid"] or not grant["cleanActionSequence"]:
            continue
        if grant["cleanActionSequence"] >= result["cleanActionSequence"]:
            raise ValueError("native result precedes its later clean unlock")
    # Losing both command and outputs cannot erase a previously requested reopen.
    previous = uart.decode_payload(initial["messageName"], initial["payload"]) if initial else None
    for step in range(1, result["cleanActionSequence"]):
        alternatives = [dict(messageName=kind, scope=process_scope(start, result, kind, step))
            for kind in ("CLEAN_UNLOCK_REQUESTED", "CLEAN_FINISH_REQUESTED")]
        intents = [store.get_native_process_receipt(item["scope"]) for item in alternatives]
        if not any(intents):
            missing.append(dict(role="actionCause", alternatives=alternatives,
                mcuBootId=result["mcuBootId"], stepSequence=step))
            continue
        if all(intents):
            raise ValueError("native clean step has contradictory original button intents")
        previous = ordered_clean_intent(previous, intents[0] or intents[1], result)
        if intents[0] is None:
            continue
        key = clean_unlock_action_key(dict(targetMcuBootId=result["mcuBootId"],
            parentCommandUid=start["mcuCommandUid"], recoveryGeneration=0, cleanActionSequence=step))
        binding = store.get_native_action_by_key(result["workUid"], key)
        if binding is None:
            missing.append(dict(role="actionBinding", workUid=result["workUid"], actionKey=key))
        else:
            if binding["permit"] != permit:
                raise ValueError("native result reopen differs from original permit")
            expected.append(("CLEAN_LOCK_POWER_CHANGED", binding["action"].action_uid, step))
    last_intent = store.get_native_process_receipt(process_scope(
        start, result, "CLEAN_FINISH_REQUESTED", result["cleanActionSequence"]))
    if last_intent is not None:
        ordered_clean_intent(previous, last_intent, result)
    return expected, missing


def terminal_weight_failure_candidate(result):
    """A typed timeout candidate still requires the complete original output cycle."""
    return (result["workType"] == "DELIVERY_SESSION" and result["finishReason"] == "FAILED"
        and result["deliveryRoundCount"] > 0 and result["initialKind"] in {"STABLE_MEAN", "TIMEOUT_MEDIAN"}
        and result["finalKind"] == "UNAVAILABLE" and result["finalFaultCode"] == "WEIGHT_TIMEOUT"
        and result["finalElapsedMs"] == 5000 and 0 <= result["finalSampleCount"] < 5)


def execution(store, permit, start_record, start, result, initial, final):
    """Read original command bindings; never confirm an effect or send a command."""
    acceptance = accepted_command_witness(store, start_record)
    missing = [] if acceptance is not None else [dict(role="startAcceptance", commandUid=start_record["command_uid"])]
    events = store.list_native_work_actuator_events(result["workUid"])
    commands = {}
    first_bundle = None
    clean = result["workType"] == "CLEAN_OPERATION"
    requires_cycle = (result["finishReason"] in {"DELIVERY_END", "DELIVERY_WINDOW_EXPIRED", "CLEAN_CONFIRMED"}
        or terminal_weight_failure_candidate(result))
    command_name = "UNLOCK_CLEAN_DOOR" if clean else "AUTHORIZE_DELIVERY_FIRST_OPEN"
    for event in events:
        value = uart.decode_payload(event["message_name"], event["payload"])
        if (value["mcuBootId"] != result["mcuBootId"] or value["portNo"] != result["portNo"]
                or value.get("operationUid" if clean else "sessionUid") != result["workUid"]
                or value["uptimeMs"] > result["completedUptimeMs"]):
            raise ValueError("native result output differs from original work or completion time")
        if requires_cycle and not clean and value.get("roundIndex", 0) > result["deliveryRoundCount"]:
            raise ValueError("native result omits an original output round")
        uid = value["mcuCommandUid"]
        if uid in commands:
            continue
        record = store.get_native_command(uid)
        binding = store.get_native_action_binding(uid)
        if record is not None and (not record["write_claimed"] or record["decision_outcome"] == "REJECTED"):
            raise ValueError("native result output contradicts original authorization dispatch")
        if record is None or binding is None:
            missing.append(dict(role="actionBinding", commandUid=uid))
            commands[uid] = None
            continue
        if record["message_name"] != command_name or binding["permit"] != permit:
            raise ValueError("native result output differs from original action permit")
        grant = uart.decode_payload(command_name, record["payload"])
        parent = "parentCommandUid" if clean else "parentStartCommandUid"
        if (grant[parent] != start_record["command_uid"] or grant["targetMcuBootId"] != result["mcuBootId"]
                or grant["portNo"] != result["portNo"] or grant["commandSequence"] <= start["commandSequence"]
                or (clean and grant["recoveryGeneration"] != 0)):
            raise ValueError("native result output authorization differs from original START")
        witness = accepted_command_witness(store, record)
        if witness is None:
            missing.append(dict(role="actionAcceptance", commandUid=uid))
        commands[uid] = dict(messageName=command_name, payload=bytes(record["payload"]),
            binding=dict(permit=asdict(binding["permit"]), action=asdict(binding["action"]),
                command_payload=binding["command_payload"]), acceptance=witness)
    if requires_cycle:
        first_key = "clean:first-unlock" if clean else "delivery:first-open"
        first = store.get_native_action_by_key(result["workUid"], first_key)
        if first is None:
            missing.append(dict(role="actionBinding", workUid=result["workUid"], actionKey=first_key))
        else:
            if first["permit"] != permit:
                raise ValueError("native result first action differs from original permit")
            name = "CLEAN_LOCK_POWER_CHANGED" if clean else "DELIVERY_DOOR_COMMAND_RESULT"
            uid = first["action"].action_uid
            outputs = [event for event in events if event["message_name"] == name
                and event["reported_command_uid"] == uid]
            if len(outputs) < 2:
                missing.append(dict(role="actuatorOutput", messageName=name, commandUid=uid,
                    mcuBootId=result["mcuBootId"]))
            elif len(outputs) != 2:
                raise ValueError("native result repeats an original action output")
            elif initial is not None and not missing:
                first_bundle = executed_bundle(store, uid)
                if first_bundle is None:
                    raise ValueError("native result contradicts original output cycle")
            if clean:
                expected, clean_missing = clean_reopen_outputs(store, permit, start, result, initial)
                missing.extend(clean_missing)
            else:
                expected = [("DELIVERY_LOCAL_DOOR_RESULT", uid, step) for step in range(2, result["deliveryRoundCount"] + 1)]
            for name, action_uid, step in expected:
                outputs = [event for event in events if event["message_name"] == name
                    and event["reported_command_uid"] == action_uid
                    and (clean or uart.decode_payload(name, event["payload"])["roundIndex"] == step)]
                if len(outputs) < 2:
                    missing.append(dict(role="actuatorOutput", messageName=name, commandUid=action_uid,
                        mcuBootId=result["mcuBootId"], stepSequence=step))
                elif len(outputs) != 2:
                    raise ValueError("native result repeats a later action output")
                else:
                    if not clean and store.get_native_process_receipt(
                            process_scope(start, result, "WORK_POSTCLOSE_WEIGHT_READY", step - 1)) is None:
                        # Phase validation below reports the missing predecessor.
                        # A dependent selection cannot be accepted without it.
                        continue
                    cause_name = "CLEAN_UNLOCK_REQUESTED" if clean else "DELIVERY_SELECTION"
                    scope = process_scope(start, result, cause_name, step if clean else step - 1)
                    cause = store.get_native_process_receipt(scope)
                    if cause is None:
                        missing.append(dict(role="actionCause", messageName=cause_name, scope=scope,
                            mcuBootId=result["mcuBootId"]))
                    elif first_bundle is not None and not missing:
                        if clean:
                            if executed_bundle(store, action_uid) is None:
                                raise ValueError("native result contradicts original reopen output cycle")
                        else:
                            selected = uart.decode_payload(cause_name, cause["payload"])
                            opened, closed = [uart.decode_payload(name, item["payload"]) for item in outputs]
                            if (selected["selection"] != "CONTINUE"
                                    or any(item["selectionEventSequence"] != selected["mcuEventSequence"] for item in (opened, closed))
                                    or not selected["mcuEventSequence"] < opened["mcuEventSequence"] < closed["mcuEventSequence"]
                                    or not selected["uptimeMs"] <= opened["uptimeMs"] <= closed["uptimeMs"]
                                    or closed["uptimeMs"] - opened["uptimeMs"] < start["deliveryAutoCloseMs"]
                                    or opened["command"] != "OPEN" or closed["command"] != "CLOSE"
                                    or any(item["outputStatus"] != "COMMAND_DISPATCHED" or item["faultCode"] != "NONE" for item in (opened, closed))):
                                raise ValueError("native local output contradicts its original CONTINUE cycle")
            for event in events:
                name = event["message_name"]
                value = uart.decode_payload(name, event["payload"])
                if clean:
                    if name != "CLEAN_LOCK_POWER_CHANGED" or value["lockPowerState"] != "DEENERGIZED" or final is None:
                        continue
                    weight = uart.decode_payload(final["messageName"], final["payload"])
                else:
                    if name not in {"DELIVERY_DOOR_COMMAND_RESULT", "DELIVERY_LOCAL_DOOR_RESULT"} or value["command"] != "CLOSE":
                        continue
                    scope = process_scope(start, result, "WORK_POSTCLOSE_WEIGHT_READY", value["roundIndex"])
                    row = store.get_native_process_receipt(scope)
                    if row is None:
                        # The final slot already contributes its precise missing descriptor.
                        if value["roundIndex"] != result["deliveryRoundCount"]:
                            missing.append(dict(role="actionMeasurement", messageName="WORK_POSTCLOSE_WEIGHT_READY",
                                scope=scope, mcuBootId=result["mcuBootId"]))
                        continue
                    weight = uart.decode_payload("WORK_POSTCLOSE_WEIGHT_READY", row["payload"])
                if (value["mcuEventSequence"] >= weight["mcuEventSequence"]
                        or value["uptimeMs"] > weight["uptimeMs"] - weight["measurementElapsedMs"]):
                    raise ValueError("native result measurement predates its original close or lock-off phase")
    return dict(startAcceptance=acceptance, commands=[item for item in commands.values() if item is not None],
        events=events, firstAction=first_bundle), missing


def reconcile(store, start_record, start, result, permit):
    """Late complete results remain complete even while their evidence is missing."""
    initial, first_missing = measurement(store, result, start, "initial")
    final, last_missing = measurement(store, result, start, "final")
    config = original_configuration(store, start_record, start)
    if config is not None:
        for source in (initial, final):
            if source is not None and uart.decode_payload(source["messageName"], source["payload"])["calibrationVersion"] != config["port"]["calibrationVersion"]:
                raise ValueError("native measurement calibration differs from original configuration")
    missing = [item for item in (first_missing, last_missing) if item is not None]
    evidence = dict(state="MATCHED", initial=initial, final=final, configuration=config,
        finalFullness=None, completion=None, missing=missing)
    if final is not None:
        value = uart.decode_payload(final["messageName"], final["payload"])
        evidence["finalFullness"] = {field["name"]: value[field["name"]]
            for field in uart.REGISTRY["fieldGroups"]["workFullnessEvidence"]}
        if value["workFullnessStatus"] != "NOT_SAMPLED":
            if value["fullnessConfigContentSha256"] != start["configContentSha256"]:
                raise ValueError("native fullness evidence differs from original configuration")
            if (value["fullnessStartedUptimeMs"] < value["uptimeMs"] - value["measurementElapsedMs"]
                    or value["fullnessCompletedUptimeMs"] > result["completedUptimeMs"]):
                raise ValueError("native fullness evidence falls outside its original phase")
            if config is not None:
                expected = {"fullnessMcuPayloadSha256": config["mcuPayloadSha256"],
                    "fullnessDistanceThresholdMm": config["port"]["fullnessDistanceThresholdMm"],
                    "fullnessMinimumValidSampleCount": config["port"]["fullnessMinimumValidSampleCount"],
                    "fullnessRequestedSampleCount": config["port"]["fullnessSampleCount"],
                    "workFullnessSensorKind": config["port"]["fullnessSensorKind"]}
                if any(value[key] != wanted for key, wanted in expected.items()):
                    raise ValueError("native fullness evidence differs from original configuration")
    evidence["completion"], terminal_missing = completion(store, result, final)
    if terminal_missing is not None:
        missing.append(terminal_missing)
    evidence["execution"], execution_missing = execution(store, permit, start_record, start, result, initial, final)
    if missing:
        evidence["state"] = "WAITING_FOR_PROCESS_CUSTODY"
    elif config is None:
        evidence["state"] = "WAITING_FOR_CONFIGURATION_CUSTODY"
    elif any(item["role"] in {"actionCause", "actionMeasurement"} for item in execution_missing):
        evidence["state"] = "WAITING_FOR_PROCESS_CUSTODY"
    elif execution_missing:
        evidence["state"] = ("WAITING_FOR_ACTUATOR_CUSTODY"
            if any(item["role"] == "actuatorOutput" for item in execution_missing) else "WAITING_FOR_COMMAND_CUSTODY")
    missing.extend(execution_missing)
    return evidence
