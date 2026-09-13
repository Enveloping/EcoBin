"""Native command/action evidence, not physical position or business completion."""
from dataclasses import asdict
import hashlib
import json
import uuid

from job_safety import JobPermit, PermanentJobSafety, PhysicalAction, action_digest
import uart2_protocol as uart


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def clean_unlock_action_key(grant):
    """One logical ledger slot per original ordinary/recovery clean step.

    Work UID is already part of the store's unique key. Including original
    boot/start/recovery avoids relabelling a later generation as an old button.
    """
    if grant["cleanActionSequence"] == 0 and grant["recoveryGeneration"] == 0:
        return "clean:first-unlock"
    return (f"native:clean-unlock:{grant['targetMcuBootId']}:{grant['parentCommandUid']}:"
        f"{grant['recoveryGeneration']}:{grant['cleanActionSequence']}")


def binding_values(record, permit, action):
    if not isinstance(permit, JobPermit) or not isinstance(action, PhysicalAction):
        raise ValueError("native action requires typed original permit and action")
    for text in (permit.permit_uid, permit.work_uid, permit.command_uid, action.action_uid, action.receipt_uid):
        parsed = uuid.UUID(text)
        if parsed.version != 4 or str(parsed) != text:
            raise ValueError("native action binding requires canonical UUIDv4 identities")
    for digest in (permit.request_digest_sha256, action.action_digest_sha256):
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("native action binding requires full SHA-256 digests")
    if not isinstance(action.action_key, str) or not action.action_key.strip() or len(action.action_key) > 160:
        raise ValueError("native action requires a bounded logical key")
    if record is None:
        raise ValueError("unknown native command")
    values = uart.decode_payload(record["message_name"], record["payload"])
    if record["message_name"] == "UNLOCK_CLEAN_DOOR" and action.action_key != clean_unlock_action_key(values):
        raise ValueError("native clean logical action identity conflict")
    key = {"DELIVERY": "sessionUid", "CLEAN": "operationUid", "FULLNESS": "detectionUid", "BASELINE": "measurementUid"}.get(permit.work_type)
    expected = action_digest(work_uid=permit.work_uid, command_uid=permit.command_uid, action_key=action.action_key,
        action_kind=record["message_name"], payload={"nativeUartPayloadHex": record["payload"].hex()})
    if (key is None or values.get(key) != permit.work_uid or action.action_uid != record["command_uid"]
        or action.action_kind != record["message_name"] or action.action_digest_sha256 != expected):
        raise ValueError("native command and permanent action binding mismatch")
    return dict(action_uid=action.action_uid, receipt_uid=action.receipt_uid, work_uid=permit.work_uid,
        platform_command_uid=permit.command_uid, action_key=action.action_key,
        permit_json=canonical(asdict(permit)), action_json=canonical(asdict(action)), command_payload=record["payload"])


def decode_binding(row, record):
    permit = JobPermit(**json.loads(row["permit_json"]))
    action = PhysicalAction(**json.loads(row["action_json"]))
    expected = binding_values(record, permit, action)
    if any(row[key] != value for key, value in expected.items()):
        raise ValueError("native action binding is corrupt")
    return {"permit": permit, "action": action, "command_payload": expected["command_payload"]}


def _wire(record):
    return {"messageName": record["message_name"], "payloadHex": record["payload"].hex()}


def accepted_command_witness(store, record, pinned=None):
    """Decision cache alone is insufficient; retain the original wire witness."""
    if record is None or record["conflict"]:
        raise ValueError("native original command missing or conflicted")
    if not record["write_claimed"] or record["decision_outcome"] != "ACCEPTED":
        return None
    witnesses = []
    for row in store.list_native_command_observations(record["command_uid"]):
        name, raw = row["message_name"], row["payload"]
        if name not in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}:
            raise ValueError("invalid native decision evidence")
        value = uart.decode_payload(name, raw)
        offset = 8 if name == "COMMAND_QUERY_RESULT" else 0
        if (raw[offset:offset + 60] != record["payload"][:60]
                or row["outcome"] != value["outcome"] or row["error_code"] != value["errorCode"]
                or row["current_boot_id"] != value["currentMcuBootId"]
                or row["highest_sequence"] != value.get("highestCommandSequence", -1)):
            raise ValueError("native decision evidence is corrupt")
        # NOT_SEEN before delayed reception is legal. The store latches a
        # conflict if NOT_SEEN arrives AFTER an accepted/rejected decision.
        if value["outcome"] in {"REJECTED", "IDENTITY_CONFLICT"}:
            raise ValueError("native original acceptance contradicted")
        if value["outcome"] == "ACCEPTED":
            witnesses.append(_wire(row))
    if record["decision_error"] != "NONE":
        raise ValueError("native accepted command has an error")
    if pinned is not None:
        if pinned not in witnesses:
            raise ValueError("native pinned acceptance witness missing")
        return pinned
    return min(witnesses, key=canonical) if witnesses else None


