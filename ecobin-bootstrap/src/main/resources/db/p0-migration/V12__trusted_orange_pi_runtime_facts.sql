-- Persist the trusted Orange Pi runtime source and the projections required
-- for backend eligibility decisions. MCU/UART values remain diagnostics only.

ALTER TABLE dev_deployment_runtime_state
    ADD COLUMN trusted_runtime_edge_event_id BIGINT NULL
        AFTER pending_reliable_event_count,
    ADD COLUMN trusted_runtime_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER trusted_runtime_edge_event_id,
    ADD COLUMN trusted_runtime_sequence BIGINT NULL
        AFTER trusted_runtime_edge_event_type,
    ADD COLUMN trusted_runtime_received_at DATETIME(3) NULL
        AFTER trusted_runtime_sequence,
    ADD COLUMN orange_pi_reported_config_version_no BIGINT NULL
        AFTER trusted_runtime_received_at,
    ADD COLUMN orange_pi_reported_config_content_sha256 BINARY(32) NULL
        AFTER orange_pi_reported_config_version_no,
    ADD COLUMN orange_pi_reported_config_mcu_payload_sha256 BINARY(32) NULL
        AFTER orange_pi_reported_config_content_sha256,
    ADD COLUMN safety_projection_edge_event_id BIGINT NULL
        AFTER trusted_runtime_received_at,
    ADD COLUMN safety_projection_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER safety_projection_edge_event_id,
    ADD COLUMN safety_projection_sequence BIGINT NULL
        AFTER safety_projection_edge_event_type,
    ADD CONSTRAINT ck_dev_runtime_trusted_source CHECK (
        (
            trusted_runtime_edge_event_id IS NULL
            AND trusted_runtime_edge_event_type IS NULL
            AND trusted_runtime_sequence IS NULL
            AND trusted_runtime_received_at IS NULL
        )
        OR
        (
            trusted_runtime_edge_event_id IS NOT NULL
            AND trusted_runtime_edge_event_type =
                'DEVICE_RUNTIME_SNAPSHOT'
            AND trusted_runtime_sequence > 0
            AND trusted_runtime_received_at IS NOT NULL
        )
    ),
    ADD CONSTRAINT ck_dev_runtime_orange_pi_config CHECK (
        (
            orange_pi_reported_config_version_no IS NULL
            AND orange_pi_reported_config_content_sha256 IS NULL
            AND orange_pi_reported_config_mcu_payload_sha256 IS NULL
        )
        OR
        (
            orange_pi_reported_config_version_no > 0
            AND orange_pi_reported_config_content_sha256 IS NOT NULL
            AND orange_pi_reported_config_mcu_payload_sha256 IS NOT NULL
        )
    ),
    ADD CONSTRAINT fk_dev_runtime_trusted_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            trusted_runtime_edge_event_id,
            trusted_runtime_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT ck_dev_runtime_safety_source CHECK (
        (
            safety_projection_edge_event_id IS NULL
            AND safety_projection_edge_event_type IS NULL
            AND safety_projection_sequence IS NULL
        )
        OR
        (
            safety_projection_edge_event_id IS NOT NULL
            AND safety_projection_edge_event_type IN (
                'DEVICE_RUNTIME_SNAPSHOT',
                'SAFETY_SENSOR_STATE_CHANGED'
            )
            AND safety_projection_sequence > 0
        )
    ),
    ADD CONSTRAINT fk_dev_runtime_safety_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            safety_projection_edge_event_id,
            safety_projection_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_dev_runtime_trusted_freshness (
        tenant_id,
        organization_id,
        trusted_runtime_received_at,
        deployment_id
    );

