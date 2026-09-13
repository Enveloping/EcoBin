"""Permanent API checks, not proof of an actual MCU output or reboot.

Confirmation receipts here model the already validated business-side evidence
boundary; the companion Pi/C tests establish that physical evidence custody.
"""
import pytest
import json
import shutil
import subprocess
import sys
from pathlib import Path

from updater_store import UpdaterStoreError
from hardware.tests.test_updater_native_recovery_close import (
    active, recovery_request,
)
from hardware.tests.test_updater_store import (
    _confirmation_payload, _uid, _operator_lock, _live_result_payload,
    _unknown_effect_quarantine_payload,
)


def arm_request(request):
    return {key: request[key] for key in ("actionUid", "dispatchAttemptToken")}


def test_confirmed_original_output_allows_one_new_close_without_changing_history(active):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    request = recovery_request(source, armed)
    prepared = store.prepare_native_recovery_close(request)
    assert prepared["state"] == "PREPARED"
    assert not prepared["mayExecute"]
    close = store.arm_physical_action(arm_request(request))
    assert close["state"] == "ARMED" and close["mayExecute"]
    assert close["ledgerSequence"] == original["ledgerSequence"] + 1
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert original["confirmedOutcome"] == "EXECUTED"
    assert original["confirmationBasis"] == "MCU_IDENTITY_BOUND_FACT"
    assert original["unknownEffectResolution"] is None
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"
    assert store.get_status()["jobGateState"] == "LOCKED"
    assert store.get_status()["unreconciledPhysicalActionCount"] == 1


@pytest.mark.parametrize("result", ["fixed-frame", "abort-not-written", "failed-safe"])
@pytest.mark.parametrize("already_prepared", [False, True])
def test_other_confirmed_source_meanings_cannot_authorize_recovery_close(active, result, already_prepared):
    store, source, armed = active
    request = recovery_request(source, armed)
    if already_prepared:
        store.prepare_native_recovery_close(request)
    if result == "fixed-frame":
        store.confirm_live_physical_action_result(_live_result_payload())
    elif result == "abort-not-written":
        store.abort_physical_action_dispatch(arm_request(source) | {
            "receiptUid": _uid(13), "evidenceDigestSha256": "7" * 64,
        })
    else:
        store.confirm_physical_action(_confirmation_payload(outcome="FAILED_SAFE"))
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    with pytest.raises(UpdaterStoreError) as rejected:
        if already_prepared:
            store.arm_physical_action(arm_request(request))
        else:
            store.prepare_native_recovery_close(request)
    assert rejected.value.code == "NATIVE_RECOVERY_SOURCE_CONFLICT"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_status()["jobGateState"] == "LOCKED"


@pytest.mark.parametrize("key,value", [
    ("expectedSourceLedgerSequence", 999),
    ("sourceActionDigestSha256", "f" * 64),
    ("sourceActionUid", _uid(89)),
    ("permitUid", _uid(89)),
    ("commandUid", _uid(89)),
])
def test_confirmed_source_still_requires_exact_original_identity(active, key, value):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    with pytest.raises(UpdaterStoreError):
        store.prepare_native_recovery_close(recovery_request(source, armed) | {key: value})
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_status()["unreconciledPhysicalActionCount"] == 0


def test_another_unconfirmed_action_is_not_hidden_by_confirmed_original(active):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    other = source | {"actionUid": _uid(70), "actionKey": "distinct-existing-action",
                      "dispatchAttemptToken": "C" * 43}
    store.prepare_physical_action(other)
    with pytest.raises(UpdaterStoreError) as unresolved:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    assert unresolved.value.code == "PHYSICAL_ACTION_RECONCILIATION_REQUIRED"
    assert store.get_physical_action({"actionUid": other["actionUid"]})["state"] == "PREPARED"


def test_old_manual_quarantine_is_not_reused_even_if_original_later_has_confirmation(active):
    store, source, armed = active
    quarantine = _unknown_effect_quarantine_payload() | {
        key: source[key] for key in ("actionKind", "actionKey", "actionDigestSha256")
    }
    store.quarantine_unknown_physical_action(quarantine)
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    assert original["unknownEffectResolution"] is not None
    with pytest.raises(UpdaterStoreError) as rejected:
        store.prepare_native_recovery_close(recovery_request(source, armed))
    assert rejected.value.code == "NATIVE_RECOVERY_SOURCE_CONFLICT"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_status()["jobGateState"] == "LOCKED"


@pytest.mark.parametrize("active", [None, "draining"], indirect=True)
def test_original_confirmation_between_preparation_and_arm_preserves_new_close(active):
    store, source, armed = active
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    owner = store.get_status()["maintenanceOwnerUid"]
    phase = store.get_status()["maintenancePhase"]
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    assert store.arm_physical_action(arm_request(request))["mayExecute"]
    assert store.get_status()["maintenanceOwnerUid"] == owner
    assert store.get_status()["maintenancePhase"] == phase
    assert store.get_status()["jobGateState"] == "LOCKED"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    with pytest.raises(UpdaterStoreError):
        store.transition_job_gate("MAINTENANCE")


def test_permanent_restart_does_not_regrant_old_armed_close_after_source_confirmation(active):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    assert store.arm_physical_action(arm_request(request))["mayExecute"]
    evidence = store.get_native_recovery_close({"actionUid": request["actionUid"]})
    store.close()
    store.initialize()
    for token in (request["dispatchAttemptToken"], "C" * 43):
        denied = store.arm_physical_action(arm_request(request) | {"dispatchAttemptToken": token})
        assert denied["state"] == "ARMED" and not denied["mayExecute"]
    assert store.get_native_recovery_close({"actionUid": request["actionUid"]}) == evidence
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    assert store.get_status()["jobGateState"] == "LOCKED"


