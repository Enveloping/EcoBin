-- Fullness is now an edge-owned state transition, not a backend-issued sample.
-- Only an explicit FULL fact for the currently installed bag blocks delivery.

UPDATE ops_reliable_task
SET state = 'CANCELLED',
    next_run_at = NULL,
    lease_token = NULL,
    lease_worker = NULL,
    lease_until = NULL,
    completed_at = UTC_TIMESTAMP(3),
    blocked_reason_code = NULL,
    blocked_diagnostic = NULL,
    handled_wake_version = wake_version,
    lock_version = lock_version + 1,
    updated_at = UTC_TIMESTAMP(3)
WHERE task_type = 'SAMPLE_FULLNESS'
  AND state IN ('PENDING', 'BLOCKED');

ALTER TABLE rec_fullness_detection
    DROP CHECK ck_rec_fullness_detection_state,
    ADD CONSTRAINT ck_rec_fullness_detection_state CHECK (
        (
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
                    trigger_type = 'CLEAN_COMPLETE'
                    AND baseline_state_snapshot IN ('INVALID', 'MISSING')
                    AND failure_code = 'WEIGHT_BASELINE_UNAVAILABLE'
                    AND initial_sample_id IS NULL
                    AND initial_sample_conclusion IS NULL
                    AND terminal_sample_id IS NULL
                    AND terminal_sample_conclusion IS NULL
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
             (terminal_sample_conclusion IS NULL))
    );

UPDATE rec_fullness_detection
SET status = 'RETIRED',
    final_result = NULL,
    failure_code = 'EDGE_REPORTED_STATE_MIGRATION',
    disposition = 'STALE_IGNORED',
    terminal_sample_id = NULL,
    terminal_sample_conclusion = NULL,
    next_sample_at = NULL,
    completed_at = UTC_TIMESTAMP(3),
    lock_version = lock_version + 1,
    updated_at = UTC_TIMESTAMP(3)
WHERE status IN ('PENDING_INITIAL_SAMPLE', 'WAITING_RECHECK');

UPDATE rec_port_capacity_state
SET detection_gate = 'READY',
    current_detection_id = NULL,
    confirmed_fullness_state = CASE
        WHEN confirmed_fullness_state = 'FULL' THEN 'FULL'
        ELSE 'NOT_FULL'
    END,
    current_fullness_event_id = CASE
        WHEN confirmed_fullness_state = 'FULL'
        THEN current_fullness_event_id
        ELSE NULL
    END,
    lock_version = lock_version + 1,
    updated_at = UTC_TIMESTAMP(3)
WHERE detection_gate <> 'READY'
   OR current_detection_id IS NOT NULL
   OR confirmed_fullness_state <> 'FULL';