ALTER TABLE dev_port_runtime_state
    ADD COLUMN cleaner_physical_close_confirmed TINYINT NULL
        AFTER clean_door_state_basis,
    ADD COLUMN last_delivery_door_command
        VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_door_contact_state,
    ADD COLUMN last_delivery_door_output_status
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER last_delivery_door_command,
    ADD COLUMN delivery_door_physical_state_basis
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER last_delivery_door_output_status,
    ADD COLUMN weight_measurement_uid
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER weight_sensor_health,
    ADD COLUMN weight_measurement_status
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER weight_measurement_uid,
    ADD COLUMN weight_value_available TINYINT NULL
        AFTER weight_measurement_status,
    ADD COLUMN reported_weight_grams BIGINT NULL
        AFTER weight_value_available,
    ADD COLUMN weight_value_kind
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER reported_weight_grams,
    ADD COLUMN weight_measurement_elapsed_ms BIGINT NULL
        AFTER weight_value_kind,
    ADD COLUMN weight_sample_count BIGINT NULL
        AFTER weight_measurement_elapsed_ms,
    ADD COLUMN calibration_version BIGINT NULL
        AFTER weight_sample_count,
    ADD COLUMN fullness_sensor_kind
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER infrared_sensor_health,
    ADD COLUMN fullness_sensor_value
        VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER fullness_sensor_kind,
    ADD COLUMN fullness_sample_basis
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER fullness_sensor_value,
    ADD COLUMN representative_distance_mm BIGINT NULL
        AFTER fullness_sample_basis,
    ADD COLUMN fullness_valid_sample_count BIGINT NULL
        AFTER representative_distance_mm,
    ADD COLUMN runtime_fault_bitmap BIGINT NULL
        AFTER smoke_sensor_health,
    ADD COLUMN trusted_runtime_edge_event_id BIGINT NULL
        AFTER runtime_fault_bitmap,
    ADD COLUMN trusted_runtime_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER trusted_runtime_edge_event_id,
    ADD COLUMN trusted_runtime_sequence BIGINT NULL
        AFTER trusted_runtime_edge_event_type,
    ADD COLUMN safety_projection_edge_event_id BIGINT NULL
        AFTER trusted_runtime_sequence,
    ADD COLUMN safety_projection_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER safety_projection_edge_event_id,
    ADD COLUMN safety_projection_sequence BIGINT NULL
        AFTER safety_projection_edge_event_type,
    DROP CHECK ck_dev_port_runtime_clean_door,
    ADD CONSTRAINT ck_dev_port_runtime_clean_door CHECK (
        clean_lock_power_state IN (
            'ENERGIZED',
            'DEENERGIZED',
            'UNKNOWN'
        )
        AND clean_solenoid_health IN (
            'OK',
            'DRIVER_FAULT',
            'DISCONNECTED',
            'UNKNOWN'
        )
        AND clean_door_inferred_state IN (
            'OPEN',
            'CLOSED',
            'UNKNOWN'
        )
        AND (
            (
                clean_door_state_basis =
                    'INFERRED_FROM_LOCK_POWER'
                AND cleaner_physical_close_confirmed IS NULL
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
                        (
                            clean_solenoid_health <> 'OK'
                            OR clean_lock_power_state = 'UNKNOWN'
                        )
                        AND clean_door_inferred_state = 'UNKNOWN'
                    )
                )
            )
            OR
            (
                clean_door_state_basis = 'NOT_OBSERVABLE'
                AND cleaner_physical_close_confirmed = 0
                AND clean_door_inferred_state = 'UNKNOWN'
            )
            OR
            (
                clean_door_state_basis = 'CLEANER_CONFIRMATION'
                AND cleaner_physical_close_confirmed = 1
                AND clean_door_inferred_state = 'CLOSED'
            )
        )
    ),
    ADD CONSTRAINT ck_dev_port_runtime_snapshot_projection CHECK (
        (
            trusted_runtime_edge_event_id IS NULL
            AND trusted_runtime_edge_event_type IS NULL
            AND trusted_runtime_sequence IS NULL
            AND last_delivery_door_command IS NULL
            AND last_delivery_door_output_status IS NULL
            AND delivery_door_physical_state_basis IS NULL
            AND weight_measurement_status IS NULL
            AND weight_value_available IS NULL
            AND weight_value_kind IS NULL
            AND fullness_sensor_kind IS NULL
            AND fullness_sensor_value IS NULL
            AND fullness_sample_basis IS NULL
            AND fullness_valid_sample_count IS NULL
            AND runtime_fault_bitmap IS NULL
        )
        OR
        (
            trusted_runtime_edge_event_id IS NOT NULL
            AND trusted_runtime_edge_event_type =
                'DEVICE_RUNTIME_SNAPSHOT'
            AND trusted_runtime_sequence > 0
            AND last_delivery_door_command IN (
                'NONE',
                'OPEN',
                'CLOSE'
            )
            AND last_delivery_door_output_status IN (
                'NOT_DISPATCHED',
                'COMMAND_DISPATCHED',
                'COMMAND_SUPERSEDED_BEFORE_DISPATCH',
                'COALESCED_WITH_EXISTING_CLOSE',
                'OUTPUT_REJECTED'
            )
            AND delivery_door_physical_state_basis =
                'NOT_OBSERVABLE'
            AND weight_measurement_status IN (
                'STABLE',
                'UNSTABLE',
                'TIMEOUT',
                'SENSOR_FAULT',
                'OVERLOAD',
                'PROTOCOL_ERROR',
                'CONFIG_ERROR',
                'DISCONNECTED'
            )
            AND weight_value_available IN (0, 1)
            AND weight_value_kind IN (
                'NONE',
                'STABLE_WINDOW_MEAN',
                'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN',
                'LAST_OBSERVED'
            )
            AND (
                (
                    weight_value_available = 0
                    AND reported_weight_grams IS NULL
                    AND weight_value_kind = 'NONE'
                )
                OR
                (
                    weight_value_available = 1
                    AND reported_weight_grams IS NOT NULL
                    AND weight_value_kind <> 'NONE'
                )
            )
            AND weight_measurement_elapsed_ms >= 0
            AND weight_sample_count >= 0
            AND calibration_version >= 0
            AND fullness_sensor_kind IN (
                'ULTRASONIC',
                'DIGITAL_INFRARED'
            )
            AND fullness_sensor_value IN ('CLEAR', 'BLOCKED')
            AND fullness_sample_basis IN (
                'MEASURED_MEDIAN',
                'NO_ECHO_CLEAR_FALLBACK',
                'INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK',
                'NOT_SAMPLED'
            )
            AND fullness_valid_sample_count >= 0
            AND runtime_fault_bitmap >= 0
        )
    ),
    ADD CONSTRAINT fk_dev_port_runtime_trusted_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            trusted_runtime_edge_event_id,
            trusted_runtime_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT ck_dev_port_runtime_safety_source CHECK (
        (
            safety_projection_edge_event_id IS NULL
            AND safety_projection_edge_event_type IS NULL
            AND safety_projection_sequence IS NULL
        )
        OR
        (
            safety_projection_edge_event_id IS NOT NULL
            AND safety_projection_edge_event_type IN (
                'DEVICE_RUNTIME_SNAPSHOT',
                'SAFETY_SENSOR_STATE_CHANGED'
            )
            AND safety_projection_sequence > 0
        )
    ),
    ADD CONSTRAINT fk_dev_port_runtime_safety_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            safety_projection_edge_event_id,
            safety_projection_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

