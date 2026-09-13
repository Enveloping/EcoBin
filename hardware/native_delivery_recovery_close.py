"""Original archived delivery to one restricted new close; no admission release.

The caller supplies live physical prerequisites. No cloud confirmation is
required for local recovery. Durable identities never grant replay permission.
"""
from dataclasses import asdict
import hashlib
import json
import secrets
from time import monotonic_ns
import uuid

from job_safety import (JobPermit, PhysicalAction, PermanentJobSafety, JobSafetyError, action_digest,
    NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS, NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS)
from mcu_session import McuCommandDispatcher
from work_recovery import canonical
import uart2_protocol as uart


def original(store, work_uid):
    issue = store.get_native_delivery_issue(work_uid)
    if issue is None:
        raise ValueError("delivery recovery close requires an archived issue")
    first = store.get_native_action_by_key(work_uid, "delivery:first-open")
    if first is None or asdict(first["permit"]) != issue["permit"]:
        raise ValueError("delivery recovery close lacks its original action")
    source = store.get_native_command(first["action"].action_uid)
    fields = uart.decode_payload(source["message_name"], source["payload"])
    if (source["message_name"] != "AUTHORIZE_DELIVERY_FIRST_OPEN" or source["conflict"]
            or not source["write_claimed"] or fields["targetMcuBootId"] != issue["sourceMcuBootId"]):
        raise ValueError("delivery recovery close original dispatch is inconsistent")
    return issue, first, source, fields


def check_occupancy(store, issue, port):
    slot = store.get_work_slot()
    if slot is None or (slot["work_uid"], slot["work_type"], slot["port_no"]) != (issue["workUid"], "DELIVERY", port):
        raise ValueError("delivery recovery close no longer owns original occupancy")


def check_source_ledger(store, first, ledger):
    from mcu_action_evidence import check_ledger, executed_bundle
    check_ledger(first, ledger)
    proof = store.get_native_action_confirmation(first["action"].action_uid)
    if ledger["state"] == "CONFIRMED":
        if proof is None:
            raise ValueError("delivery recovery close lacks original confirmed output proof")
        check_ledger(first, ledger, proof)
        actual = executed_bundle(store, first["action"].action_uid, json.loads(proof["bundle_json"]))
        if actual is None or canonical(actual) != proof["bundle_json"]:
            raise ValueError("delivery recovery close original confirmation lost its raw evidence")
    elif ledger["state"] != "ARMED" or (proof is not None and proof["state"] == "CONFIRMED"):
        raise ValueError("delivery recovery close original permanent state is inconsistent")


