-- Cloud cancellation records intent withdrawal, not physical success or rollback.
-- No historical evidence, ownership, bag binding or completed work is removed.

ALTER TABLE dev_config_application
    DROP CHECK ck_dev_config_app_status,
    ADD CONSTRAINT ck_dev_config_app_status CHECK (
        status IN ('PENDING', 'EDGE_SAVED', 'APPLIED', 'FAILED', 'CANCELLED')
    );

ALTER TABLE dev_config_application
    DROP CHECK ck_dev_config_app_state_shape,
    ADD CONSTRAINT ck_dev_config_app_state_shape CHECK (
        ((
            status = 'PENDING'
            AND edge_persisted_at IS NULL
            AND mcu_synced_at IS NULL
            AND applied_at IS NULL
        )
        OR
        (
            status = 'EDGE_SAVED'
            AND reported_version_no IS NOT NULL
            AND reported_content_sha256 IS NOT NULL
            AND reported_mcu_payload_sha256 IS NOT NULL
            AND edge_persisted_at IS NOT NULL
            AND mcu_synced_at IS NULL
            AND applied_at IS NULL
        )
        OR
        (
            status = 'APPLIED'
            AND reported_version_no IS NOT NULL
            AND reported_content_sha256 IS NOT NULL
            AND reported_mcu_payload_sha256 IS NOT NULL
            AND edge_persisted_at IS NOT NULL
            AND mcu_synced_at IS NOT NULL
            AND applied_at IS NOT NULL
        )
        OR
        (
            status = 'FAILED'
            AND reported_version_no IS NOT NULL
            AND reported_content_sha256 IS NOT NULL
            AND reported_mcu_payload_sha256 IS NOT NULL
            AND applied_at IS NULL
            AND last_failure_at IS NOT NULL
            AND last_failure_code IS NOT NULL
            AND (
                mcu_synced_at IS NULL
                OR edge_persisted_at IS NOT NULL
            )
        )) OR (status = 'CANCELLED' AND applied_at IS NULL AND last_failure_at IS NOT NULL AND last_failure_code IN ('DEVICE_DISABLED', 'DEVICE_RETIRED'))
    );

ALTER TABLE dev_mcu_firmware_deployment
    DROP CHECK ck_dev_mcu_deployment_status,
    ADD CONSTRAINT ck_dev_mcu_deployment_status CHECK (
        (deployment_status IN (
            'PENDING', 'QUEUED', 'PACKAGE_FETCH_FAILED',
            'PREFLIGHT', 'PREPARED',
            'FLASHING_TARGET', 'VERIFYING_TARGET', 'ROLLING_BACK',
            'VERIFYING_ROLLBACK', 'SUCCEEDED', 'ROLLED_BACK',
            'FAILED_LOCKED', 'REJECTED'
        )
        AND target_attempt_count BETWEEN 0 AND 3
        AND rollback_attempt_count BETWEEN 0 AND 3
        AND lock_version >= 0
        AND (
            (deployment_status = 'PENDING'
                AND command_uid IS NULL
                AND reliable_task_uid IS NULL
                AND queued_at IS NULL)
            OR (deployment_status <> 'PENDING'
                AND command_uid IS NOT NULL
                AND reliable_task_uid IS NOT NULL
                AND queued_at IS NOT NULL)
        )
        AND (
            (deployment_status IN (
                'PACKAGE_FETCH_FAILED', 'FAILED_LOCKED', 'REJECTED'
            )
                AND error_code IS NOT NULL)
            OR deployment_status NOT IN (
                'PACKAGE_FETCH_FAILED', 'FAILED_LOCKED', 'REJECTED'
            )
        )
        AND (
            (deployment_status IN (
                'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED'
            ) AND completed_at IS NOT NULL)
            OR (deployment_status NOT IN (
                'SUCCEEDED', 'ROLLED_BACK', 'FAILED_LOCKED', 'REJECTED'
            ) AND completed_at IS NULL)
        )) OR (deployment_status = 'LOCAL_CANCELLED' AND completed_at IS NOT NULL AND error_code IN ('DEVICE_DISABLED', 'DEVICE_RETIRED') AND target_attempt_count = 0 AND rollback_attempt_count = 0 AND lock_version >= 0 AND ((command_uid IS NULL AND reliable_task_uid IS NULL AND queued_at IS NULL) OR (command_uid IS NOT NULL AND reliable_task_uid IS NOT NULL AND queued_at IS NOT NULL)))
    );

