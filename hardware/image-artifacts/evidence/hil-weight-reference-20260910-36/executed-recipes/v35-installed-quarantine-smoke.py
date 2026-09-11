"""Exercise only synthetic state using the ARM64 image's installed updater."""
from pathlib import Path
import sys

sys.path.insert(0, '/opt/ecobin/updater/current/app')
from updater_store import UpdaterStore, UpdaterStoreError
import updater_agent

def uid(number):
    return f'00000000-0000-4000-8000-{number:012x}'

directory = Path('/tmp/v35-quarantine-smoke')
directory.mkdir(mode=0o700)
store = UpdaterStore(directory / 'updater.db', release_version='updater-20260910-35', enable_stage4_candidate=True)
store.initialize()
try:
    status = store.get_status()
    assert status['jobGateState'] == 'LOCKED'
    store.activate_stage4_job_gate({
        'operationUid': uid(900), 'evidenceDigest': 'f' * 64,
        'expectedManagementStateSequence': status['managementStateSequence'],
    })
    store.request_job_permit({
        'permitUid': uid(1), 'workUid': uid(2), 'commandUid': uid(3),
        'workType': 'DELIVERY', 'requestDigestSha256': 'a' * 64,
    })
    store.begin_job({'permitUid': uid(1), 'beginUid': uid(4), 'permitDigestSha256': 'a' * 64})
    store.prepare_physical_action({
        'actionUid': uid(5), 'permitUid': uid(1), 'workUid': uid(2), 'commandUid': uid(3),
        'actionKey': 'delivery.door.unlock.1', 'actionKind': 'DELIVERY_DOOR_UNLOCK',
        'actionDigestSha256': 'c' * 64, 'dispatchAttemptToken': 'A' * 43,
    })
    store.arm_physical_action({'actionUid': uid(5), 'dispatchAttemptToken': 'A' * 43})
    resolution = {
        'resolutionUid': uid(17), 'actionUid': uid(5), 'permitUid': uid(1),
        'workUid': uid(2), 'commandUid': uid(3), 'actionKey': 'delivery.door.unlock.1',
        'actionKind': 'DELIVERY_DOOR_UNLOCK', 'actionDigestSha256': 'c' * 64,
        'expectedLedgerSequence': 1, 'evidenceDigestSha256': '9' * 64,
    }
    assert store.quarantine_unknown_physical_action(resolution)['disposition'] == 'ACCEPTED'
    assert store.quarantine_unknown_physical_action(resolution)['disposition'] == 'DUPLICATE'
    action = store.get_physical_action({'actionUid': uid(5)})
    assert action['state'] == 'ARMED' and action['confirmedOutcome'] is None
    assert action['unknownEffectResolution']['resolutionUid'] == uid(17)
    completion = {
        'permitUid': uid(1), 'completionUid': uid(18),
        'outcome': 'SUCCEEDED', 'completionDigestSha256': '8' * 64,
    }
    try:
        store.complete_job(completion)
    except UpdaterStoreError as error:
        assert error.code == 'JOB_QUARANTINE_REQUIRES_CANCELLATION'
    else:
        raise AssertionError('unknown physical effect was accepted as success')
    completion['outcome'] = 'CANCELLED'
    assert store.complete_job(completion)['completionOutcome'] == 'CANCELLED'
    assert store.get_status()['jobGateState'] == 'OPEN'
finally:
    store.close()
store = UpdaterStore(directory / 'updater.db', release_version='updater-20260910-35', enable_stage4_candidate=True)
store.initialize()
try:
    assert store.get_physical_action({'actionUid': uid(5)})['state'] == 'ARMED'
    assert store.quarantine_unknown_physical_action(resolution)['disposition'] == 'DUPLICATE'
finally:
    store.close()
print('installed-quarantine-ledger=PASS originalArmedPreserved=true duplicateSafe=true successRejected=true cancelled=true restartEvidenceRetained=true')