def _checked_binding_one(store, row):
    value = json.loads(row["binding_json"])
    if (canonical(value) != row["binding_json"] or hashlib.sha256(row["binding_json"].encode("ascii")).hexdigest() != row["binding_sha256"]):
        raise ValueError("delivery recovery close binding is corrupt")
    if set(value) != {"permit", "action", "evidence"}:
        raise ValueError("delivery recovery close binding fields are corrupt")
    permit, action = JobPermit(**value["permit"]), PhysicalAction(**value["action"])
    issue, first, source, fields = original(store, permit.work_uid)
    record = store.get_native_command(action.action_uid)
    if record is None or record["message_name"] != "SAFE_CLOSE":
        raise ValueError("delivery recovery close command missing")
    close = uart.decode_payload("SAFE_CLOSE", record["payload"])
    evidence = value["evidence"]
    if set(evidence) not in (NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS, NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS):
        raise ValueError("delivery recovery close ancestry fields are incomplete")
    if type(evidence["expectedSourceLedgerSequence"]) is not int or evidence["expectedSourceLedgerSequence"] < 1:
        raise ValueError("delivery recovery close original ledger sequence is invalid")
    successor = "predecessorActionUid" in evidence
    predecessor = evidence["predecessorActionUid"] if successor else first["action"].action_uid
    if (("predecessor_action_uid" in row.keys() and row["predecessor_action_uid"] != predecessor)
            or (successor and "predecessor_action_uid" not in row.keys())):
        raise ValueError("delivery recovery close predecessor index is corrupt")
    intent = store.get_native_work_recovery_intent(issue["recoveryUid"])
    recovery_uid = evidence["recoveryUid"] if successor else issue["recoveryUid"]
    expected = dict(recoveryUid=recovery_uid, sourceActionUid=first["action"].action_uid,
        sourceActionDigestSha256=first["action"].action_digest_sha256,
        expectedSourceLedgerSequence=evidence["expectedSourceLedgerSequence"],
        sourceMcuBootId=issue["sourceMcuBootId"], targetMcuBootId=evidence["targetMcuBootId"] if successor else issue["targetMcuBootId"],
        portNo=fields["portNo"], reason="MCU_RESTART_DATA_LOSS", recoveryEvidenceSha256=intent["evidence_sha256"],
        sourceCommandPayloadHex=source["payload"].hex(), closeCommandPayloadHex=record["payload"].hex())
    if successor:
        expected.update({key: evidence[key] for key in NATIVE_RECOVERY_CLOSE_SUCCESSOR_EVIDENCE_FIELDS
            - NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS})
        if (type(evidence["expectedPredecessorLedgerSequence"]) is not int
                or evidence["expectedPredecessorLedgerSequence"] <= evidence["expectedSourceLedgerSequence"]
                or predecessor in {action.action_uid, first["action"].action_uid}
                or recovery_uid == issue["recoveryUid"]):
            raise ValueError("delivery recovery close predecessor identity is invalid")
    digest = action_digest(work_uid=permit.work_uid, command_uid=permit.command_uid, action_key=action.action_key,
        action_kind="SAFE_CLOSE", payload={"nativeUartPayloadHex": record["payload"].hex()})
    if (asdict(permit) != issue["permit"] or row["issue_uid"] != issue["issueUid"]
            or row["action_uid"] != action.action_uid or action.action_kind != "SAFE_CLOSE"
            or action.action_key != "native:recovery-close:" + recovery_uid
            or action.action_digest_sha256 != digest or evidence != expected
            or type(evidence["expectedSourceLedgerSequence"]) is not int or evidence["expectedSourceLedgerSequence"] < 1
            or close["scope"] != "SINGLE_DELIVERY_DOOR" or close["portNo"] != fields["portNo"]
            or close["targetMcuBootId"] != expected["targetMcuBootId"]
            or type(expected["targetMcuBootId"]) is not int or expected["targetMcuBootId"] < issue["targetMcuBootId"]
            or not 100 < close["executionDeadlineMs"] <= 0xFFFFFFFF):
        raise ValueError("delivery recovery close differs from archived original identity")
    for uid in (action.action_uid, action.receipt_uid, recovery_uid):
        if str(uuid.UUID(uid)) != uid or uuid.UUID(uid).version != 4:
            raise ValueError("delivery recovery close requires stable UUIDv4 identities")
    return dict(permit=permit, action=action, evidence=evidence)


def _retired_parent_proof(store, conn, binding):
    row = conn.execute("SELECT * FROM native_recovery_close_retirement WHERE action_uid=?",
        (binding["action"].action_uid,)).fetchone()
    if row is None or row["state"] != "RETIRED":
        raise ValueError("delivery recovery close successor requires a retired predecessor")
    actual = canonical(retirement_bundle(store, conn, binding["action"].action_uid, binding=binding))
    if actual != row["bundle_json"] or retirement_digest(actual) != row["evidence_sha256"]:
        raise ValueError("delivery recovery close predecessor retirement evidence is corrupt")
    return dict(row)


def _predecessor_proof(store, conn, binding):
    from native_recovery_close_isolation import checked
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_recovery_close_isolation'").fetchone():
        row = conn.execute("SELECT * FROM native_recovery_close_isolation WHERE action_uid=?",
            (binding["action"].action_uid,)).fetchone()
        if row is not None:
            proof = checked(store, conn, row, binding=binding)
            if proof["state"] != "ISOLATED":
                raise ValueError("delivery recovery close predecessor isolation is not confirmed")
            return proof, json.loads(proof["bundle_json"])["bootObservation"]["observedMcuBootId"]
    return _retired_parent_proof(store, conn, binding), binding["evidence"]["targetMcuBootId"]