ALTER TABLE dev_edge_software_deployment
    DROP CHECK ck_dev_edge_deployment_state,
    ADD CONSTRAINT ck_dev_edge_deployment_state CHECK (
        deployment_status IN (
            'PLANNED', 'QUEUED', 'RECEIVED', 'DOWNLOADING',
            'VERIFYING_PACKAGE', 'PACKAGE_READY', 'WAITING_FOR_IDLE',
            'MIGRATING_DATA', 'ACTIVATING', 'VERIFYING_TARGET',
            'OBSERVING', 'ROLLING_BACK', 'VERIFYING_ROLLBACK',
            'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
            'FAILED_LOCKED', 'DOWNLOAD_AUTHORIZATION_REQUIRED',
            'CANCELLED', 'LOCAL_CANCELLED'
        )
        AND eligibility_status = 'ELIGIBLE'
        AND source_management_state_sequence BETWEEN 1 AND 9007199254740991
        AND stage_sequence BETWEEN 0 AND 9007199254740991
        AND business_admission_state IN (
            'OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED'
        )
        AND download_attempt_count BETWEEN 0 AND 10
        AND target_attempt_count BETWEEN 0 AND 10
        AND rollback_attempt_count BETWEEN 0 AND 10
        AND database_restored IN (0, 1)
        AND lock_version >= 0
        AND (
            (deployment_status IN ('PLANNED', 'LOCAL_CANCELLED')
                AND command_uid IS NULL
                AND reliable_task_uid IS NULL
                AND control_sequence IS NULL
                AND queued_at IS NULL)
            OR (deployment_status <> 'PLANNED'
                AND command_uid IS NOT NULL
                AND reliable_task_uid IS NOT NULL
                AND control_sequence BETWEEN 1 AND 9007199254740991
                AND queued_at IS NOT NULL)
        )
        AND cancellation_status IN (
            'NONE', 'QUEUED', 'CANCELLED', 'TOO_LATE'
        )
        AND (
            cancellation_status = 'NONE'
            OR cancel_control_sequence > control_sequence
        )
        AND (
            (cancellation_status = 'NONE'
                AND cancel_command_uid IS NULL
                AND cancel_reliable_task_uid IS NULL
                AND cancel_control_sequence IS NULL
                AND cancel_reason IS NULL
                AND cancel_requested_by_platform_admin_id IS NULL
                AND cancel_requested_at IS NULL
                AND cancel_result_at IS NULL)
            OR (cancellation_status = 'QUEUED'
                AND cancel_command_uid IS NOT NULL
                AND cancel_reliable_task_uid IS NOT NULL
                AND cancel_control_sequence BETWEEN 1 AND 9007199254740991
                AND CHAR_LENGTH(TRIM(cancel_reason)) BETWEEN 1 AND 500
                AND cancel_requested_by_platform_admin_id IS NOT NULL
                AND cancel_requested_at IS NOT NULL
                AND cancel_result_at IS NULL)
            OR (cancellation_status IN ('CANCELLED', 'TOO_LATE')
                AND cancel_command_uid IS NOT NULL
                AND cancel_reliable_task_uid IS NOT NULL
                AND cancel_control_sequence BETWEEN 1 AND 9007199254740991
                AND CHAR_LENGTH(TRIM(cancel_reason)) BETWEEN 1 AND 500
                AND cancel_requested_by_platform_admin_id IS NOT NULL
                AND cancel_requested_at IS NOT NULL
                AND cancel_result_at IS NOT NULL)
        )
        AND (
            (deployment_status = 'CANCELLED'
                AND cancellation_status = 'CANCELLED')
            OR (deployment_status <> 'CANCELLED'
                AND cancellation_status <> 'CANCELLED')
        )
        AND (deployment_status <> 'LOCAL_CANCELLED' OR (cancellation_status = 'NONE' AND completed_at IS NOT NULL AND error_code IN ('DEVICE_DISABLED', 'DEVICE_RETIRED') AND stage_sequence = 0))
    );

