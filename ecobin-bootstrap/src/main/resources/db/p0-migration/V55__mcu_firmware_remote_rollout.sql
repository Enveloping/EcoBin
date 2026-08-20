-- V55: immutable MCU firmware releases and explicitly operator-driven rollout.
-- Firmware packages are signed offline and stored in private COS.  The
-- backend only records their immutable identity and creates short-lived,
-- read-only download grants when a reliable command is actually dispatched.

-- These fields deliberately start NULL. They become eligible only after an
-- authenticated DEVICE_RUNTIME_SNAPSHOT carries a successful revision-2 F3
-- identity observation (STATUS=00), or after a verified cloud update result.
-- A blanket migration backfill would turn an unobserved MCU into a trusted one.
ALTER TABLE dev_device_asset
    ADD COLUMN mcu_firmware_version_code BIGINT UNSIGNED NULL
        AFTER factory_bag_set_sha256,
    ADD COLUMN mcu_firmware_identity_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER mcu_firmware_version_code,
    ADD COLUMN mcu_fixed_frame_revision INT NULL
        AFTER mcu_firmware_identity_hex,
    ADD CONSTRAINT ck_dev_asset_mcu_firmware_v55 CHECK (
        (
            mcu_firmware_version_code IS NULL
            AND mcu_firmware_identity_hex IS NULL
            AND mcu_fixed_frame_revision IS NULL
        )
        OR (
            mcu_firmware_version_code BETWEEN 1 AND 4294967295
            AND mcu_firmware_identity_hex REGEXP '^[0-9a-f]{16}$'
            AND mcu_fixed_frame_revision = 2
        )
    );