-- Device-reported recovery is evidence. A safety-blocking fault still needs
-- explicit staff/system verification before its authoritative status changes.
ALTER TABLE dev_device_fault_event
    ADD COLUMN device_recovery_observed_edge_event_id BIGINT NULL
        AFTER recovery_reason,
    ADD COLUMN device_recovery_observed_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER device_recovery_observed_edge_event_id,
    ADD COLUMN device_recovery_observed_at DATETIME(3) NULL
        AFTER device_recovery_observed_edge_event_type,
    ADD CONSTRAINT ck_dev_fault_device_recovery_evidence CHECK (
        (
            device_recovery_observed_edge_event_id IS NULL
            AND device_recovery_observed_edge_event_type IS NULL
            AND device_recovery_observed_at IS NULL
        )
        OR
        (
            device_recovery_observed_edge_event_id IS NOT NULL
            AND device_recovery_observed_edge_event_type =
                'DEVICE_FAULT_RECOVERED'
            AND device_recovery_observed_at IS NOT NULL
        )
    ),
    ADD CONSTRAINT fk_dev_fault_device_recovery_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            device_recovery_observed_edge_event_id,
            device_recovery_observed_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE dev_fault_recovery_observation (
    fault_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NULL,
    component_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fault_code VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    impact_level VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_edge_event_id BIGINT NOT NULL,
    source_edge_event_type VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (fault_uid),
    CONSTRAINT uq_dev_fault_recovery_observation_edge
        UNIQUE (source_edge_event_id),
    CONSTRAINT ck_dev_fault_recovery_observation_uid CHECK (
        fault_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_fault_recovery_observation_source CHECK (
        source_edge_event_type = 'DEVICE_FAULT_RECOVERED'
        AND impact_level IN (
            'DEGRADED',
            'BUSINESS_BLOCKING',
            'SAFETY_BLOCKING'
        )
    ),
    CONSTRAINT fk_dev_fault_recovery_observation_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_recovery_observation_deployment
        FOREIGN KEY (tenant_id, organization_id, deployment_id)
        REFERENCES dev_device_deployment (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_recovery_observation_port
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id
        )
        REFERENCES dev_port (
            tenant_id,
            organization_id,
            deployment_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_fault_recovery_observation_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            source_edge_event_id,
            source_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;

-- Confirmation commands reuse the existing durable device-command executor.
-- The source event link makes one confirmation command per reliable fact.
ALTER TABLE dev_device_command
    ADD COLUMN confirmation_source_edge_event_id BIGINT NULL
        AFTER baseline_measurement_id,
    ADD COLUMN confirmation_source_edge_event_type
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER confirmation_source_edge_event_id,
    ADD CONSTRAINT uq_dev_command_confirmation_event
        UNIQUE (confirmation_source_edge_event_id),
    DROP CHECK ck_dev_command_target_shape,
    ADD CONSTRAINT ck_dev_command_target_shape CHECK (
        (
            command_type = 'START_DELIVERY_SESSION'
            AND delivery_session_id IS NOT NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
            AND confirmation_source_edge_event_id IS NULL
            AND confirmation_source_edge_event_type IS NULL
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
            AND confirmation_source_edge_event_id IS NULL
            AND confirmation_source_edge_event_type IS NULL
        )
        OR
        (
            command_type = 'APPLY_CONFIGURATION'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NOT NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
            AND confirmation_source_edge_event_id IS NULL
            AND confirmation_source_edge_event_type IS NULL
        )
        OR
        (
            command_type = 'SAMPLE_FULLNESS'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NOT NULL
            AND baseline_measurement_id IS NULL
            AND confirmation_source_edge_event_id IS NULL
            AND confirmation_source_edge_event_type IS NULL
        )
        OR
        (
            command_type = 'MEASURE_EMPTY_BAG_BASELINE'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NOT NULL
            AND confirmation_source_edge_event_id IS NULL
            AND confirmation_source_edge_event_type IS NULL
        )
        OR
        (
            command_type = 'CONFIRM_EDGE_EVENT'
            AND delivery_session_id IS NULL
            AND clean_operation_id IS NULL
            AND config_application_id IS NULL
            AND fullness_detection_id IS NULL
            AND baseline_measurement_id IS NULL
            AND confirmation_source_edge_event_id IS NOT NULL
            AND confirmation_source_edge_event_type IN (
                'CONFIGURATION_PROGRESS',
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED'
            )
        )
    ),
    ADD CONSTRAINT fk_dev_command_confirmation_edge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            confirmation_source_edge_event_id,
            confirmation_source_edge_event_type
        )
        REFERENCES dev_edge_event (
            tenant_id,
            organization_id,
            deployment_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;