def checked_binding(store, row):
    """Validate the whole immutable ancestry without recursive getter calls."""
    result = current = _checked_binding_one(store, row)
    seen_actions, seen_receipts, seen_recoveries = set(), set(), set()
    while True:
        action, evidence = current["action"], current["evidence"]
        for seen, uid in ((seen_actions, action.action_uid), (seen_receipts, action.receipt_uid),
                (seen_recoveries, evidence["recoveryUid"])):
            if uid in seen:
                raise ValueError("delivery recovery close ancestry reuses an identity")
            seen.add(uid)
        if "predecessorActionUid" not in evidence:
            return result
        parent_row = store._conn.execute("SELECT * FROM native_delivery_recovery_close WHERE action_uid=?",
            (evidence["predecessorActionUid"],)).fetchone()
        if parent_row is None:
            raise ValueError("delivery recovery close predecessor binding missing")
        parent = _checked_binding_one(store, parent_row)
        parent_evidence = parent["evidence"]
        fixed = NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS - {"recoveryUid", "closeCommandPayloadHex", "targetMcuBootId"}
        child_command = store.get_native_command(action.action_uid)
        parent_command = store.get_native_command(parent["action"].action_uid)
        proof, target_boot = _predecessor_proof(store, store._conn, parent)
        if (current["permit"] != parent["permit"]
                or any(evidence[key] != parent_evidence[key] for key in fixed)
                or evidence["targetMcuBootId"] != target_boot
                or (child_command["mcu_boot_id"] == parent_command["mcu_boot_id"]
                    and child_command["command_sequence"] <= parent_command["command_sequence"])
                or evidence["expectedPredecessorLedgerSequence"] <= parent_evidence.get(
                    "expectedPredecessorLedgerSequence", parent_evidence["expectedSourceLedgerSequence"])
                or evidence["predecessorReceiptUid"] != parent["action"].receipt_uid
                or evidence["predecessorRetirementEvidenceSha256"] != proof["evidence_sha256"]):
            raise ValueError("delivery recovery close ancestry differs from original retired preparation")
        current = parent


def _insert_binding(conn, issue_uid, permit, action, evidence, predecessor):
    raw = canonical(dict(permit=asdict(permit), action=asdict(action), evidence=evidence))
    values = (action.action_uid, issue_uid, raw, hashlib.sha256(raw.encode("ascii")).hexdigest())
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(native_delivery_recovery_close)")}
    if "predecessor_action_uid" in columns:
        conn.execute("""INSERT INTO native_delivery_recovery_close
            (action_uid,issue_uid,binding_json,binding_sha256,predecessor_action_uid) VALUES(?,?,?,?,?)""",
            (*values, predecessor))
    else:
        conn.execute("""INSERT INTO native_delivery_recovery_close
            (action_uid,issue_uid,binding_json,binding_sha256) VALUES(?,?,?,?)""", values)


def prepare_in_transaction(store, conn, work_uid, ledger, current_boot, window):
    if type(window) is not int or not 100 < window <= 0xFFFFFFFF:
        raise ValueError("delivery recovery close execution window is invalid")
    issue, first, source, fields = original(store, work_uid)
    check_occupancy(store, issue, fields["portNo"])
    # A damaged predecessor index must not make an existing root disappear and
    # silently authorize a second root preparation for the same archived work.
    for existing in conn.execute("SELECT * FROM native_delivery_recovery_close WHERE issue_uid=?", (issue["issueUid"],)):
        checked_binding(store, existing)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(native_delivery_recovery_close)")}
    if "predecessor_action_uid" in columns:
        old = conn.execute("SELECT * FROM native_delivery_recovery_close WHERE predecessor_action_uid=?",
            (first["action"].action_uid,)).fetchone()
    else:
        old = conn.execute("SELECT * FROM native_delivery_recovery_close WHERE issue_uid=?", (issue["issueUid"],)).fetchone()
    if old is not None:
        return checked_binding(store, old), False
    if current_boot() != issue["targetMcuBootId"]:
        raise ValueError("delivery recovery close requires fresh original target boot")
    permit = first["permit"]
    check_source_ledger(store, first, ledger)
    uid, receipt = str(uuid.uuid4()), str(uuid.uuid4())
    record = store._prepare_native_command_in_tx(conn, "SAFE_CLOSE", uid, issue["targetMcuBootId"],
        dict(scope="SINGLE_DELIVERY_DOOR", portNo=fields["portNo"], executionDeadlineMs=window))
    key = "native:recovery-close:" + issue["recoveryUid"]
    action = PhysicalAction(uid, receipt, key, "SAFE_CLOSE", action_digest(work_uid=work_uid,
        command_uid=permit.command_uid, action_key=key, action_kind="SAFE_CLOSE",
        payload={"nativeUartPayloadHex": record["payload"].hex()}))
    intent = store.get_native_work_recovery_intent(issue["recoveryUid"])
    evidence = dict(recoveryUid=issue["recoveryUid"], sourceActionUid=first["action"].action_uid,
        sourceActionDigestSha256=first["action"].action_digest_sha256, expectedSourceLedgerSequence=ledger["ledgerSequence"],
        sourceMcuBootId=issue["sourceMcuBootId"], targetMcuBootId=issue["targetMcuBootId"], portNo=fields["portNo"],
        reason="MCU_RESTART_DATA_LOSS", recoveryEvidenceSha256=intent["evidence_sha256"],
        sourceCommandPayloadHex=source["payload"].hex(), closeCommandPayloadHex=record["payload"].hex())
    _insert_binding(conn, issue["issueUid"], permit, action, evidence, first["action"].action_uid)
    return store.get_native_delivery_recovery_close(uid), True


