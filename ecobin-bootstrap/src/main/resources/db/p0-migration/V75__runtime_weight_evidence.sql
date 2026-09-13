-- P1AU: keep actual runtime weight identity and fault evidence; no historical backfill.
-- Old non-median projection rules are unchanged. A new median must satisfy the full shape.
ALTER TABLE dev_port_runtime_state
    ADD COLUMN weight_fault_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER calibration_version,
    ADD COLUMN weight_mcu_boot_id BIGINT NULL AFTER weight_fault_code,
    ADD COLUMN weight_mcu_event_sequence BIGINT NULL AFTER weight_mcu_boot_id,
    ADD CONSTRAINT ck_dev_port_runtime_weight_identity CHECK (
        (weight_fault_code IS NULL OR weight_fault_code REGEXP '^[A-Z][A-Z0-9_]{0,63}$')
        AND (weight_mcu_boot_id IS NULL OR weight_mcu_boot_id BETWEEN 1 AND 9007199254740991)
        AND (weight_mcu_event_sequence IS NULL OR weight_mcu_event_sequence BETWEEN 1 AND 4294967295)
    ),
    DROP CHECK ck_dev_port_runtime_snapshot_projection,
    ADD CONSTRAINT ck_dev_port_runtime_snapshot_projection CHECK (
        (
            ((
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
        ))
            AND (weight_value_kind IS NULL OR weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            ((
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
                'LAST_OBSERVED',
                'TIMEOUT_MEDIAN'
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
        ))
            AND weight_value_kind = 'TIMEOUT_MEDIAN'
            AND trusted_runtime_edge_event_id IS NOT NULL
            AND trusted_runtime_sequence IS NOT NULL
            AND weight_measurement_uid IS NOT NULL
            AND weight_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND weight_measurement_status = 'UNSTABLE'
            AND weight_value_available = 1
            AND reported_weight_grams BETWEEN -2147483648 AND 2147483647
            AND weight_sensor_health = 'OK'
            AND weight_fault_code IS NULL
            AND weight_measurement_elapsed_ms = 5000
            AND weight_sample_count BETWEEN 5 AND 32
            AND calibration_version BETWEEN 0 AND 4294967295
            AND weight_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND weight_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    );
