-- V66: authoritative cancellation for one dispatched business-runtime update.
--
-- A platform request does not itself cancel an update.  The device reports
-- either CANCELLED (before mutation) or TOO_LATE (the update keeps running).

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v56,
    ADD CONSTRAINT ck_ops_task_sources_v66 CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_asset_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_asset_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR (
                    source_device_asset_id IS NOT NULL
                    AND (
                        (
                            scope_kind = 'PLATFORM'
                            AND tenant_id IS NULL
                            AND organization_id IS NULL
                            AND source_device_command_id IS NULL
                            AND task_type IN (
                                'REQUEST_DEVICE_ACCEPTANCE',
                                'AUTHORIZE_FACTORY_SEAL',
                                'START_MCU_FIRMWARE_UPDATE',
                                'START_BUSINESS_RUNTIME_UPDATE',
                                'CANCEL_BUSINESS_RUNTIME_UPDATE',
                                'SYNC_DEVICE_ENTRY_URL',
                                'OPEN_REMOTE_SUPPORT_TUNNEL',
                                'CLOSE_REMOTE_SUPPORT_TUNNEL',
                                'CONFIRM_EDGE_EVENT'
                            )
                        )
                        OR (
                            scope_kind = 'ORGANIZATION'
                            AND (
                                source_device_command_id IS NOT NULL
                                OR (
                                    source_device_command_id IS NULL
                                    AND task_type IN (
                                        'CONFIRM_EDGE_EVENT',
                                        'PROVIDE_PHOTO_UPLOAD_GRANT'
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    );

ALTER TABLE dev_edge_software_deployment
    DROP CHECK ck_dev_edge_deployment_state,
    DROP CHECK ck_dev_edge_deployment_times,
    ADD COLUMN cancel_command_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER control_sequence,
    ADD COLUMN cancel_reliable_task_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER cancel_command_uid,
    ADD COLUMN cancel_control_sequence BIGINT UNSIGNED NULL
        AFTER cancel_reliable_task_uid,
    ADD COLUMN cancellation_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'NONE'
        AFTER cancel_control_sequence,
    ADD COLUMN cancel_reason VARCHAR(500) NULL
        AFTER cancellation_status,
    ADD COLUMN cancel_requested_by_platform_admin_id BIGINT NULL
        AFTER cancel_reason,
    ADD COLUMN cancel_requested_at DATETIME(3) NULL
        AFTER cancel_requested_by_platform_admin_id,
    ADD COLUMN cancel_result_at DATETIME(3) NULL
        AFTER cancel_requested_at,
    ADD CONSTRAINT uq_dev_edge_deployment_cancel_command
        UNIQUE (cancel_command_uid),
    ADD CONSTRAINT uq_dev_edge_deployment_cancel_task
        UNIQUE (cancel_reliable_task_uid),
    ADD CONSTRAINT fk_dev_edge_deployment_cancel_admin
        FOREIGN KEY (cancel_requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT ck_dev_edge_deployment_state CHECK (
        deployment_status IN (
            'PLANNED', 'QUEUED', 'RECEIVED', 'DOWNLOADING',
            'VERIFYING_PACKAGE', 'PACKAGE_READY', 'WAITING_FOR_IDLE',
            'MIGRATING_DATA', 'ACTIVATING', 'VERIFYING_TARGET',
            'OBSERVING', 'ROLLING_BACK', 'VERIFYING_ROLLBACK',
            'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
            'FAILED_LOCKED', 'DOWNLOAD_AUTHORIZATION_REQUIRED',
            'CANCELLED'
        )
        AND eligibility_status = 'ELIGIBLE'
        AND source_management_state_sequence BETWEEN 1 AND 9007199254740991
        AND source_business_release_sequence BETWEEN 1 AND 9007199254740991
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
            (deployment_status = 'PLANNED'
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
    ),
    ADD CONSTRAINT ck_dev_edge_deployment_times CHECK (
        updated_at >= created_at
        AND (queued_at IS NULL OR queued_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= queued_at)
        AND (cancel_requested_at IS NULL
            OR cancel_requested_at >= queued_at)
        AND (cancel_result_at IS NULL
            OR cancel_result_at >= cancel_requested_at)
    );

ALTER TABLE dev_edge_software_rollout_action
    DROP CHECK ck_dev_edge_rollout_action_type,
    ADD CONSTRAINT ck_dev_edge_rollout_action_type CHECK (
        action_type IN ('CREATE', 'START_VALIDATION', 'REQUEST_CANCEL', 'STOP')
        AND resulting_status IN (
            'DRAFT', 'VALIDATING', 'AWAITING_PROMOTION',
            'VALIDATION_FAILED', 'STOPPED'
        )
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    );

CREATE TABLE dev_edge_software_deployment_cancel_result (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    cancel_command_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    edge_update_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    control_sequence BIGINT UNSIGNED NOT NULL,
    result VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    observed_stage VARCHAR(40)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_admission_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    payload_sha256 BINARY(32) NOT NULL,
    normalized_payload JSON NOT NULL,
    occurred_at DATETIME(3) NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_cancel_result_event UNIQUE (event_uid),
    CONSTRAINT uq_dev_edge_cancel_result_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_edge_cancel_result_command UNIQUE (cancel_command_uid),
    CONSTRAINT ck_dev_edge_cancel_result CHECK (
        control_sequence BETWEEN 1 AND 9007199254740991
        AND result IN ('CANCELLED', 'TOO_LATE')
        AND observed_stage IN (
            'RECEIVED', 'VERIFYING_PACKAGE', 'PACKAGE_READY',
            'WAITING_FOR_IDLE', 'MIGRATING_DATA', 'ACTIVATING',
            'VERIFYING_TARGET', 'OBSERVING', 'ROLLING_BACK',
            'VERIFYING_ROLLBACK', 'SUCCEEDED', 'ROLLED_BACK',
            'DEFERRED', 'REJECTED', 'FAILED_LOCKED'
        )
        AND business_admission_state IN (
            'OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED'
        )
        AND (
            (result = 'CANCELLED' AND error_code IS NULL)
            OR (result = 'TOO_LATE'
                AND error_code = 'BUSINESS_UPDATE_CANCEL_TOO_LATE')
        )
    ),
    CONSTRAINT fk_dev_edge_cancel_result_inbox
        FOREIGN KEY (source_inbox_id) REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_cancel_result_deployment
        FOREIGN KEY (deployment_id) REFERENCES dev_edge_software_deployment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_cancel_result_update (
        edge_update_uid, control_sequence
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

DELIMITER //

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_cancel_result_v66_immutable
BEFORE UPDATE ON dev_edge_software_deployment_cancel_result
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business deployment cancellation result is immutable';
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_cancel_result_v66_no_delete
BEFORE DELETE ON dev_edge_software_deployment_cancel_result
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business deployment cancellation result cannot be deleted';
END//

DELIMITER ;
