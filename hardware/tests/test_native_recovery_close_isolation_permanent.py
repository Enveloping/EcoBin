"""New MCU boot isolates an old target without inventing its past effect."""
import pytest
import sqlite3
import json
import hashlib
import uart2_protocol as uart
from hardware.tests.test_updater_native_recovery_close import active, wire_digest
from hardware.tests.test_native_recovery_close_withdrawal_permanent import armed_close
from hardware.tests.test_updater_recovery_close_successor import successor_request, retire_payload
from hardware.tests.test_updater_store import _uid, _operator_lock
from updater_store import UpdaterStoreError
from job_safety import JobPermit, PhysicalAction, PermanentJobSafety, NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS
from updater_agent import UpdaterControlHandler, build_control_actions


def isolation_payload(request, retirement, boot=None):
    boot = request["targetMcuBootId"] + 1 if boot is None else boot
    return {key: value for key, value in retirement.items() if key != "retirementEvidenceSha256"} | dict(
        isolationEvidenceSha256="c" * 64, observedMcuBootId=boot,
        bootObservationMessageName="BOOT_PROBE_REPLY",
        bootObservationPayloadHex=uart.encode_payload("BOOT_PROBE_REPLY", dict(probeId=81, mcuBootId=boot)).hex())


def test_reboot_isolation_preserves_original_authorization_and_unknown_past_effect(active):
    store, request, retirement = armed_close(active)
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    status = store.get_status()
    payload = isolation_payload(request, retirement)
    result = store.isolate_native_recovery_close_after_reboot(payload)
    assert result["state"] == "ISOLATED_BY_REBOOT"
    assert result["dispositionBasis"] == "NEWER_MCU_BOOT_COMMAND_TARGET_ISOLATED"
    assert result["pastEffect"] == "UNKNOWN" and result["mayExecute"] is False
    assert result["observedMcuBootId"] == payload["observedMcuBootId"]
    assert result["evidenceDigestSha256"] == payload["isolationEvidenceSha256"]
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before
    assert store.get_status() == status
    assert store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]}) == result | {"disposition": "FOUND"}
    assert store.isolate_native_recovery_close_after_reboot(payload) == result | {"disposition": "DUPLICATE"}


def crossboot_successor(parent, disposition, generation=2):
    request = successor_request(parent, disposition, generation)
    request["targetMcuBootId"] = disposition["observedMcuBootId"]
    values = uart.decode_payload("SAFE_CLOSE", bytes.fromhex(request["closeCommandPayloadHex"]))
    values.update(targetMcuBootId=request["targetMcuBootId"], commandSequence=1)
    values["commandDigestSha256"] = uart.compute_command_digest("SAFE_CLOSE", values)
    raw = uart.encode_payload("SAFE_CLOSE", values)
    return request | dict(closeCommandPayloadHex=raw.hex(), actionDigestSha256=wire_digest(request, raw))


def test_isolated_parent_allows_only_exact_new_boot_successor_and_preserves_chain(active):
    store, request, retirement = armed_close(active)
    isolated = store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    child = crossboot_successor(request, isolated)
    assert store.prepare_native_recovery_close_successor(child)["state"] == "PREPARED"
    assert store.arm_physical_action(dict(actionUid=child["actionUid"], dispatchAttemptToken=child["dispatchAttemptToken"]))["mayExecute"] is True
    store.close()
    store.initialize()
    assert store.get_native_recovery_close({"actionUid": child["actionUid"]})["targetMcuBootId"] == isolated["observedMcuBootId"]
    assert store.get_physical_action({"actionUid": request["actionUid"]})["state"] == "ARMED"


