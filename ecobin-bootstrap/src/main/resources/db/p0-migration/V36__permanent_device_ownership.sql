-- D-047 / I-057: one physical asset has one permanent tenant and one
-- permanent organization. The target database is still empty, so this is an
-- intentional breaking cutover with no legacy deployment compatibility.

DELIMITER $$

CREATE PROCEDURE p0_assert_v36_device_cutover_is_empty()
BEGIN
    IF EXISTS (SELECT 1 FROM dev_device_asset LIMIT 1) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'V36 permanent ownership cutover requires an empty device inventory';
    END IF;
END$$

CALL p0_assert_v36_device_cutover_is_empty()$$
DROP PROCEDURE p0_assert_v36_device_cutover_is_empty$$

DELIMITER ;

-- The immutable registration trigger is recreated after its attribution
-- column changes from deployment to physical asset.
DROP TRIGGER trg_iam_org_user_registration_immutable;

-- MySQL cannot rename a column while another foreign key refers to it. Capture
-- every affected retained relationship, remove it, and recreate the same
-- relationship with asset column names after all renames. Foreign keys that
-- point at a deleted legacy table are deliberately not rebuilt.
SET SESSION group_concat_max_len = 65535;

CREATE TEMPORARY TABLE p0_v36_foreign_key_rebuild (
    id BIGINT NOT NULL AUTO_INCREMENT,
    drop_sql TEXT NOT NULL,
    add_sql TEXT NULL,
    PRIMARY KEY (id)
);

INSERT INTO p0_v36_foreign_key_rebuild (drop_sql, add_sql)
SELECT
    CONCAT(
        'ALTER TABLE `', rc.table_name,
        '` DROP FOREIGN KEY `', rc.constraint_name, '`'
    ),
    CASE
        WHEN rc.referenced_table_name IN (
            'dev_device_deployment',
            'dev_asset_active_deployment',
            'dev_asset_tenant_allocation',
            'dev_asset_active_tenant_allocation',
            'dev_deployment_acceptance',
            'dev_asset_reclaim_record',
            'dev_onenet_credential_rotation_confirmation',
            'dev_asset_maintenance_clearance'
        ) THEN NULL
        ELSE CONCAT(
            'ALTER TABLE `',
            REPLACE(
                rc.table_name,
                'dev_deployment_runtime_state',
                'dev_device_runtime_state'
            ),
            '` ADD CONSTRAINT `',
            REPLACE(rc.constraint_name, 'deployment', 'asset'),
            '` FOREIGN KEY (',
            (
                SELECT GROUP_CONCAT(
                    CONCAT(
                        '`', REPLACE(k.column_name, 'deployment', 'asset'), '`'
                    )
                    ORDER BY k.ordinal_position SEPARATOR ','
                )
                FROM information_schema.key_column_usage k
                WHERE k.constraint_schema = rc.constraint_schema
                  AND k.table_name = rc.table_name
                  AND k.constraint_name = rc.constraint_name
            ),
            ') REFERENCES `',
            REPLACE(
                rc.referenced_table_name,
                'dev_deployment_runtime_state',
                'dev_device_runtime_state'
            ),
            '` (',
            (
                SELECT GROUP_CONCAT(
                    CONCAT(
                        '`',
                        REPLACE(
                            k.referenced_column_name,
                            'deployment',
                            'asset'
                        ),
                        '`'
                    )
                    ORDER BY k.ordinal_position SEPARATOR ','
                )
                FROM information_schema.key_column_usage k
                WHERE k.constraint_schema = rc.constraint_schema
                  AND k.table_name = rc.table_name
                  AND k.constraint_name = rc.constraint_name
            ),
            ') ON DELETE ', rc.delete_rule,
            ' ON UPDATE ', rc.update_rule
        )
    END