def prepare_successor_in_transaction(store, conn, predecessor_action_uid, source_ledger,
        predecessor_ledger, current_boot, window, predecessor_disposition=None):
    if type(window) is not int or not 100 < window <= 0xFFFFFFFF:
        raise ValueError("delivery recovery close execution window is invalid")
    parent = store.get_native_delivery_recovery_close(predecessor_action_uid)
    if parent is None:
        raise ValueError("delivery recovery close predecessor binding missing")
    permit = parent["permit"]
    issue, first, _, fields = original(store, permit.work_uid)
    check_occupancy(store, issue, fields["portNo"])
    for existing in conn.execute("SELECT * FROM native_delivery_recovery_close WHERE issue_uid=?", (issue["issueUid"],)):
        checked_binding(store, existing)
    proof, target_boot = _predecessor_proof(store, conn, parent)
    if predecessor_disposition is None:
        check_retirement_ledger(parent, proof, predecessor_ledger)
    elif predecessor_disposition.get("state") == "ISOLATED_BY_REBOOT":
        from native_recovery_close_isolation import check_disposition
        if proof["state"] != "ISOLATED":
            raise ValueError("delivery recovery close predecessor isolation proof mismatch")
        check_disposition(parent, proof, predecessor_ledger, predecessor_disposition)
    else:
        check_withdrawal_disposition(parent, proof, predecessor_ledger, predecessor_disposition)
    parent_sequence = predecessor_ledger.get("ledgerSequence")
    if (type(parent_sequence) is not int or parent_sequence <= parent["evidence"].get(
            "expectedPredecessorLedgerSequence", parent["evidence"]["expectedSourceLedgerSequence"])):
        raise ValueError("delivery recovery close predecessor ledger order is invalid")
    # Idempotency names exactly one direct child, never its newest descendant.
    old = conn.execute("SELECT * FROM native_delivery_recovery_close WHERE predecessor_action_uid=?",
        (predecessor_action_uid,)).fetchone()
    if old is not None:
        binding = checked_binding(store, old)
        if binding["evidence"]["expectedPredecessorLedgerSequence"] != parent_sequence:
            raise ValueError("delivery recovery close predecessor ledger changed")
        return binding, False
    if current_boot() != target_boot:
        raise ValueError("delivery recovery close requires fresh original target boot")
    check_source_ledger(store, first, source_ledger)
    if source_ledger["ledgerSequence"] != parent["evidence"]["expectedSourceLedgerSequence"]:
        raise ValueError("delivery recovery close original source ledger changed")
    uid, receipt, recovery_uid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    record = store._prepare_native_command_in_tx(conn, "SAFE_CLOSE", uid, target_boot,
        dict(scope="SINGLE_DELIVERY_DOOR", portNo=fields["portNo"], executionDeadlineMs=window))
    key = "native:recovery-close:" + recovery_uid
    action = PhysicalAction(uid, receipt, key, "SAFE_CLOSE", action_digest(work_uid=permit.work_uid,
        command_uid=permit.command_uid, action_key=key, action_kind="SAFE_CLOSE",
        payload={"nativeUartPayloadHex": record["payload"].hex()}))
    evidence = {key: value for key, value in parent["evidence"].items() if key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS}
    evidence.update(recoveryUid=recovery_uid, targetMcuBootId=target_boot, closeCommandPayloadHex=record["payload"].hex(),
        predecessorActionUid=predecessor_action_uid, expectedPredecessorLedgerSequence=parent_sequence,
        predecessorReceiptUid=parent["action"].receipt_uid,
        predecessorRetirementEvidenceSha256=proof["evidence_sha256"])
    _insert_binding(conn, issue["issueUid"], permit, action, evidence, predecessor_action_uid)
    return store.get_native_delivery_recovery_close(uid), True