def test_candidate_business_only_client_routes_exact_isolation_and_readback(active):
    store, request, retirement = armed_close(active)
    actions = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102},
        business_uids={3102}, enable_stage4_candidate=True)
    class Client:
        def request(self, operation, body):
            rpc = actions[operation]
            assert rpc.allowed_uids == frozenset({3102})
            assert set(body) == rpc.payload_fields
            return rpc.handler(body)
    safety = PermanentJobSafety(Client())
    permit = JobPermit(request["permitUid"], request["workUid"], request["commandUid"], "DELIVERY", "a" * 64)
    action = PhysicalAction(request["actionUid"], retirement["receiptUid"], request["actionKey"], "SAFE_CLOSE", request["actionDigestSha256"])
    payload = isolation_payload(request, retirement)
    result = safety.isolate_native_recovery_close_after_reboot(permit, action=action,
        evidence={key: request[key] for key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS},
        isolation_evidence_sha256="c" * 64,
        boot_observation={key: payload[key] for key in ("observedMcuBootId", "bootObservationMessageName", "bootObservationPayloadHex")})
    assert result["state"] == "ISOLATED_BY_REBOOT"
    disabled = build_control_actions(UpdaterControlHandler(store), allowed_uids={0, 3102})
    assert not {"ISOLATE_NATIVE_RECOVERY_CLOSE_AFTER_REBOOT", "ISOLATE_NATIVE_RECOVERY_CLOSE_SUCCESSOR_AFTER_REBOOT"} & set(disabled)


@pytest.mark.parametrize("change", [dict(observedMcuBootId=0), dict(observedMcuBootId=True),
    dict(observedMcuBootId=3.0), dict(observedMcuBootId=999), dict(bootObservationMessageName="COMMAND_QUERY_REPLY"),
    dict(bootObservationPayloadHex="00"), dict(bootObservationPayloadHex=""), dict(bootObservationPayloadHex="ab cd"),
    dict(extra="ignored"), dict(isolationEvidenceSha256="bad"), dict(workUid=_uid(198))])
def test_invalid_or_unrelated_isolation_evidence_is_rejected_without_mutation(active, change):
    store, request, retirement = armed_close(active)
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    with pytest.raises(UpdaterStoreError):
        store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement) | change)
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before
    with pytest.raises(UpdaterStoreError, match="does not exist"):
        store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})


@pytest.mark.parametrize("offset", [-1, 0])
def test_valid_reply_for_same_or_older_boot_does_not_prove_isolation(active, offset):
    store, request, retirement = armed_close(active)
    with pytest.raises(UpdaterStoreError, match="newer positive"):
        store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement, request["targetMcuBootId"] + offset))


@pytest.mark.parametrize("stage", ["absent", "prepared", "confirmed", "withdrawn"])
def test_isolation_cannot_relabel_never_authorized_confirmed_or_withdrawn_facts(active, stage):
    from hardware.tests.test_updater_native_recovery_close import recovery_request
    from hardware.tests.test_updater_recovery_close_retirement import retirement_request
    store, source, source_armed = active
    request = recovery_request(source, source_armed)
    retirement = retirement_request(source, source_armed)
    if stage != "absent":
        store.prepare_native_recovery_close(request)
    if stage in {"confirmed", "withdrawn"}:
        store.arm_physical_action(dict(actionUid=request["actionUid"], dispatchAttemptToken=request["dispatchAttemptToken"]))
        if stage == "confirmed":
            store.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(199), outcome="EXECUTED",
                confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))
        else:
            store.withdraw_native_recovery_close_dispatch(retirement)
    with pytest.raises(UpdaterStoreError):
        store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))


@pytest.mark.parametrize("operation", ["arm", "prepare", "generic_prepare", "confirm", "abort", "live", "withdraw", "retire"])
def test_isolation_prevents_old_action_authorization_or_history_rewrite(active, operation):
    store, request, retirement = armed_close(active)
    isolated = store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    if operation == "arm":
        assert store.arm_physical_action(dict(actionUid=request["actionUid"], dispatchAttemptToken=request["dispatchAttemptToken"]))["mayExecute"] is False
    elif operation == "prepare":
        assert store.prepare_native_recovery_close(request)["mayExecute"] is False
    elif operation == "generic_prepare":
        assert store.prepare_physical_action(request)["mayExecute"] is False
    else:
        payload = dict(actionUid=request["actionUid"], receiptUid=_uid(198),
            dispatchAttemptToken=request["dispatchAttemptToken"], evidenceDigestSha256="d" * 64)
        with pytest.raises(UpdaterStoreError):
            if operation == "confirm":
                store.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(198),
                    outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="d" * 64))
            elif operation == "abort":
                store.abort_physical_action_dispatch(payload)
            elif operation == "live":
                store.confirm_live_physical_action_result(payload | {"outcome": "EXECUTED"})
            elif operation == "withdraw":
                store.withdraw_native_recovery_close_dispatch(retirement)
            else:
                store.retire_native_recovery_close(retirement)
    assert store.get_physical_action({"actionUid": request["actionUid"]}) == before
    assert store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]}) == isolated | {"disposition": "FOUND"}


