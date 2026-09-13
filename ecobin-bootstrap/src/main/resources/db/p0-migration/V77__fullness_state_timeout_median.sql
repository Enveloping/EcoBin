-- Edge-owned current-bag fullness accepts the agreed five-second median.
-- Preserve historic unknown value metadata as NULL; never relabel or backfill it.
-- One atomic ALTER retains every existing identity/configuration/value constraint.
-- Existing INSERT-only fact grants cover the new columns; no UPDATE permission.
ALTER TABLE dev_fullness_state_fact
    ADD COLUMN weight_value_available TINYINT NULL AFTER total_weight_g,
    ADD COLUMN weight_value_kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER weight_value_available,
    DROP CHECK ck_dev_fullness_state_values,
    ADD CONSTRAINT ck_dev_fullness_state_values CHECK (
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
        AND (
            (measurement_status = 'STABLE' AND (
                (weight_value_available IS NULL AND weight_value_kind IS NULL)
                OR (weight_value_available IS NOT NULL AND weight_value_available = 1
                    AND weight_value_kind IS NOT NULL AND weight_value_kind = 'STABLE_WINDOW_MEAN')
            ))
            OR (measurement_status = 'UNSTABLE'
                AND weight_value_available IS NOT NULL AND weight_value_available = 1
                AND weight_value_kind IS NOT NULL AND weight_value_kind = 'TIMEOUT_MEDIAN'
                AND measurement_elapsed_ms = 5000 AND sample_count BETWEEN 5 AND 32)
        )
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
    );