ALTER TABLE rec_fullness_detection
    DROP CHECK ck_rec_fullness_detection_state,
    ADD CONSTRAINT ck_rec_fullness_detection_state CHECK (
        ((
            (
                status = 'PENDING_INITIAL_SAMPLE'
                AND final_result IS NULL
                AND failure_code IS NULL
                AND disposition = 'PENDING'
                AND initial_sample_id IS NULL
                AND initial_sample_conclusion IS NULL
                AND terminal_sample_id IS NULL
                AND terminal_sample_conclusion IS NULL
                AND completed_at IS NULL
            )
            OR
            (
                status = 'WAITING_RECHECK'
                AND final_result IS NULL
                AND failure_code IS NULL
                AND disposition = 'PENDING'
                AND initial_sample_id IS NOT NULL
                AND initial_sample_conclusion = 'FULL'
                AND terminal_sample_id IS NULL
                AND terminal_sample_conclusion IS NULL
                AND next_sample_at IS NOT NULL
                AND completed_at IS NULL
            )
            OR
            (
                status = 'COMPLETED'
                AND final_result IN ('NOT_FULL', 'FULL')
                AND failure_code IS NULL
                AND disposition IN ('APPLIED', 'STALE_IGNORED')
                AND terminal_sample_id IS NOT NULL
                AND terminal_sample_conclusion = final_result
                AND completed_at IS NOT NULL
            )
            OR
            (
                status = 'FAILED'
                AND final_result = 'SOURCE_FAILED'
                AND failure_code IS NOT NULL
                AND CHAR_LENGTH(TRIM(failure_code)) > 0
                AND disposition IN ('APPLIED', 'STALE_IGNORED')
                AND completed_at IS NOT NULL
                AND (
                    (
                        terminal_sample_id IS NOT NULL
                        AND terminal_sample_conclusion = 'SOURCE_FAILED'
                    )
                    OR (
                        initial_sample_id IS NULL
                        AND initial_sample_conclusion IS NULL
                        AND terminal_sample_id IS NULL
                        AND terminal_sample_conclusion IS NULL
                        AND (
                            failure_code = 'EDGE_RESTARTED'
                            OR (
                                trigger_type = 'CLEAN_COMPLETE'
                                AND baseline_state_snapshot IN (
                                    'INVALID', 'MISSING'
                                )
                                AND failure_code =
                                    'WEIGHT_BASELINE_UNAVAILABLE'
                            )
                        )
                    )
                )
            )
            OR
            (
                status = 'RETIRED'
                AND final_result IS NULL
                AND failure_code = 'EDGE_REPORTED_STATE_MIGRATION'
                AND disposition = 'STALE_IGNORED'
                AND terminal_sample_id IS NULL
                AND terminal_sample_conclusion IS NULL
                AND next_sample_at IS NULL
                AND completed_at IS NOT NULL
            )
        )
        AND ((initial_sample_id IS NULL) =
             (initial_sample_conclusion IS NULL))
        AND ((terminal_sample_id IS NULL) =
             (terminal_sample_conclusion IS NULL))) OR (status = 'CANCELLED' AND final_result IS NULL AND failure_code IN ('DEVICE_DISABLED', 'DEVICE_RETIRED') AND disposition = 'STALE_IGNORED' AND next_sample_at IS NULL AND completed_at IS NOT NULL)
    );

