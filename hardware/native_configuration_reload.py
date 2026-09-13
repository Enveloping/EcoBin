"""Durable reload identity for an already APPLIED native standing configuration.

This is not a command executor. Runtime must establish fresh boot/device facts,
no business/pending configuration, and maintenance authorization before each
write. These local transactions do not prepare commands, touch UART, or reopen
the original cloud application. Pi-only restarts reuse the exact stored IDs.
"""
import hashlib
import json
import uuid

from mcu_configuration import NativeMcuConfiguration
from onenet_wire import _validate_command_envelope, canonical_payload_sha256
import uart2_protocol as uart


MARKER = "nativeConfigurationReload"
PROFILE = "ecobin-native-configuration-reload-v1"


def _uids(values, count):
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("native configuration reload part IDs are incomplete")
    for value in values:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value or not uuid.UUID(value).int:
            raise ValueError("native configuration reload part ID is invalid")
    if len(set(values)) != count:
        raise ValueError("native configuration reload part IDs are duplicated")


def _accepted(store, record):
    if not record["write_claimed"] or record["decision_outcome"] != "ACCEPTED" or record["decision_error"] != "NONE":
        return False
    for observation in store.list_native_command_observations(record["command_uid"]):
        name, raw = observation["message_name"], bytes(observation["payload"])
        if name not in {"COMMAND_DECISION", "COMMAND_QUERY_RESULT"}:
            raise ValueError("native configuration acceptance witness is corrupt")
        value = uart.decode_payload(name, raw)
        offset = 8 if name == "COMMAND_QUERY_RESULT" else 0
        if (raw[offset:offset + 60] == record["payload"][:60]
                and value["currentMcuBootId"] == record["mcu_boot_id"]
                and value["outcome"] == "ACCEPTED" and value["errorCode"] == "NONE"):
            return True
    raise ValueError("native configuration acceptance lacks its original wire witness")


def _parts(store, candidate, application, uids, boot, *, complete=False, retired=False):
    _uids(uids, candidate.part_count)
    records, missing, previous = [], False, None
    for index, uid in enumerate(uids, 1):
        record = store.get_native_command(uid)
        if record is None:
            missing = True
            continue
        if (missing or record["conflict"] or record["dispatch_retired"]
                or bool(record["boot_retired"]) != retired or record["mcu_boot_id"] != boot
                or record["decision_outcome"] == "REJECTED"):
            raise ValueError("native configuration reload parts conflict, are retired or have a gap")
        if previous is not None and (previous["command_sequence"] >= record["command_sequence"]
                                    or not _accepted(store, previous)):
            raise ValueError("native configuration reload parts lack ordered acceptance")
        expected = candidate.encode_part(index, application_uid=application, mcu_command_uid=uid,
            target_mcu_boot_id=boot, command_sequence=record["command_sequence"])
        if (record["message_name"], record["payload"]) != expected:
            raise ValueError("native configuration reload part bytes differ from the original configuration")
        if record["decision_outcome"] == "ACCEPTED":
            if not _accepted(store, record):
                raise ValueError("native configuration part was not claimed before acceptance")
        records.append(record)
        previous = record
    if complete and (missing or len(records) != candidate.part_count or not _accepted(store, records[-1])):
        raise ValueError("native configuration reload requires all parts actually ACCEPTED")
    return records


def _source(store, application, device_name):
    row = store.get_latest_applied_configuration()
    if row is None or row["application_uid"] != application or row["device_name"] != device_name:
        raise ValueError("native configuration reload requires the latest APPLIED configuration for this device")
    original = store.get_command(row["command_uid"])
    if original is None or original["state"] != "COMPLETED" or original["command_type"] != "APPLY_CONFIGURATION":
        raise ValueError("native configuration reload requires its COMPLETED original cloud command")
    command = original["payload"]
    # Standing settings were already applied while authorized. Rechecking their
    # durable source is not a new cloud execution, so its old expiry is ignored
    # ONLY here; pending applications and business START retain their deadlines.
    _validate_command_envelope(command, trusted_environment=None,
        trusted_business_release_download_base_url=None, expiry_reference_time=None)
    canonical = canonical_payload_sha256({key: value for key, value in command.items() if key != "cosGrant"})
    if (canonical != original["canonical_sha256"] or command["commandUid"] != row["command_uid"]
            or command["commandType"] != "APPLY_CONFIGURATION" or command["targetDeviceName"] != device_name
            or command["payload"] != row["payload"] or row["payload"]["applicationUid"] != application
            or row["payload"]["config"] != dict(version=row["config_version"], contentSha256=row["content_sha256"],
                                                mcuPayloadSha256=row["mcu_payload_sha256"])):
        raise ValueError("native configuration reload differs from its original cloud authority")
    candidate = NativeMcuConfiguration.from_cloud_payload(row["payload"])
    _uids(row["part_command_uids"], candidate.part_count)
    first = store.get_native_command(row["part_command_uids"][0])
    if (first is None or row["commit_mcu_command_uid"] != row["part_command_uids"][-1]
            or original["mcu_command_uid"] != row["commit_mcu_command_uid"]):
        raise ValueError("native configuration reload lost its original applied part identity")
    boot = first["mcu_boot_id"]
    if store.get_native_boot_observation(boot) is None:
        raise ValueError("native configuration reload lacks its original boot witness")
    records = _parts(store, candidate, application, row["part_command_uids"], boot,
                     complete=True, retired=True)
    source = dict(applicationUid=application, commandUid=row["command_uid"], deviceName=device_name,
        commandCanonicalSha256=canonical, config=row["payload"]["config"], sourceMcuBootId=boot,
        originalParts=[dict(commandUid=record["command_uid"], commandSequence=record["command_sequence"],
                            payloadSha256=hashlib.sha256(record["payload"]).hexdigest()) for record in records])
    result = {} if original["result"] is None else original["result"]
    if not isinstance(result, dict):
        raise ValueError("native configuration reload original command result is malformed")
    return row, candidate, source, result


