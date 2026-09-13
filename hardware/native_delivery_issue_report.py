"""Native issue evidence -> reliable OneNet facts, never normal completion.

OneNet strings are bounded to 512 characters: raw artifacts use 256-byte hex
parts with immutable artifact identity/digest. No truncation, COS grant or action.
"""
import hashlib
import json

from work_recovery import canonical
from onenet_wire import canonical_payload_sha256
import uart2_protocol as uart


ARCHIVE_EVENT = "DELIVERY_ISSUE_ARCHIVED"
EVIDENCE_EVENT = "DELIVERY_ISSUE_EVIDENCE_APPENDED"
PART_BYTES = 256


def validate_envelope(event):
    payload = event["payload"]
    if (event["target"] != dict(type="DELIVERY_SESSION", uid=payload["sessionUid"])
            or event["commandUid"] != payload["originalCommandUid"]
            or payload["businessValue"] != "NONE"
            or event["payloadSha256"] != canonical_payload_sha256(payload)):
        raise ValueError("native issue envelope differs from original work or issue-only policy")
    if event["eventType"] == ARCHIVE_EVENT:
        start = uart.decode_payload("START_DELIVERY_SESSION", bytes.fromhex(payload["originalStartPayloadHex"]))
        boot = uart.decode_payload(payload["bootObservationType"], bytes.fromhex(payload["bootObservationPayloadHex"]))
        if (event["eventUid"] != payload["issueUid"] or payload["reason"] != "MCU_RESTART_FINAL_RESULT_UNAVAILABLE"
                or payload["finalResultAtArchive"] != "ABSENT"
                or not payload["sourceMcuBootId"] < payload["targetMcuBootId"]
                or start["sessionUid"] != payload["sessionUid"] or start["portNo"] != payload["portNo"]
                or start["targetMcuBootId"] != payload["sourceMcuBootId"] or boot["mcuBootId"] != payload["targetMcuBootId"]):
            raise ValueError("native issue archive lacks original identity and positive reboot evidence")
    else:
        raw = bytes.fromhex(payload["dataHex"])
        size, part, count = payload["evidenceSizeBytes"], payload["partIndex"], payload["partCount"]
        if (any(type(v) is not int for v in (size, part, count, payload["evidenceIndex"]))
                or event["eventUid"] == payload["issueUid"] or count != (size+255)//256
                or not 1 <= part <= count or len(raw) != min(256, size-(part-1)*256)
                or (count == 1 and hashlib.sha256(raw).hexdigest() != payload["evidenceSha256"])
                or ((payload["evidenceKind"] == "PROCESS_FACT") != (payload["evidenceIndex"] > 0))
                or (payload["evidenceKind"] == "ARCHIVE_CONTEXT" and payload["evidenceSha256"] != payload["archiveEvidenceSha256"])):
            raise ValueError("native issue fragment identity/size/digest is inconsistent")


def archive_digest(issue):
    return hashlib.sha256(canonical(issue).encode("ascii")).hexdigest()


def header_payload(store, issue):
    command = store.get_command(issue["permit"]["command_uid"])["payload"]
    record = store.get_native_command(issue["startCommandUid"])
    start = uart.decode_payload(record["message_name"], record["payload"])
    boot = store.get_native_boot_observation(issue["targetMcuBootId"])
    return dict(issueUid=issue["issueUid"], sessionUid=issue["workUid"],
        originalCommandUid=issue["permit"]["command_uid"], originalCommandPayloadSha256=command["payloadSha256"],
        portNo=start["portNo"], sourceMcuBootId=issue["sourceMcuBootId"], targetMcuBootId=issue["targetMcuBootId"],
        reason=issue["reason"], businessValue="NONE", finalResultAtArchive="ABSENT",
        archiveEvidenceSha256=archive_digest(issue), knownFactCount=issue["knownFactCount"],
        originalStartPayloadHex=record["payload"].hex(), bootObservationType=boot["message_name"],
        bootObservationPayloadHex=boot["payload"].hex())