@pytest.mark.parametrize("offset", [-1, 1])
def test_successor_cannot_choose_another_boot_than_parent_isolation(active, offset):
    store, request, retirement = armed_close(active)
    isolated = store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    child = crossboot_successor(request, isolated | {"observedMcuBootId": isolated["observedMcuBootId"] + offset})
    with pytest.raises(UpdaterStoreError, match="predecessor"):
        store.prepare_native_recovery_close_successor(child)


def test_repeated_reset_then_withdrawn_same_boot_child_keeps_exact_ancestry(active):
    store, request, retirement = armed_close(active)
    first = store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    second_request = crossboot_successor(request, first)
    store.prepare_native_recovery_close_successor(second_request)
    store.arm_physical_action(dict(actionUid=second_request["actionUid"], dispatchAttemptToken=second_request["dispatchAttemptToken"]))
    second = store.isolate_native_recovery_close_successor_after_reboot(isolation_payload(second_request, retire_payload(second_request)))
    third_request = crossboot_successor(second_request, second, 3)
    store.prepare_native_recovery_close_successor(third_request)
    store.arm_physical_action(dict(actionUid=third_request["actionUid"], dispatchAttemptToken=third_request["dispatchAttemptToken"]))
    third = store.withdraw_native_recovery_close_successor_dispatch(retire_payload(third_request, 3))
    fourth = successor_request(third_request, third, 4)
    store.prepare_native_recovery_close_successor(fourth)
    assert store.arm_physical_action(dict(actionUid=fourth["actionUid"], dispatchAttemptToken=fourth["dispatchAttemptToken"]))["mayExecute"] is True
    store.close()
    store.initialize()
    assert store.get_native_recovery_close({"actionUid": fourth["actionUid"]})["sourceActionUid"] == request["sourceActionUid"]


def test_isolation_retry_after_reopen_preserves_original_operator_stop(active):
    store, request, retirement = armed_close(active)
    _operator_lock(store, 199)
    payload = isolation_payload(request, retirement)
    original = store.isolate_native_recovery_close_after_reboot(payload)
    store.close()
    store.initialize()
    assert store.isolate_native_recovery_close_after_reboot(payload) == original | {"disposition": "DUPLICATE"}
    with pytest.raises(UpdaterStoreError, match="job gate"):
        store.prepare_native_recovery_close_successor(crossboot_successor(request, original))


@pytest.mark.parametrize("key,value", [("pastEffect", "NOT_EXECUTED"), ("mayExecute", 0),
    ("observedMcuBootId", 3.0), ("ledgerSequence", 2.0), ("dispositionBasis", "PREPARED_NOT_ARMED")])
def test_rehashed_isolation_type_or_past_effect_change_is_rejected(active, key, value):
    store, request, retirement = armed_close(active)
    store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    with sqlite3.connect(store.path) as db:
        body = json.loads(db.execute("SELECT payload_json FROM native_recovery_close_disposition").fetchone()[0])
        body[key] = value
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        db.execute("UPDATE native_recovery_close_disposition SET payload_json=?,payload_sha256=?", (raw, hashlib.sha256(raw.encode("ascii")).hexdigest()))
    with pytest.raises(RuntimeError, match="disposition"):
        store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})
    store.close()
    with pytest.raises(RuntimeError, match="disposition"):
        store.initialize()


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("isolation_first", [False, True])
def test_isolation_receipt_and_original_source_disposition_are_mutually_exclusive(active, manual, isolation_first):
    store, request, retirement = armed_close(active)
    payload = isolation_payload(request, retirement)
    source = active[1]
    def resolve_source():
        if manual:
            return store.quarantine_unknown_physical_action(dict(resolutionUid=retirement["receiptUid"],
                **{key: source[key] for key in ("actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256")},
                expectedLedgerSequence=active[2]["ledgerSequence"], evidenceDigestSha256="b" * 64))
        return store.confirm_physical_action(dict(actionUid=source["actionUid"], receiptUid=retirement["receiptUid"],
            outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))
    if isolation_first:
        store.isolate_native_recovery_close_after_reboot(payload)
        with pytest.raises(UpdaterStoreError, match="receipt"):
            resolve_source()
    else:
        resolve_source()
        with pytest.raises(UpdaterStoreError):
            store.isolate_native_recovery_close_after_reboot(payload)