FROM information_schema.referential_constraints rc
WHERE rc.constraint_schema = DATABASE()
  AND rc.table_name NOT IN (
      'dev_device_deployment',
      'dev_asset_active_deployment',
      'dev_asset_tenant_allocation',
      'dev_asset_active_tenant_allocation',
      'dev_deployment_acceptance',
      'dev_asset_reclaim_record',
      'dev_onenet_credential_rotation_confirmation',
      'dev_asset_maintenance_clearance'
  )
  AND EXISTS (
      SELECT 1
      FROM information_schema.key_column_usage affected
      WHERE affected.constraint_schema = rc.constraint_schema
        AND affected.table_name = rc.table_name
        AND affected.constraint_name = rc.constraint_name
        AND (
            affected.column_name LIKE '%deployment%'
            OR affected.referenced_column_name LIKE '%deployment%'
            OR affected.table_name = 'dev_deployment_runtime_state'
            OR affected.referenced_table_name IN (
                'dev_device_deployment',
                'dev_asset_active_deployment',
                'dev_asset_tenant_allocation',
                'dev_asset_active_tenant_allocation',
                'dev_deployment_acceptance',
                'dev_asset_reclaim_record',
                'dev_onenet_credential_rotation_confirmation',
                'dev_asset_maintenance_clearance'
            )
        )
  )
ORDER BY rc.table_name, rc.constraint_name;

DELIMITER $$

CREATE PROCEDURE p0_v36_drop_affected_foreign_keys()
BEGIN
    DECLARE done INT DEFAULT 0;
    DECLARE statement_text TEXT;
    DECLARE statements CURSOR FOR
        SELECT drop_sql
        FROM p0_v36_foreign_key_rebuild
        ORDER BY id;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

    OPEN statements;
    drop_loop: LOOP
        FETCH statements INTO statement_text;
        IF done = 1 THEN
            LEAVE drop_loop;
        END IF;
        SET @p0_v36_sql = statement_text;
        PREPARE p0_v36_statement FROM @p0_v36_sql;
        EXECUTE p0_v36_statement;
        DEALLOCATE PREPARE p0_v36_statement;
    END LOOP;
    CLOSE statements;
END$$

CREATE PROCEDURE p0_v36_rebuild_retained_foreign_keys()
BEGIN
    DECLARE done INT DEFAULT 0;
    DECLARE statement_text TEXT;
    DECLARE statements CURSOR FOR
        SELECT add_sql
        FROM p0_v36_foreign_key_rebuild
        WHERE add_sql IS NOT NULL
        ORDER BY id;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

    OPEN statements;
    add_loop: LOOP
        FETCH statements INTO statement_text;
        IF done = 1 THEN
            LEAVE add_loop;
        END IF;
        SET @p0_v36_sql = statement_text;
        PREPARE p0_v36_statement FROM @p0_v36_sql;
        EXECUTE p0_v36_statement;
        DEALLOCATE PREPARE p0_v36_statement;
    END LOOP;
    CLOSE statements;
END$$

CALL p0_v36_drop_affected_foreign_keys()$$

DELIMITER ;

