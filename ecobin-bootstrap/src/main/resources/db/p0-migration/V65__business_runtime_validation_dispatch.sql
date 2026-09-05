-- V65: first real business-runtime deployment slice.
--
-- Only the explicitly selected validation device can be dispatched here.
-- Later migrations may add promotion and wave dispatch without changing the
-- immutable command/deployment identities introduced by this migration.

ALTER TABLE dev_edge_software_rollout
    DROP CHECK ck_dev_edge_rollout_policy,
    DROP CHECK ck_dev_edge_rollout_stop,
    MODIFY COLUMN rollout_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_dev_edge_rollout_policy CHECK (
        rollout_status IN (
            'DRAFT', 'VALIDATING', 'AWAITING_PROMOTION',
            'VALIDATION_FAILED', 'ACTIVE', 'COMPLETED', 'STOPPED'
        )
        AND batch_size BETWEEN 1 AND 100
        AND maximum_wave_no BETWEEN 0 AND 1000
        AND (
            (rollout_status IN ('DRAFT', 'STOPPED') AND current_wave_no = -1)
            OR (rollout_status IN (
                    'VALIDATING', 'AWAITING_PROMOTION', 'VALIDATION_FAILED'
                ) AND current_wave_no = 0)
            OR (rollout_status IN ('ACTIVE', 'COMPLETED')
                AND current_wave_no BETWEEN 0 AND maximum_wave_no)
        )
        AND observation_window_seconds BETWEEN 60 AND 86400
        AND download_timeout_seconds BETWEEN 60 AND 86400
        AND drain_timeout_seconds BETWEEN 60 AND 86400
        AND maximum_retry_count BETWEEN 0 AND 10
        AND remote_dispatch_enabled_snapshot IN (0, 1)
        AND CHAR_LENGTH(TRIM(change_reason)) BETWEEN 1 AND 500
        AND lock_version >= 0
    ),
    ADD CONSTRAINT ck_dev_edge_rollout_stop CHECK (
        (rollout_status <> 'STOPPED'
            AND stopped_by_platform_admin_id IS NULL
            AND stopped_at IS NULL
            AND stop_reason IS NULL)
        OR (rollout_status = 'STOPPED'
            AND stopped_by_platform_admin_id IS NOT NULL
            AND stopped_at IS NOT NULL
            AND CHAR_LENGTH(TRIM(stop_reason)) BETWEEN 1 AND 500)
    );

ALTER TABLE dev_edge_software_deployment
    DROP CHECK ck_dev_edge_deployment_state,
    DROP CHECK ck_dev_edge_deployment_times,
    MODIFY COLUMN deployment_status VARCHAR(40)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD COLUMN command_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER eligibility_sha256,
    ADD COLUMN reliable_task_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER command_uid,
    ADD COLUMN edge_update_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER reliable_task_uid,
    ADD COLUMN control_sequence BIGINT UNSIGNED NULL AFTER edge_update_uid,
    ADD COLUMN stage_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER control_sequence,
    ADD COLUMN business_admission_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'OPEN'
        AFTER stage_sequence,
    ADD COLUMN download_attempt_count INT NOT NULL DEFAULT 0
        AFTER business_admission_state,
    ADD COLUMN target_attempt_count INT NOT NULL DEFAULT 0
        AFTER download_attempt_count,
    ADD COLUMN rollback_attempt_count INT NOT NULL DEFAULT 0
        AFTER target_attempt_count,
    ADD COLUMN installed_release_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER rollback_attempt_count,
    ADD COLUMN installed_version_name VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER installed_release_uid,
    ADD COLUMN installed_release_sequence BIGINT UNSIGNED NULL
        AFTER installed_version_name,
    ADD COLUMN installed_package_sha256 BINARY(32) NULL
        AFTER installed_release_sequence,
    ADD COLUMN database_restored TINYINT NOT NULL DEFAULT 0
        AFTER installed_package_sha256,
    ADD COLUMN error_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER database_restored,
    ADD COLUMN last_event_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER error_code,
    ADD COLUMN queued_at DATETIME(3) NULL AFTER last_event_uid,
    ADD COLUMN completed_at DATETIME(3) NULL AFTER queued_at,
    ADD CONSTRAINT uq_dev_edge_deployment_command UNIQUE (command_uid),
    ADD CONSTRAINT uq_dev_edge_deployment_task UNIQUE (reliable_task_uid),
    ADD CONSTRAINT uq_dev_edge_deployment_update UNIQUE (edge_update_uid),
    ADD CONSTRAINT uq_dev_edge_deployment_event UNIQUE (last_event_uid),
    ADD CONSTRAINT ck_dev_edge_deployment_state CHECK (
        deployment_status IN (
            'PLANNED', 'QUEUED', 'RECEIVED', 'DOWNLOADING',
            'VERIFYING_PACKAGE', 'PACKAGE_READY', 'WAITING_FOR_IDLE',
            'MIGRATING_DATA', 'ACTIVATING', 'VERIFYING_TARGET',
            'OBSERVING', 'ROLLING_BACK', 'VERIFYING_ROLLBACK',
            'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
            'FAILED_LOCKED', 'DOWNLOAD_AUTHORIZATION_REQUIRED'
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
    ),
    ADD CONSTRAINT ck_dev_edge_deployment_times CHECK (
        updated_at >= created_at
        AND (queued_at IS NULL OR queued_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= queued_at)
    );

ALTER TABLE dev_edge_software_rollout_action
    DROP CHECK ck_dev_edge_rollout_action_type,
    MODIFY COLUMN resulting_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_dev_edge_rollout_action_type CHECK (
        action_type IN ('CREATE', 'START_VALIDATION', 'STOP')
        AND resulting_status IN (
            'DRAFT', 'VALIDATING', 'AWAITING_PROMOTION',
            'VALIDATION_FAILED', 'STOPPED'
        )
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    );

CREATE TABLE dev_edge_software_deployment_progress (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_update_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stage VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stage_sequence BIGINT UNSIGNED NOT NULL,
    business_admission_state VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    download_attempt_count INT NOT NULL,
    target_attempt_count INT NOT NULL,
    rollback_attempt_count INT NOT NULL,
    installed_release_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    installed_version_name VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    installed_release_sequence BIGINT UNSIGNED NULL,
    installed_package_sha256 BINARY(32) NULL,
    database_restored TINYINT NOT NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    payload_sha256 BINARY(32) NOT NULL,
    normalized_payload JSON NOT NULL,
    occurred_at DATETIME(3) NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_progress_event UNIQUE (event_uid),
    CONSTRAINT uq_dev_edge_progress_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_edge_progress_stage UNIQUE (
        deployment_id, stage_sequence
    ),
    CONSTRAINT ck_dev_edge_progress_counts CHECK (
        stage_sequence BETWEEN 1 AND 9007199254740991
        AND download_attempt_count BETWEEN 0 AND 10
        AND target_attempt_count BETWEEN 0 AND 10
        AND rollback_attempt_count BETWEEN 0 AND 10
        AND database_restored IN (0, 1)
        AND business_admission_state IN (
            'OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED'
        )
    ),
    CONSTRAINT fk_dev_edge_progress_inbox FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_progress_deployment FOREIGN KEY (deployment_id)
        REFERENCES dev_edge_software_deployment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_progress_update (edge_update_uid, stage_sequence)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

DELIMITER //

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_progress_v65_immutable
BEFORE UPDATE ON dev_edge_software_deployment_progress
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business deployment progress is immutable';
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_progress_v65_no_delete
BEFORE DELETE ON dev_edge_software_deployment_progress
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business deployment progress cannot be deleted';
END//

DELIMITER ;
