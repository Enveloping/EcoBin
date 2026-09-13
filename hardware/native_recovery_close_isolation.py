"""Historical unknown close effect fenced by a newer positively observed boot."""
from dataclasses import asdict
import hashlib
import json
from time import monotonic_ns
import uuid
from job_safety import PermanentJobSafety
from work_recovery import canonical
import uart2_protocol as uart


def digest(raw):
    return hashlib.sha256(b"EcoBin/native-recovery-close-isolation/v1\0" + raw.encode("ascii")).hexdigest()


def _snapshot(store, conn, uid, command, pinned=None):
    if pinned is not None and (not isinstance(pinned, dict) or set(pinned) != {"observations", "outputs"}
            or any(not isinstance(values, list) or sorted(values, key=canonical) != values
                or len({canonical(value) for value in values}) != len(values) for values in pinned.values())):
        raise ValueError("recovery close isolation frozen evidence is corrupt")
    observations = []
    for row in store.list_native_command_observations(uid):
        name, raw = row["message_name"], row["payload"]
        entry = dict(messageName=name, payloadHex=raw.hex())
        if pinned is not None and entry not in pinned["observations"]:
            continue
        value = uart.decode_payload(name, raw)
        offset = 8 if name == "COMMAND_QUERY_RESULT" else 0
        if (name not in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}
                or raw[offset:offset + 60] != command["payload"][:60]
                or row["outcome"] != value["outcome"] or row["error_code"] != value["errorCode"]
                or row["current_boot_id"] != value["currentMcuBootId"]
                or row["highest_sequence"] != value.get("highestCommandSequence", -1)):
            raise ValueError("recovery close isolation command observation is corrupt")
        observations.append(entry)
    outputs = []
    close = uart.decode_payload("SAFE_CLOSE", command["payload"])
    for row in conn.execute("""SELECT * FROM native_actuator_event
            WHERE reported_command_uid=? OR substr(payload,21,16)=?""", (uid, uuid.UUID(uid).bytes)):
        entry = dict(messageName=row["message_name"], payloadHex=row["payload"].hex(), savedHex=row["saved_payload"].hex())
        if pinned is not None and entry not in pinned["outputs"]:
            continue
        if pinned is None:
            event = store.get_native_actuator_event(row["mcu_boot_id"], row["event_sequence"])
        else:
            store._verify_native_actuator_row(row)  # A later conflict cannot erase already frozen raw evidence.
            event = dict(row)
        if event["message_name"] != "SAFE_CLOSE_RESULT":
            raise ValueError("recovery close isolation has contradictory output kind")
        value = uart.decode_payload("SAFE_CLOSE_RESULT", event["payload"])
        expected = dict(mcuCommandUid=uid, mcuBootId=command["mcu_boot_id"], scope=close["scope"],
            portNo=close["portNo"], command="CLOSE", physicalDoorStateBasis="NOT_OBSERVABLE")
        if any(value[key] != expected_value for key, expected_value in expected.items()):
            raise ValueError("recovery close isolation output differs from original identity")
        outputs.append(entry)
    snapshot = dict(observations=sorted(observations, key=canonical), outputs=sorted(outputs, key=canonical))
    if len(outputs) > 1 or (pinned is not None and snapshot != pinned):
        raise ValueError("recovery close isolation lost or duplicated original evidence")
    return snapshot