def test_manual_resolution_cannot_replace_isolated_unknown_past_effect(active):
    store, request, retirement = armed_close(active)
    isolated = store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    before = store.get_physical_action({"actionUid": request["actionUid"]})
    with pytest.raises(UpdaterStoreError, match="isolated"):
        store.quarantine_unknown_physical_action(dict(resolutionUid=_uid(191),
            **{key: request[key] for key in ("actionUid", "permitUid", "workUid", "commandUid", "actionKey", "actionKind", "actionDigestSha256")},
            expectedLedgerSequence=before["ledgerSequence"], evidenceDigestSha256="b" * 64))
    assert store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]}) == isolated | {"disposition": "FOUND"}


@pytest.mark.parametrize("withdrawn", [False, True])
def test_non_reset_parent_cannot_gain_cross_boot_authority(active, withdrawn):
    from hardware.tests.test_updater_native_recovery_close import recovery_request
    from hardware.tests.test_updater_recovery_close_retirement import retirement_request
    store, source, armed = active
    if withdrawn:
        store, request, retirement = armed_close(active)
        proof = store.withdraw_native_recovery_close_dispatch(retirement)
    else:
        request = recovery_request(source, armed)
        proof = store.retire_native_recovery_close(retirement_request(source, armed))
    child = crossboot_successor(request, proof | {"observedMcuBootId": request["targetMcuBootId"] + 1})
    with pytest.raises(UpdaterStoreError, match="predecessor"):
        store.prepare_native_recovery_close_successor(child)


def test_isolation_and_real_output_confirmation_serialize_across_permanent_owners(active):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from hardware.tests.test_updater_store import _store
    store, request, retirement = armed_close(active)
    other = _store(store.path, "competing-updater", candidate=True)
    barrier = Barrier(2)
    def isolate():
        barrier.wait()
        try:
            return store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))["state"]
        except UpdaterStoreError:
            return "DENIED"
    def confirm():
        barrier.wait()
        try:
            return other.confirm_physical_action(dict(actionUid=request["actionUid"], receiptUid=_uid(191),
                outcome="EXECUTED", confirmationBasis="MCU_IDENTITY_BOUND_FACT", evidenceDigestSha256="b" * 64))["state"]
        except UpdaterStoreError:
            return "DENIED"
    try:
        with ThreadPoolExecutor(2) as pool:
            one, two = pool.submit(isolate), pool.submit(confirm)
            assert (one.result(), two.result()) in {("ISOLATED_BY_REBOOT", "DENIED"), ("DENIED", "CONFIRMED")}
    finally:
        other.close()


@pytest.mark.parametrize("column,value", [("armed_at", "2000-01-01T00:00:00Z"), ("arm_runtime_instance_uid", _uid(195))])
def test_changed_original_arm_history_blocks_isolation_get_and_rearm(active, column, value):
    store, request, retirement = armed_close(active)
    store.isolate_native_recovery_close_after_reboot(isolation_payload(request, retirement))
    with sqlite3.connect(store.path) as db:
        db.execute(f"UPDATE physical_action_ledger SET {column}=? WHERE action_uid=?", (value, request["actionUid"]))
    with pytest.raises(RuntimeError, match="disposition"):
        store.get_native_recovery_close_disposition({"actionUid": request["actionUid"]})
    with pytest.raises(RuntimeError, match="disposition"):
        store.arm_physical_action(dict(actionUid=request["actionUid"], dispatchAttemptToken=request["dispatchAttemptToken"]))