def _current_boot(store, conn, boot):
    if (type(boot) is not int or boot <= 0 or boot != store._native_counter(conn, "native_current_boot")
            or store.get_native_boot_observation(boot) is None):
        raise ValueError("native configuration reload requires the positively observed current boot")


def _marker(result, source, candidate):
    if MARKER not in result:
        return None
    marker = result[MARKER]
    if (not isinstance(marker, dict) or set(marker) != {"state", "evidence", "evidenceSha256"}
            or marker["state"] not in {"PREPARED", "APPLIED"}):
        raise ValueError("native configuration reload marker is malformed")
    evidence = marker["evidence"]
    if (not isinstance(evidence, dict) or set(evidence) != {"profile", "source", "targetMcuBootId", "partCommandUids"}
            or evidence["profile"] != PROFILE or evidence["source"] != source
            or type(evidence["targetMcuBootId"]) is not int or evidence["targetMcuBootId"] <= source["sourceMcuBootId"]
            or marker["evidenceSha256"] != canonical_payload_sha256(evidence)):
        raise ValueError("native configuration reload marker is corrupt or has a different source")
    _uids(evidence["partCommandUids"], candidate.part_count)
    if set(evidence["partCommandUids"]) & {part["commandUid"] for part in source["originalParts"]}:
        raise ValueError("native configuration reload must not reuse original part IDs")
    return marker


def _result(marker, payload):
    evidence = marker["evidence"]
    return dict(state=marker["state"], application_uid=evidence["source"]["applicationUid"],
        command_uid=evidence["source"]["commandUid"], target_mcu_boot_id=evidence["targetMcuBootId"],
        part_command_uids=list(evidence["partCommandUids"]), payload=payload,
        evidence_sha256=marker["evidenceSha256"])


def _save(conn, command_uid, result):
    updated = conn.execute("UPDATE command_inbox SET result_json=? WHERE command_uid=? AND state='COMPLETED'",
        (json.dumps(result, ensure_ascii=False, sort_keys=True), command_uid))
    if updated.rowcount != 1:
        raise ValueError("native configuration reload original command changed")


def prepare(store, application_uid, *, target_mcu_boot_id, device_name):
    """Persist only one current reload's IDs. Never prepare or send a command."""
    with store._standalone_native_transaction() as conn:
        _current_boot(store, conn, target_mcu_boot_id)
        row, candidate, source, result = _source(store, application_uid, device_name)
        if target_mcu_boot_id <= source["sourceMcuBootId"]:
            raise ValueError("native configuration reload requires a genuinely newer MCU boot")
        marker = _marker(result, source, candidate)
        if marker is not None:
            boot = marker["evidence"]["targetMcuBootId"]
            if boot > target_mcu_boot_id:
                raise ValueError("native configuration reload cannot return to an old boot")
            _parts(store, candidate, application_uid, marker["evidence"]["partCommandUids"], boot,
                   complete=marker["state"] == "APPLIED", retired=boot < target_mcu_boot_id)
            if boot == target_mcu_boot_id:
                return _result(marker, row["payload"])
            if store.get_native_boot_observation(boot) is None:
                raise ValueError("native configuration reload lost its previous boot witness")
        evidence = dict(profile=PROFILE, source=source, targetMcuBootId=target_mcu_boot_id,
                        partCommandUids=[str(uuid.uuid4()) for _ in range(candidate.part_count)])
        marker = dict(state="PREPARED", evidence=evidence, evidenceSha256=canonical_payload_sha256(evidence))
        result[MARKER] = marker
        _save(conn, row["command_uid"], result)
        return _result(marker, row["payload"])


def _read_current(store, conn, application, boot, device):
    _current_boot(store, conn, boot)
    row, candidate, source, result = _source(store, application, device)
    marker = _marker(result, source, candidate)
    if marker is not None:
        if marker["evidence"]["targetMcuBootId"] != boot:
            raise ValueError("native configuration reload belongs to a different target boot")
        _parts(store, candidate, application, marker["evidence"]["partCommandUids"], boot,
               complete=marker["state"] == "APPLIED")
    return row, candidate, result, marker


def read(store, application_uid, *, target_mcu_boot_id, device_name):
    """Validate exact current reload membership; historical boot facts are not freshness."""
    with store._standalone_native_transaction() as conn:
        row, _, _, marker = _read_current(store, conn, application_uid, target_mcu_boot_id, device_name)
        return None if marker is None else _result(marker, row["payload"])


def complete(store, application_uid, *, target_mcu_boot_id, device_name):
    """Record all new parts accepted, not cloud reapplication or business admission."""
    with store._standalone_native_transaction() as conn:
        row, candidate, result, marker = _read_current(store, conn, application_uid, target_mcu_boot_id, device_name)
        if marker is None:
            raise ValueError("native configuration reload has not been prepared")
        _parts(store, candidate, application_uid, marker["evidence"]["partCommandUids"], target_mcu_boot_id,
               complete=True)
        if marker["state"] != "APPLIED":
            marker["state"] = "APPLIED"
            _save(conn, row["command_uid"], result)
        return _result(marker, row["payload"])
