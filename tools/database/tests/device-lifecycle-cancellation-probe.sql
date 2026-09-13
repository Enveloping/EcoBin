-- Run against a migrated disposable V72 database as its test owner.
-- LIKE copies the actual CHECK constraints; probe rows do not enter domain tables.
CREATE TEMPORARY TABLE lifecycle_mcu_probe LIKE dev_mcu_firmware_deployment;
CREATE TEMPORARY TABLE lifecycle_edge_probe LIKE dev_edge_software_deployment;

INSERT INTO lifecycle_mcu_probe (
    deployment_uid, rollout_id, release_id, asset_id, deployment_kind, wave_no,
    deployment_status, created_at, updated_at
) VALUES ('10000000-0000-4000-8000-000000000001', 1, 1, 1, 'VALIDATION', 0,
    'PENDING', UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO lifecycle_mcu_probe (
    deployment_uid, rollout_id, release_id, asset_id, deployment_kind, wave_no,
    deployment_status, command_uid, reliable_task_uid, queued_at, created_at, updated_at
) VALUES ('10000000-0000-4000-8000-000000000002', 1, 1, 2, 'WAVE', 1,
    'QUEUED', '20000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002',
    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));

INSERT INTO lifecycle_edge_probe (
    deployment_uid, rollout_id, release_id, asset_id, deployment_kind, wave_no,
    deployment_status, eligibility_status, eligibility_snapshot, eligibility_sha256,
    source_software_fact_id, source_management_state_sequence, source_business_baseline_kind,
    created_at, updated_at
) VALUES ('40000000-0000-4000-8000-000000000001', 1, 1, 1, 'VALIDATION', 0,
    'PLANNED', 'ELIGIBLE', JSON_OBJECT(), UNHEX(REPEAT('01', 32)), 1, 1, 'IMAGE_BRIDGE',
    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));
INSERT INTO lifecycle_edge_probe (
    deployment_uid, rollout_id, release_id, asset_id, deployment_kind, wave_no,
    deployment_status, eligibility_status, eligibility_snapshot, eligibility_sha256,
    source_software_fact_id, source_management_state_sequence, source_business_baseline_kind,
    command_uid, reliable_task_uid, control_sequence, queued_at, created_at, updated_at
) VALUES ('40000000-0000-4000-8000-000000000002', 1, 1, 2, 'WAVE', 1,
    'QUEUED', 'ELIGIBLE', JSON_OBJECT(), UNHEX(REPEAT('01', 32)), 1, 1, 'IMAGE_BRIDGE',
    '50000000-0000-4000-8000-000000000002', '60000000-0000-4000-8000-000000000002', 1,
    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3), UTC_TIMESTAMP(3));

UPDATE lifecycle_mcu_probe SET deployment_status = 'LOCAL_CANCELLED',
    error_code = 'DEVICE_DISABLED', completed_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3);
UPDATE lifecycle_edge_probe SET deployment_status = 'LOCAL_CANCELLED',
    error_code = 'DEVICE_RETIRED', completed_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3);
SELECT 'MCU_LOCAL_CANCELLATION', COUNT(*) FROM lifecycle_mcu_probe WHERE deployment_status = 'LOCAL_CANCELLED';
SELECT 'EDGE_LOCAL_CANCELLATION', COUNT(*) FROM lifecycle_edge_probe WHERE deployment_status = 'LOCAL_CANCELLED';
DROP TEMPORARY TABLE lifecycle_mcu_probe;
DROP TEMPORARY TABLE lifecycle_edge_probe;