class NativeDeliveryRecoveryClose:
    def __init__(self, store, safety, boot, write, *, revalidate, clock=lambda: monotonic_ns() // 1000000):
        if not isinstance(safety, PermanentJobSafety) or safety.enabled is not True or not callable(revalidate):
            raise ValueError("delivery recovery close requires permanent authority and live prerequisites")
        self.store, self.safety, self.boot = store, safety, boot
        self.clock, self.revalidate = clock, revalidate
        self.live = {}  # Never restore a process's dispatch token or deadline.
        self.dispatcher = McuCommandDispatcher(store, boot, write, arm=self._arm, clock=clock)

    def prepare(self, work_uid, *, execution_window_ms):
        _, first, _, _ = original(self.store, work_uid)
        started = self.clock()
        prepared, created = self.store.prepare_native_delivery_recovery_close(work_uid,
            self.safety.get_physical_action(first["action"].action_uid),
            current_boot=lambda: self.boot.current_boot(self.clock()), execution_window_ms=execution_window_ms)
        if created:
            self.live[prepared["action"].action_uid] = (secrets.token_hex(32), started + execution_window_ms)
        return prepared

    def prepare_successor(self, predecessor_action_uid, *, execution_window_ms):
        started = self.clock()
        parent = self.store.get_native_delivery_recovery_close(predecessor_action_uid)
        if parent is None:
            raise ValueError("delivery recovery close predecessor binding missing")
        source_ledger = self.safety.get_physical_action(parent["evidence"]["sourceActionUid"])
        predecessor_ledger = self.safety.get_physical_action(predecessor_action_uid)
        predecessor_disposition = (self.safety.get_native_recovery_close_disposition(predecessor_action_uid)
            if predecessor_ledger.get("dispatchMode") == "TWO_PHASE_V3" else None)
        prepared, created = self.store.prepare_native_delivery_recovery_close_successor(predecessor_action_uid,
            source_ledger, predecessor_ledger, current_boot=lambda: self.boot.current_boot(self.clock()),
            execution_window_ms=execution_window_ms, predecessor_disposition=predecessor_disposition)
        if created:
            self.live[prepared["action"].action_uid] = (secrets.token_hex(32), started + execution_window_ms)
        return prepared

    def _check(self, record):
        if self.store.get_native_recovery_close_retirement(record["command_uid"]) is not None:
            raise JobSafetyError("RECOVERY_CLOSE_RETIRING", "original recovery close dispatch was withdrawn")
        binding = self.store.get_native_delivery_recovery_close(record["command_uid"])
        if binding is None or binding["evidence"]["closeCommandPayloadHex"] != record["payload"].hex():
            raise ValueError("delivery recovery close binding missing or changed")
        active = self.live.get(record["command_uid"])
        if active is None:
            raise JobSafetyError("RECOVERY_DISPATCH_NOT_LIVE", "restart must reconcile the original close, not resend")
        if self.clock() >= active[1]:
            raise JobSafetyError("COMMAND_EXPIRED", "original recovery close deadline expired")
        self.revalidate(dict(record))
        source_ledger = self.safety.get_physical_action(binding["evidence"]["sourceActionUid"])
        if self.store.validate_native_recovery_close_source(record["command_uid"], source_ledger) != binding:
            raise ValueError("delivery recovery close binding changed during prerequisites")
        if self.clock() >= active[1]:
            raise JobSafetyError("COMMAND_EXPIRED", "recovery prerequisites exceeded deadline")
        return binding, active[0]

    def _arm(self, record):
        binding, token = self._check(record)
        self.safety.prepare_native_recovery_close(binding["permit"], action=binding["action"],
            evidence=binding["evidence"], dispatch_attempt_token=token)
        self._check(record)
        self.safety.arm_physical_action(binding["action"], dispatch_attempt_token=token)
        self._check(record)
        return lambda: self._check(record)

    def send_once(self, action_uid):
        return self.dispatcher.send_once(action_uid)

    def poll(self, action_uid, now_ms):
        return self.dispatcher.poll(action_uid, now_ms)

    def accept_frame(self, frame, now_ms):
        return self.dispatcher.accept_frame(frame, now_ms)


def close_effect_bundle(store, conn, action_uid, pinned=None):
    """Historical output proof only; PB5/position and present safety are separate."""
    from mcu_action_evidence import accepted_command_witness
    binding = store.get_native_delivery_recovery_close(action_uid)
    if binding is None:
        raise ValueError("native recovery close binding missing")
    command = store.get_native_command(action_uid)
    acceptance = accepted_command_witness(store, command, pinned.get("acceptance") if pinned else None)
    if acceptance is None:
        return None
    # The command UID is at byte 20 of every actuator event. Inspect both the
    # wire identity and its copied index, so corruption cannot hide a witness.
    rows = conn.execute("""SELECT mcu_boot_id,event_sequence FROM native_actuator_event
        WHERE reported_command_uid=? OR substr(payload,21,16)=?
        ORDER BY mcu_boot_id,event_sequence LIMIT 2""", (action_uid, uuid.UUID(action_uid).bytes)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("native recovery close has multiple output claims")
    output = store.get_native_actuator_event(rows[0]["mcu_boot_id"], rows[0]["event_sequence"])
    if output["message_name"] != "SAFE_CLOSE_RESULT":
        raise ValueError("native recovery close has wrong output kind")
    value = uart.decode_payload("SAFE_CLOSE_RESULT", output["payload"])
    expected = dict(mcuCommandUid=action_uid, mcuBootId=command["mcu_boot_id"],
        scope="SINGLE_DELIVERY_DOOR", portNo=binding["evidence"]["portNo"], command="CLOSE",
        physicalDoorStateBasis="NOT_OBSERVABLE")
    if any(value[k] != v for k, v in expected.items()):
        raise ValueError("native recovery close output differs from original identity")
    if value["outputStatus"] not in {"COMMAND_DISPATCHED", "COALESCED_WITH_EXISTING_CLOSE"} or value["faultCode"] != "NONE":
        return None  # A rejected output cannot establish an executed close.
    return dict(version="ecobin-native-recovery-close-effect-v1", outcome="EXECUTED", basis="MCU_IDENTITY_BOUND_FACT",
        permit=asdict(binding["permit"]), action=asdict(binding["action"]), recovery=binding["evidence"],
        command=dict(messageName="SAFE_CLOSE", payloadHex=command["payload"].hex()), acceptance=acceptance,
        output=dict(messageName="SAFE_CLOSE_RESULT", payloadHex=output["payload"].hex(), savedHex=output["saved_payload"].hex()))


def checked_close_confirmation(store, conn, row):
    from mcu_action_evidence import checked_confirmation
    proof = checked_confirmation(row)
    bundle = close_effect_bundle(store, conn, row["action_uid"], json.loads(proof["bundle_json"]))
    if bundle is None or canonical(bundle) != proof["bundle_json"]:
        raise ValueError("native recovery close confirmation lost its original custody")
    return proof


class NativeRecoveryCloseReconciler:
    """Durable new-close proof to permanent ledger; never UART, money or release."""
    def __init__(self, store, safety):
        if not isinstance(safety, PermanentJobSafety) or safety.enabled is not True:
            raise ValueError("native recovery close reconciliation requires permanent authority")
        self.store, self.safety = store, safety

    def reconcile_pending(self, *, limit=32):
        results = []
        for uid in self.store.list_pending_native_recovery_close_uids(limit=limit):
            result = self.reconcile(uid)
            if result is not None:
                results.append(result)
        return results

    def reconcile(self, action_uid):
        from mcu_action_evidence import check_ledger
        proof = self.store.prepare_native_recovery_close_confirmation(action_uid)
        if proof is None:
            return None
        binding = self.store.get_native_delivery_recovery_close(action_uid)
        permit, action = binding["permit"], binding["action"]
        owner = self.safety.get_job_permit(permit.permit_uid)
        expected_owner = dict(permitUid=permit.permit_uid, workUid=permit.work_uid, commandUid=permit.command_uid,
            workType="DELIVERY", requestDigestSha256=permit.request_digest_sha256, state="ACTIVE")
        if any(owner.get(k) != v for k, v in expected_owner.items()):
            raise ValueError("native recovery close permanent owner mismatch")
        saved = self.safety.get_native_recovery_close(action_uid)
        expected = dict(binding["evidence"], actionUid=action_uid, permitUid=permit.permit_uid, workUid=permit.work_uid,
            commandUid=permit.command_uid, actionKey=action.action_key, actionKind=action.action_kind,
            actionDigestSha256=action.action_digest_sha256, disposition="FOUND")
        if saved != expected:
            raise ValueError("native recovery close permanent evidence mismatch")
        ledger = self.safety.get_physical_action(action_uid)
        check_ledger(binding, ledger)
        if ledger["state"] != "CONFIRMED":
            if ledger["state"] != "ARMED" or proof["state"] == "CONFIRMED":
                raise ValueError("native recovery close permanent action is not armed")
            if self.store.prepare_native_recovery_close_confirmation(action_uid) != proof:
                raise ValueError("native recovery close evidence changed during ledger query")
            self.safety.confirm_physical_action(action, outcome="EXECUTED",
                evidence_sha256=proof["evidence_sha256"], confirmation_basis="MCU_IDENTITY_BOUND_FACT")
            ledger = self.safety.get_physical_action(action_uid)
        check_ledger(binding, ledger, proof)
        return self.store.confirm_native_recovery_close_effect(action_uid, ledger)


def retirement_bundle(store, conn, action_uid, *, binding=None):
    if binding is None:
        binding = store.get_native_delivery_recovery_close(action_uid)
    if binding is None:
        raise ValueError("recovery close retirement lacks original binding")
    record = store.get_native_command(action_uid)
    actuator_names = tuple(uart.REGISTRY["sessionPolicy"]["actuatorEventMessages"])
    conflicting_output = conn.execute("SELECT 1 FROM native_actuator_event_conflict WHERE message_name IN ("
        + ",".join("?" for _ in actuator_names) + ") AND substr(payload,21,16)=? LIMIT 1",
        (*actuator_names, uuid.UUID(action_uid).bytes)).fetchone()
    if (record["write_claimed"] or record["conflict"] or record["decision_outcome"] is not None
            or conflicting_output
            or store.list_native_command_observations(action_uid)
            or conn.execute("SELECT 1 FROM native_recovery_close_confirmation WHERE action_uid=?", (action_uid,)).fetchone()
            or conn.execute("SELECT 1 FROM native_actuator_event WHERE reported_command_uid=? OR substr(payload,21,16)=?",
                (action_uid, uuid.UUID(action_uid).bytes)).fetchone()):
        raise ValueError("recovery close retirement cannot claim an observed or write-claimed command was unsent")
    return dict(version="ecobin-native-recovery-close-retirement-v1", intent="WITHDRAW_UNCLAIMED_PREPARATION",
        permit=asdict(binding["permit"]), action=asdict(binding["action"]), recovery=binding["evidence"],
        command=dict(messageName="SAFE_CLOSE", payloadHex=record["payload"].hex()))


def retirement_digest(raw):
    return hashlib.sha256(b"EcoBin/native-recovery-close-retirement/v1\0" + raw.encode("ascii")).hexdigest()


def checked_retirement(store, conn, row):
    proof = dict(row)
    actual = canonical(retirement_bundle(store, conn, proof["action_uid"]))
    if (actual != proof["bundle_json"] or retirement_digest(actual) != proof["evidence_sha256"]
            or proof["state"] not in {"PENDING", "RETIRED"}):
        raise ValueError("recovery close retirement evidence is corrupt")
    return proof


def check_retirement_ledger(binding, proof, ledger):
    permit, action = binding["permit"], binding["action"]
    expected = dict(actionUid=action.action_uid, permitUid=permit.permit_uid, workUid=permit.work_uid,
        commandUid=permit.command_uid, actionKey=action.action_key, actionKind="SAFE_CLOSE",
        actionDigestSha256=action.action_digest_sha256, state="CONFIRMED", dispatchMode="PREPARED_ONLY",
        confirmedOutcome="NOT_EXECUTED", confirmationBasis="PREPARED_NOT_ARMED", receiptUid=action.receipt_uid,
        evidenceDigestSha256=proof["evidence_sha256"], unknownEffectResolution=None, mayExecute=False)
    if any(ledger.get(k) != v for k, v in expected.items()):
        raise ValueError("recovery close retirement permanent confirmation mismatch")


class NativeRecoveryCloseRetirement:
    """Withdraw old dispatch durably, then prove permanent authorization never existed."""
    def __init__(self, store, safety):
        if not isinstance(safety, PermanentJobSafety) or safety.enabled is not True:
            raise ValueError("recovery close retirement requires permanent authority")
        self.store, self.safety = store, safety

    def reconcile_pending(self, *, limit=32):
        return [self.reconcile(uid) for uid in self.store.list_pending_native_recovery_close_retirements(limit=limit)]

    def reconcile(self, action_uid):
        proof = self.store.prepare_native_recovery_close_retirement(action_uid)
        binding = self.store.get_native_delivery_recovery_close(action_uid)
        self.safety.retire_native_recovery_close(binding["permit"], action=binding["action"],
            evidence=binding["evidence"], retirement_evidence_sha256=proof["evidence_sha256"])
        ledger = self.safety.get_physical_action(action_uid)
        check_retirement_ledger(binding, proof, ledger)
        return self.store.confirm_native_recovery_close_retirement(action_uid, ledger)


def check_withdrawal_disposition(binding, proof, ledger, disposition):
    """A withdrawn send right does not rewrite the original authorization fact."""
    from mcu_action_evidence import check_ledger
    check_ledger(binding, ledger)
    if (ledger.get("state") != "ARMED" or ledger.get("dispatchMode") != "TWO_PHASE_V3"
            or ledger.get("confirmedOutcome") is not None or ledger.get("confirmationBasis") is not None
            or ledger.get("receiptUid") is not None or ledger.get("evidenceDigestSha256") is not None
            or ledger.get("unknownEffectResolution") is not None
            or type(ledger.get("ledgerSequence")) is not int):
        raise ValueError("recovery close withdrawal lost original armed authorization")
    permit, action = binding["permit"], binding["action"]
    expected = dict(binding["evidence"], actionUid=action.action_uid, permitUid=permit.permit_uid,
        workUid=permit.work_uid, commandUid=permit.command_uid, actionKey=action.action_key,
        actionKind=action.action_kind, actionDigestSha256=action.action_digest_sha256,
        receiptUid=action.receipt_uid, ledgerSequence=ledger["ledgerSequence"],
        dispositionBasis="AUTHORIZED_DISPATCH_WITHDRAWN_BEFORE_WRITE_CLAIM",
        evidenceDigestSha256=proof["evidence_sha256"], state="DISPATCH_WITHDRAWN",
        mayExecute=False, disposition="FOUND")
    if (not isinstance(disposition, dict) or disposition != expected
            or any(type(disposition[key]) is not type(value) for key, value in expected.items())):
        raise ValueError("recovery close withdrawal permanent disposition mismatch")


class NativeRecoveryCloseWithdrawal:
    """Withdraw an authorized unclaimed send; retain the original ARMED ledger."""
    def __init__(self, store, safety):
        if not isinstance(safety, PermanentJobSafety) or safety.enabled is not True:
            raise ValueError("recovery close withdrawal requires permanent authority")
        self.store, self.safety = store, safety

    def reconcile(self, action_uid):
        proof = self.store.prepare_native_recovery_close_retirement(action_uid)
        binding = self.store.get_native_delivery_recovery_close(action_uid)
        self.safety.withdraw_native_recovery_close_dispatch(binding["permit"], action=binding["action"],
            evidence=binding["evidence"], retirement_evidence_sha256=proof["evidence_sha256"])
        ledger = self.safety.get_physical_action(action_uid)
        disposition = self.safety.get_native_recovery_close_disposition(action_uid)
        check_withdrawal_disposition(binding, proof, ledger, disposition)
        return self.store.confirm_native_recovery_close_withdrawal(action_uid, ledger, disposition)
