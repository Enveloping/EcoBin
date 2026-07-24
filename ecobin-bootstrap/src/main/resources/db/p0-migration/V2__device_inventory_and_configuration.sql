-- Device inventory, immutable deployment/configuration history, and current
-- runtime projections. No device or configuration instance is seeded here.

CREATE TABLE dev_device_asset (
    id BIGINT NOT NULL AUTO_INCREMENT,
    hardware_sn VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    production_batch VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    expected_port_count SMALLINT NOT NULL,
    lifecycle_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    retired_at DATETIME(3) NULL,
    retirement_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_asset_hardware_sn UNIQUE (hardware_sn),
    CONSTRAINT ck_dev_asset_hardware_sn_nonblank
        CHECK (
            BINARY hardware_sn = BINARY TRIM(hardware_sn)
            AND CHAR_LENGTH(TRIM(hardware_sn)) > 0
        ),
    CONSTRAINT ck_dev_asset_port_count CHECK (expected_port_count BETWEEN 1 AND 6),
    CONSTRAINT ck_dev_asset_lifecycle CHECK (
        lifecycle_status IN (
            'IN_STOCK',
            'ALLOCATED',
            'IN_USE',
            'MAINTENANCE',
            'RETIRED'
        )
    ),
    CONSTRAINT ck_dev_asset_retired_shape CHECK (
        (
            lifecycle_status = 'RETIRED'
            AND retired_at IS NOT NULL
            AND retirement_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(retirement_reason)) > 0
        )
        OR
        (
            lifecycle_status <> 'RETIRED'
            AND retired_at IS NULL
            AND retirement_reason IS NULL
        )
    ),
    CONSTRAINT ck_dev_asset_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_asset_times CHECK (
        updated_at >= created_at
        AND (retired_at IS NULL OR retired_at >= created_at)
    ),
    INDEX ix_dev_asset_lifecycle (lifecycle_status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_device_deployment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    public_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lifecycle_status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_enabled TINYINT NOT NULL,
    commissioned_at DATETIME(3) NULL,
    enabled_at DATETIME(3) NULL,
    ended_at DATETIME(3) NULL,
    end_method VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    end_reason VARCHAR(500) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_deployment_public_code UNIQUE (public_code),
    CONSTRAINT uq_dev_deployment_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_deployment_asset_id UNIQUE (asset_id, id),
    CONSTRAINT uq_dev_deployment_scope_asset_id
        UNIQUE (tenant_id, organization_id, asset_id, id),
    CONSTRAINT ck_dev_deployment_public_code CHECK (
        public_code REGEXP '^Dp_[A-Za-z0-9_-]{6,61}$'
    ),
    CONSTRAINT ck_dev_deployment_lifecycle CHECK (
        lifecycle_status IN (
            'PENDING_INSTALL',
            'COMMISSIONING',
            'ENABLED',
            'MAINTENANCE',
            'DISABLED',
            'ENDED'
        )
    ),
    CONSTRAINT ck_dev_deployment_business_enabled CHECK (business_enabled IN (0, 1)),
    CONSTRAINT ck_dev_deployment_ended_shape CHECK (
        (
            lifecycle_status = 'ENDED'
            AND business_enabled = 0
            AND ended_at IS NOT NULL
            AND end_method IS NOT NULL
            AND CHAR_LENGTH(TRIM(end_method)) > 0
        )
        OR
        (
            lifecycle_status <> 'ENDED'
            AND ended_at IS NULL
            AND end_method IS NULL
            AND end_reason IS NULL
        )
    ),
    CONSTRAINT ck_dev_deployment_times CHECK (
        updated_at >= created_at
        AND (commissioned_at IS NULL OR commissioned_at >= created_at)
        AND (enabled_at IS NULL OR enabled_at >= created_at)
        AND (ended_at IS NULL OR ended_at >= created_at)
        AND (
            end_reason IS NULL
            OR CHAR_LENGTH(TRIM(end_reason)) > 0
        )
    ),
    CONSTRAINT ck_dev_deployment_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_deployment_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_deployment_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_deployment_org_list (
        tenant_id,
        organization_id,
        lifecycle_status,
        business_enabled,
        id
    ),
    INDEX ix_dev_deployment_asset_history (asset_id, created_at, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_asset_active_deployment (
    asset_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    acquired_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT uq_dev_active_deployment UNIQUE (deployment_id),
    CONSTRAINT uq_dev_active_asset_scope_deployment
        UNIQUE (asset_id, tenant_id, organization_id, deployment_id),
    CONSTRAINT fk_dev_active_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_active_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_active_asset_deployment
        FOREIGN KEY (asset_id, deployment_id)
        REFERENCES dev_device_deployment (asset_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_active_deployment_scope
        FOREIGN KEY (tenant_id, organization_id, asset_id, deployment_id)
        REFERENCES dev_device_deployment (
            tenant_id,
            organization_id,
            asset_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_active_scope (
        tenant_id,
        organization_id,
        deployment_id,
        asset_id
    ),
    INDEX ix_dev_active_asset_deployment_fk (
        asset_id,
        deployment_id
    ),
    INDEX ix_dev_active_scope_deployment_fk (
        tenant_id,
        organization_id,
        asset_id,
        deployment_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_port (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_no SMALLINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_port_deployment_no UNIQUE (deployment_id, port_no),
    CONSTRAINT uq_dev_port_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_port_scope_deployment_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT ck_dev_port_no CHECK (port_no BETWEEN 1 AND 6),
    CONSTRAINT fk_dev_port_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_port_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_port_scope_deployment (
        tenant_id,
        organization_id,
        deployment_id,
        port_no
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_config_version (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    version_no BIGINT NOT NULL,
    schema_version INT NOT NULL,
    device_display_name VARCHAR(100) NOT NULL,
    location_address VARCHAR(500) NULL,
    latitude DECIMAL(10, 7) NULL,
    longitude DECIMAL(10, 7) NULL,
    edge_heartbeat_interval_ms BIGINT NOT NULL,
    edge_heartbeat_miss_threshold INT NOT NULL,
    mcu_heartbeat_interval_ms BIGINT NOT NULL,
    mcu_heartbeat_miss_threshold INT NOT NULL,
    door_close_retry_limit INT NOT NULL,
    continue_delivery_wait_ms BIGINT NOT NULL,
    negative_weight_threshold_g BIGINT NOT NULL DEFAULT 500,
    delivery_auto_close_ms BIGINT NOT NULL,
    weight_measurement_timeout_ms BIGINT NOT NULL,
    clean_solenoid_pulse_ms BIGINT NOT NULL,
    smoke_monitoring_enabled TINYINT NOT NULL,
    content_sha256 BINARY(32) NOT NULL,
    mcu_payload_sha256 BINARY(32) NOT NULL,
    publication_source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    published_by_staff_account_id BIGINT NULL,
    published_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_config_deployment_version UNIQUE (deployment_id, version_no),
    CONSTRAINT uq_dev_config_reported_snapshot UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        version_no,
        content_sha256,
        mcu_payload_sha256
    ),
    CONSTRAINT uq_dev_config_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_config_scope_deployment_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT uq_dev_config_expected_snapshot UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        id,
        version_no,
        content_sha256
    ),
    CONSTRAINT uq_dev_config_expected_mcu_snapshot UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        id,
        version_no,
        content_sha256,
        mcu_payload_sha256
    ),
    CONSTRAINT uq_dev_config_session_snapshot UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        id,
        version_no,
        content_sha256,
        mcu_payload_sha256,
        negative_weight_threshold_g,
        continue_delivery_wait_ms
    ),
    CONSTRAINT ck_dev_config_versions CHECK (
        version_no BETWEEN 1 AND 9007199254740991
        AND schema_version > 0
    ),
    CONSTRAINT ck_dev_config_coordinates CHECK (
        (latitude IS NULL AND longitude IS NULL)
        OR
        (
            latitude BETWEEN -90.0000000 AND 90.0000000
            AND longitude BETWEEN -180.0000000 AND 180.0000000
        )
    ),
    CONSTRAINT ck_dev_config_device_parameters CHECK (
        edge_heartbeat_interval_ms BETWEEN 1 AND 4294967295
        AND edge_heartbeat_miss_threshold BETWEEN 1 AND 4294967295
        AND mcu_heartbeat_interval_ms BETWEEN 1 AND 4294967295
        AND mcu_heartbeat_miss_threshold BETWEEN 1 AND 4294967295
        AND door_close_retry_limit BETWEEN 0 AND 4294967295
        AND continue_delivery_wait_ms = 30000
        AND negative_weight_threshold_g BETWEEN 1 AND 4294967295
        AND delivery_auto_close_ms BETWEEN 1000 AND 4294967295
        AND weight_measurement_timeout_ms BETWEEN 1 AND 4294967295
        AND clean_solenoid_pulse_ms BETWEEN 1 AND 4294967295
        AND smoke_monitoring_enabled IN (0, 1)
    ),
    CONSTRAINT ck_dev_config_publisher_shape CHECK (
        (
            publication_source = 'STAFF'
            AND published_by_staff_account_id IS NOT NULL
        )
        OR
        (
            publication_source = 'SYSTEM'
            AND published_by_staff_account_id IS NULL
        )
    ),
    CONSTRAINT ck_dev_config_times
        CHECK (published_at >= created_at),
    CONSTRAINT fk_dev_config_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_config_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_config_publisher
        FOREIGN KEY (tenant_id, published_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_config_scope_version (
        tenant_id,
        organization_id,
        deployment_id,
        version_no DESC
    ),
    INDEX ix_dev_config_publisher_fk (
        tenant_id,
        published_by_staff_account_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_port_config_snapshot (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    config_version_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    display_name VARCHAR(32) NOT NULL,
    business_enabled TINYINT NOT NULL,
    unit_price_yuan_per_kg DECIMAL(15, 4) NOT NULL,
    fullness_mode VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    configured_full_weight_g BIGINT NOT NULL,
    delivery_settle_delay_ms BIGINT NOT NULL,
    fullness_settle_wait_ms BIGINT NOT NULL,
    fullness_confirmation_wait_ms BIGINT NOT NULL,
    door_auto_close_timeout_ms BIGINT NOT NULL,
    weight_stable_window_ms BIGINT NOT NULL,
    weight_maximum_fluctuation_g BIGINT NOT NULL,
    weight_required_sample_count INT NOT NULL,
    weight_measurement_timeout_ms BIGINT NOT NULL,
    weight_minimum_g BIGINT NOT NULL,
    weight_maximum_g BIGINT NOT NULL,
    calibration_version BIGINT NOT NULL,
    infrared_sample_timeout_ms BIGINT NOT NULL,
    delivery_door_operation_timeout_ms BIGINT NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_port_config_version_port
        UNIQUE (config_version_id, port_id),
    CONSTRAINT uq_dev_port_config_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_port_config_session_ref
        UNIQUE (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            port_id,
            id,
            unit_price_yuan_per_kg
        ),
    CONSTRAINT ck_dev_port_config_enabled CHECK (business_enabled IN (0, 1)),
    CONSTRAINT ck_dev_port_config_price CHECK (
        unit_price_yuan_per_kg BETWEEN 0.0001 AND 429496.7295
    ),
    CONSTRAINT ck_dev_port_config_fullness_mode CHECK (
        fullness_mode IN (
            'INFRARED_ONLY',
            'WEIGHT_ONLY',
            'INFRARED_OR_WEIGHT'
        )
    ),
    CONSTRAINT ck_dev_port_config_parameters CHECK (
        BINARY display_name = BINARY TRIM(display_name)
        AND CHAR_LENGTH(display_name) BETWEEN 1 AND 32
        AND configured_full_weight_g BETWEEN 1 AND 4294967295
        AND delivery_settle_delay_ms BETWEEN 0 AND 4294967295
        AND fullness_settle_wait_ms BETWEEN 0 AND 4294967295
        AND fullness_confirmation_wait_ms BETWEEN 0 AND 4294967295
        AND door_auto_close_timeout_ms BETWEEN 1000 AND 4294967295
        AND weight_stable_window_ms BETWEEN 1 AND 4294967295
        AND weight_maximum_fluctuation_g BETWEEN 0 AND 4294967295
        AND weight_required_sample_count BETWEEN 1 AND 65535
        AND weight_measurement_timeout_ms BETWEEN 1 AND 4294967295
        AND weight_minimum_g BETWEEN -2147483648 AND 2147483647
        AND weight_maximum_g BETWEEN 1 AND 2147483647
        AND weight_minimum_g < weight_maximum_g
        AND calibration_version BETWEEN 0 AND 4294967295
        AND infrared_sample_timeout_ms BETWEEN 1 AND 4294967295
        AND delivery_door_operation_timeout_ms BETWEEN 1 AND 4294967295
    ),
    CONSTRAINT fk_dev_port_config_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_port_config_version
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_port_config_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_port_config_scope (
        tenant_id,
        organization_id,
        deployment_id,
        port_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_config_application (
    id BIGINT NOT NULL AUTO_INCREMENT,
    application_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    config_version_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reported_version_no BIGINT NULL,
    reported_content_sha256 BINARY(32) NULL,
    reported_mcu_payload_sha256 BINARY(32) NULL,
    edge_persisted_at DATETIME(3) NULL,
    mcu_synced_at DATETIME(3) NULL,
    applied_at DATETIME(3) NULL,
    last_failure_at DATETIME(3) NULL,
    last_failure_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_config_app_uid UNIQUE (application_uid),
    CONSTRAINT uq_dev_config_app_version UNIQUE (config_version_id),
    CONSTRAINT uq_dev_config_app_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_dev_config_app_scope_deployment_id
        UNIQUE (tenant_id, organization_id, deployment_id, id),
    CONSTRAINT ck_dev_config_app_uid_v4 CHECK (
        application_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_config_app_status CHECK (
        status IN ('PENDING', 'EDGE_SAVED', 'APPLIED', 'FAILED')
    ),
    CONSTRAINT ck_dev_config_app_reported_shape CHECK (
        (
            reported_version_no IS NULL
            AND reported_content_sha256 IS NULL
            AND reported_mcu_payload_sha256 IS NULL
        )
        OR
        (
            reported_version_no IS NOT NULL
            AND reported_content_sha256 IS NOT NULL
            AND reported_mcu_payload_sha256 IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_config_app_failure_shape CHECK (
        (
            last_failure_at IS NULL
            AND last_failure_code IS NULL
        )
        OR
        (
            last_failure_at IS NOT NULL
            AND last_failure_code IS NOT NULL
            AND CHAR_LENGTH(TRIM(last_failure_code)) > 0
        )
    ),
    CONSTRAINT ck_dev_config_app_state_shape CHECK (
        (
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
        )
    ),
    CONSTRAINT ck_dev_config_app_times CHECK (
        updated_at >= created_at
        AND (edge_persisted_at IS NULL OR edge_persisted_at >= created_at)
        AND (
            mcu_synced_at IS NULL
            OR (
                edge_persisted_at IS NOT NULL
                AND mcu_synced_at >= edge_persisted_at
            )
        )
        AND (
            applied_at IS NULL
            OR (
                mcu_synced_at IS NOT NULL
                AND applied_at >= mcu_synced_at
            )
        )
        AND (last_failure_at IS NULL OR last_failure_at >= created_at)
    ),
    CONSTRAINT ck_dev_config_app_lock_version CHECK (lock_version >= 0),
    CONSTRAINT fk_dev_config_app_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_config_app_expected
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            reported_version_no,
            reported_content_sha256
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            version_no,
            content_sha256
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_config_app_reported_mcu
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id,
            reported_version_no,
            reported_content_sha256,
            reported_mcu_payload_sha256
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            version_no,
            content_sha256,
            mcu_payload_sha256
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_config_app_version
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            config_version_id
        )
        REFERENCES dev_config_version (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_config_app_scope_state (
        tenant_id,
        organization_id,
        deployment_id,
        status,
        updated_at,
        id
    ),
    INDEX ix_dev_config_app_expected_fk (
        tenant_id,
        organization_id,
        deployment_id,
        config_version_id,
        reported_version_no,
        reported_content_sha256,
        reported_mcu_payload_sha256
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_deployment_runtime_state (
    deployment_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    edge_connection_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mcu_link_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    safety_status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    aggregate_weight_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    camera_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    local_storage_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    clock_sync_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    edge_boot_id BIGINT NULL,
    edge_software_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    mcu_firmware_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    mcu_boot_id BIGINT NULL,
    uart_state VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL,
    uart_protocol_major INT NULL,
    uart_protocol_minor INT NULL,
    capability_bitmap_hex CHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_mcu_reset_reason VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    applied_config_version_no BIGINT NULL,
    applied_config_content_sha256 BINARY(32) NULL,
    applied_mcu_payload_sha256 BINARY(32) NULL,
    local_storage_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    clock_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    pending_reliable_event_count BIGINT NULL,
    last_heartbeat_at DATETIME(3) NULL,
    last_device_event_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (deployment_id),
    CONSTRAINT uq_dev_runtime_scope_id
        UNIQUE (tenant_id, organization_id, deployment_id),
    CONSTRAINT ck_dev_runtime_edge CHECK (
        edge_connection_status IN ('ONLINE', 'OFFLINE', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_runtime_mcu CHECK (
        mcu_link_status IN ('ONLINE', 'OFFLINE', 'INCOMPATIBLE', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_runtime_safety CHECK (
        safety_status IN ('SAFE', 'OPERATION_BLOCKED', 'SAFETY_BLOCKED', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_runtime_health CHECK (
        aggregate_weight_health IN ('OK', 'DEGRADED', 'FAILED', 'UNKNOWN')
        AND camera_health IN ('OK', 'DEGRADED', 'FAILED', 'UNKNOWN')
        AND local_storage_health IN ('OK', 'DEGRADED', 'FAILED', 'UNKNOWN')
        AND clock_sync_health IN ('OK', 'DEGRADED', 'FAILED', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_runtime_uart_shape CHECK (
        (
            uart_state IS NULL
            AND
            uart_protocol_major IS NULL
            AND uart_protocol_minor IS NULL
            AND capability_bitmap_hex IS NULL
        )
        OR
        (
            uart_state IS NOT NULL
            AND uart_state IN (
                'DISCONNECTED',
                'NEGOTIATING',
                'READY',
                'INCOMPATIBLE',
                'FAULT'
            )
            AND capability_bitmap_hex IS NOT NULL
            AND capability_bitmap_hex REGEXP '^[0-9a-f]{16}$'
            AND (
                (
                    uart_protocol_major IS NULL
                    AND uart_protocol_minor IS NULL
                )
                OR
                (
                    uart_protocol_major IS NOT NULL
                    AND uart_protocol_minor IS NOT NULL
                    AND uart_protocol_major BETWEEN 0 AND 255
                    AND uart_protocol_minor BETWEEN 0 AND 255
                )
            )
        )
    ),
    CONSTRAINT ck_dev_runtime_edge_identity CHECK (
        (
            edge_boot_id IS NULL
            AND edge_software_version IS NULL
        )
        OR
        (
            edge_boot_id IS NOT NULL
            AND edge_boot_id > 0
            AND edge_software_version IS NOT NULL
            AND CHAR_LENGTH(TRIM(edge_software_version)) > 0
        )
    ),
    CONSTRAINT ck_dev_runtime_snapshot_projection CHECK (
        (
            local_storage_state IS NULL
            AND clock_state IS NULL
            AND pending_reliable_event_count IS NULL
        )
        OR
        (
            local_storage_state IS NOT NULL
            AND clock_state IS NOT NULL
            AND pending_reliable_event_count IS NOT NULL
            AND local_storage_state IN (
                'HEALTHY',
                'DEGRADED',
                'READ_ONLY',
                'FULL',
                'CORRUPT'
            )
            AND clock_state IN ('SYNCED', 'ESTIMATED', 'UNAVAILABLE')
            AND pending_reliable_event_count BETWEEN 0 AND 4294967295
        )
    ),
    CONSTRAINT ck_dev_runtime_mcu_version CHECK (
        mcu_firmware_version IS NULL
        OR CHAR_LENGTH(TRIM(mcu_firmware_version)) > 0
    ),
    CONSTRAINT ck_dev_runtime_mcu_boot
        CHECK (mcu_boot_id IS NULL OR mcu_boot_id > 0),
    CONSTRAINT ck_dev_runtime_applied_config_shape CHECK (
        (
            applied_config_version_no IS NULL
            AND applied_config_content_sha256 IS NULL
            AND applied_mcu_payload_sha256 IS NULL
        )
        OR
        (
            applied_config_version_no IS NOT NULL
            AND applied_config_version_no > 0
            AND applied_config_content_sha256 IS NOT NULL
            AND applied_mcu_payload_sha256 IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_runtime_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_runtime_times CHECK (
        updated_at >= created_at
        AND (last_heartbeat_at IS NULL OR last_heartbeat_at >= created_at)
        AND (last_device_event_at IS NULL OR last_device_event_at >= created_at)
    ),
    CONSTRAINT fk_dev_runtime_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_runtime_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_runtime_applied_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            applied_config_version_no,
            applied_config_content_sha256,
            applied_mcu_payload_sha256
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
    INDEX ix_dev_runtime_org_blocked (
        tenant_id,
        organization_id,
        safety_status,
        mcu_link_status,
        deployment_id
    ),
    INDEX ix_dev_runtime_offline (
        edge_connection_status,
        last_heartbeat_at,
        deployment_id
    ),
    INDEX ix_dev_runtime_applied_config_fk (
        tenant_id,
        organization_id,
        deployment_id,
        applied_config_version_no,
        applied_config_content_sha256,
        applied_mcu_payload_sha256
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE dev_port_runtime_state (
    port_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    delivery_door_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_door_actuator_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_door_contact_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    clean_lock_power_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    clean_solenoid_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    clean_door_inferred_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    clean_door_state_basis VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    weight_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    infrared_value VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    infrared_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    smoke_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    smoke_sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    safety_status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    pending_delivery_result_session_id BIGINT NULL,
    last_observed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (port_id),
    CONSTRAINT uq_dev_port_runtime_scope_id
        UNIQUE (tenant_id, organization_id, deployment_id, port_id),
    CONSTRAINT ck_dev_port_runtime_door CHECK (
        delivery_door_state IN (
            'CLOSED',
            'OPENING',
            'OPEN',
            'CLOSING',
            'JAMMED',
            'UNKNOWN'
        )
        AND delivery_door_actuator_health IN (
            'OK',
            'TIMEOUT',
            'ACTUATOR_FAULT',
            'SWITCH_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND delivery_door_contact_state IN ('OPEN', 'CLOSED', 'UNAVAILABLE', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_port_runtime_clean_door CHECK (
        clean_door_state_basis = 'INFERRED_FROM_LOCK_POWER'
        AND clean_lock_power_state IN ('ENERGIZED', 'DEENERGIZED', 'UNKNOWN')
        AND clean_solenoid_health IN (
            'OK',
            'DRIVER_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND clean_door_inferred_state IN ('OPEN', 'CLOSED', 'UNKNOWN')
        AND (
            (
                clean_solenoid_health = 'OK'
                AND clean_lock_power_state = 'ENERGIZED'
                AND clean_door_inferred_state = 'OPEN'
            )
            OR
            (
                clean_solenoid_health = 'OK'
                AND clean_lock_power_state = 'DEENERGIZED'
                AND clean_door_inferred_state = 'CLOSED'
            )
            OR
            (
                clean_solenoid_health = 'OK'
                AND clean_lock_power_state = 'UNKNOWN'
                AND clean_door_inferred_state = 'UNKNOWN'
            )
            OR
            (
                clean_solenoid_health IN (
                    'DRIVER_FAULT',
                    'DISCONNECTED',
                    'UNKNOWN'
                )
                AND clean_door_inferred_state = 'UNKNOWN'
            )
        )
    ),
    CONSTRAINT ck_dev_port_runtime_sensors CHECK (
        weight_sensor_health IN (
            'OK',
            'TIMEOUT',
            'SENSOR_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND infrared_value IN ('CLEAR', 'BLOCKED', 'UNKNOWN')
        AND infrared_sensor_health IN (
            'OK',
            'TIMEOUT',
            'SENSOR_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND (
            (infrared_sensor_health = 'OK' AND infrared_value IN ('CLEAR', 'BLOCKED'))
            OR
            (infrared_sensor_health <> 'OK' AND infrared_value = 'UNKNOWN')
        )
        AND smoke_state IN ('NORMAL', 'ALARM', 'UNKNOWN')
        AND smoke_sensor_health IN (
            'OK',
            'SENSOR_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND (
            (smoke_sensor_health = 'OK' AND smoke_state IN ('NORMAL', 'ALARM'))
            OR
            (smoke_sensor_health <> 'OK' AND smoke_state = 'UNKNOWN')
        )
    ),
    CONSTRAINT ck_dev_port_runtime_safety CHECK (
        safety_status IN ('SAFE', 'OPERATION_BLOCKED', 'SAFETY_BLOCKED', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_port_runtime_lock_version CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_port_runtime_times CHECK (
        updated_at >= created_at
        AND (last_observed_at IS NULL OR last_observed_at >= created_at)
    ),
    CONSTRAINT fk_dev_port_runtime_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_port_runtime_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_port_runtime_deployment (
        tenant_id,
        organization_id,
        deployment_id,
        safety_status,
        port_id
    ),
    INDEX ix_dev_port_runtime_pending (
        tenant_id,
        organization_id,
        deployment_id,
        pending_delivery_result_session_id
    ),
    INDEX ix_dev_port_runtime_pending_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        pending_delivery_result_session_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