-- The asset itself is now the only ownership and lifecycle root.
ALTER TABLE dev_device_asset
    DROP CHECK ck_dev_asset_lifecycle,
    DROP CHECK ck_dev_asset_retired_shape,
    DROP CHECK ck_dev_asset_lock_version,
    DROP CHECK ck_dev_asset_times,
    ADD COLUMN asset_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL AFTER id,
    ADD COLUMN device_public_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL AFTER asset_uid,
    ADD COLUMN tenant_id BIGINT NULL AFTER expected_port_count,
    ADD COLUMN tenant_assigned_at DATETIME(3) NULL AFTER tenant_id,
    ADD COLUMN organization_id BIGINT NULL AFTER tenant_assigned_at,
    ADD COLUMN organization_assigned_at DATETIME(3) NULL
        AFTER organization_id,
    ADD COLUMN acceptance_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'PENDING'
        AFTER organization_assigned_at,
    ADD COLUMN accepted_at DATETIME(3) NULL AFTER acceptance_status,
    ADD COLUMN acceptance_evidence_sha256 BINARY(32) NULL AFTER accepted_at,
    ADD COLUMN last_acceptance_evaluated_at DATETIME(3) NULL
        AFTER acceptance_evidence_sha256,
    ADD COLUMN acceptance_failure_json JSON NULL
        AFTER last_acceptance_evaluated_at,
    ADD COLUMN miniapp_qr_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'NOT_ASSIGNED'
        AFTER acceptance_failure_json,
    ADD COLUMN miniapp_qr_object_key VARCHAR(512)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER miniapp_qr_status,
    ADD COLUMN miniapp_qr_generated_at DATETIME(3) NULL
        AFTER miniapp_qr_object_key,
    ADD COLUMN disabled_at DATETIME(3) NULL AFTER lifecycle_status,
    ADD COLUMN disable_reason VARCHAR(500) NULL AFTER disabled_at,
    RENAME COLUMN lock_version TO control_version,
    MODIFY COLUMN lifecycle_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'NORMAL',
    ADD CONSTRAINT uq_dev_asset_uid UNIQUE (asset_uid),
    ADD CONSTRAINT uq_dev_asset_public_code UNIQUE (device_public_code),
    ADD CONSTRAINT uq_dev_asset_scope_id
        UNIQUE (tenant_id, organization_id, id),
    ADD CONSTRAINT ck_dev_asset_uid_v4 CHECK (
        asset_uid = LOWER(asset_uid)
        AND asset_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT ck_dev_asset_public_code CHECK (
        device_public_code REGEXP '^Dv_[A-Za-z0-9_-]{24,61}$'
    ),
    ADD CONSTRAINT ck_dev_asset_permanent_assignment CHECK (
        (
            tenant_id IS NULL
            AND tenant_assigned_at IS NULL
            AND organization_id IS NULL
            AND organization_assigned_at IS NULL
        )
        OR
        (
            tenant_id IS NOT NULL
            AND tenant_assigned_at IS NOT NULL
            AND organization_id IS NULL
            AND organization_assigned_at IS NULL
        )
        OR
        (
            tenant_id IS NOT NULL
            AND tenant_assigned_at IS NOT NULL
            AND organization_id IS NOT NULL
            AND organization_assigned_at IS NOT NULL
            AND organization_assigned_at >= tenant_assigned_at
        )
    ),
    ADD CONSTRAINT ck_dev_asset_acceptance CHECK (
        acceptance_status IN ('PENDING', 'FAILED', 'PASSED')
        AND (
            (
                acceptance_status = 'PASSED'
                AND accepted_at IS NOT NULL
                AND acceptance_evidence_sha256 IS NOT NULL
                AND acceptance_failure_json IS NULL
            )
            OR
            (
                acceptance_status IN ('PENDING', 'FAILED')
                AND accepted_at IS NULL
            )
        )
    ),
    ADD CONSTRAINT ck_dev_asset_qr CHECK (
        miniapp_qr_status IN (
            'NOT_ASSIGNED', 'PENDING', 'READY', 'FAILED'
        )
        AND (
            (
                miniapp_qr_status = 'READY'
                AND miniapp_qr_object_key IS NOT NULL
                AND miniapp_qr_generated_at IS NOT NULL
            )
            OR
            (
                miniapp_qr_status <> 'READY'
                AND miniapp_qr_object_key IS NULL
                AND miniapp_qr_generated_at IS NULL
            )
        )
    ),
    ADD CONSTRAINT ck_dev_asset_lifecycle_v36 CHECK (
        lifecycle_status IN ('NORMAL', 'DISABLED', 'RETIRED')
    ),
    ADD CONSTRAINT ck_dev_asset_control_shape CHECK (
        (
            lifecycle_status = 'NORMAL'
            AND disabled_at IS NULL
            AND disable_reason IS NULL
            AND retired_at IS NULL
            AND retirement_reason IS NULL
        )
        OR
        (
            lifecycle_status = 'DISABLED'
            AND disabled_at IS NOT NULL
            AND disable_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(disable_reason)) > 0
            AND retired_at IS NULL
            AND retirement_reason IS NULL
        )
        OR
        (
            lifecycle_status = 'RETIRED'
            AND retired_at IS NOT NULL
            AND retirement_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(retirement_reason)) > 0
            AND (
                (disabled_at IS NULL AND disable_reason IS NULL)
                OR
                (
                    disabled_at IS NOT NULL
                    AND disable_reason IS NOT NULL
                    AND CHAR_LENGTH(TRIM(disable_reason)) > 0
                )
            )
        )
    ),
    ADD CONSTRAINT ck_dev_asset_control_version CHECK (control_version >= 0),
    ADD CONSTRAINT ck_dev_asset_times_v36 CHECK (
        updated_at >= created_at
        AND (tenant_assigned_at IS NULL OR tenant_assigned_at >= created_at)
        AND (
            last_acceptance_evaluated_at IS NULL
            OR last_acceptance_evaluated_at >= created_at
        )
        AND (accepted_at IS NULL OR accepted_at >= created_at)
        AND (disabled_at IS NULL OR disabled_at >= created_at)
        AND (retired_at IS NULL OR retired_at >= created_at)
    ),
    ADD CONSTRAINT fk_dev_asset_tenant
        FOREIGN KEY (tenant_id) REFERENCES iam_tenant (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_asset_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_asset_tenant (
        tenant_id, lifecycle_status, id
    ),
    ADD INDEX ix_dev_asset_organization (
        tenant_id, organization_id, lifecycle_status, id
    ),
    ADD INDEX ix_dev_asset_acceptance (
        acceptance_status, last_acceptance_evaluated_at, id
    );

-- Rename retained device and recycling facts from deployment ownership to
-- physical asset ownership. Configuration versions now increase for the whole
-- life of the machine.
ALTER TABLE dev_port
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_config_version
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_config_application
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_port_config_snapshot
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_edge_event
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_delivery_session
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_device_command
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_device_command_event
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_physical_result
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_device_fault_event
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_fault_recovery_observation
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_fullness_state_fact
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_photo_upload_grant_request
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_port_runtime_state
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE dev_deployment_runtime_state
    RENAME COLUMN deployment_id TO asset_id;
RENAME TABLE dev_deployment_runtime_state TO dev_device_runtime_state;

ALTER TABLE rec_clean_operation
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_clean_record
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_delivery_order
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_fullness_detection
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_fullness_state_change
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_photo_terminal_fact
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_port_baseline_measurement
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_port_capacity_state
    RENAME COLUMN deployment_id TO asset_id;
ALTER TABLE rec_port_clean_restart_interlock
    RENAME COLUMN deployment_id TO asset_id;

ALTER TABLE iam_organization_user
    RENAME COLUMN registered_via_deployment_id TO registered_via_asset_id;
ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources;
ALTER TABLE ops_reliable_task
    RENAME COLUMN source_device_deployment_id TO source_device_asset_id;
ALTER TABLE ops_reliable_task
    ADD CONSTRAINT ck_ops_task_sources_v36 CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_asset_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR
        (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_asset_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR
                (
                    source_device_asset_id IS NOT NULL
                    AND (
                        (
                            scope_kind = 'PLATFORM'
                            AND tenant_id IS NULL
                            AND organization_id IS NULL
                            AND source_device_command_id IS NULL
                            AND task_type =
                                'REQUEST_DEVICE_ACCEPTANCE'
                        )
                        OR
                        (
                            scope_kind = 'ORGANIZATION'
                            AND (
                                source_device_command_id IS NOT NULL
                                OR
                                (
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

-- Occupancy already had the physical asset primary key. Its duplicate
-- deployment column is removed and its work references are rebuilt with the
-- asset key.
ALTER TABLE dev_device_occupancy
    DROP INDEX ix_dev_occupancy_scope,
    DROP INDEX ix_dev_occupancy_active_fk,
    DROP INDEX ix_dev_occupancy_delivery_fk,
    DROP INDEX ix_dev_occupancy_clean_operation_fk,
    DROP COLUMN deployment_id,
    ADD CONSTRAINT fk_dev_occupancy_asset_scope
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_occupancy_scope_v36 (
        tenant_id, organization_id, occupancy_kind, asset_id
    ),
    ADD INDEX ix_dev_occupancy_delivery_v36 (
        tenant_id, organization_id, asset_id, delivery_session_id
    ),
    ADD INDEX ix_dev_occupancy_clean_v36 (
        tenant_id, organization_id, asset_id, clean_operation_id
    );

DELIMITER $$

CALL p0_v36_rebuild_retained_foreign_keys()$$
DROP PROCEDURE p0_v36_drop_affected_foreign_keys$$
DROP PROCEDURE p0_v36_rebuild_retained_foreign_keys$$

DELIMITER ;

DROP TEMPORARY TABLE p0_v36_foreign_key_rebuild;

-- Replace direct deployment foreign keys with permanent asset scope checks.
ALTER TABLE dev_port
    ADD CONSTRAINT fk_dev_port_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_config_version
    ADD CONSTRAINT fk_dev_config_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_device_runtime_state
    ADD CONSTRAINT fk_dev_runtime_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_device_command
    ADD CONSTRAINT fk_dev_command_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_device_fault_event
    ADD CONSTRAINT fk_dev_fault_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_edge_event
    ADD CONSTRAINT fk_dev_edge_event_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_fault_recovery_observation
    ADD CONSTRAINT fk_dev_fault_recovery_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE dev_photo_upload_grant_request
    ADD CONSTRAINT fk_dev_photo_grant_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE rec_photo_terminal_fact
    ADD CONSTRAINT fk_rec_photo_terminal_asset_v36
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE iam_organization_user
    ADD CONSTRAINT fk_iam_org_user_registration_asset_v36
        FOREIGN KEY (tenant_id, organization_id, registered_via_asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;
ALTER TABLE ops_reliable_task
    ADD CONSTRAINT fk_ops_task_asset_identity_v36
        FOREIGN KEY (source_device_asset_id)
        REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_ops_task_device_asset_v36
        FOREIGN KEY (tenant_id, organization_id, source_device_asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;

-- Automatic platform acceptance stores append-only evidence. A PASSED row can
-- only represent real hardware; simulator evidence remains useful for
-- diagnostics but is constrained to FAILED.
CREATE TABLE dev_device_acceptance_evidence (
    id BIGINT NOT NULL AUTO_INCREMENT,
    evidence_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    asset_id BIGINT NOT NULL,
    challenge_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_schema_version INT NOT NULL,
    edge_store_instance_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    edge_software_version VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    edge_protocol_version VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mcu_firmware_version VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    onenet_online TINYINT NOT NULL,
    persistent_store_healthy TINYINT NOT NULL,
    trusted_time_healthy TINYINT NOT NULL,
    configuration_persistence_healthy TINYINT NOT NULL,
    mcu_communication_healthy TINYINT NOT NULL,
    sensors_healthy TINYINT NOT NULL,
    cameras_capture_healthy TINYINT NOT NULL,
    camera_upload_healthy TINYINT NOT NULL,
    mcu_simulated TINYINT NOT NULL,
    cameras_simulated TINYINT NOT NULL,
    evaluation_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    failure_reasons_json JSON NOT NULL,
    evidence_json JSON NOT NULL,
    evidence_sha256 BINARY(32) NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_acceptance_evidence_uid UNIQUE (evidence_uid),
    CONSTRAINT uq_dev_acceptance_challenge
        UNIQUE (asset_id, challenge_uid),
    CONSTRAINT uq_dev_acceptance_command
        UNIQUE (asset_id, command_uid),
    CONSTRAINT uq_dev_acceptance_evidence_digest
        UNIQUE (asset_id, evidence_sha256),
    CONSTRAINT ck_dev_acceptance_evidence_uid CHECK (
        evidence_uid = LOWER(evidence_uid)
        AND challenge_uid = LOWER(challenge_uid)
        AND command_uid = LOWER(command_uid)
        AND edge_store_instance_uid = LOWER(edge_store_instance_uid)
        AND evidence_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND challenge_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND edge_store_instance_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_acceptance_evidence_flags CHECK (
        onenet_online IN (0, 1)
        AND persistent_store_healthy IN (0, 1)
        AND trusted_time_healthy IN (0, 1)
        AND configuration_persistence_healthy IN (0, 1)
        AND mcu_communication_healthy IN (0, 1)
        AND sensors_healthy IN (0, 1)
        AND cameras_capture_healthy IN (0, 1)
        AND camera_upload_healthy IN (0, 1)
        AND mcu_simulated IN (0, 1)
        AND cameras_simulated IN (0, 1)
    ),
    CONSTRAINT ck_dev_acceptance_evidence_result CHECK (
        evaluation_status IN ('PASSED', 'FAILED')
        AND JSON_TYPE(failure_reasons_json) = 'ARRAY'
        AND JSON_TYPE(evidence_json) = 'OBJECT'
        AND (
            evaluation_status = 'FAILED'
            OR (
                onenet_online = 1
                AND persistent_store_healthy = 1
                AND trusted_time_healthy = 1
                AND configuration_persistence_healthy = 1
                AND mcu_communication_healthy = 1
                AND sensors_healthy = 1
                AND cameras_capture_healthy = 1
                AND camera_upload_healthy = 1
                AND mcu_simulated = 0
                AND cameras_simulated = 0
                AND JSON_LENGTH(failure_reasons_json) = 0
            )
        )
    ),
    CONSTRAINT ck_dev_acceptance_evidence_times CHECK (
        evidence_schema_version > 0
        AND received_at >= observed_at
        AND created_at = received_at
    ),
    CONSTRAINT fk_dev_acceptance_evidence_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_acceptance_evidence_asset (
        asset_id, received_at DESC, id DESC
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Factory-installed bags exist before tenant/organization assignment. Once an
-- organization is assigned, the application creates the scoped recycling bag
-- facts and asks the real device to remeasure tare automatically.
CREATE TABLE dev_factory_installed_bag (
    id BIGINT NOT NULL AUTO_INCREMENT,
    asset_id BIGINT NOT NULL,
    port_no SMALLINT NOT NULL,
    bag_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tare_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'PENDING',
    last_failure_code VARCHAR(100)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    installed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_factory_bag_asset_port UNIQUE (asset_id, port_no),
    CONSTRAINT uq_dev_factory_bag_code UNIQUE (bag_code),
    CONSTRAINT ck_dev_factory_bag_port CHECK (port_no BETWEEN 1 AND 6),
    CONSTRAINT ck_dev_factory_bag_code CHECK (
        bag_code REGEXP '^[A-Za-z0-9_-]{8,64}$'
    ),
    CONSTRAINT ck_dev_factory_bag_tare CHECK (
        tare_status IN ('PENDING', 'MEASURING', 'READY', 'FAILED')
        AND (
            (tare_status = 'FAILED' AND last_failure_code IS NOT NULL)
            OR (tare_status <> 'FAILED' AND last_failure_code IS NULL)
        )
    ),
    CONSTRAINT ck_dev_factory_bag_times CHECK (
        installed_at >= created_at AND updated_at >= created_at
    ),
    CONSTRAINT fk_dev_factory_bag_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_factory_bag_tare (asset_id, tare_status, port_no)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Initial tare is a system-owned continuation of permanent organization
-- assignment. It has no human actor and its resulting baseline must not be
-- mislabeled as a manual remeasurement.
ALTER TABLE rec_port_baseline_measurement
    DROP CHECK ck_rec_baseline_measurement_actor,
    ADD CONSTRAINT ck_rec_baseline_measurement_actor CHECK (
        (
            initiator_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            initiator_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
        OR
        (
            initiator_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
        )
    );

ALTER TABLE rec_port_weight_baseline
    DROP CHECK ck_rec_weight_baseline_source,
    ADD CONSTRAINT ck_rec_weight_baseline_source CHECK (
        (
            source_type = 'INITIAL_BINDING'
            AND source_bag_event_id IS NOT NULL
            AND source_physical_result_id IS NULL
            AND source_clean_record_id IS NULL
            AND source_measurement_id IS NULL
        )
        OR
        (
            source_type = 'CLEAN_COMPLETE'
            AND source_bag_event_id IS NOT NULL
            AND source_physical_result_id IS NOT NULL
            AND source_clean_record_id IS NOT NULL
            AND source_measurement_id IS NULL
        )
        OR
        (
            source_type IN (
                'MANUAL_REMEASUREMENT',
                'AUTOMATIC_INITIAL'
            )
            AND source_bag_event_id IS NULL
            AND source_physical_result_id IS NOT NULL
            AND source_clean_record_id IS NULL
            AND source_measurement_id IS NOT NULL
        )
    );

-- Recreate immutable organization-user registration attribution for assets.
DELIMITER $$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_iam_org_user_registration_immutable
BEFORE UPDATE ON iam_organization_user
FOR EACH ROW
BEGIN
    IF NOT (NEW.tenant_id <=> OLD.tenant_id)
        OR NOT (NEW.organization_id <=> OLD.organization_id)
        OR NOT (
            NEW.organization_miniapp_id
            <=> OLD.organization_miniapp_id
        )
        OR NOT (NEW.openid <=> OLD.openid)
        OR NOT (NEW.registered_at <=> OLD.registered_at)
        OR NOT (
            NEW.registered_via_asset_id
            <=> OLD.registered_via_asset_id
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'organization user registration identity and attribution are immutable';
    END IF;
END$$

-- Database enforcement for NULL -> one value only. It prevents accidental
-- reassignment even if a future application bug bypasses the use case.
CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_dev_asset_permanent_assignment
BEFORE UPDATE ON dev_device_asset
FOR EACH ROW
BEGIN
    IF NOT (NEW.asset_uid <=> OLD.asset_uid)
        OR NOT (NEW.device_public_code <=> OLD.device_public_code)
        OR NOT (NEW.hardware_sn <=> OLD.hardware_sn) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'device public and hardware identities are immutable';
    END IF;

    IF OLD.tenant_id IS NOT NULL
        AND (
            NOT (NEW.tenant_id <=> OLD.tenant_id)
            OR NOT (NEW.tenant_assigned_at <=> OLD.tenant_assigned_at)
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'device tenant assignment is permanent';
    END IF;

    IF OLD.organization_id IS NOT NULL
        AND (
            NOT (NEW.organization_id <=> OLD.organization_id)
            OR NOT (
                NEW.organization_assigned_at
                <=> OLD.organization_assigned_at
            )
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'device organization assignment is permanent';
    END IF;

    IF OLD.lifecycle_status = 'RETIRED'
        AND NEW.lifecycle_status <> 'RETIRED' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'retired device cannot be restored';
    END IF;
END$$

DELIMITER ;

-- The deployment/allocation model is removed, including grants and reference
-- data. Keeping disabled permission rows would still be a hidden compatibility
-- model and would make authorization audits ambiguous.
DELETE grant_row
FROM iam_staff_permission_grant grant_row
JOIN iam_permission_definition permission
  ON permission.id = grant_row.permission_definition_id
WHERE permission.permission_code IN (
    'device.allocation.manage',
    'device.business.manage'
);

DELETE FROM iam_permission_definition
WHERE permission_code IN (
    'device.allocation.manage',
    'device.business.manage'
);

INSERT INTO iam_permission_definition (
    permission_code, scope_kind, permission_name,
    description, enabled, created_at
) VALUES (
    'device.assignment.manage',
    'TENANT',
    '永久分配设备机构',
    '将本租户尚未分配机构的设备永久分配一次；不能回收、调拨或重新分配。',
    1,
    '2026-08-07 00:00:00.000'
);

-- No retained table references these histories after the asset foreign keys
-- above are installed. They are intentionally deleted rather than kept as a
-- hidden compatibility model.
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE dev_asset_reclaim_record;
DROP TABLE dev_deployment_acceptance;
DROP TABLE dev_asset_active_deployment;
DROP TABLE dev_device_deployment;
DROP TABLE dev_asset_active_tenant_allocation;
DROP TABLE dev_asset_tenant_allocation;
DROP TABLE dev_onenet_credential_rotation_confirmation;
DROP TABLE dev_asset_maintenance_clearance;
SET FOREIGN_KEY_CHECKS = 1;
