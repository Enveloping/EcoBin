-- V64: Orange Pi business-runtime release control plane.
--
-- This migration intentionally stops before remote dispatch.  It supports
-- immutable artifact verification, explicit approval, eligibility snapshots
-- and rollout planning.  No reliable device task or OneNet command is created
-- by the V64 application.

ALTER TABLE dev_edge_software_release
    DROP CHECK ck_dev_edge_software_release_identity,
    ADD CONSTRAINT ck_dev_edge_software_release_identity CHECK (
        BINARY version_name = BINARY TRIM(version_name)
        AND CHAR_LENGTH(version_name) BETWEEN 5 AND 32
        AND version_name REGEXP
            '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z-]+([.][0-9A-Za-z-]+)*)?([+][0-9A-Za-z-]+([.][0-9A-Za-z-]+)*)?$'
        AND release_sequence BETWEEN 1 AND 9007199254740991
        AND package_format_version BETWEEN 1 AND 65535
        AND backend_command_contract_version BETWEEN 1 AND 65535
        AND device_event_contract_version BETWEEN 1 AND 65535
    );

CREATE TABLE dev_edge_software_release_sequence (
    singleton_id TINYINT NOT NULL,
    last_release_sequence BIGINT UNSIGNED NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (singleton_id),
    CONSTRAINT ck_dev_edge_release_sequence_singleton CHECK (
        singleton_id = 1
        AND last_release_sequence BETWEEN 0 AND 9007199254740991
        AND lock_version >= 0
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO dev_edge_software_release_sequence (
    singleton_id, last_release_sequence, lock_version, updated_at
) SELECT 1, COALESCE(MAX(release_sequence), 0), 0, UTC_TIMESTAMP(3)
  FROM dev_edge_software_release;

CREATE TABLE dev_edge_software_release_control (
    id BIGINT NOT NULL AUTO_INCREMENT,
    release_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    create_operation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_name VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_sequence BIGINT UNSIGNED NOT NULL,
    release_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    verification_operation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    package_object_key VARCHAR(512)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    signature_object_key VARCHAR(512)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    package_sha256 BINARY(32) NULL,
    package_size BIGINT UNSIGNED NULL,
    signature_sha256 BINARY(32) NULL,
    signature_bytes BINARY(64) NULL,
    signing_key_id VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    declaration_id BIGINT NULL,
    verification_error_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    verification_error_message VARCHAR(500) NULL,
    release_notes VARCHAR(1000) NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    verified_by_platform_admin_id BIGINT NULL,
    verified_at DATETIME(3) NULL,
    approved_by_platform_admin_id BIGINT NULL,
    approved_at DATETIME(3) NULL,
    suspended_by_platform_admin_id BIGINT NULL,
    suspended_at DATETIME(3) NULL,
    suspension_reason VARCHAR(500) NULL,
    retired_by_platform_admin_id BIGINT NULL,
    retired_at DATETIME(3) NULL,
    retirement_reason VARCHAR(500) NULL,
    artifact_uploaded_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_release_control_uid UNIQUE (release_uid),
    CONSTRAINT uq_dev_edge_release_control_create UNIQUE (create_operation_uid),
    CONSTRAINT uq_dev_edge_release_control_version UNIQUE (version_name),
    CONSTRAINT uq_dev_edge_release_control_sequence UNIQUE (release_sequence),
    CONSTRAINT uq_dev_edge_release_control_declaration UNIQUE (declaration_id),
    CONSTRAINT ck_dev_edge_release_control_uids CHECK (
        release_uid = LOWER(release_uid)
        AND release_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND create_operation_uid = LOWER(create_operation_uid)
        AND create_operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            verification_operation_uid IS NULL
            OR (
                verification_operation_uid = LOWER(verification_operation_uid)
                AND verification_operation_uid REGEXP
                    '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            )
        )
    ),
    CONSTRAINT ck_dev_edge_release_control_identity CHECK (
        BINARY version_name = BINARY TRIM(version_name)
        AND CHAR_LENGTH(version_name) BETWEEN 5 AND 32
        AND version_name REGEXP
            '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z-]+([.][0-9A-Za-z-]+)*)?([+][0-9A-Za-z-]+([.][0-9A-Za-z-]+)*)?$'
        AND release_sequence BETWEEN 1 AND 9007199254740991
        AND package_object_key = CONCAT(
            'edge-runtime/releases/', release_uid, '/package.tar.gz')
        AND signature_object_key = CONCAT(
            'edge-runtime/releases/', release_uid, '/package.sig')
        AND lock_version >= 0
    ),
    CONSTRAINT ck_dev_edge_release_control_artifact CHECK (
        (
            package_sha256 IS NULL
            AND package_size IS NULL
            AND signature_sha256 IS NULL
            AND signature_bytes IS NULL
            AND signing_key_id IS NULL
            AND artifact_uploaded_at IS NULL
        ) OR (
            package_sha256 IS NOT NULL
            AND package_size BETWEEN 1 AND 1610612736
            AND signature_sha256 IS NOT NULL
            AND signature_bytes IS NOT NULL
            AND signing_key_id REGEXP '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$'
            AND artifact_uploaded_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_edge_release_control_status CHECK (
        release_status IN (
            'DRAFT', 'VERIFYING', 'VERIFICATION_FAILED',
            'AWAITING_APPROVAL', 'READY', 'SUSPENDED', 'RETIRED'
        )
        AND (
            release_status = 'DRAFT'
            OR package_sha256 IS NOT NULL
        )
        AND (
            (release_status = 'VERIFYING'
                AND verification_operation_uid IS NOT NULL)
            OR (release_status <> 'VERIFYING'
                AND verification_operation_uid IS NULL)
        )
        AND (
            (release_status = 'VERIFICATION_FAILED'
                AND verification_error_code IS NOT NULL
                AND verification_error_message IS NOT NULL
                AND declaration_id IS NULL)
            OR (release_status <> 'VERIFICATION_FAILED'
                AND verification_error_code IS NULL
                AND verification_error_message IS NULL)
        )
        AND (
            release_status IN (
                'DRAFT', 'VERIFYING', 'VERIFICATION_FAILED'
            )
            OR (
                declaration_id IS NOT NULL
                AND verified_by_platform_admin_id IS NOT NULL
                AND verified_at IS NOT NULL
            )
        )
        AND (
            release_status IN (
                'DRAFT', 'VERIFYING', 'VERIFICATION_FAILED',
                'AWAITING_APPROVAL'
            )
            OR (
                approved_by_platform_admin_id IS NOT NULL
                AND approved_at IS NOT NULL
            )
        )
        AND (
            (release_status = 'SUSPENDED'
                AND suspended_by_platform_admin_id IS NOT NULL
                AND suspended_at IS NOT NULL
                AND CHAR_LENGTH(TRIM(suspension_reason)) BETWEEN 1 AND 500)
            OR release_status <> 'SUSPENDED'
        )
        AND (
            (release_status = 'RETIRED'
                AND retired_by_platform_admin_id IS NOT NULL
                AND retired_at IS NOT NULL
                AND CHAR_LENGTH(TRIM(retirement_reason)) BETWEEN 1 AND 500)
            OR release_status <> 'RETIRED'
        )
    ),
    CONSTRAINT ck_dev_edge_release_control_times CHECK (
        updated_at >= created_at
        AND (artifact_uploaded_at IS NULL OR artifact_uploaded_at >= created_at)
        AND (verified_at IS NULL OR verified_at >= created_at)
        AND (approved_at IS NULL OR approved_at >= verified_at)
        AND (suspended_at IS NULL OR suspended_at >= approved_at)
        AND (retired_at IS NULL OR retired_at >= approved_at)
    ),
    CONSTRAINT fk_dev_edge_release_control_declaration
        FOREIGN KEY (declaration_id) REFERENCES dev_edge_software_release (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_control_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_control_verifier
        FOREIGN KEY (verified_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_control_approver
        FOREIGN KEY (approved_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_control_suspender
        FOREIGN KEY (suspended_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_control_retirer
        FOREIGN KEY (retired_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_release_control_list (
        release_status, created_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_edge_software_release_action (
    id BIGINT NOT NULL AUTO_INCREMENT,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_control_id BIGINT NOT NULL,
    action_type VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    requested_by_platform_admin_id BIGINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    resulting_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_release_action_operation UNIQUE (operation_uid),
    CONSTRAINT ck_dev_edge_release_action_uid CHECK (
        operation_uid = LOWER(operation_uid)
        AND operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_release_action_type CHECK (
        action_type IN (
            'CREATE', 'UPLOAD', 'START_VERIFICATION',
            'VERIFICATION_PASSED', 'VERIFICATION_FAILED',
            'APPROVE', 'SUSPEND', 'RESUME', 'RETIRE'
        )
        AND resulting_status IN (
            'DRAFT', 'VERIFYING', 'VERIFICATION_FAILED',
            'AWAITING_APPROVAL', 'READY', 'SUSPENDED', 'RETIRED'
        )
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT fk_dev_edge_release_action_release
        FOREIGN KEY (release_control_id)
        REFERENCES dev_edge_software_release_control (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_release_action_admin
        FOREIGN KEY (requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_release_action_history (release_control_id, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_edge_software_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT,
    rollout_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    create_operation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    release_control_id BIGINT NOT NULL,
    rollout_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    validation_asset_id BIGINT NOT NULL,
    batch_size INT NOT NULL,
    maximum_wave_no INT NOT NULL,
    current_wave_no INT NOT NULL DEFAULT -1,
    observation_window_seconds INT NOT NULL DEFAULT 1800,
    download_timeout_seconds INT NOT NULL DEFAULT 1800,
    drain_timeout_seconds INT NOT NULL DEFAULT 1800,
    maximum_retry_count INT NOT NULL DEFAULT 3,
    remote_dispatch_enabled_snapshot TINYINT NOT NULL DEFAULT 0,
    change_reason VARCHAR(500) NOT NULL,
    created_by_platform_admin_id BIGINT NOT NULL,
    stopped_by_platform_admin_id BIGINT NULL,
    stopped_at DATETIME(3) NULL,
    stop_reason VARCHAR(500) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_rollout_uid UNIQUE (rollout_uid),
    CONSTRAINT uq_dev_edge_rollout_create UNIQUE (create_operation_uid),
    CONSTRAINT ck_dev_edge_rollout_uids CHECK (
        rollout_uid = LOWER(rollout_uid)
        AND rollout_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND create_operation_uid = LOWER(create_operation_uid)
        AND create_operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_rollout_policy CHECK (
        rollout_status IN ('DRAFT', 'STOPPED')
        AND batch_size BETWEEN 1 AND 100
        AND maximum_wave_no BETWEEN 0 AND 1000
        AND current_wave_no = -1
        AND observation_window_seconds BETWEEN 60 AND 86400
        AND download_timeout_seconds BETWEEN 60 AND 86400
        AND drain_timeout_seconds BETWEEN 60 AND 86400
        AND maximum_retry_count BETWEEN 0 AND 10
        AND remote_dispatch_enabled_snapshot = 0
        AND CHAR_LENGTH(TRIM(change_reason)) BETWEEN 1 AND 500
        AND lock_version >= 0
    ),
    CONSTRAINT ck_dev_edge_rollout_stop CHECK (
        (rollout_status = 'DRAFT'
            AND stopped_by_platform_admin_id IS NULL
            AND stopped_at IS NULL
            AND stop_reason IS NULL)
        OR (rollout_status = 'STOPPED'
            AND stopped_by_platform_admin_id IS NOT NULL
            AND stopped_at IS NOT NULL
            AND CHAR_LENGTH(TRIM(stop_reason)) BETWEEN 1 AND 500)
    ),
    CONSTRAINT ck_dev_edge_rollout_times CHECK (
        updated_at >= created_at
        AND (stopped_at IS NULL OR stopped_at >= created_at)
    ),
    CONSTRAINT fk_dev_edge_rollout_release
        FOREIGN KEY (release_control_id)
        REFERENCES dev_edge_software_release_control (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_rollout_validation_asset
        FOREIGN KEY (validation_asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_rollout_creator
        FOREIGN KEY (created_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_rollout_stopper
        FOREIGN KEY (stopped_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_rollout_list (rollout_status, created_at DESC, id DESC),
    INDEX ix_dev_edge_rollout_release (release_control_id, rollout_status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_edge_software_deployment (
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
    deployment_status VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    eligibility_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    eligibility_snapshot JSON NOT NULL,
    eligibility_sha256 BINARY(32) NOT NULL,
    source_software_fact_id BIGINT NOT NULL,
    source_management_state_sequence BIGINT UNSIGNED NOT NULL,
    source_business_release_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_business_release_sequence BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_deployment_uid UNIQUE (deployment_uid),
    CONSTRAINT uq_dev_edge_deployment_asset UNIQUE (rollout_id, asset_id),
    CONSTRAINT ck_dev_edge_deployment_uid CHECK (
        deployment_uid = LOWER(deployment_uid)
        AND deployment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND source_business_release_uid = LOWER(source_business_release_uid)
        AND source_business_release_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_deployment_scope CHECK (
        (tenant_id IS NULL AND organization_id IS NULL)
        OR tenant_id IS NOT NULL
    ),
    CONSTRAINT ck_dev_edge_deployment_wave CHECK (
        (deployment_kind = 'VALIDATION' AND wave_no = 0)
        OR (deployment_kind = 'WAVE' AND wave_no BETWEEN 1 AND 1000)
    ),
    CONSTRAINT ck_dev_edge_deployment_state CHECK (
        deployment_status = 'PLANNED'
        AND eligibility_status = 'ELIGIBLE'
        AND source_management_state_sequence BETWEEN 1 AND 9007199254740991
        AND source_business_release_sequence BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_dev_edge_deployment_times CHECK (updated_at >= created_at),
    CONSTRAINT fk_dev_edge_deployment_rollout
        FOREIGN KEY (rollout_id) REFERENCES dev_edge_software_rollout (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_deployment_release
        FOREIGN KEY (release_id) REFERENCES dev_edge_software_release (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_deployment_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_deployment_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_deployment_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_deployment_fact
        FOREIGN KEY (source_software_fact_id)
        REFERENCES dev_device_software_fact (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_deployment_wave (
        rollout_id, wave_no, deployment_status, id
    ),
    INDEX ix_dev_edge_deployment_asset (
        asset_id, deployment_status, created_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_edge_software_rollout_action (
    id BIGINT NOT NULL AUTO_INCREMENT,
    operation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rollout_id BIGINT NOT NULL,
    action_type VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    requested_by_platform_admin_id BIGINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    resulting_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_rollout_action_operation UNIQUE (operation_uid),
    CONSTRAINT ck_dev_edge_rollout_action_uid CHECK (
        operation_uid = LOWER(operation_uid)
        AND operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_rollout_action_type CHECK (
        action_type IN ('CREATE', 'STOP')
        AND resulting_status IN ('DRAFT', 'STOPPED')
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT fk_dev_edge_rollout_action_rollout
        FOREIGN KEY (rollout_id) REFERENCES dev_edge_software_rollout (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_rollout_action_admin
        FOREIGN KEY (requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_rollout_action_history (rollout_id, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

DELIMITER //

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_release_control_v64_identity
BEFORE UPDATE ON dev_edge_software_release_control
FOR EACH ROW
BEGIN
    IF NEW.release_uid <> OLD.release_uid
        OR NEW.create_operation_uid <> OLD.create_operation_uid
        OR BINARY NEW.version_name <> BINARY OLD.version_name
        OR NEW.release_sequence <> OLD.release_sequence
        OR BINARY NEW.package_object_key <> BINARY OLD.package_object_key
        OR BINARY NEW.signature_object_key <> BINARY OLD.signature_object_key
        OR (OLD.package_sha256 IS NOT NULL AND (
            NOT (NEW.package_sha256 <=> OLD.package_sha256)
            OR NOT (NEW.package_size <=> OLD.package_size)
            OR NOT (NEW.signature_sha256 <=> OLD.signature_sha256)
            OR NOT (NEW.signature_bytes <=> OLD.signature_bytes)
            OR NOT (NEW.signing_key_id <=> OLD.signing_key_id)
        ))
        OR (OLD.declaration_id IS NOT NULL
            AND NOT (NEW.declaration_id <=> OLD.declaration_id))
        OR (OLD.verified_by_platform_admin_id IS NOT NULL AND (
            NOT (NEW.verified_by_platform_admin_id
                <=> OLD.verified_by_platform_admin_id)
            OR NOT (NEW.verified_at <=> OLD.verified_at)
        ))
        OR NEW.created_by_platform_admin_id <> OLD.created_by_platform_admin_id
        OR NEW.created_at <> OLD.created_at THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'business release identity and artifact are immutable';
    END IF;
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_release_action_v64_immutable
BEFORE UPDATE ON dev_edge_software_release_action
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business release actions are immutable';
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_release_action_v64_no_delete
BEFORE DELETE ON dev_edge_software_release_action
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business release actions cannot be deleted';
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_rollout_action_v64_immutable
BEFORE UPDATE ON dev_edge_software_rollout_action
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business rollout actions are immutable';
END//

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_edge_rollout_action_v64_no_delete
BEFORE DELETE ON dev_edge_software_rollout_action
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'business rollout actions cannot be deleted';
END//

DELIMITER ;