def artifact_sources(store, issue):
    yield "ARCHIVE_CONTEXT", 0, canonical(issue).encode("ascii")
    cursor = 0
    while rows := store.list_native_delivery_issue_facts(issue["issueUid"], after_sequence=cursor, limit=1000):
        for row in rows:
            # Exactly the per-fact bytes used by the original custody fingerprint.
            value = {k:row[k] for k in ("source_kind", "source_key", "message_name", "metadata_json")}
            value["payloadHex"] = bytes(row["payload"]).hex()
            yield "PROCESS_FACT", row["fact_sequence"], canonical(value).encode("ascii")
        cursor = rows[-1]["fact_sequence"]
    for row in store.list_native_delivery_issue_results(issue["issueUid"]):
        yield "FINAL_RESULT", 0, bytes(row["payload"])


def artifact_parts(issue, kind, index, raw):
    if not raw or len(raw) > 4294967295:
        raise ValueError("native issue artifact size is outside the contract")
    count = (len(raw) + PART_BYTES - 1) // PART_BYTES
    evidence_sha, archive_sha = hashlib.sha256(raw).hexdigest(), archive_digest(issue)
    for part in range(count):
        payload = dict(issueUid=issue["issueUid"], sessionUid=issue["workUid"],
            originalCommandUid=issue["permit"]["command_uid"], archiveEvidenceSha256=archive_sha,
            businessValue="NONE", evidenceKind=kind, evidenceIndex=index,
            evidenceSha256=evidence_sha, evidenceSizeBytes=len(raw),
            partIndex=part+1, partCount=count, dataHex=raw[part*PART_BYTES:(part+1)*PART_BYTES].hex())
        yield (kind, index, part+1), EVIDENCE_EVENT, payload


def report_sources(store, issue):
    yield ("ARCHIVE", 0, 0), ARCHIVE_EVENT, header_payload(store, issue)
    for kind, index, raw in artifact_sources(store, issue):
        yield from artifact_parts(issue, kind, index, raw)


def checked_report(store, row, issue, expected):
    event_row = store.get_event(row["event_uid"])
    if event_row is None:
        raise ValueError("native issue report lost original event")
    event = json.loads(event_row["payload_json"])
    key, event_type, payload = expected
    if (key != (row["evidence_kind"], row["evidence_index"], row["part_index"])
            or event_row["event_type"] != event_type or event.get("eventType") != event_type
            or event.get("payload") != payload or event.get("payloadSha256") != canonical_payload_sha256(payload)
            or event.get("eventUid") != row["event_uid"] or event.get("edgeEventSequence") != event_row["edge_event_sequence"]
            or event.get("commandUid") != issue["permit"]["command_uid"]
            or event.get("target") != dict(type="DELIVERY_SESSION", uid=issue["workUid"])
            or event_row["work_uid"] != issue["workUid"]
            or row["device_name"] != issue["deviceName"]
            or canonical_payload_sha256(event) != row["event_sha256"]):
        raise ValueError("native issue report differs from immutable original evidence")
    return dict(row)


def confirmation_for_issue_report(store, conn, task, command, device_name, *, persisted=False):
    """A decision about exact diagnostic evidence, never a normal order reference."""
    from onenet_wire import validate_command_envelope, validate_stored_confirmation_envelope
    original = conn.execute("SELECT work_uid FROM native_delivery_issue WHERE issue_uid=?",
        (task["issue_uid"],)).fetchone()
    if original is None:
        raise ValueError("native issue confirmation lost its original archive")
    issue = store.get_native_delivery_issue(original["work_uid"])
    reports = store.list_native_delivery_issue_reports(original["work_uid"])
    if not any(row["event_uid"] == task["event_uid"] for row in reports):
        raise ValueError("native issue confirmation lost its original report")
    if not isinstance(command, dict):
        raise ValueError("native issue confirmation requires its original command envelope")
    stable = {key: value for key, value in command.items() if key != "cosGrant"}
    if (set(stable) != {"schemaVersion", "payloadSchemaVersion", "commandUid", "commandType",
            "targetDeviceName", "target", "issuedAt", "expiresAt", "payloadSha256", "payload"}
            or command.get("cosGrant") is not None or command.get("commandType") != "CONFIRM_EDGE_EVENT"):
        raise ValueError("native issue confirmation command shape is invalid")
    (validate_stored_confirmation_envelope if persisted else validate_command_envelope)(stable)
    payload = stable["payload"]
    event = json.loads(store.get_event(task["event_uid"])["payload_json"])
    if (device_name != issue["deviceName"] or stable["targetDeviceName"] != device_name
            or payload["originalEventUid"] != task["event_uid"]
            or payload["originalPayloadSha256"] != event["payloadSha256"]
            or payload["resultReferences"]
            or (payload["outcome"] == "BUSINESS_APPLIED"
                and payload["effectKind"] not in {"UPDATED", "NO_ACTION_REQUIRED"})):
        raise ValueError("native issue confirmation differs from original diagnostic-only evidence")
    return stable, issue