def bundle(store, conn, uid, *, binding=None, boot_observation=None, pinned=None):
    if binding is None:
        binding = store.get_native_delivery_recovery_close(uid)
    if binding is None:
        raise ValueError("recovery close isolation lacks original binding")
    command = store.get_native_command(uid)
    if not command["write_claimed"] or not command["boot_retired"] or command["dispatch_retired"]:
        raise ValueError("recovery close isolation requires a claimed old boot command")
    if conn.execute("SELECT 1 FROM native_recovery_close_retirement WHERE action_uid=?", (uid,)).fetchone():
        raise ValueError("recovery close isolation cannot replace a withdrawal")
    if conn.execute("SELECT 1 FROM native_recovery_close_confirmation WHERE action_uid=?", (uid,)).fetchone():
        raise ValueError("recovery close isolation cannot replace reliable output")
    boot_observation = pinned["bootObservation"] if pinned is not None else boot_observation
    if not isinstance(boot_observation, dict) or set(boot_observation) != {
            "observedMcuBootId", "bootObservationMessageName", "bootObservationPayloadHex"}:
        raise ValueError("recovery close isolation boot witness is incomplete")
    boot_id = boot_observation["observedMcuBootId"]
    if type(boot_id) is not int or boot_id <= command["mcu_boot_id"]:
        raise ValueError("recovery close isolation requires a newer boot")
    witness = store.get_native_boot_observation(boot_id)
    if witness is None or boot_observation != dict(observedMcuBootId=boot_id,
            bootObservationMessageName=witness["message_name"], bootObservationPayloadHex=witness["payload"].hex()):
        raise ValueError("recovery close isolation lost original owned boot witness")
    snapshot = _snapshot(store, conn, uid, command, pinned["availableEvidence"] if pinned is not None else None)
    if pinned is None:
        if command["conflict"] or command["decision_outcome"] not in {None, "ACCEPTED"}:
            raise ValueError("known failed or conflicting close requires separate disposition")
        if conn.execute("SELECT 1 FROM native_actuator_event_conflict WHERE substr(payload,21,16)=?",
                (uuid.UUID(uid).bytes,)).fetchone():
            raise ValueError("conflicting close requires separate disposition")
        for entry in snapshot["outputs"]:
            value = uart.decode_payload("SAFE_CLOSE_RESULT", bytes.fromhex(entry["payloadHex"]))
            if value["outputStatus"] not in {"COMMAND_DISPATCHED", "COALESCED_WITH_EXISTING_CLOSE"} or value["faultCode"] != "NONE":
                raise ValueError("known failed close requires separate disposition")
    return dict(version="ecobin-native-recovery-close-isolation-v1", pastEffect="UNKNOWN",
        intent="ISOLATE_OLD_TARGET_AFTER_NEWER_MCU_BOOT", permit=asdict(binding["permit"]),
        action=asdict(binding["action"]), recovery=binding["evidence"], bootObservation=boot_observation,
        command=dict(messageName="SAFE_CLOSE", payloadHex=command["payload"].hex()), availableEvidence=snapshot)


def checked(store, conn, row, *, binding=None):
    proof = dict(row)
    raw = proof["bundle_json"]
    actual = bundle(store, conn, proof["action_uid"], binding=binding, pinned=json.loads(raw))
    if (canonical(actual) != raw or digest(raw) != proof["evidence_sha256"]
            or proof["state"] not in {"PENDING", "ISOLATED"}):
        raise ValueError("recovery close isolation evidence is corrupt")
    return proof


def check_disposition(binding, proof, ledger, disposition):
    from mcu_action_evidence import check_ledger
    check_ledger(binding, ledger)
    if (ledger.get("state") != "ARMED" or ledger.get("confirmedOutcome") is not None
            or ledger.get("confirmationBasis") is not None or ledger.get("receiptUid") is not None
            or ledger.get("evidenceDigestSha256") is not None or type(ledger.get("ledgerSequence")) is not int):
        raise ValueError("recovery close isolation lost original authorized history")
    permit, action = binding["permit"], binding["action"]
    expected = dict(binding["evidence"], **json.loads(proof["bundle_json"])["bootObservation"],
        actionUid=action.action_uid, permitUid=permit.permit_uid, workUid=permit.work_uid,
        commandUid=permit.command_uid, actionKey=action.action_key, actionKind=action.action_kind,
        actionDigestSha256=action.action_digest_sha256, receiptUid=action.receipt_uid,
        ledgerSequence=ledger["ledgerSequence"], evidenceDigestSha256=proof["evidence_sha256"],
        state="ISOLATED_BY_REBOOT", dispositionBasis="NEWER_MCU_BOOT_COMMAND_TARGET_ISOLATED",
        pastEffect="UNKNOWN", mayExecute=False, disposition="FOUND")
    if (not isinstance(disposition, dict) or disposition != expected
            or any(type(disposition[key]) is not type(value) for key, value in expected.items())):
        raise ValueError("recovery close isolation permanent disposition mismatch")


class NativeRecoveryCloseIsolation:
    def __init__(self, store, safety, boot, *, clock=lambda: monotonic_ns() // 1000000):
        from mcu_session import McuBootSession
        if (not isinstance(safety, PermanentJobSafety) or safety.enabled is not True
                or not isinstance(boot, McuBootSession) or not callable(clock)):
            raise ValueError("recovery close isolation requires permanent authority and live boot session")
        self.store, self.safety, self.boot, self.clock = store, safety, boot, clock

    def reconcile(self, action_uid):
        proof = self.store.prepare_native_recovery_close_isolation(action_uid,
            current_boot=lambda: self.boot.current_boot(self.clock()))
        if proof is None:
            return None
        binding = self.store.get_native_delivery_recovery_close(action_uid)
        self.safety.isolate_native_recovery_close_after_reboot(binding["permit"], action=binding["action"],
            evidence=binding["evidence"], isolation_evidence_sha256=proof["evidence_sha256"],
            boot_observation=json.loads(proof["bundle_json"])["bootObservation"])
        ledger = self.safety.get_physical_action(action_uid)
        disposition = self.safety.get_native_recovery_close_disposition(action_uid)
        return self.store.confirm_native_recovery_close_isolation(action_uid, ledger, disposition)