ALTER TABLE dev_edge_event
    DROP CHECK ck_dev_edge_event_type_class,
    DROP CHECK ck_dev_edge_event_target_type,
    DROP CHECK ck_dev_edge_event_target_pair,
    ADD CONSTRAINT ck_dev_edge_event_type_class CHECK (
        (
            event_type IN (
                'DEVICE_COMMAND_OBSERVED',
                'CONFIGURATION_PROGRESS',
                'DELIVERY_COMPLETE',
                'CLEAN_COMPLETE',
                'FULLNESS_SAMPLE_COMPLETE',
                'FULLNESS_STATE_CHANGED',
                'BASELINE_MEASUREMENT_COMPLETE',
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND delivery_class = 'RELIABLE_FACT'
        )
        OR (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND delivery_class = 'CONTROL_RECEIPT'
        )
        OR (
            event_type = 'DEVICE_RUNTIME_SNAPSHOT'
            AND delivery_class = 'TELEMETRY_SNAPSHOT'
        )
    ),
    ADD CONSTRAINT ck_dev_edge_event_target_type CHECK (
        target_type IN (
            'DEVICE_COMMAND',
            'CONFIGURATION_APPLICATION',
            'DELIVERY_SESSION',
            'CLEAN_OPERATION',
            'FULLNESS_DETECTION',
            'PORT_FULLNESS_STATE',
            'BASELINE_MEASUREMENT',
            'DEVICE_DEPLOYMENT',
            'BUSINESS_CONFIRMATION',
            'PHOTO_GRANT_REQUEST'
        )
    ),
    ADD CONSTRAINT ck_dev_edge_event_target_pair CHECK (
        (event_type = 'DEVICE_COMMAND_OBSERVED'
            AND target_type = 'DEVICE_COMMAND')
        OR (event_type = 'CONFIGURATION_PROGRESS'
            AND target_type = 'CONFIGURATION_APPLICATION')
        OR (event_type = 'DELIVERY_COMPLETE'
            AND target_type = 'DELIVERY_SESSION')
        OR (event_type = 'CLEAN_COMPLETE'
            AND target_type = 'CLEAN_OPERATION')
        OR (event_type = 'FULLNESS_SAMPLE_COMPLETE'
            AND target_type = 'FULLNESS_DETECTION')
        OR (event_type = 'FULLNESS_STATE_CHANGED'
            AND target_type = 'PORT_FULLNESS_STATE')
        OR (event_type = 'BASELINE_MEASUREMENT_COMPLETE'
            AND target_type = 'BASELINE_MEASUREMENT')
        OR (
            event_type IN (
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'DEVICE_RUNTIME_SNAPSHOT'
            )
            AND target_type = 'DEVICE_DEPLOYMENT'
        )
        OR (
            event_type IN (
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND target_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        )
        OR (event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND target_type = 'BUSINESS_CONFIRMATION')
    );

CREATE TABLE dev_fullness_state_fact (
    id BIGINT NOT NULL AUTO_INCREMENT,
    state_change_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    reported_edge_event_type VARCHAR(48)
        CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS ('FULLNESS_STATE_CHANGED') STORED,
    edge_event_sequence BIGINT NOT NULL,
    bag_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reported_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_work_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_work_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fullness_mode VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fullness_sensor_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fullness_sensor_value VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    confirmation_basis VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    measurement_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    measurement_status VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    total_weight_g BIGINT NOT NULL,
    measurement_elapsed_ms BIGINT NOT NULL,
    sample_count INT NOT NULL,
    calibration_version BIGINT NOT NULL,
    sensor_health VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NULL,
    mcu_boot_id BIGINT NOT NULL,
    mcu_event_sequence BIGINT NOT NULL,
    baseline_weight_g BIGINT NULL,
    configured_full_weight_g BIGINT NOT NULL,
    fullness_percent_hundredths BIGINT NULL,
    weight_full TINYINT NULL,
    reported_config_version_no BIGINT NOT NULL,
    reported_config_content_sha256 BINARY(32) NOT NULL,
    reported_config_mcu_payload_sha256 BINARY(32) NOT NULL,
    device_occurred_at DATETIME(3) NOT NULL,
    backend_received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_fullness_state_uid UNIQUE (state_change_uid),
    CONSTRAINT uq_dev_fullness_state_edge UNIQUE (edge_event_id),
    CONSTRAINT uq_dev_fullness_state_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_dev_fullness_state_uid CHECK (
        state_change_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND bag_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND source_work_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND measurement_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_fullness_state_values CHECK (
        edge_event_sequence > 0
        AND reported_state IN ('FULL', 'NOT_FULL')
        AND source_work_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        AND fullness_mode IN (
            'SENSOR_ONLY', 'WEIGHT_ONLY', 'SENSOR_OR_WEIGHT'
        )
        AND fullness_sensor_kind IN ('ULTRASONIC', 'DIGITAL_INFRARED')
        AND fullness_sensor_value IN ('CLEAR', 'BLOCKED', 'NOT_SAMPLED')
        AND confirmation_basis IN (
            'FIXED_FRAME_CACHED_FINAL_OBSERVATION',
            'MCU_INDEPENDENT_RECHECK'
        )
        AND measurement_status = 'STABLE'
        AND total_weight_g BETWEEN -2147483648 AND 2147483647
        AND measurement_elapsed_ms BETWEEN 0 AND 4294967295
        AND sample_count BETWEEN 1 AND 65535
        AND calibration_version BETWEEN 0 AND 4294967295
        AND sensor_health = 'OK'
        AND fault_code IS NULL
        AND mcu_boot_id BETWEEN 1 AND 9007199254740991
        AND mcu_event_sequence BETWEEN 1 AND 4294967295
        AND configured_full_weight_g BETWEEN 1 AND 4294967295
        AND reported_config_version_no BETWEEN 1 AND 9007199254740991
        AND (
            (baseline_weight_g IS NULL
                AND fullness_percent_hundredths IS NULL
                AND weight_full IS NULL)
            OR (baseline_weight_g IS NOT NULL
                AND fullness_percent_hundredths IS NOT NULL
                AND fullness_percent_hundredths >= 0
                AND weight_full IN (0, 1))
        )
    ),
    CONSTRAINT fk_dev_fullness_state_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fullness_state_edge
        FOREIGN KEY (
            tenant_id, organization_id, deployment_id,
            edge_event_id, reported_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id, organization_id, deployment_id,
            id, event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_fullness_state_port_sequence (
        tenant_id, organization_id, deployment_id,
        port_id, edge_event_sequence
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_port_capacity_state
    ADD COLUMN current_bag_id BIGINT NULL
        AFTER current_fullness_event_id,
    ADD COLUMN current_fullness_state_change_id BIGINT NULL
        AFTER current_bag_id,
    ADD COLUMN last_fullness_edge_event_id BIGINT NULL
        AFTER current_fullness_state_change_id,
    ADD COLUMN last_fullness_edge_event_sequence BIGINT NULL
        AFTER last_fullness_edge_event_id,
    ADD COLUMN last_fullness_reported_at DATETIME(3) NULL
        AFTER last_fullness_edge_event_sequence;

UPDATE rec_port_capacity_state capacity
LEFT JOIN rec_bag_current_occupancy occupancy
  ON occupancy.tenant_id = capacity.tenant_id
 AND occupancy.organization_id = capacity.organization_id
 AND occupancy.port_id = capacity.port_id
 AND occupancy.occupancy_type = 'PORT_BOUND'
SET capacity.current_bag_id = occupancy.bag_id;

CREATE TABLE rec_fullness_state_change (
    id BIGINT NOT NULL AUTO_INCREMENT,
    state_change_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    bag_id BIGINT NOT NULL,
    reported_bag_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    device_state_fact_id BIGINT NOT NULL,
    edge_event_id BIGINT NOT NULL,
    edge_event_sequence BIGINT NOT NULL,
    source_work_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_work_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    delivery_order_id BIGINT NULL,
    clean_record_id BIGINT NULL,
    reported_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    disposition VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    device_occurred_at DATETIME(3) NOT NULL,
    backend_received_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_fullness_state_uid UNIQUE (state_change_uid),
    CONSTRAINT uq_rec_fullness_state_fact UNIQUE (device_state_fact_id),
    CONSTRAINT uq_rec_fullness_state_edge UNIQUE (edge_event_id),
    CONSTRAINT uq_rec_fullness_state_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_fullness_state_values CHECK (
        reported_state IN ('FULL', 'NOT_FULL')
        AND disposition IN (
            'APPLIED', 'NO_STATE_CHANGE',
            'STALE_BAG', 'STALE_SEQUENCE'
        )
        AND edge_event_sequence > 0
        AND (
            (source_work_type = 'DELIVERY_SESSION'
                AND delivery_order_id IS NOT NULL
                AND clean_record_id IS NULL)
            OR (source_work_type = 'CLEAN_OPERATION'
                AND delivery_order_id IS NULL
                AND clean_record_id IS NOT NULL)
        )
    ),
    CONSTRAINT fk_rec_fullness_state_port
        FOREIGN KEY (tenant_id, organization_id, deployment_id, port_id)
        REFERENCES dev_port (tenant_id, organization_id, deployment_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_state_bag
        FOREIGN KEY (tenant_id, organization_id, bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_state_device_fact
        FOREIGN KEY (tenant_id, organization_id, device_state_fact_id)
        REFERENCES dev_fullness_state_fact (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_state_delivery
        FOREIGN KEY (tenant_id, organization_id, delivery_order_id)
        REFERENCES rec_delivery_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_fullness_state_clean
        FOREIGN KEY (tenant_id, organization_id, clean_record_id)
        REFERENCES rec_clean_record (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_fullness_state_port_sequence (
        tenant_id, organization_id, deployment_id,
        port_id, edge_event_sequence
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_port_capacity_state
    ADD CONSTRAINT fk_rec_capacity_current_bag
        FOREIGN KEY (tenant_id, organization_id, current_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_capacity_current_state_change
        FOREIGN KEY (
            tenant_id, organization_id,
            current_fullness_state_change_id
        )
        REFERENCES rec_fullness_state_change (
            tenant_id, organization_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_capacity_last_state_edge
        FOREIGN KEY (
            tenant_id, organization_id, deployment_id,
            last_fullness_edge_event_id
        )
        REFERENCES dev_edge_event (
            tenant_id, organization_id, deployment_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_capacity_current_bag (
        tenant_id, organization_id, current_bag_id
    ),
    ADD INDEX ix_rec_capacity_current_state_change (
        tenant_id, organization_id, current_fullness_state_change_id
    );