def checked_issue_confirmation(store, conn, row):
    task = conn.execute("SELECT * FROM native_delivery_issue_report WHERE event_uid=?", (row["event_uid"],)).fetchone()
    accepted = conn.execute("SELECT * FROM confirmation_inbox WHERE confirmation_uid=?", (row["confirmation_uid"],)).fetchone()
    command = json.loads(row["command_json"])
    if (task is None or accepted is None or canonical_payload_sha256(command) != row["command_sha256"]
            or accepted["canonical_sha256"] != row["command_sha256"]
            or accepted["command_uid"] != command.get("commandUid")):
        raise ValueError("native issue confirmation command custody is corrupt")
    stable, issue = confirmation_for_issue_report(store, conn, task, command, task["device_name"], persisted=True)
    payload = stable["payload"]
    if (accepted["event_uid"] != row["event_uid"] or accepted["outcome"] != payload["outcome"]
            or row["confirmation_uid"] != payload["confirmationUid"]
            or json.loads(accepted["payload_json"]) != payload):
        raise ValueError("native issue confirmation inbox differs from original decision")
    receipt = store.get_event(accepted["receipt_event_uid"])
    event = store.get_event(row["event_uid"])
    if receipt is None or event["state"] != "CONFIRMED" or event["confirmed_at"] is None:
        raise ValueError("native issue confirmation lost its atomic receipt")
    body = json.loads(receipt["payload_json"])
    expected = {key: payload[key] for key in ("confirmationUid", "originalEventUid", "originalPayloadSha256", "outcome")}
    if (canonical_payload_sha256(body) != row["receipt_sha256"]
            or body.get("payload") != expected or body.get("payloadSha256") != canonical_payload_sha256(expected)
            or body.get("commandUid") != stable["commandUid"] or body.get("eventUid") != accepted["receipt_event_uid"]
            or body.get("eventType") != "BUSINESS_CONFIRMATION_RECEIPT"
            or receipt["event_type"] != body["eventType"] or receipt["work_uid"] != row["event_uid"]
            or body.get("edgeEventSequence") != receipt["edge_event_sequence"]
            or body.get("target") != {"type": "BUSINESS_CONFIRMATION", "uid": row["confirmation_uid"]}):
        raise ValueError("native issue confirmation receipt differs from original decision")
    return dict(eventUid=row["event_uid"], issueUid=issue["issueUid"], workUid=issue["workUid"],
        evidenceKind=task["evidence_kind"], evidenceIndex=task["evidence_index"], partIndex=task["part_index"],
        businessValue="NONE", confirmationUid=row["confirmation_uid"], receiptEventUid=accepted["receipt_event_uid"],
        **{key: payload[key] for key in ("outcome", "effectKind", "resultReferences", "processedAt", "errorCode", "quarantineUid")})


class NativeDeliveryIssueReporter:
    def __init__(self, store, *, device_name):
        if not isinstance(device_name, str) or not device_name:
            raise ValueError("native issue reporter requires a device identity")
        self.store, self.device_name = store, device_name

    def prepare(self, work_uid, *, limit=50):
        return self.store.prepare_native_delivery_issue_reports(work_uid, device_name=self.device_name, limit=limit)