-- Stop pre-existing inactive-device maintenance work as well. Business histories,
-- delivery/cleaning recovery and transport acknowledgements are not rewritten.
ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_dispatch_wait,
    ADD CONSTRAINT ck_ops_task_dispatch_wait CHECK (
        (state = 'PENDING' AND (
            dispatch_wait_reason IS NULL
            OR dispatch_wait_reason IN ('DEVICE_OFFLINE', 'DEVICE_PRESENCE_UNKNOWN',
                'RUNTIME_MISSING', 'AWAITING_DEVICE_EVIDENCE', 'DEVICE_DISABLED')
            OR dispatch_wait_reason REGEXP
                '^PAYOUT_NOT_ENOUGH:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )) OR (state <> 'PENDING' AND dispatch_wait_reason IS NULL)
    );

UPDATE dev_config_application application
JOIN dev_device_asset asset ON asset.id = application.asset_id
SET application.status = 'CANCELLED',
    application.last_failure_code = CONCAT('DEVICE_', asset.lifecycle_status),
    application.last_failure_at = UTC_TIMESTAMP(3), application.updated_at = UTC_TIMESTAMP(3),
    application.lock_version = application.lock_version + 1
WHERE asset.lifecycle_status = 'RETIRED'
  AND application.status IN ('PENDING', 'EDGE_SAVED');

UPDATE ops_reliable_task task
JOIN dev_device_asset asset ON asset.id = task.source_device_asset_id
SET task.state = 'CANCELLED', task.next_run_at = NULL,
    task.lease_token = NULL, task.lease_worker = NULL, task.lease_until = NULL,
    task.handled_wake_version = task.wake_version, task.completed_at = UTC_TIMESTAMP(3),
    task.blocked_reason_code = NULL, task.blocked_diagnostic = NULL, task.dispatch_wait_reason = NULL,
    task.updated_at = UTC_TIMESTAMP(3), task.lock_version = task.lock_version + 1
WHERE asset.lifecycle_status IN ('DISABLED', 'RETIRED')
  AND task.state IN ('PENDING', 'BLOCKED') AND task.execution_lane = 'DEVICE'
  AND task.task_type IN ('ENSURE_DEVICE_CONFIGURATION', 'REQUEST_DEVICE_ACCEPTANCE',
      'SAMPLE_FULLNESS', 'MEASURE_EMPTY_BAG_BASELINE', 'SYNC_DEVICE_ENTRY_URL')
  AND (asset.lifecycle_status = 'RETIRED' OR task.task_type <> 'ENSURE_DEVICE_CONFIGURATION');

UPDATE ops_reliable_task task
JOIN dev_device_asset asset ON asset.id = task.source_device_asset_id
SET task.dispatch_wait_reason = CASE WHEN task.dispatch_wait_reason = 'AWAITING_DEVICE_EVIDENCE'
        THEN task.dispatch_wait_reason ELSE 'DEVICE_DISABLED' END,
    task.updated_at = UTC_TIMESTAMP(3), task.lock_version = task.lock_version + 1
WHERE asset.lifecycle_status = 'DISABLED' AND task.state = 'PENDING'
  AND task.task_type IN ('ENSURE_DEVICE_CONFIGURATION', 'START_MCU_FIRMWARE_UPDATE', 'START_BUSINESS_RUNTIME_UPDATE');

UPDATE rec_port_baseline_measurement measurement
JOIN dev_device_asset asset ON asset.id = measurement.asset_id
SET measurement.status = 'TECHNICAL_ABORTED',
    measurement.fault_code = CONCAT('DEVICE_', asset.lifecycle_status),
    measurement.completed_at = UTC_TIMESTAMP(3), measurement.updated_at = UTC_TIMESTAMP(3),
    measurement.lock_version = measurement.lock_version + 1
WHERE asset.lifecycle_status IN ('DISABLED', 'RETIRED') AND measurement.status = 'PENDING';

UPDATE rec_fullness_detection detection
JOIN dev_device_asset asset ON asset.id = detection.asset_id
SET detection.status = 'CANCELLED', detection.failure_code = CONCAT('DEVICE_', asset.lifecycle_status),
    detection.disposition = 'STALE_IGNORED', detection.next_sample_at = NULL,
    detection.completed_at = UTC_TIMESTAMP(3), detection.updated_at = UTC_TIMESTAMP(3),
    detection.lock_version = detection.lock_version + 1
WHERE asset.lifecycle_status IN ('DISABLED', 'RETIRED')
  AND detection.status IN ('PENDING_INITIAL_SAMPLE', 'WAITING_RECHECK');

UPDATE rec_port_capacity_state capacity
JOIN rec_fullness_detection detection ON detection.id = capacity.current_detection_id
SET capacity.detection_gate = 'READY', capacity.current_detection_id = NULL,
    capacity.updated_at = UTC_TIMESTAMP(3), capacity.lock_version = capacity.lock_version + 1
WHERE detection.status = 'CANCELLED';
