-- Device operations and authenticated physical evidence.
-- There is intentionally no cloud delivery-cycle table: one delivery session
-- is the sole work/result/order idempotency root.

CREATE TABLE dev_delivery_session (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    device_config_version_id BIGINT NOT NULL,
    device_config_version_no BIGINT NOT NULL,
    device_config_content_sha256 BINARY(32) NOT NULL,
    device_config_mcu_payload_sha256 BINARY(32) NOT NULL,
    port_config_snapshot_id BIGINT NOT NULL,
    delivery_config_version_id BIGINT NOT NULL,
    delivery_config_content_sha256 BINARY(32) NOT NULL,
    bag_id BIGINT NOT NULL,
    bag_code_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    unit_price_yuan_per_kg DECIMAL(15, 4) NOT NULL,
    open_balance_floor_cent BIGINT NOT NULL,
    max_review_abs_weight_g BIGINT NOT NULL,
    negative_weight_anomaly_threshold_g BIGINT NOT NULL DEFAULT 500,
    local_end_selection_timeout_ms BIGINT NOT NULL,
    authorization_expires_at DATETIME(3) NOT NULL,
    result_recovery_deadline_at DATETIME(3) NOT NULL,
    first_edge_accepted_at DATETIME(3) NULL,
    first_physical_progress_at DATETIME(3) NULL,
    device_completed_at DATETIME(3) NULL,
    ended_at DATETIME(3) NULL,
    end_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_delivery_session_uid UNIQUE (session_uid),
    CONSTRAINT uq_dev_delivery_session_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_delivery_session_deployment_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT uq_dev_delivery_session_port_id
        UNIQUE (tenant_id, organization_id, deployment_id, port_id, id),
    CONSTRAINT uq_dev_delivery_session_frozen_config UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        id,
        device_config_version_no,
        device_config_content_sha256,
        device_config_mcu_payload_sha256
    ),
    CONSTRAINT ck_dev_delivery_session_uid_v4 CHECK (
        session_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_delivery_session_status CHECK (
        status IN (
            'PREPARED',
            'AUTHORIZATION_QUEUED',
            'IN_PROGRESS',
            'RESULT_PENDING_RECOVERY',
            'BUSINESS_CONFIRMED',
            'PRE_OPEN_ENDED'
        )
    ),
    CONSTRAINT ck_dev_delivery_session_snapshot_values CHECK (
        unit_price_yuan_per_kg > 0
        AND open_balance_floor_cent < 0
        AND max_review_abs_weight_g BETWEEN 1 AND 1000000
        AND negative_weight_anomaly_threshold_g > 0
        AND local_end_selection_timeout_ms = 30000
        AND CHAR_LENGTH(TRIM(bag_code_snapshot)) > 0
    ),
    CONSTRAINT ck_dev_delivery_session_terminal_shape CHECK (
        (
            status IN (
                'PREPARED',
                'AUTHORIZATION_QUEUED',
                'IN_PROGRESS',
                'RESULT_PENDING_RECOVERY'
            )
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status IN ('BUSINESS_CONFIRMED', 'PRE_OPEN_ENDED')
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(end_reason)) > 0
        )
    ),
    CONSTRAINT ck_dev_delivery_session_progress_shape CHECK (
        first_physical_progress_at IS NULL
        OR first_edge_accepted_at IS NOT NULL
    ),
    CONSTRAINT ck_dev_delivery_session_times CHECK (
        updated_at >= created_at
        AND authorization_expires_at > created_at
        AND result_recovery_deadline_at > authorization_expires_at
        AND (
            first_edge_accepted_at IS NULL
            OR first_edge_accepted_at >= created_at
        )
        AND (
            first_physical_progress_at IS NULL
            OR first_physical_progress_at >= first_edge_accepted_at
        )
        AND (
            device_completed_at IS NULL
            OR (
                first_edge_accepted_at IS NOT NULL
                AND device_completed_at >= first_edge_accepted_at
            )
        )
        AND (ended_at IS NULL OR ended_at >= created_at)
    ),
    CONSTRAINT ck_dev_delivery_session_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_delivery_session_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_session_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_session_user
        FOREIGN KEY (tenant_id, organization_id, organization_user_id)
        REFERENCES iam_organization_user (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_session_device_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id,
            device_config_version_no,
            device_config_content_sha256,
            device_config_mcu_payload_sha256,
            negative_weight_anomaly_threshold_g,
            local_end_selection_timeout_ms
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            version_no,
            content_sha256,
            mcu_payload_sha256,
            negative_weight_threshold_g,
            continue_delivery_wait_ms
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_session_port_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_config_version_id,
            port_id,
            port_config_snapshot_id,
            unit_price_yuan_per_kg
        )
        REFERENCES dev_port_config_snapshot (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            port_id,
            id,
            unit_price_yuan_per_kg
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_delivery_session_device_active (
        tenant_id,
        organization_id,
        deployment_id,
        status,
        created_at,
        id
    ),
    INDEX ix_dev_delivery_session_user_history (
        tenant_id,
        organization_id,
        organization_user_id,
        created_at,
        id
    ),
    INDEX ix_dev_delivery_session_authorization (
        status,
        authorization_expires_at,
        id
    ),
    INDEX ix_dev_delivery_session_recovery (
        status,
        result_recovery_deadline_at,
        id
    ),
    INDEX ix_dev_delivery_session_port_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id,
        port_id,
        port_config_snapshot_id,
        unit_price_yuan_per_kg
    ),
    INDEX ix_dev_delivery_session_device_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        device_config_version_id,
        device_config_version_no,
        device_config_content_sha256,
        device_config_mcu_payload_sha256,
        negative_weight_anomaly_threshold_g,
        local_end_selection_timeout_ms
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE dev_port_runtime_state
    ADD CONSTRAINT fk_dev_port_runtime_pending_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            pending_delivery_result_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE dev_device_fault_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    fault_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NULL,
    component_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fault_key BINARY(32) NOT NULL,
    impact_level VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    first_source_edge_event_id BIGINT NULL,
    first_source_edge_event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL,
    first_source_evidence_sha256 BINARY(32) NOT NULL,
    first_detected_at DATETIME(3) NOT NULL,
    last_detected_at DATETIME(3) NOT NULL,
    discovery_count BIGINT NOT NULL DEFAULT 1,
    recovery_source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL,
    recovery_source_edge_event_id BIGINT NULL,
    recovery_source_edge_event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL,
    recovery_method VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    recovery_audit_log_id BIGINT NULL,
    recovered_at DATETIME(3) NULL,
    recovered_by_staff_account_id BIGINT NULL,
    recovery_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    active_fault_key BINARY(32)
        GENERATED ALWAYS AS (
            CASE WHEN status = 'OPEN' THEN fault_key ELSE NULL END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_fault_uid UNIQUE (fault_uid),
    CONSTRAINT uq_dev_fault_active_key UNIQUE (active_fault_key),
    CONSTRAINT uq_dev_fault_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_dev_fault_uid_v4 CHECK (
        fault_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_fault_code_nonblank
        CHECK (CHAR_LENGTH(TRIM(fault_code)) > 0),
    CONSTRAINT ck_dev_fault_impact CHECK (
        impact_level IN ('DEGRADED', 'BUSINESS_BLOCKING', 'SAFETY_BLOCKING')
    ),
    CONSTRAINT ck_dev_fault_status CHECK (status IN ('OPEN', 'RECOVERED')),
    CONSTRAINT ck_dev_fault_first_source CHECK (
        (
            first_source_kind = 'EDGE_EVENT'
            AND first_source_edge_event_id IS NOT NULL
            AND first_source_edge_event_type IS NOT NULL
            AND first_source_edge_event_type = 'DEVICE_FAULT_OBSERVED'
        )
        OR
        (
            first_source_kind = 'INTERNAL_DETECTION'
            AND first_source_edge_event_id IS NULL
            AND first_source_edge_event_type IS NULL
        )
    ),
    CONSTRAINT ck_dev_fault_discovery_count CHECK (discovery_count >= 1),
    CONSTRAINT ck_dev_fault_recovery_shape CHECK (
        (
            status = 'OPEN'
            AND recovered_at IS NULL
            AND recovery_source_kind IS NULL
            AND recovery_source_edge_event_id IS NULL
            AND recovery_source_edge_event_type IS NULL
            AND recovery_method IS NULL
            AND recovery_audit_log_id IS NULL
            AND recovered_by_staff_account_id IS NULL
            AND recovery_reason IS NULL
        )
        OR
        (
            status = 'RECOVERED'
            AND recovered_at IS NOT NULL
            AND (
                recovery_source_kind IS NULL
                OR recovery_source_kind IN (
                    'EDGE_EVENT',
                    'STAFF_CONFIRMED',
                    'SYSTEM_VERIFIED'
                )
            )
            AND (
                (
                    recovery_source_kind = 'EDGE_EVENT'
                    AND recovery_source_edge_event_id IS NOT NULL
                    AND recovery_source_edge_event_type IS NOT NULL
                    AND recovery_source_edge_event_type = 'DEVICE_FAULT_RECOVERED'
                )
                OR
                (
                    (
                        recovery_source_kind IS NULL
                        OR recovery_source_kind <> 'EDGE_EVENT'
                    )
                    AND recovery_source_edge_event_id IS NULL
                    AND recovery_source_edge_event_type IS NULL
                )
            )
            AND (
                recovery_method IS NULL
                OR CHAR_LENGTH(TRIM(recovery_method)) > 0
            )
            AND (
                recovery_reason IS NULL
                OR CHAR_LENGTH(TRIM(recovery_reason)) > 0
            )
            AND (
                impact_level <> 'SAFETY_BLOCKING'
                OR recovered_by_staff_account_id IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_dev_fault_times CHECK (
        last_detected_at >= first_detected_at
        AND first_detected_at >= created_at
        AND (recovered_at IS NULL OR recovered_at >= last_detected_at)
    ),
    CONSTRAINT ck_dev_fault_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_fault_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_recovery_staff
        FOREIGN KEY (tenant_id, recovered_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_fault_first_edge (
        tenant_id,
        organization_id,
        deployment_id,
        first_source_edge_event_id,
        first_source_edge_event_type
    ),
    INDEX ix_dev_fault_recovery_edge (
        tenant_id,
        organization_id,
        deployment_id,
        recovery_source_edge_event_id,
        recovery_source_edge_event_type
    ),
    INDEX ix_dev_fault_recovery_staff (
        tenant_id,
        recovered_by_staff_account_id
    ),
    INDEX ix_dev_fault_org_active (
        tenant_id,
        organization_id,
        status,
        impact_level,
        last_detected_at,
        id
    ),
    INDEX ix_dev_fault_port_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_occupancy (
    asset_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    occupancy_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_session_id BIGINT NULL,
    clean_operation_id BIGINT NULL,
    acquired_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (asset_id),
    CONSTRAINT uq_dev_occupancy_delivery_session UNIQUE (delivery_session_id),
    CONSTRAINT uq_dev_occupancy_clean_operation UNIQUE (clean_operation_id),
    CONSTRAINT ck_dev_occupancy_target_shape CHECK (
        (
            occupancy_kind = 'DELIVERY'
            AND delivery_session_id IS NOT NULL
            AND clean_operation_id IS NULL
        )
        OR
        (
            occupancy_kind = 'CLEAN'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_occupancy_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_occupancy_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_occupancy_active_deployment
        FOREIGN KEY (
            asset_id,
            tenant_id,
            organization_id,
            deployment_id
        )
        REFERENCES dev_asset_active_deployment (
            asset_id,
            tenant_id,
            organization_id,
            deployment_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_occupancy_delivery_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            delivery_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_occupancy_scope (
        tenant_id,
        organization_id,
        deployment_id,
        occupancy_kind,
        asset_id
    ),
    INDEX ix_dev_occupancy_active_fk (
        asset_id,
        tenant_id,
        organization_id,
        deployment_id
    ),
    INDEX ix_dev_occupancy_delivery_fk (
        tenant_id,
        organization_id,
        deployment_id,
        delivery_session_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_command (
    id BIGINT NOT NULL AUTO_INCREMENT,
    command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    command_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_session_id BIGINT NULL,
    clean_operation_id BIGINT NULL,
    config_application_id BIGINT NULL,
    fullness_detection_id BIGINT NULL,
    baseline_measurement_id BIGINT NULL,
    payload_schema_version INT NOT NULL,
    semantic_payload JSON NOT NULL,
    semantic_payload_sha256 BINARY(32) NOT NULL,
    physical_state VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    queued_at DATETIME(3) NULL,
    edge_accepted_at DATETIME(3) NULL,
    physical_started_at DATETIME(3) NULL,
    physical_ended_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_command_uid UNIQUE (command_uid),
    CONSTRAINT uq_dev_command_scope_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT uq_dev_command_type_ref
        UNIQUE (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type
        ),
    CONSTRAINT uq_dev_command_delivery_ref
        UNIQUE (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type,
            delivery_session_id
        ),
    CONSTRAINT ck_dev_command_uid_v4 CHECK (
        command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_command_target_shape CHECK (
        (
            command_type = 'START_DELIVERY_SESSION'
            AND delivery_session_id IS NOT NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type IN (
                'START_CLEAN_OPERATION',
                'END_CLEAN_BEFORE_UNLOCK',
                'RESUME_CLEAN_OPERATION'
            )
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NOT NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'APPLY_CONFIGURATION'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NOT NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'SAMPLE_FULLNESS'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NOT NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            command_type = 'MEASURE_EMPTY_BAG_BASELINE'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_command_payload_version CHECK (payload_schema_version > 0),
    CONSTRAINT ck_dev_command_physical_state CHECK (
        physical_state IN (
            'CREATED',
            'QUEUED',
            'EDGE_ACCEPTED',
            'PHYSICAL_STARTED',
            'PHYSICAL_SUCCEEDED',
            'PHYSICAL_FAILED',
            'PRE_START_FAILED'
        )
    ),
    CONSTRAINT ck_dev_command_state_shape CHECK (
        (
            physical_state = 'CREATED'
            AND queued_at IS NULL
            AND edge_accepted_at IS NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'QUEUED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'EDGE_ACCEPTED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'PHYSICAL_STARTED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NOT NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state IN ('PHYSICAL_SUCCEEDED', 'PHYSICAL_FAILED')
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NOT NULL
            AND physical_ended_at IS NOT NULL
        )
        OR
        (
            physical_state = 'PRE_START_FAILED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_command_time_order CHECK (
        updated_at >= created_at
        AND (queued_at IS NULL OR queued_at >= created_at)
        AND (
            edge_accepted_at IS NULL
            OR (
                queued_at IS NOT NULL
                AND edge_accepted_at >= queued_at
            )
        )
        AND (
            physical_started_at IS NULL
            OR (
                edge_accepted_at IS NOT NULL
                AND physical_started_at >= edge_accepted_at
            )
        )
        AND (
            physical_ended_at IS NULL
            OR (
                physical_started_at IS NOT NULL
                AND physical_ended_at >= physical_started_at
            )
            OR (
                physical_started_at IS NULL
                AND edge_accepted_at IS NOT NULL
                AND physical_ended_at >= edge_accepted_at
            )
        )
    ),
    CONSTRAINT ck_dev_command_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_command_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_delivery_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            delivery_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_config_application
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            config_application_id
        )
        REFERENCES dev_config_application (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_command_deployment_state (
        tenant_id,
        organization_id,
        deployment_id,
        physical_state,
        created_at,
        id
    ),
    INDEX ix_dev_command_delivery_target (delivery_session_id, created_at, id),
    INDEX ix_dev_command_clean_target (clean_operation_id, created_at, id),
    INDEX ix_dev_command_config_target (config_application_id, created_at, id),
    INDEX ix_dev_command_fullness_target (fullness_detection_id, created_at, id),
    INDEX ix_dev_command_baseline_target (baseline_measurement_id, created_at, id),
    INDEX ix_dev_command_delivery_fk (
        tenant_id,
        organization_id,
        deployment_id,
        delivery_session_id
    ),
    INDEX ix_dev_command_config_app_fk (
        tenant_id,
        organization_id,
        deployment_id,
        config_application_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_edge_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_event_sequence BIGINT NOT NULL,
    event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_class VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    schema_version INT NOT NULL,
    target_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_stable_key_sha256 BINARY(32) NOT NULL,
    device_occurred_at DATETIME(3) NULL,
    clock_quality VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    backend_received_at DATETIME(3) NOT NULL,
    payload_sha256 BINARY(32) NOT NULL,
    canonical_sha256 BINARY(32) NOT NULL,
    source_inbox_id BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_edge_event_uid UNIQUE (event_uid),
    CONSTRAINT uq_dev_edge_event_sequence
        UNIQUE (deployment_id, edge_event_sequence),
    CONSTRAINT uq_dev_edge_event_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_edge_event_scope_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT uq_dev_edge_event_typed_branch
        UNIQUE (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        ),
    CONSTRAINT ck_dev_edge_event_uid_v4 CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_edge_event_sequence
        CHECK (edge_event_sequence > 0 AND schema_version > 0),
    CONSTRAINT ck_dev_edge_event_type_class CHECK (
        (
            event_type IN (
                'DEVICE_COMMAND_OBSERVED',
                'CONFIGURATION_PROGRESS',
                'DELIVERY_COMPLETE',
                'CLEAN_COMPLETE',
                'FULLNESS_SAMPLE_COMPLETE',
                'BASELINE_MEASUREMENT_COMPLETE',
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND delivery_class = 'RELIABLE_FACT'
        )
        OR
        (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND delivery_class = 'CONTROL_RECEIPT'
        )
        OR
        (
            event_type = 'DEVICE_RUNTIME_SNAPSHOT'
            AND delivery_class = 'TELEMETRY_SNAPSHOT'
        )
    ),
    CONSTRAINT ck_dev_edge_event_target_type CHECK (
        target_type IN (
            'DEVICE_COMMAND',
            'CONFIGURATION_APPLICATION',
            'DELIVERY_SESSION',
            'CLEAN_OPERATION',
            'FULLNESS_DETECTION',
            'BASELINE_MEASUREMENT',
            'DEVICE_DEPLOYMENT',
            'BUSINESS_CONFIRMATION',
            'PHOTO_GRANT_REQUEST'
        )
    ),
    CONSTRAINT ck_dev_edge_event_target_pair CHECK (
        (
            event_type = 'DEVICE_COMMAND_OBSERVED'
            AND target_type = 'DEVICE_COMMAND'
        )
        OR
        (
            event_type = 'CONFIGURATION_PROGRESS'
            AND target_type = 'CONFIGURATION_APPLICATION'
        )
        OR
        (
            event_type = 'DELIVERY_COMPLETE'
            AND target_type = 'DELIVERY_SESSION'
        )
        OR
        (
            event_type = 'CLEAN_COMPLETE'
            AND target_type = 'CLEAN_OPERATION'
        )
        OR
        (
            event_type = 'FULLNESS_SAMPLE_COMPLETE'
            AND target_type = 'FULLNESS_DETECTION'
        )
        OR
        (
            event_type = 'BASELINE_MEASUREMENT_COMPLETE'
            AND target_type = 'BASELINE_MEASUREMENT'
        )
        OR
        (
            event_type IN (
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'DEVICE_RUNTIME_SNAPSHOT'
            )
            AND target_type = 'DEVICE_DEPLOYMENT'
        )
        OR
        (
            event_type IN (
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND target_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        )
        OR
        (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND target_type = 'BUSINESS_CONFIRMATION'
        )
    ),
    CONSTRAINT ck_dev_edge_event_clock_shape CHECK (
        (
            clock_quality = 'SYNCED'
            AND device_occurred_at IS NOT NULL
        )
        OR
        (
            clock_quality IN ('ESTIMATED', 'UNAVAILABLE')
            AND device_occurred_at IS NULL
        )
    ),
    CONSTRAINT ck_dev_edge_event_times
        CHECK (created_at >= backend_received_at),
    CONSTRAINT fk_dev_edge_event_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_edge_event_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_edge_event_deployment_time (
        tenant_id,
        organization_id,
        deployment_id,
        backend_received_at,
        id
    ),
    INDEX ix_dev_edge_event_type_time (
        event_type,
        backend_received_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE dev_device_fault_event
    ADD CONSTRAINT fk_dev_fault_first_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            first_source_edge_event_id,
            first_source_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_dev_fault_recovery_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            recovery_source_edge_event_id,
            recovery_source_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE dev_device_command_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    edge_event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    command_id BIGINT NOT NULL,
    delivery_session_id BIGINT NULL,
    observed_command_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    observation_stage VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mcu_command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_command_event_edge UNIQUE (edge_event_id),
    CONSTRAINT ck_dev_command_event_edge_type
        CHECK (edge_event_type = 'DEVICE_COMMAND_OBSERVED'),
    CONSTRAINT ck_dev_command_event_type CHECK (
        observed_command_type IN (
            'START_DELIVERY_SESSION',
            'START_CLEAN_OPERATION',
            'END_CLEAN_BEFORE_UNLOCK',
            'RESUME_CLEAN_OPERATION',
            'SAMPLE_FULLNESS',
            'MEASURE_EMPTY_BAG_BASELINE'
        )
    ),
    CONSTRAINT ck_dev_command_event_session_shape CHECK (
        (
            observed_command_type = 'START_DELIVERY_SESSION'
            AND delivery_session_id IS NOT NULL
        )
        OR
        (
            observed_command_type <> 'START_DELIVERY_SESSION'
            AND delivery_session_id IS NULL
        )
    ),
    CONSTRAINT ck_dev_command_event_stage CHECK (
        observation_stage IN (
            'RECEIVED',
            'ACCEPTED',
            'REJECTED',
            'MCU_ACCEPTED',
            'PRE_START_FAILED',
            'FAILED'
        )
    ),
    CONSTRAINT ck_dev_command_event_result_shape CHECK (
        (
            observation_stage IN ('RECEIVED', 'ACCEPTED')
            AND mcu_command_uid IS NULL
            AND error_code IS NULL
        )
        OR
        (
            observation_stage = 'REJECTED'
            AND mcu_command_uid IS NULL
            AND error_code IS NOT NULL
        )
        OR
        (
            observation_stage = 'MCU_ACCEPTED'
            AND mcu_command_uid IS NOT NULL
            AND error_code IS NULL
        )
        OR
        (
            observation_stage IN ('PRE_START_FAILED', 'FAILED')
            AND error_code IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_command_event_error_code CHECK (
        error_code IS NULL
        OR error_code REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
    ),
    CONSTRAINT ck_dev_command_event_mcu_uid_v4 CHECK (
        mcu_command_uid IS NULL
        OR mcu_command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT fk_dev_command_event_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_event_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            edge_event_id,
            edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_event_command_type
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            command_id,
            observed_command_type
        )
        REFERENCES dev_device_command (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_command_event_delivery_ref
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            command_id,
            observed_command_type,
            delivery_session_id
        )
        REFERENCES dev_device_command (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type,
            delivery_session_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_command_event_command (
        tenant_id,
        organization_id,
        deployment_id,
        command_id,
        observed_command_type,
        created_at,
        id
    ),
    INDEX ix_dev_command_event_edge_fk (
        tenant_id,
        organization_id,
        deployment_id,
        edge_event_id,
        edge_event_type
    ),
    INDEX ix_dev_command_event_delivery_fk (
        tenant_id,
        organization_id,
        deployment_id,
        command_id,
        observed_command_type,
        delivery_session_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_physical_result (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    edge_event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    command_id BIGINT NOT NULL,
    command_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reported_config_version_no BIGINT NOT NULL,
    reported_config_content_sha256 BINARY(32) NOT NULL,
    reported_config_mcu_payload_sha256 BINARY(32) NOT NULL,
    result_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_session_id BIGINT NULL,
    clean_operation_id BIGINT NULL,
    fullness_sample_id BIGINT NULL,
    baseline_measurement_id BIGINT NULL,
    delivery_pre_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_pre_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_pre_weight_g BIGINT NULL,
    delivery_pre_last_observed_weight_g BIGINT NULL,
    delivery_pre_measurement_elapsed_ms BIGINT NULL,
    delivery_pre_sample_count INT NULL,
    delivery_pre_calibration_version BIGINT NULL,
    delivery_pre_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_pre_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_pre_mcu_boot_id BIGINT NULL,
    delivery_pre_mcu_event_sequence BIGINT NULL,
    delivery_post_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_post_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_post_weight_g BIGINT NULL,
    delivery_post_last_observed_weight_g BIGINT NULL,
    delivery_post_measurement_elapsed_ms BIGINT NULL,
    delivery_post_sample_count INT NULL,
    delivery_post_calibration_version BIGINT NULL,
    delivery_post_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_post_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_post_mcu_boot_id BIGINT NULL,
    delivery_post_mcu_event_sequence BIGINT NULL,
    delivery_net_weight_g BIGINT NULL,
    delivery_final_door_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_final_door_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    delivery_completion_reason VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    negative_weight_anomaly TINYINT NULL,
    clean_pre_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_pre_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_pre_weight_g BIGINT NULL,
    clean_pre_last_observed_weight_g BIGINT NULL,
    clean_pre_measurement_elapsed_ms BIGINT NULL,
    clean_pre_sample_count INT NULL,
    clean_pre_calibration_version BIGINT NULL,
    clean_pre_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_pre_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_pre_mcu_boot_id BIGINT NULL,
    clean_pre_mcu_event_sequence BIGINT NULL,
    clean_final_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_final_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_final_weight_g BIGINT NULL,
    clean_final_last_observed_weight_g BIGINT NULL,
    clean_final_measurement_elapsed_ms BIGINT NULL,
    clean_final_sample_count INT NULL,
    clean_final_calibration_version BIGINT NULL,
    clean_final_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_final_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_final_mcu_boot_id BIGINT NULL,
    clean_final_mcu_event_sequence BIGINT NULL,
    clean_removed_net_weight_g BIGINT NULL,
    clean_new_baseline_weight_g BIGINT NULL,
    cleaner_completion_confirmed TINYINT NULL,
    clean_lock_power_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_solenoid_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_door_inferred_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clean_door_state_basis VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_total_weight_g BIGINT NULL,
    fullness_last_observed_weight_g BIGINT NULL,
    fullness_measurement_elapsed_ms BIGINT NULL,
    fullness_sample_count INT NULL,
    fullness_calibration_version BIGINT NULL,
    fullness_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    fullness_mcu_boot_id BIGINT NULL,
    fullness_mcu_event_sequence BIGINT NULL,
    infrared_value VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    infrared_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    baseline_measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    baseline_measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    baseline_total_weight_g BIGINT NULL,
    baseline_last_observed_weight_g BIGINT NULL,
    baseline_measurement_elapsed_ms BIGINT NULL,
    baseline_sample_count INT NULL,
    baseline_calibration_version BIGINT NULL,
    baseline_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    baseline_fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    baseline_mcu_boot_id BIGINT NULL,
    baseline_mcu_event_sequence BIGINT NULL,
    empty_bag_confirmed TINYINT NULL,
    uart_protocol_major INT NULL,
    uart_protocol_minor INT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_result_edge UNIQUE (edge_event_id),
    CONSTRAINT uq_dev_result_command UNIQUE (command_id),
    CONSTRAINT uq_dev_result_delivery UNIQUE (delivery_session_id),
    CONSTRAINT uq_dev_result_clean UNIQUE (clean_operation_id),
    CONSTRAINT uq_dev_result_fullness_sample UNIQUE (fullness_sample_id),
    CONSTRAINT uq_dev_result_baseline UNIQUE (baseline_measurement_id),
    CONSTRAINT uq_dev_result_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_result_delivery_ref UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        id,
        delivery_session_id
    ),
    CONSTRAINT ck_dev_result_target_shape CHECK (
        (
            result_type = 'DELIVERY'
            AND delivery_session_id IS NOT NULL
            AND clean_operation_id IS NULL
            AND fullness_sample_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            result_type = 'CLEAN'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NOT NULL
            AND fullness_sample_id IS NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND fullness_sample_id IS NOT NULL
            AND baseline_measurement_id IS NULL
        )
        OR
        (
            result_type = 'BASELINE_MEASUREMENT'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND fullness_sample_id IS NULL
            AND baseline_measurement_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_result_branch_fields CHECK (
        (
            result_type = 'DELIVERY'
            AND delivery_pre_measurement_uid IS NOT NULL
            AND delivery_post_measurement_uid IS NOT NULL
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
            AND delivery_final_door_state IS NOT NULL
            AND delivery_final_door_health IS NOT NULL
            AND delivery_final_door_state = 'CLOSED'
            AND delivery_final_door_health = 'OK'
        )
        OR
        (
            result_type = 'CLEAN'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_completion_reason IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NOT NULL
            AND clean_final_measurement_uid IS NOT NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
        )
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_completion_reason IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NOT NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
        )
        OR
        (
            result_type = 'BASELINE_MEASUREMENT'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_completion_reason IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_result_edge_type CHECK (
        (result_type = 'DELIVERY' AND edge_event_type = 'DELIVERY_COMPLETE')
        OR
        (result_type = 'CLEAN' AND edge_event_type = 'CLEAN_COMPLETE')
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND edge_event_type = 'FULLNESS_SAMPLE_COMPLETE'
        )
        OR
        (
            result_type = 'BASELINE_MEASUREMENT'
            AND edge_event_type = 'BASELINE_MEASUREMENT_COMPLETE'
        )
    ),
    CONSTRAINT ck_dev_result_command_type CHECK (
        (
            result_type = 'DELIVERY'
            AND command_type = 'START_DELIVERY_SESSION'
        )
        OR
        (
            result_type = 'CLEAN'
            AND command_type IN (
                'START_CLEAN_OPERATION',
                'RESUME_CLEAN_OPERATION'
            )
        )
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND command_type = 'SAMPLE_FULLNESS'
        )
        OR
        (
            result_type = 'BASELINE_MEASUREMENT'
            AND command_type = 'MEASURE_EMPTY_BAG_BASELINE'
        )
    ),
    CONSTRAINT ck_dev_result_reported_config
        CHECK (reported_config_version_no > 0),
    CONSTRAINT ck_dev_result_delivery_pre_measurement CHECK (
        (
            delivery_pre_measurement_uid IS NULL
            AND delivery_pre_measurement_status IS NULL
            AND delivery_pre_weight_g IS NULL
            AND delivery_pre_last_observed_weight_g IS NULL
            AND delivery_pre_measurement_elapsed_ms IS NULL
            AND delivery_pre_sample_count IS NULL
            AND delivery_pre_calibration_version IS NULL
            AND delivery_pre_sensor_health IS NULL
            AND delivery_pre_fault_code IS NULL
            AND delivery_pre_mcu_boot_id IS NULL
            AND delivery_pre_mcu_event_sequence IS NULL
        )
        OR
        (
            delivery_pre_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_pre_measurement_status IS NOT NULL
            AND delivery_pre_measurement_elapsed_ms IS NOT NULL
            AND delivery_pre_sample_count IS NOT NULL
            AND delivery_pre_calibration_version IS NOT NULL
            AND delivery_pre_sensor_health IS NOT NULL
            AND delivery_pre_mcu_boot_id IS NOT NULL
            AND delivery_pre_mcu_event_sequence IS NOT NULL
            AND delivery_pre_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND delivery_pre_sample_count BETWEEN 0 AND 65535
            AND delivery_pre_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                delivery_pre_last_observed_weight_g IS NULL
                OR delivery_pre_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    delivery_pre_measurement_status = 'STABLE'
                    AND delivery_pre_weight_g IS NOT NULL
                    AND delivery_pre_fault_code IS NULL
                )
                OR
                (
                    delivery_pre_measurement_status <> 'STABLE'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    delivery_pre_measurement_status = 'STABLE'
                    AND delivery_pre_weight_g BETWEEN -2147483648 AND 2147483647
                    AND delivery_pre_sample_count >= 1
                    AND delivery_pre_sensor_health = 'OK'
                    AND delivery_pre_fault_code IS NULL
                )
                OR
                (
                    delivery_pre_measurement_status = 'UNSTABLE'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_sensor_health = 'OK'
                    AND delivery_pre_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    delivery_pre_measurement_status = 'TIMEOUT'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_sensor_health = 'TIMEOUT'
                    AND delivery_pre_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    delivery_pre_measurement_status = 'SENSOR_FAULT'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND delivery_pre_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    delivery_pre_measurement_status = 'OVERLOAD'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_sensor_health = 'OK'
                    AND delivery_pre_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_delivery_post_measurement CHECK (
        (
            delivery_post_measurement_uid IS NULL
            AND delivery_post_measurement_status IS NULL
            AND delivery_post_weight_g IS NULL
            AND delivery_post_last_observed_weight_g IS NULL
            AND delivery_post_measurement_elapsed_ms IS NULL
            AND delivery_post_sample_count IS NULL
            AND delivery_post_calibration_version IS NULL
            AND delivery_post_sensor_health IS NULL
            AND delivery_post_fault_code IS NULL
            AND delivery_post_mcu_boot_id IS NULL
            AND delivery_post_mcu_event_sequence IS NULL
        )
        OR
        (
            delivery_post_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_post_measurement_status IS NOT NULL
            AND delivery_post_measurement_elapsed_ms IS NOT NULL
            AND delivery_post_sample_count IS NOT NULL
            AND delivery_post_calibration_version IS NOT NULL
            AND delivery_post_sensor_health IS NOT NULL
            AND delivery_post_mcu_boot_id IS NOT NULL
            AND delivery_post_mcu_event_sequence IS NOT NULL
            AND delivery_post_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND delivery_post_sample_count BETWEEN 0 AND 65535
            AND delivery_post_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_post_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_post_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                delivery_post_last_observed_weight_g IS NULL
                OR delivery_post_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    delivery_post_measurement_status = 'STABLE'
                    AND delivery_post_weight_g IS NOT NULL
                    AND delivery_post_fault_code IS NULL
                )
                OR
                (
                    delivery_post_measurement_status <> 'STABLE'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    delivery_post_measurement_status = 'STABLE'
                    AND delivery_post_weight_g BETWEEN -2147483648 AND 2147483647
                    AND delivery_post_sample_count >= 1
                    AND delivery_post_sensor_health = 'OK'
                    AND delivery_post_fault_code IS NULL
                )
                OR
                (
                    delivery_post_measurement_status = 'UNSTABLE'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_sensor_health = 'OK'
                    AND delivery_post_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    delivery_post_measurement_status = 'TIMEOUT'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_sensor_health = 'TIMEOUT'
                    AND delivery_post_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    delivery_post_measurement_status = 'SENSOR_FAULT'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND delivery_post_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    delivery_post_measurement_status = 'OVERLOAD'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_sensor_health = 'OK'
                    AND delivery_post_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_clean_pre_measurement CHECK (
        (
            clean_pre_measurement_uid IS NULL
            AND clean_pre_measurement_status IS NULL
            AND clean_pre_weight_g IS NULL
            AND clean_pre_last_observed_weight_g IS NULL
            AND clean_pre_measurement_elapsed_ms IS NULL
            AND clean_pre_sample_count IS NULL
            AND clean_pre_calibration_version IS NULL
            AND clean_pre_sensor_health IS NULL
            AND clean_pre_fault_code IS NULL
            AND clean_pre_mcu_boot_id IS NULL
            AND clean_pre_mcu_event_sequence IS NULL
        )
        OR
        (
            clean_pre_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND clean_pre_measurement_status IS NOT NULL
            AND clean_pre_measurement_elapsed_ms IS NOT NULL
            AND clean_pre_sample_count IS NOT NULL
            AND clean_pre_calibration_version IS NOT NULL
            AND clean_pre_sensor_health IS NOT NULL
            AND clean_pre_mcu_boot_id IS NOT NULL
            AND clean_pre_mcu_event_sequence IS NOT NULL
            AND clean_pre_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND clean_pre_sample_count BETWEEN 0 AND 65535
            AND clean_pre_calibration_version BETWEEN 0 AND 4294967295
            AND clean_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                clean_pre_last_observed_weight_g IS NULL
                OR clean_pre_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    clean_pre_measurement_status = 'STABLE'
                    AND clean_pre_weight_g IS NOT NULL
                    AND clean_pre_fault_code IS NULL
                )
                OR
                (
                    clean_pre_measurement_status <> 'STABLE'
                    AND clean_pre_weight_g IS NULL
                    AND clean_pre_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    clean_pre_measurement_status = 'STABLE'
                    AND clean_pre_weight_g BETWEEN -2147483648 AND 2147483647
                    AND clean_pre_sample_count >= 1
                    AND clean_pre_sensor_health = 'OK'
                    AND clean_pre_fault_code IS NULL
                )
                OR
                (
                    clean_pre_measurement_status = 'UNSTABLE'
                    AND clean_pre_weight_g IS NULL
                    AND clean_pre_sensor_health = 'OK'
                    AND clean_pre_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    clean_pre_measurement_status = 'TIMEOUT'
                    AND clean_pre_weight_g IS NULL
                    AND clean_pre_sensor_health = 'TIMEOUT'
                    AND clean_pre_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    clean_pre_measurement_status = 'SENSOR_FAULT'
                    AND clean_pre_weight_g IS NULL
                    AND clean_pre_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND clean_pre_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    clean_pre_measurement_status = 'OVERLOAD'
                    AND clean_pre_weight_g IS NULL
                    AND clean_pre_sensor_health = 'OK'
                    AND clean_pre_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_clean_final_measurement CHECK (
        (
            clean_final_measurement_uid IS NULL
            AND clean_final_measurement_status IS NULL
            AND clean_final_weight_g IS NULL
            AND clean_final_last_observed_weight_g IS NULL
            AND clean_final_measurement_elapsed_ms IS NULL
            AND clean_final_sample_count IS NULL
            AND clean_final_calibration_version IS NULL
            AND clean_final_sensor_health IS NULL
            AND clean_final_fault_code IS NULL
            AND clean_final_mcu_boot_id IS NULL
            AND clean_final_mcu_event_sequence IS NULL
        )
        OR
        (
            clean_final_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND clean_final_measurement_status IS NOT NULL
            AND clean_final_measurement_elapsed_ms IS NOT NULL
            AND clean_final_sample_count IS NOT NULL
            AND clean_final_calibration_version IS NOT NULL
            AND clean_final_sensor_health IS NOT NULL
            AND clean_final_mcu_boot_id IS NOT NULL
            AND clean_final_mcu_event_sequence IS NOT NULL
            AND clean_final_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND clean_final_sample_count BETWEEN 0 AND 65535
            AND clean_final_calibration_version BETWEEN 0 AND 4294967295
            AND clean_final_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_final_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                clean_final_last_observed_weight_g IS NULL
                OR clean_final_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    clean_final_measurement_status = 'STABLE'
                    AND clean_final_weight_g IS NOT NULL
                    AND clean_final_fault_code IS NULL
                )
                OR
                (
                    clean_final_measurement_status <> 'STABLE'
                    AND clean_final_weight_g IS NULL
                    AND clean_final_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    clean_final_measurement_status = 'STABLE'
                    AND clean_final_weight_g BETWEEN -2147483648 AND 2147483647
                    AND clean_final_sample_count >= 1
                    AND clean_final_sensor_health = 'OK'
                    AND clean_final_fault_code IS NULL
                )
                OR
                (
                    clean_final_measurement_status = 'UNSTABLE'
                    AND clean_final_weight_g IS NULL
                    AND clean_final_sensor_health = 'OK'
                    AND clean_final_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    clean_final_measurement_status = 'TIMEOUT'
                    AND clean_final_weight_g IS NULL
                    AND clean_final_sensor_health = 'TIMEOUT'
                    AND clean_final_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    clean_final_measurement_status = 'SENSOR_FAULT'
                    AND clean_final_weight_g IS NULL
                    AND clean_final_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND clean_final_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    clean_final_measurement_status = 'OVERLOAD'
                    AND clean_final_weight_g IS NULL
                    AND clean_final_sensor_health = 'OK'
                    AND clean_final_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_fullness_measurement CHECK (
        (
            fullness_measurement_uid IS NULL
            AND fullness_measurement_status IS NULL
            AND fullness_total_weight_g IS NULL
            AND fullness_last_observed_weight_g IS NULL
            AND fullness_measurement_elapsed_ms IS NULL
            AND fullness_sample_count IS NULL
            AND fullness_calibration_version IS NULL
            AND fullness_sensor_health IS NULL
            AND fullness_fault_code IS NULL
            AND fullness_mcu_boot_id IS NULL
            AND fullness_mcu_event_sequence IS NULL
        )
        OR
        (
            fullness_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND fullness_measurement_status IS NOT NULL
            AND fullness_measurement_elapsed_ms IS NOT NULL
            AND fullness_sample_count IS NOT NULL
            AND fullness_calibration_version IS NOT NULL
            AND fullness_sensor_health IS NOT NULL
            AND fullness_mcu_boot_id IS NOT NULL
            AND fullness_mcu_event_sequence IS NOT NULL
            AND fullness_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND fullness_sample_count BETWEEN 0 AND 65535
            AND fullness_calibration_version BETWEEN 0 AND 4294967295
            AND fullness_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND fullness_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                fullness_last_observed_weight_g IS NULL
                OR fullness_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    fullness_measurement_status = 'STABLE'
                    AND fullness_total_weight_g IS NOT NULL
                    AND fullness_fault_code IS NULL
                )
                OR
                (
                    fullness_measurement_status <> 'STABLE'
                    AND fullness_total_weight_g IS NULL
                    AND fullness_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    fullness_measurement_status = 'STABLE'
                    AND fullness_total_weight_g BETWEEN -2147483648 AND 2147483647
                    AND fullness_sample_count >= 1
                    AND fullness_sensor_health = 'OK'
                    AND fullness_fault_code IS NULL
                )
                OR
                (
                    fullness_measurement_status = 'UNSTABLE'
                    AND fullness_total_weight_g IS NULL
                    AND fullness_sensor_health = 'OK'
                    AND fullness_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    fullness_measurement_status = 'TIMEOUT'
                    AND fullness_total_weight_g IS NULL
                    AND fullness_sensor_health = 'TIMEOUT'
                    AND fullness_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    fullness_measurement_status = 'SENSOR_FAULT'
                    AND fullness_total_weight_g IS NULL
                    AND fullness_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND fullness_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    fullness_measurement_status = 'OVERLOAD'
                    AND fullness_total_weight_g IS NULL
                    AND fullness_sensor_health = 'OK'
                    AND fullness_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_baseline_measurement CHECK (
        (
            baseline_measurement_uid IS NULL
            AND baseline_measurement_status IS NULL
            AND baseline_total_weight_g IS NULL
            AND baseline_last_observed_weight_g IS NULL
            AND baseline_measurement_elapsed_ms IS NULL
            AND baseline_sample_count IS NULL
            AND baseline_calibration_version IS NULL
            AND baseline_sensor_health IS NULL
            AND baseline_fault_code IS NULL
            AND baseline_mcu_boot_id IS NULL
            AND baseline_mcu_event_sequence IS NULL
        )
        OR
        (
            baseline_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND baseline_measurement_status IS NOT NULL
            AND baseline_measurement_elapsed_ms IS NOT NULL
            AND baseline_sample_count IS NOT NULL
            AND baseline_calibration_version IS NOT NULL
            AND baseline_sensor_health IS NOT NULL
            AND baseline_mcu_boot_id IS NOT NULL
            AND baseline_mcu_event_sequence IS NOT NULL
            AND baseline_measurement_elapsed_ms BETWEEN 0 AND 4294967295
            AND baseline_sample_count BETWEEN 0 AND 65535
            AND baseline_calibration_version BETWEEN 0 AND 4294967295
            AND baseline_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND baseline_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND (
                baseline_last_observed_weight_g IS NULL
                OR baseline_last_observed_weight_g BETWEEN -2147483648 AND 2147483647
            )
            AND (
                (
                    baseline_measurement_status = 'STABLE'
                    AND baseline_total_weight_g IS NOT NULL
                    AND baseline_fault_code IS NULL
                )
                OR
                (
                    baseline_measurement_status <> 'STABLE'
                    AND baseline_total_weight_g IS NULL
                    AND baseline_fault_code IS NOT NULL
                )
            )
            AND (
                (
                    baseline_measurement_status = 'STABLE'
                    AND baseline_total_weight_g BETWEEN -2147483648 AND 2147483647
                    AND baseline_sample_count >= 1
                    AND baseline_sensor_health = 'OK'
                    AND baseline_fault_code IS NULL
                )
                OR
                (
                    baseline_measurement_status = 'UNSTABLE'
                    AND baseline_total_weight_g IS NULL
                    AND baseline_sensor_health = 'OK'
                    AND baseline_fault_code = 'WEIGHT_UNSTABLE'
                )
                OR
                (
                    baseline_measurement_status = 'TIMEOUT'
                    AND baseline_total_weight_g IS NULL
                    AND baseline_sensor_health = 'TIMEOUT'
                    AND baseline_fault_code = 'WEIGHT_TIMEOUT'
                )
                OR
                (
                    baseline_measurement_status = 'SENSOR_FAULT'
                    AND baseline_total_weight_g IS NULL
                    AND baseline_sensor_health IN (
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND baseline_fault_code = 'WEIGHT_SENSOR'
                )
                OR
                (
                    baseline_measurement_status = 'OVERLOAD'
                    AND baseline_total_weight_g IS NULL
                    AND baseline_sensor_health = 'OK'
                    AND baseline_fault_code = 'WEIGHT_OVERLOAD'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_delivery_shape CHECK (
        result_type <> 'DELIVERY'
        OR
        (
            delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_measurement_status = 'STABLE'
            AND delivery_pre_weight_g IS NOT NULL
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_completion_reason IS NOT NULL
            AND negative_weight_anomaly IS NOT NULL
            AND negative_weight_anomaly IN (0, 1)
            AND (
                (
                    delivery_completion_reason IN (
                        'USER_ENDED',
                        'SELECTION_WINDOW_EXPIRED'
                    )
                    AND delivery_post_measurement_status = 'STABLE'
                    AND delivery_post_weight_g IS NOT NULL
                    AND delivery_net_weight_g IS NOT NULL
                    AND delivery_net_weight_g =
                        delivery_post_weight_g - delivery_pre_weight_g
                )
                OR
                (
                    delivery_completion_reason = 'TERMINAL_WEIGHT_FAILURE'
                    AND delivery_post_measurement_status IN (
                        'UNSTABLE',
                        'TIMEOUT',
                        'SENSOR_FAULT',
                        'OVERLOAD'
                    )
                    AND delivery_post_weight_g IS NULL
                    AND delivery_net_weight_g IS NULL
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_clean_shape CHECK (
        result_type <> 'CLEAN'
        OR
        (
            clean_pre_measurement_uid IS NOT NULL
            AND clean_pre_measurement_status = 'STABLE'
            AND clean_pre_weight_g IS NOT NULL
            AND clean_final_measurement_uid IS NOT NULL
            AND cleaner_completion_confirmed IS NOT NULL
            AND clean_lock_power_state IS NOT NULL
            AND clean_solenoid_health IS NOT NULL
            AND clean_door_inferred_state IS NOT NULL
            AND clean_door_state_basis IS NOT NULL
            AND cleaner_completion_confirmed = 1
            AND clean_lock_power_state = 'DEENERGIZED'
            AND clean_solenoid_health = 'OK'
            AND clean_door_inferred_state = 'CLOSED'
            AND clean_door_state_basis = 'INFERRED_FROM_LOCK_POWER'
            AND (
                (
                    clean_final_measurement_status = 'STABLE'
                    AND clean_final_weight_g IS NOT NULL
                    AND clean_new_baseline_weight_g IS NOT NULL
                    AND clean_new_baseline_weight_g = clean_final_weight_g
                )
                OR
                (
                    clean_final_measurement_status IN (
                        'UNSTABLE',
                        'TIMEOUT',
                        'SENSOR_FAULT',
                        'OVERLOAD'
                    )
                    AND clean_final_weight_g IS NULL
                    AND clean_removed_net_weight_g IS NULL
                    AND clean_new_baseline_weight_g IS NULL
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_fullness_shape CHECK (
        result_type <> 'FULLNESS_SAMPLE'
        OR
        (
            fullness_measurement_uid IS NOT NULL
            AND fullness_measurement_status IS NOT NULL
            AND infrared_health IS NOT NULL
            AND infrared_value IS NOT NULL
            AND fullness_measurement_status IN (
                'STABLE',
                'UNSTABLE',
                'TIMEOUT',
                'SENSOR_FAULT',
                'OVERLOAD'
            )
            AND (
                (
                    fullness_measurement_status = 'STABLE'
                    AND fullness_total_weight_g IS NOT NULL
                )
                OR
                (
                    fullness_measurement_status <> 'STABLE'
                    AND fullness_total_weight_g IS NULL
                )
            )
            AND (
                (
                    infrared_health = 'OK'
                    AND infrared_value IN ('CLEAR', 'BLOCKED')
                )
                OR
                (
                    infrared_health IN (
                        'TIMEOUT',
                        'SENSOR_FAULT',
                        'DISCONNECTED',
                        'UNKNOWN'
                    )
                    AND infrared_value = 'UNKNOWN'
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_baseline_shape CHECK (
        result_type <> 'BASELINE_MEASUREMENT'
        OR
        (
            baseline_measurement_uid IS NOT NULL
            AND baseline_measurement_status IS NOT NULL
            AND empty_bag_confirmed IS NOT NULL
            AND baseline_measurement_status IN (
                'STABLE',
                'UNSTABLE',
                'TIMEOUT',
                'SENSOR_FAULT',
                'OVERLOAD'
            )
            AND empty_bag_confirmed = 1
            AND (
                (
                    baseline_measurement_status = 'STABLE'
                    AND baseline_total_weight_g IS NOT NULL
                )
                OR
                (
                    baseline_measurement_status <> 'STABLE'
                    AND baseline_total_weight_g IS NULL
                )
            )
        )
    ),
    CONSTRAINT ck_dev_result_uart CHECK (
        (uart_protocol_major IS NULL AND uart_protocol_minor IS NULL)
        OR
        (
            uart_protocol_major IS NOT NULL
            AND uart_protocol_major >= 0
            AND uart_protocol_minor IS NOT NULL
            AND uart_protocol_minor >= 0
        )
    ),
    CONSTRAINT fk_dev_result_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_command_type
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            command_id,
            command_type
        )
        REFERENCES dev_device_command (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_delivery_command
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            command_id,
            command_type,
            delivery_session_id
        )
        REFERENCES dev_device_command (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            command_type,
            delivery_session_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_reported_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            reported_config_version_no,
            reported_config_content_sha256,
            reported_config_mcu_payload_sha256
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            version_no,
            content_sha256,
            mcu_payload_sha256
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_delivery_frozen_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            delivery_session_id,
            reported_config_version_no,
            reported_config_content_sha256,
            reported_config_mcu_payload_sha256
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            device_config_version_no,
            device_config_content_sha256,
            device_config_mcu_payload_sha256
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            edge_event_id,
            edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_result_delivery_session
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            delivery_session_id
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_result_deployment_time (
        tenant_id,
        organization_id,
        deployment_id,
        created_at,
        id
    ),
    INDEX ix_dev_result_port_time (
        tenant_id,
        organization_id,
        port_id,
        created_at,
        id
    ),
    INDEX ix_dev_result_edge_fk (
        tenant_id,
        organization_id,
        deployment_id,
        edge_event_id,
        edge_event_type
    ),
    INDEX ix_dev_result_command_fk (
        tenant_id,
        organization_id,
        deployment_id,
        command_id,
        command_type,
        delivery_session_id
    ),
    INDEX ix_dev_result_reported_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        reported_config_version_no,
        reported_config_content_sha256,
        reported_config_mcu_payload_sha256
    ),
    INDEX ix_dev_result_delivery_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        delivery_session_id,
        reported_config_version_no,
        reported_config_content_sha256,
        reported_config_mcu_payload_sha256
    ),
    INDEX ix_dev_result_delivery_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        delivery_session_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