@pytest.mark.parametrize("boundary", ["prepare", "arm", "retry-arm"])
def test_confirmed_source_never_bypasses_operator_stop(active, boundary):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    request = recovery_request(source, armed)
    if boundary != "prepare":
        store.prepare_native_recovery_close(request)
    if boundary == "retry-arm":
        store.arm_physical_action(arm_request(request))
    _operator_lock(store, 99)
    with pytest.raises(UpdaterStoreError) as stopped:
        if boundary == "prepare":
            store.prepare_native_recovery_close(request)
        else:
            store.arm_physical_action(arm_request(request))
    assert stopped.value.code == "JOB_GATE_CLOSED"
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


@pytest.mark.parametrize("key,value", [
    ("recoveryEvidenceSha256", "8" * 64),
    ("recoveryUid", _uid(88)),
    ("sourceActionDigestSha256", "f" * 64),
])
def test_confirmed_source_does_not_allow_replacing_existing_recovery_evidence(active, key, value):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    original = store.get_native_recovery_close({"actionUid": request["actionUid"]})
    with pytest.raises(UpdaterStoreError):
        store.prepare_native_recovery_close(request | {key: value})
    assert store.get_native_recovery_close({"actionUid": request["actionUid"]}) == original
    assert store.get_physical_action({"actionUid": request["actionUid"]})["state"] == "PREPARED"


def test_close_confirmation_preserves_original_receipt_and_requires_explicit_job_completion(active):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    store.arm_physical_action(arm_request(request))
    close = store.confirm_physical_action(_confirmation_payload(receipt_number=64) | {
        "actionUid": request["actionUid"], "evidenceDigestSha256": "8" * 64,
    })
    assert close["confirmedOutcome"] == "EXECUTED"
    assert store.get_physical_action({"actionUid": source["actionUid"]}) == original
    with pytest.raises(UpdaterStoreError) as conflict:
        store.confirm_physical_action(_confirmation_payload(receipt_number=65))
    assert conflict.value.code == "PHYSICAL_ACTION_RECEIPT_CONFLICT"
    assert store.get_status()["unreconciledPhysicalActionCount"] == 0
    assert store.get_status()["jobGateState"] == "LOCKED"
    assert store.get_job_permit({"permitUid": source["permitUid"]})["state"] == "ACTIVE"


def test_completed_original_permit_cannot_authorize_new_recovery_close(active):
    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    store.complete_job({"permitUid": source["permitUid"], "completionUid": _uid(64),
        "outcome": "CANCELLED", "completionDigestSha256": "8" * 64})
    with pytest.raises(UpdaterStoreError):
        store.prepare_native_recovery_close(recovery_request(source, armed))
    with pytest.raises(UpdaterStoreError) as absent:
        store.get_native_recovery_close({"actionUid": _uid(60)})
    assert absent.value.code == "NATIVE_RECOVERY_NOT_FOUND"


def test_permanent_inventory_reopens_confirmed_source_and_prepared_close_without_business_imports(active, tmp_path):
    from install.runtime_payload_manifest import DEVICE_UPDATER_FILES

    store, source, armed = active
    store.confirm_physical_action(_confirmation_payload())
    request = recovery_request(source, armed)
    store.prepare_native_recovery_close(request)
    original = store.get_physical_action({"actionUid": source["actionUid"]})
    recovery = store.get_native_recovery_close({"actionUid": request["actionUid"]})
    store.close()
    stage = tmp_path / "permanent-only"
    hardware = Path(__file__).resolve().parents[1]
    for name in DEVICE_UPDATER_FILES:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hardware / name, target)
    state = stage / "state"
    state.mkdir(mode=0o700)
    copied_database = state / "updater.db"
    shutil.copyfile(store.path, copied_database)
    copied_database.chmod(0o600)
    code = r'''
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import updater_store, updater_agent, job_safety, uart2_protocol
for module in (updater_store, updater_agent, job_safety, uart2_protocol):
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
assert 'edge_store' not in sys.modules
assert 'native_delivery_recovery_close' not in sys.modules
original, recovery, request = json.loads(sys.argv[2])
store = updater_store.UpdaterStore(root / 'state' / 'updater.db',
    release_version='permanent-only-recovery', enable_stage4_candidate=True)
store.initialize()
assert store.get_physical_action({'actionUid': original['actionUid']}) == original
assert store.get_native_recovery_close({'actionUid': request['actionUid']}) == recovery
assert store.get_physical_action({'actionUid': request['actionUid']})['state'] == 'PREPARED'
# The same business attempt retained its token; the permanent service restarted
# before granting the first ARM, not after a previously granted physical write.
arm = {key: request[key] for key in ('actionUid', 'dispatchAttemptToken')}
assert store.arm_physical_action(arm)['mayExecute'] is True
assert store.get_physical_action({'actionUid': original['actionUid']}) == original
assert store.get_job_permit({'permitUid': request['permitUid']})['state'] == 'ACTIVE'
assert store.get_status()['jobGateState'] == 'LOCKED'
store.close()
store.initialize()
assert store.arm_physical_action(arm)['mayExecute'] is False
assert store.get_native_recovery_close({'actionUid': request['actionUid']}) == recovery
assert store.get_physical_action({'actionUid': original['actionUid']}) == original
store.close()
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(stage),
        json.dumps([original, recovery, request])], cwd=stage,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
