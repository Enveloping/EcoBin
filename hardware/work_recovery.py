"""Native candidate work recovery; no serial writes, money or admission changes.

The boot session supplies fresh observations. EdgeStore serializes evaluation
with result custody. A stored intent is not an issue archive or action permit.
The separate delivery archive freezes the approved issue-only verdict; neither
entry closes the door, completes the permanent permit or enables admission.
"""
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json
from time import monotonic_ns
import uuid

from job_safety import JobPermit
from mcu_session import McuBootSession
import uart2_protocol as uart


class NativeWorkRecovery:
    def __init__(self, store, boot: McuBootSession, *,
                 clock: Callable[[], int] = lambda: monotonic_ns() // 1000000):
        if not isinstance(boot, McuBootSession) or not callable(clock):
            raise ValueError("native recovery requires the live boot observer and monotonic clock")
        self._store, self._boot, self._clock = store, boot, clock

    def evaluate(self, permit: JobPermit, start_command_uid: str) -> dict:
        return self._store.evaluate_native_work_recovery(permit, start_command_uid,
            current_boot=lambda: self._boot.current_boot(self._clock()))

    def archive_delivery(self, permit: JobPermit, start_command_uid: str, *, device_name: str) -> dict:
        return self._store.archive_native_delivery_issue(permit, start_command_uid, device_name=device_name,
            current_boot=lambda: self._boot.current_boot(self._clock()))


def original_work(store, permit, start_uid):
    if not isinstance(permit, JobPermit) or permit.work_type not in {"DELIVERY", "CLEAN"}:
        raise ValueError("native recovery requires the original delivery or clean permit")
    for value in (permit.permit_uid, permit.work_uid, permit.command_uid):
        if not isinstance(value, str) or str(uuid.UUID(value)) != value or uuid.UUID(value).version != 4:
            raise ValueError("native recovery requires canonical original UUIDv4 identities")
    # The native registry accepts a nonzero opaque UUID for MCU commands;
    # permanent/cloud permit identities have the stricter UUIDv4 contract.
    if not isinstance(start_uid, str) or str(uuid.UUID(start_uid)) != start_uid or not uuid.UUID(start_uid).int:
        raise ValueError("native recovery requires the original native command identity")
    digest = permit.request_digest_sha256
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("native recovery requires the original request digest")
    clean = permit.work_type == "CLEAN"
    name, key = ("START_CLEAN_OPERATION", "operationUid") if clean else ("START_DELIVERY_SESSION", "sessionUid")
    record = store.get_native_command(start_uid)
    if record is None or record["message_name"] != name or record["conflict"]:
        raise ValueError("native original START missing, conflicted or wrong kind")
    start = uart.decode_payload(name, record["payload"])
    occupied = store.get_work_slot()
    if (occupied is None or (occupied["work_uid"], occupied["work_type"], occupied["port_no"])
            != (permit.work_uid, permit.work_type, start["portNo"]) or start[key] != permit.work_uid):
        raise ValueError("native recovery does not own the original work occupancy")
    binding = store.get_native_action_by_key(permit.work_uid, "clean:first-unlock" if clean else "delivery:first-open")
    if binding is not None and binding["permit"] != permit:
        raise ValueError("native recovery permit differs from the original action")
    return record, start


def complete_result(store, conn, permit, record, start, *, validate_report=True):
    """A complete wire result includes FAILED results, not only usable weights."""
    if store.get_native_delivery_issue(permit.work_uid) is not None:
        raise ValueError("native delivery is archived; complete result is evidence only")
    # Inspect both projections and wire identities: a corrupt index field must
    # not hide a saved result and manufacture an absence-based recovery intent.
    rows = conn.execute("""SELECT * FROM native_mcu_result WHERE work_uid=?
        OR substr(payload,13,16)=? OR substr(payload,71,16)=?""",
        (permit.work_uid, uuid.UUID(permit.work_uid).bytes, uuid.UUID(record["command_uid"]).bytes)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("native work has conflicting complete result identities")
    row = rows[0]
    value = uart.decode_payload("WORK_RESULT", bytes(row["payload"]))
    expected = dict(mcuBootId=start["targetMcuBootId"], workUid=permit.work_uid,
        workType="CLEAN_OPERATION" if permit.work_type == "CLEAN" else "DELIVERY_SESSION",
        portNo=start["portNo"], configVersion=start["configVersion"],
        originCommandUid=record["command_uid"], originCommandSequence=record["command_sequence"])
    if (any(value[key] != wanted for key, wanted in expected.items())
            or (row["mcu_boot_id"], row["result_sequence"], row["result_digest"], row["work_uid"])
            != (value["mcuBootId"], value["resultSequence"], value["resultDigestSha256"], value["workUid"])):
        raise ValueError("native complete result does not match the original START")
    if not record["write_claimed"] or record["decision_outcome"] == "REJECTED":
        raise ValueError("native complete result contradicts the original START dispatch")
    key = (row["mcu_boot_id"], row["result_sequence"])
    if conn.execute("SELECT 1 FROM native_mcu_result_conflict WHERE mcu_boot_id=? AND result_sequence=?", key).fetchone():
        raise ValueError("native complete result has an unresolved conflict")
    task = conn.execute("SELECT * FROM native_result_report_outbox WHERE mcu_boot_id=? AND result_sequence=?", key).fetchone()
    if task is None or task["state"] not in {"PENDING_CLASSIFICATION", "REPORT_CREATED"}:
        raise ValueError("native complete result lacks its durable classification task")
    if task["state"] == "REPORT_CREATED" and validate_report:
        store._native_report_binding(conn, task)
    from native_result_evidence import reconcile
    return dict(status="COMPLETE_RESULT_AVAILABLE", result=dict(row), task=dict(task),
        evidence=reconcile(store, record, start, value, permit))


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def delivery_issue_authority(store, permit, record, start, device_name):
    from native_result_evidence import original_configuration
    from native_result_report import original_authority
    config = original_configuration(store, record, start)
    if config is None or original_authority(store, permit, start, {"configuration": config}, device_name) is None:
        raise ValueError("native delivery issue lacks original device/command custody")


def match_issue_result(store, issue, payload):
    value = uart.decode_payload("WORK_RESULT", payload)
    record = store.get_native_command(issue["startCommandUid"])
    start = uart.decode_payload(record["message_name"], record["payload"])
    expected = dict(mcuBootId=issue["sourceMcuBootId"], workUid=issue["workUid"],
        workType="DELIVERY_SESSION", portNo=start["portNo"], configVersion=start["configVersion"],
        originCommandUid=record["command_uid"], originCommandSequence=record["command_sequence"])
    if any(value[key] != wanted for key, wanted in expected.items()):
        raise ValueError("native delivery issue late result differs from original identity")


def checked_delivery_issue(store, conn, row):
    if row is None:
        return None
    issue = json.loads(row["evidence_json"])
    if (canonical(issue) != row["evidence_json"]
            or hashlib.sha256(row["evidence_json"].encode("ascii")).hexdigest() != row["evidence_sha256"]):
        raise ValueError("native delivery issue evidence is corrupt")
    if (issue.get("profile") != "ecobin-native-delivery-issue-v1"
            or issue.get("reason") != "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
            or issue.get("reasonText") != "单片机重启，未取得最终结果包"
            or issue.get("settlementAllowed") is not False
            or issue.get("finalResultAtArchive") != "ABSENT"
            or (issue["issueUid"], issue["workUid"], issue["recoveryUid"])
            != (row["issue_uid"], row["work_uid"], row["recovery_uid"])):
        raise ValueError("native delivery issue identity or decision is corrupt")
    intent = store.get_native_work_recovery_intent(issue["recoveryUid"])
    if (intent is None or issue["permit"] != intent["evidence"]["permit"]
            or issue["permit"]["work_type"] != "DELIVERY" or issue["permit"]["work_uid"] != issue["workUid"]
            or issue["startCommandUid"] != intent["start_command_uid"]
            or issue["sourceMcuBootId"] != intent["evidence"]["sourceMcuBootId"]
            or issue["targetMcuBootId"] != intent["target_mcu_boot_id"]):
        raise ValueError("native delivery issue differs from original recovery evidence")
    record = store.get_native_command(issue["startCommandUid"])
    start = uart.decode_payload(record["message_name"], record["payload"])
    delivery_issue_authority(store, JobPermit(**issue["permit"]), record, start, issue["deviceName"])
    count, digest = facts_fingerprint(conn.execute(
        "SELECT * FROM native_delivery_issue_fact WHERE issue_uid=? ORDER BY fact_sequence", (issue["issueUid"],)))
    if (count, digest) != (issue["knownFactCount"], issue["knownFactsSha256"]):
        raise ValueError("native delivery issue lost original process evidence")
    return issue


def known_facts(store, conn, permit, start):
    """Stream copies of known wire facts; never synthesize absent measurements."""
    boot, work = start["targetMcuBootId"], permit.work_uid
    work_key = "operationUid" if permit.work_type == "CLEAN" else "sessionUid"
    for row in conn.execute("SELECT * FROM native_mcu_command WHERE mcu_boot_id=? ORDER BY command_sequence", (boot,)):
        command = store.get_native_command(row["command_uid"])
        value = uart.decode_payload(command["message_name"], command["payload"])
        if value.get(work_key) != work:
            continue
        yield dict(source_kind="COMMAND", source_key=command["command_uid"], message_name=command["message_name"],
            payload=command["payload"], metadata_json=canonical(dict(writeClaimed=bool(command["write_claimed"]),
                decisionOutcome=command["decision_outcome"], identityConflict=bool(command["conflict"]))))
        for observation in store.list_native_command_observations(command["command_uid"]):
            uart.decode_payload(observation["message_name"], observation["payload"])
            offset = 8 if observation["message_name"] == "COMMAND_QUERY_RESULT" else 0
            if observation["payload"][offset:offset + 60] != command["payload"][:60]:
                raise ValueError("native recovery command observation identity is corrupt")
            yield dict(source_kind="COMMAND_OBSERVATION", source_key=hashlib.sha256(observation["payload"]).hexdigest(),
                message_name=observation["message_name"], payload=observation["payload"], metadata_json="{}")
    sources = (("MEASUREMENT", "native_measurement_event", None),
        ("ACTUATOR", "native_actuator_event", None),
        ("DELIVERY_SELECTION", "native_delivery_selection", "DELIVERY_SELECTION"),
        ("CLEAN_INTENT", "native_clean_intent", None),
        ("CLEAN_CONFIRMATION", "native_clean_confirmation", "CLEAN_COMPLETION_CONFIRMED"))
    for kind, table, fixed_name in sources:
        for row in conn.execute(f"SELECT * FROM {table} WHERE mcu_boot_id=? ORDER BY event_sequence", (boot,)):
            row = dict(row)
            name = fixed_name or row["message_name"]
            if kind == "MEASUREMENT":
                receipt = conn.execute("SELECT scope,saved_payload FROM native_process_receipt WHERE mcu_boot_id=? AND event_sequence=?",
                    (boot, row["event_sequence"])).fetchone()
                if receipt is not None:
                    row.update(dict(receipt))
            value = uart.decode_payload(name, bytes(row["payload"]))
            scoped_work = None
            if row.get("scope") is not None:
                scoped_work = uart.decode_payload("QUERY_PROCESS_EVENT", (1).to_bytes(8, "big") + bytes(row["scope"]))["workUid"]
            if work not in {value.get(work_key), row.get("reported_work_uid"), row.get("work_uid"), scoped_work}:
                continue
            # Reuse existing custody validators before giving bytes a new
            # snapshot digest. That includes old checksum and conflict checks.
            checked = (store.get_native_actuator_event(boot, row["event_sequence"]) if kind == "ACTUATOR"
                else store.get_native_process_event(name, boot, row["event_sequence"]))
            if checked is None or checked["payload"] != row["payload"]:
                raise ValueError("native recovery original fact custody is missing")
            if row.get("scope") is not None:
                checked_receipt = store.get_native_process_receipt(bytes(row["scope"]))
                if checked_receipt is None or checked_receipt["payload"] != row["payload"]:
                    raise ValueError("native recovery original scoped receipt is missing")
            if (value["mcuBootId"], value["mcuEventSequence"], value["portNo"]) != (boot, row["event_sequence"], start["portNo"]):
                raise ValueError("native recovery fact identity does not match original work")
            metadata = {}
            for column, key in (("scope", "scopeHex"), ("saved_payload", "savedPayloadHex")):
                if row.get(column) is not None:
                    metadata[key] = bytes(row[column]).hex()
            yield dict(source_kind=kind, source_key=str(row["event_sequence"]), message_name=name,
                payload=bytes(row["payload"]), metadata_json=canonical(metadata))


def facts_fingerprint(facts):
    digest, count = hashlib.sha256(b"ECOBIN:NATIVE-WORK-RECOVERY-FACTS:v1\0"), 0
    for row in facts:
        value = {key: row[key] for key in ("source_kind", "source_key", "message_name", "metadata_json")}
        value["payloadHex"] = bytes(row["payload"]).hex()
        raw = canonical(value).encode("ascii")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
        count += 1
    return count, digest.hexdigest()


def recovery_evidence(store, conn, permit, record, start, witness, recovery_uid):
    count, digest = facts_fingerprint(known_facts(store, conn, permit, start))
    first = store.get_native_action_by_key(permit.work_uid,
        "clean:first-unlock" if permit.work_type == "CLEAN" else "delivery:first-open")
    return dict(profile="ecobin-native-work-recovery-intent-v1", recoveryUid=recovery_uid,
        reason="MCU_RESTART_RESULT_UNAVAILABLE", dataLossClassified=False,
        permit=asdict(permit), sourceMcuBootId=start["targetMcuBootId"],
        targetMcuBootId=witness["boot_id"], portNo=start["portNo"], completeResultAtEvaluation="ABSENT",
        originalStart=dict(messageName=record["message_name"], payloadHex=record["payload"].hex(),
            writeClaimed=bool(record["write_claimed"]), decisionOutcome=record["decision_outcome"]),
        firstAction=asdict(first["action"]) if first else None,
        bootObservation=dict(messageName=witness["message_name"], payloadHex=witness["payload"].hex()),
        knownFactCount=count, knownFactsSha256=digest)


def checked_intent(store, conn, row):
    if row is None:
        return None
    evidence = json.loads(row["evidence_json"])
    if (canonical(evidence) != row["evidence_json"]
            or hashlib.sha256(row["evidence_json"].encode("ascii")).hexdigest() != row["evidence_sha256"]):
        raise ValueError("native recovery intent evidence is corrupt")
    if (evidence.get("profile") != "ecobin-native-work-recovery-intent-v1"
            or evidence.get("reason") != "MCU_RESTART_RESULT_UNAVAILABLE"
            or evidence.get("dataLossClassified") is not False
            or evidence.get("completeResultAtEvaluation") != "ABSENT"
            or (evidence["recoveryUid"], evidence["permit"]["work_uid"], evidence["targetMcuBootId"])
            != (row["recovery_uid"], row["work_uid"], row["target_mcu_boot_id"])):
        raise ValueError("native recovery intent identity is corrupt")
    start = store.get_native_command(row["start_command_uid"])
    original = evidence["originalStart"]
    if (start is None or start["message_name"] != original["messageName"]
            or start["payload"].hex() != original["payloadHex"] or start["mcu_boot_id"] != evidence["sourceMcuBootId"]
            or not 0 < evidence["sourceMcuBootId"] < evidence["targetMcuBootId"]):
        raise ValueError("native recovery original START evidence is missing or corrupt")
    if original.get("writeClaimed") is not True or not start["write_claimed"] or original.get("decisionOutcome") not in {None, "ACCEPTED"}:
        raise ValueError("native recovery original dispatch evidence is corrupt")
    clean = start["message_name"] == "START_CLEAN_OPERATION"
    wire = uart.decode_payload(start["message_name"], start["payload"])
    if (start["message_name"] not in {"START_DELIVERY_SESSION", "START_CLEAN_OPERATION"}
            or evidence["permit"]["work_type"] != ("CLEAN" if clean else "DELIVERY")
            or evidence["permit"]["work_uid"] != wire["operationUid" if clean else "sessionUid"]
            or type(evidence["portNo"]) is not int or evidence["portNo"] != wire["portNo"]):
        raise ValueError("native recovery original work identity is corrupt")
    binding = store.get_native_action_by_key(row["work_uid"], "clean:first-unlock" if clean else "delivery:first-open")
    if ((binding is None and evidence["firstAction"] is not None)
            or (binding is not None and (asdict(binding["permit"]) != evidence["permit"]
                or asdict(binding["action"]) != evidence["firstAction"]))):
        raise ValueError("native recovery original permit/action binding is missing or corrupt")
    witness = store.get_native_boot_observation(row["target_mcu_boot_id"])
    if witness is None or evidence["bootObservation"] != dict(messageName=witness["message_name"], payloadHex=witness["payload"].hex()):
        raise ValueError("native recovery boot witness is missing or corrupt")
    count, digest = facts_fingerprint(conn.execute("SELECT * FROM native_work_recovery_fact WHERE recovery_uid=? ORDER BY fact_sequence",
        (row["recovery_uid"],)))
    if (count, digest) != (evidence["knownFactCount"], evidence["knownFactsSha256"]):
        raise ValueError("native recovery fact custody is missing or corrupt")
    return dict(row) | {"evidence": evidence}
