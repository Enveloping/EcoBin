-- P1AV: preserve independent-baseline value kind; unknown historical metadata stays NULL.
-- Atomic constraint replacement; no data backfill, new tables or relaxed stale-result policy.
ALTER TABLE dev_physical_result
    ADD COLUMN baseline_weight_value_available TINYINT(1) NULL AFTER baseline_measurement_status,
    ADD COLUMN baseline_weight_value_kind VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER baseline_weight_value_available,
    ADD CONSTRAINT ck_dev_result_baseline_value_kind CHECK (
        (baseline_weight_value_available IS NULL AND baseline_weight_value_kind IS NULL)
        OR COALESCE((
            result_type = 'BASELINE_MEASUREMENT'
            AND baseline_weight_value_available IN (0, 1)
            AND baseline_weight_value_kind IN (
                'NONE', 'STABLE_WINDOW_MEAN', 'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN', 'LAST_OBSERVED', 'TIMEOUT_MEDIAN'
            )
            AND (
                (baseline_weight_value_available = 0 AND baseline_weight_value_kind = 'NONE'
                    AND baseline_total_weight_g IS NULL AND baseline_last_observed_weight_g IS NULL)
                OR (baseline_weight_value_available = 1 AND baseline_weight_value_kind <> 'NONE'
                    AND (
                        (baseline_total_weight_g IS NOT NULL AND baseline_last_observed_weight_g IS NULL)
                        OR (baseline_total_weight_g IS NULL AND baseline_last_observed_weight_g IS NOT NULL)
                    ))
            )
        ), FALSE)
    ),
    DROP CHECK ck_dev_result_baseline_measurement,
    ADD CONSTRAINT ck_dev_result_baseline_measurement CHECK (
        (
            ((
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
        ))
            AND (baseline_weight_value_kind IS NULL OR baseline_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            result_type = 'BASELINE_MEASUREMENT'
            AND baseline_measurement_id IS NOT NULL
            AND baseline_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND baseline_weight_value_available = 1
            AND baseline_measurement_uid IS NOT NULL
            AND baseline_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND baseline_measurement_status = 'UNSTABLE'
            AND baseline_total_weight_g BETWEEN -2147483648 AND 2147483647
            AND baseline_last_observed_weight_g IS NULL
            AND baseline_measurement_elapsed_ms = 5000
            AND baseline_sample_count BETWEEN 5 AND 32
            AND baseline_calibration_version BETWEEN 0 AND 4294967295
            AND baseline_sensor_health = 'OK'
            AND baseline_fault_code IS NULL
            AND baseline_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND baseline_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND empty_bag_confirmed = 1
        ), FALSE)
    ),
    DROP CHECK ck_dev_result_baseline_shape,
    ADD CONSTRAINT ck_dev_result_baseline_shape CHECK (
        (
            (result_type <> 'BASELINE_MEASUREMENT'
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
        ))
            AND (baseline_weight_value_kind IS NULL OR baseline_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            result_type = 'BASELINE_MEASUREMENT'
            AND baseline_measurement_id IS NOT NULL
            AND baseline_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND baseline_weight_value_available = 1
            AND baseline_measurement_uid IS NOT NULL
            AND baseline_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND baseline_measurement_status = 'UNSTABLE'
            AND baseline_total_weight_g BETWEEN -2147483648 AND 2147483647
            AND baseline_last_observed_weight_g IS NULL
            AND baseline_measurement_elapsed_ms = 5000
            AND baseline_sample_count BETWEEN 5 AND 32
            AND baseline_calibration_version BETWEEN 0 AND 4294967295
            AND baseline_sensor_health = 'OK'
            AND baseline_fault_code IS NULL
            AND baseline_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND baseline_mcu_event_sequence BETWEEN 1 AND 4294967295
            AND empty_bag_confirmed = 1
        ), FALSE)
    );