def confirmed_first_clean_unlock(store, permit, record):
    """An ordinary reopen retains its first pulse's original permit/config.

    Caller holds a SQLite snapshot. Only historical consistency is checked
    here; the dispatch gate still owns current readiness/deadline checks.
    """
    if record["message_name"] != "UNLOCK_CLEAN_DOOR":
        return None
    grant = uart.decode_payload("UNLOCK_CLEAN_DOOR", record["payload"])
    if grant["cleanActionSequence"] == 0 or grant["recoveryGeneration"] != 0:
        return None
    first = store.get_native_action_by_key(permit.work_uid, "clean:first-unlock")
    if first is None or first["permit"] != permit:
        raise ValueError("native first clean unlock is not confirmed for this permit")
    uid = first["action"].action_uid
    proof = store.get_native_action_confirmation(uid)
    if proof is None or proof["state"] != "CONFIRMED":
        raise ValueError("native first clean unlock is not confirmed")
    original = store.get_native_command(uid)
    if original["message_name"] != "UNLOCK_CLEAN_DOOR":
        raise ValueError("native first clean unlock has wrong kind")
    first_grant = uart.decode_payload("UNLOCK_CLEAN_DOOR", original["payload"])
    if (first_grant["cleanActionSequence"] != 0 or first_grant["recoveryGeneration"] != 0
            or first_grant["commandSequence"] >= grant["commandSequence"]
            or any(first_grant[key] != grant[key] for key in
                ("targetMcuBootId", "operationUid", "parentCommandUid", "portNo", "unlockPulseMs"))):
        raise ValueError("native reopen differs from first clean unlock")
    bundle = executed_bundle(store, uid, json.loads(proof["bundle_json"]))
    if bundle is None or canonical(bundle) != proof["bundle_json"]:
        raise ValueError("native first clean unlock proof lost original evidence")
    return {"actionUid": uid, "evidenceSha256": proof["evidence_sha256"], "command": _wire(original)}