CREATE TABLE dev_mcu_firmware_release (
    id BIGINT NOT NULL AUTO_INCREMENT,
    release_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    firmware_version VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    firmware_version_code BIGINT UNSIGNED NOT NULL,
    firmware_identity_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    hardware_compatibility VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fixed_frame_revision INT NOT NULL,
    package_object_key VARCHAR(512)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    package_sha256 BINARY(32) NOT NULL,
    package_size BIGINT UNSIGNED NOT NULL,
    release_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_notes VARCHAR(1000) NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    promoted_by_platform_admin_id BIGINT NULL,
    promoted_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_mcu_release_uid UNIQUE (release_uid),
    CONSTRAINT uq_dev_mcu_release_operation UNIQUE (operation_uid),
    CONSTRAINT uq_dev_mcu_release_package UNIQUE (package_sha256),
    CONSTRAINT uq_dev_mcu_release_identity UNIQUE (
        hardware_compatibility,
        firmware_version_code,
        firmware_identity_hex
    ),
    CONSTRAINT ck_dev_mcu_release_uids CHECK (
        release_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_mcu_release_version CHECK (
        firmware_version REGEXP
            '^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$'
        AND firmware_version_code BETWEEN 1 AND 4294967295
        AND firmware_identity_hex REGEXP '^[0-9a-f]{16}$'
        AND hardware_compatibility = 'ECOBIN_MAINBOARD_V1.1'
        AND fixed_frame_revision = 2
    ),
    CONSTRAINT ck_dev_mcu_release_package CHECK (
        package_object_key REGEXP
            '^ecobin/mcu-firmware/[0-9a-f-]{36}/[0-9a-f]{64}\\.efw$'
        AND package_size BETWEEN 1 AND 131072
    ),
    CONSTRAINT ck_dev_mcu_release_status CHECK (
        release_status IN ('READY', 'PROMOTED', 'ARCHIVED')
        AND (
            (release_status = 'READY'
                AND promoted_by_platform_admin_id IS NULL
                AND promoted_at IS NULL)
            OR (release_status IN ('PROMOTED', 'ARCHIVED')
                AND promoted_by_platform_admin_id IS NOT NULL
                AND promoted_at IS NOT NULL)
        )
    ),
    CONSTRAINT ck_dev_mcu_release_times CHECK (
        updated_at >= created_at
        AND (promoted_at IS NULL OR promoted_at >= created_at)
    ),
    CONSTRAINT fk_dev_mcu_release_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_release_promoter
        FOREIGN KEY (promoted_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_mcu_release_list (release_status, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_mcu_firmware_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT,
    rollout_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_id BIGINT NOT NULL,
    rollout_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    batch_size INT NOT NULL,
    maximum_wave_no INT NOT NULL,
    current_wave_no INT NOT NULL DEFAULT -1,
    validation_asset_id BIGINT NOT NULL,
    change_reason VARCHAR(500) NOT NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    promoted_by_platform_admin_id BIGINT NULL,
    promoted_at DATETIME(3) NULL,
    stopped_by_platform_admin_id BIGINT NULL,
    stopped_at DATETIME(3) NULL,
    stop_reason VARCHAR(500) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_mcu_rollout_uid UNIQUE (rollout_uid),
    CONSTRAINT uq_dev_mcu_rollout_operation UNIQUE (operation_uid),
    CONSTRAINT ck_dev_mcu_rollout_uids CHECK (
        rollout_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_mcu_rollout_shape CHECK (
        batch_size BETWEEN 1 AND 100
        AND maximum_wave_no BETWEEN 1 AND 1000
        AND current_wave_no BETWEEN -1 AND maximum_wave_no
        AND lock_version >= 0
        AND CHAR_LENGTH(TRIM(change_reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_dev_mcu_rollout_status CHECK (
        rollout_status IN (
            'DRAFT', 'VALIDATING', 'VALIDATION_FAILED',
            'AWAITING_PROMOTION', 'ACTIVE', 'COMPLETED', 'STOPPED'
        )
        AND (
            (rollout_status IN (
                'DRAFT', 'VALIDATING', 'VALIDATION_FAILED',
                'AWAITING_PROMOTION'
            ) AND promoted_at IS NULL)
            OR (rollout_status IN ('ACTIVE', 'COMPLETED', 'STOPPED')
                AND promoted_at IS NOT NULL)
        )
        AND (
            (rollout_status = 'STOPPED'
                AND stopped_by_platform_admin_id IS NOT NULL
                AND stopped_at IS NOT NULL
                AND CHAR_LENGTH(TRIM(stop_reason)) BETWEEN 1 AND 500)
            OR (rollout_status <> 'STOPPED'
                AND stopped_by_platform_admin_id IS NULL
                AND stopped_at IS NULL
                AND stop_reason IS NULL)
        )
    ),
    CONSTRAINT ck_dev_mcu_rollout_times CHECK (
        updated_at >= created_at
        AND (promoted_at IS NULL OR promoted_at >= created_at)
        AND (stopped_at IS NULL OR stopped_at >= created_at)
    ),
    CONSTRAINT fk_dev_mcu_rollout_release
        FOREIGN KEY (release_id) REFERENCES dev_mcu_firmware_release (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_rollout_validation_asset
        FOREIGN KEY (validation_asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_rollout_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_rollout_promoter
        FOREIGN KEY (promoted_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_rollout_stopper
        FOREIGN KEY (stopped_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_mcu_rollout_list (rollout_status, created_at DESC, id DESC),
    INDEX ix_dev_mcu_rollout_release (release_id, rollout_status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_mcu_firmware_deployment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    deployment_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rollout_id BIGINT NOT NULL,
    release_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    tenant_id BIGINT NULL,
    organization_id BIGINT NULL,
    deployment_kind VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    wave_no INT NOT NULL,
    deployment_status VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    reliable_task_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    edge_update_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    target_attempt_count INT NOT NULL DEFAULT 0,
    rollback_attempt_count INT NOT NULL DEFAULT 0,
    installed_firmware_version VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    installed_firmware_version_code BIGINT UNSIGNED NULL,
    installed_firmware_identity_hex CHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    queued_at DATETIME(3) NULL,
    completed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_mcu_deployment_uid UNIQUE (deployment_uid),
    CONSTRAINT uq_dev_mcu_deployment_asset UNIQUE (rollout_id, asset_id),
    CONSTRAINT uq_dev_mcu_deployment_command UNIQUE (command_uid),
    CONSTRAINT uq_dev_mcu_deployment_task UNIQUE (reliable_task_uid),
    CONSTRAINT ck_dev_mcu_deployment_uid CHECK (
        deployment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_mcu_deployment_scope CHECK (
        (tenant_id IS NULL AND organization_id IS NULL)
        OR (tenant_id IS NOT NULL)
    ),
    CONSTRAINT ck_dev_mcu_deployment_wave CHECK (
        (deployment_kind = 'VALIDATION' AND wave_no = 0)
        OR (deployment_kind = 'WAVE' AND wave_no >= 1)
    ),
    CONSTRAINT ck_dev_mcu_deployment_status CHECK (
        deployment_status IN (
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
        )
    ),
    CONSTRAINT ck_dev_mcu_deployment_installed CHECK (
        (
            installed_firmware_version IS NULL
            AND installed_firmware_version_code IS NULL
            AND installed_firmware_identity_hex IS NULL
        )
        OR (
            installed_firmware_version IS NOT NULL
            AND installed_firmware_version_code BETWEEN 1 AND 4294967295
            AND installed_firmware_identity_hex REGEXP '^[0-9a-f]{16}$'
        )
    ),
    CONSTRAINT ck_dev_mcu_deployment_times CHECK (
        updated_at >= created_at
        AND (queued_at IS NULL OR queued_at >= created_at)
        AND (completed_at IS NULL OR completed_at >= created_at)
    ),
    CONSTRAINT fk_dev_mcu_deployment_rollout
        FOREIGN KEY (rollout_id) REFERENCES dev_mcu_firmware_rollout (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_deployment_release
        FOREIGN KEY (release_id) REFERENCES dev_mcu_firmware_release (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_deployment_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_deployment_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_deployment_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_mcu_deployment_wave (
        rollout_id, wave_no, deployment_status, id
    ),
    INDEX ix_dev_mcu_deployment_asset_status (
        asset_id, deployment_status, updated_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_mcu_firmware_progress (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_update_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    stage VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_attempt_count INT NOT NULL,
    rollback_attempt_count INT NOT NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    payload_sha256 BINARY(32) NOT NULL,
    normalized_payload JSON NOT NULL,
    occurred_at DATETIME(3) NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_mcu_progress_event UNIQUE (event_uid),
    CONSTRAINT uq_dev_mcu_progress_inbox UNIQUE (source_inbox_id),
    CONSTRAINT ck_dev_mcu_progress_uid CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND edge_update_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_mcu_progress_attempts CHECK (
        target_attempt_count BETWEEN 0 AND 3
        AND rollback_attempt_count BETWEEN 0 AND 3
    ),
    CONSTRAINT fk_dev_mcu_progress_inbox
        FOREIGN KEY (source_inbox_id) REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_progress_deployment
        FOREIGN KEY (deployment_id) REFERENCES dev_mcu_firmware_deployment (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_mcu_progress_deployment (deployment_id, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_mcu_firmware_rollout_action (
    id BIGINT NOT NULL AUTO_INCREMENT,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rollout_id BIGINT NOT NULL,
    action_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    resulting_wave_no INT NULL,
    requested_by_platform_admin_id BIGINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_mcu_rollout_action_operation UNIQUE (operation_uid),
    CONSTRAINT ck_dev_mcu_rollout_action_uid CHECK (
        operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_mcu_rollout_action_type CHECK (
        action_type IN (
            'START_VALIDATION', 'PROMOTE', 'ADVANCE_WAVE', 'STOP'
        )
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT fk_dev_mcu_rollout_action_rollout
        FOREIGN KEY (rollout_id) REFERENCES dev_mcu_firmware_rollout (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_mcu_rollout_action_admin
        FOREIGN KEY (requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_mcu_rollout_action_history (rollout_id, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