def saved_clean_reopen_intent(store, grant):
    """Look up the exact original START/config/step, never an unscoped raw event."""
    start_record = store.get_native_command(grant["parentCommandUid"])
    if start_record is None or start_record["message_name"] != "START_CLEAN_OPERATION":
        raise ValueError("native clean original start command missing")
    start = uart.decode_payload("START_CLEAN_OPERATION", start_record["payload"])
    if (start["targetMcuBootId"] != grant["targetMcuBootId"] or start["portNo"] != grant["portNo"]
            or start["operationUid"] != grant["operationUid"]):
        raise ValueError("native reopen intent start relationship mismatch")
    scope = {key: start[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence", "portNo", "configVersion")}
    scope.update(queryId=1, workUid=start["operationUid"], workType="CLEAN_OPERATION",
        eventMessageType="CLEAN_UNLOCK_REQUESTED", stepSequence=grant["cleanActionSequence"])
    return store.get_native_process_receipt(uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:])


def executed_bundle(store, action_uid, pinned=None):
    """Initial delivery cycle / ordinary clean pulses, from durable facts.

    No current-boot/clock freshness check: these are historical output facts,
    not a new execution permission, physical door position or completed work.
    Caller holds one SQLite snapshot. Missing facts return None, never zero.
    """
    binding = store.get_native_action_binding(action_uid)
    if binding is None:
        raise ValueError("native original action binding missing")
    permit, action = binding["permit"], binding["action"]
    command = store.get_native_command(action_uid)
    clean = action.action_kind == "UNLOCK_CLEAN_DOOR"
    if action.action_kind not in {"UNLOCK_CLEAN_DOOR", "AUTHORIZE_DELIVERY_FIRST_OPEN"}:
        raise ValueError("native action kind has no effect reconciliation policy")
    grant = uart.decode_payload(action.action_kind, command["payload"])
    if clean and grant["recoveryGeneration"] != 0:
        raise ValueError("native recovery unlock requires its own effect policy")
    work_type, work_key = ("CLEAN", "operationUid") if clean else ("DELIVERY", "sessionUid")
    start_name = "START_CLEAN_OPERATION" if clean else "START_DELIVERY_SESSION"
    start_uid = grant["parentCommandUid" if clean else "parentStartCommandUid"]
    start_record = store.get_native_command(start_uid)
    if start_record is None or start_record["message_name"] != start_name:
        raise ValueError("native original start command missing or wrong kind")
    start = uart.decode_payload(start_name, start_record["payload"])
    if (permit.work_type != work_type or start[work_key] != permit.work_uid
            or start["portNo"] != grant["portNo"] or start["targetMcuBootId"] != grant["targetMcuBootId"]
            or start["commandSequence"] >= grant["commandSequence"]):
        raise ValueError("native action and original start relationship mismatch")
    acceptance = {}
    for key, record in (("start", start_record), ("action", command)):
        acceptance[key] = accepted_command_witness(store, record, pinned["acceptance"][key] if pinned else None)
        if acceptance[key] is None:
            return None
    initial_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
    scope = {key: start[key] for key in ("mcuCommandUid", "commandDigestSha256", "targetMcuBootId", "commandSequence", "portNo", "configVersion")}
    scope.update(queryId=1, workUid=permit.work_uid, workType="CLEAN_OPERATION" if clean else "DELIVERY_SESSION",
        eventMessageType=initial_name, stepSequence=0 if clean else 1)
    raw_scope = uart.encode_payload("QUERY_PROCESS_EVENT", scope)[8:]
    initial_record = store.get_native_process_receipt(raw_scope)
    if initial_record is None:
        return None
    initial = uart.decode_payload(initial_name, initial_record["payload"])
    if initial["measurementKind"] not in {"STABLE_MEAN", "TIMEOUT_MEDIAN"}:
        raise ValueError("native initial measurement cannot authorize opening")
    if not clean and initial["measurementUid"] != grant["firstPreOpenMeasurementUid"]:
        raise ValueError("native original initial measurement mismatch")
    reopen_record = None
    first_unlock = None
    if clean and grant["cleanActionSequence"]:
        first_unlock = confirmed_first_clean_unlock(store, permit, command)
        reopen_record = saved_clean_reopen_intent(store, grant)
        if reopen_record is None:
            return None
        reopen = uart.decode_payload("CLEAN_UNLOCK_REQUESTED", reopen_record["payload"])
        if (reopen["mcuEventSequence"] <= initial["mcuEventSequence"] or reopen["uptimeMs"] < initial["uptimeMs"]):
            raise ValueError("native reopen intent predates original initial measurement")
    output_name = "CLEAN_LOCK_POWER_CHANGED" if clean else "DELIVERY_DOOR_COMMAND_RESULT"
    rows = store.list_native_action_actuator_events(action_uid, output_name)
    outputs = []
    for row in rows:
        value = uart.decode_payload(row["message_name"], row["payload"])
        if (value["mcuBootId"] != grant["targetMcuBootId"] or value.get(work_key) != permit.work_uid
                or value["portNo"] != grant["portNo"]):
            raise ValueError("native output claims another boot/work/port")
        if row["message_name"] == output_name:
            outputs.append((row, value))
    if len(outputs) < 2:
        return None
    if len(outputs) != 2:
        raise ValueError("native action claims repeated outputs")
    (first_row, first), (last_row, last) = outputs
    if (not initial["mcuEventSequence"] < first["mcuEventSequence"] < last["mcuEventSequence"]
            or not initial["uptimeMs"] <= first["uptimeMs"] <= last["uptimeMs"]):
        raise ValueError("native output chronology contradicts original measurement")
    if reopen_record and (reopen["mcuEventSequence"] >= first["mcuEventSequence"] or reopen["uptimeMs"] > first["uptimeMs"]):
        raise ValueError("native reopen output predates its button intent")
    if clean:
        if (first["lockPowerState"] != "ENERGIZED" or last["lockPowerState"] != "DEENERGIZED"
                or last["uptimeMs"] - first["uptimeMs"] < grant["unlockPulseMs"]):
            return None  # Short/interrupted pulse is NOT a normal executed proof.
    elif (first["command"] != "OPEN" or last["command"] != "CLOSE"
            or any(v["roundIndex"] != 1 or v["outputStatus"] != "COMMAND_DISPATCHED" or v["faultCode"] != "NONE" for _, v in outputs)
            or last["uptimeMs"] - first["uptimeMs"] < start["deliveryAutoCloseMs"]):
        return None
    bundle = dict(version="ecobin-native-output-effect-v1", outcome="EXECUTED", basis="MCU_IDENTITY_BOUND_FACT",
        permit=asdict(permit), action=asdict(action), command=_wire(command), start=_wire(start_record),
        acceptance=acceptance, initial=_wire(initial_record) | {"scopeHex": raw_scope.hex(), "savedHex": initial_record["saved_payload"].hex()},
        outputs=[_wire(row) | {"savedHex": row["saved_payload"].hex()} for row in (first_row, last_row)])
    if reopen_record:
        bundle.update(version="ecobin-native-clean-reopen-effect-v1", firstUnlock=first_unlock, reopenIntent=_wire(reopen_record) | {
            "scopeHex": reopen_record["scope"].hex(), "savedHex": reopen_record["saved_payload"].hex()})
    return bundle


def bundle_digest(bundle_json):
    return hashlib.sha256(b"EcoBin/native-output-effect/v1\0" + bundle_json.encode("ascii")).hexdigest()


def checked_confirmation(row):
    value = dict(row)
    bundle = json.loads(value["bundle_json"])
    if (canonical(bundle) != value["bundle_json"] or bundle_digest(value["bundle_json"]) != value["evidence_sha256"]
            or bundle["action"]["action_uid"] != value["action_uid"]
            or value["state"] not in {"PENDING", "CONFIRMED"}):
        raise ValueError("native action confirmation is corrupt")
    return value


def check_ledger(binding, ledger, confirmation=None):
    permit, action = binding["permit"], binding["action"]
    expected = dict(actionUid=action.action_uid, permitUid=permit.permit_uid, workUid=permit.work_uid,
        commandUid=permit.command_uid, actionKey=action.action_key, actionKind=action.action_kind,
        actionDigestSha256=action.action_digest_sha256, dispatchMode="TWO_PHASE_V3")
    if any(ledger.get(key) != value for key, value in expected.items()) or ledger.get("unknownEffectResolution") is not None:
        raise ValueError("native permanent action identity/resolution conflict")
    if confirmation is not None:
        expected = dict(state="CONFIRMED", confirmedOutcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT",
            receiptUid=action.receipt_uid, evidenceDigestSha256=confirmation["evidence_sha256"])
        if any(ledger.get(key) != value for key, value in expected.items()):
            raise ValueError("native permanent confirmation does not match original proof")


class NativeActionReconciler:
    """Local proof outbox -> idempotent permanent confirmation. Never sends UART.

    The two stores commit separately. A lost reply leaves PENDING; restart reads
    the original identities/proof and queries the ledger before any retry.
    Neither confirmation nor failure here releases work or creates cloud data.
    """
    def __init__(self, store, safety):
        if not isinstance(safety, PermanentJobSafety) or not safety.enabled:
            raise ValueError("native reconciliation requires permanent job safety")
        self._store, self._safety = store, safety

    def reconcile_pending(self, *, limit=32):
        results = []
        for uid in self._store.list_pending_native_action_uids(limit=limit):
            result = self.reconcile(uid)
            if result is not None:
                results.append(result)
        return results

    def reconcile(self, action_uid):
        proof = self._store.prepare_native_action_confirmation(action_uid)
        if proof is None:
            return None
        binding = self._store.get_native_action_binding(action_uid)
        permit, action = binding["permit"], binding["action"]
        owner = self._safety.get_job_permit(permit.permit_uid)
        expected = dict(permitUid=permit.permit_uid, workUid=permit.work_uid, commandUid=permit.command_uid,
            workType=permit.work_type, requestDigestSha256=permit.request_digest_sha256, state="ACTIVE")
        if any(owner.get(key) != value for key, value in expected.items()):
            raise ValueError("native permanent work ownership mismatch")
        ledger = self._safety.get_physical_action(action_uid)
        check_ledger(binding, ledger)
        if ledger["state"] != "CONFIRMED":
            if ledger["state"] != "ARMED" or proof["state"] == "CONFIRMED":
                raise ValueError("native permanent action is not armed")
            # Recheck after external reads. No DB transaction spans an RPC.
            current = self._store.prepare_native_action_confirmation(action_uid)
            if current != proof:
                raise ValueError("native action evidence changed before confirmation")
            self._safety.confirm_physical_action(action, outcome="EXECUTED",
                evidence_sha256=proof["evidence_sha256"], confirmation_basis="MCU_IDENTITY_BOUND_FACT")
            ledger = self._safety.get_physical_action(action_uid)
        check_ledger(binding, ledger, proof)
        return self._store.confirm_native_action_effect(action_uid, ledger)
