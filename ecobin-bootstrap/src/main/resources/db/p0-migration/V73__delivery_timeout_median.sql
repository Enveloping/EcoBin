-- P1AS: complete delivery weights may be explicitly non-stable timeout medians.
-- Preserve every old non-median branch. Median-only alternatives fail closed on NULL.
-- Do not relabel original facts, backfill weights, change funds, or relax other result types.
-- All five CHECK replacements are one atomic MySQL ALTER TABLE statement.
ALTER TABLE dev_physical_result
    DROP CHECK ck_dev_result_delivery_pre_measurement,
    DROP CHECK ck_dev_result_delivery_pre_weight_value,
    DROP CHECK ck_dev_result_delivery_post_measurement,
    DROP CHECK ck_dev_result_delivery_post_weight_value,
    DROP CHECK ck_dev_result_delivery_shape,
    ADD CONSTRAINT ck_dev_result_delivery_pre_measurement CHECK (
        (
            ((
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
        ))
            AND (delivery_pre_weight_value_kind IS NULL
                 OR delivery_pre_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            delivery_pre_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_pre_measurement_status = 'UNSTABLE'
            AND delivery_pre_weight_value_available = 1
            AND delivery_pre_weight_g BETWEEN -2147483648 AND 2147483647
            AND delivery_pre_last_observed_weight_g IS NULL
            AND delivery_pre_measurement_elapsed_ms = 5000
            AND delivery_pre_sample_count BETWEEN 5 AND 32
            AND delivery_pre_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_pre_sensor_health = 'OK'
            AND delivery_pre_fault_code IS NULL
            AND delivery_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_delivery_pre_weight_value CHECK (
        (
            ((
            delivery_pre_measurement_uid IS NULL
            AND delivery_pre_weight_value_available IS NULL
            AND delivery_pre_weight_value_kind IS NULL
        )
        OR
        (
            delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_weight_value_available IN (0, 1)
            AND delivery_pre_weight_value_kind IN (
                'NONE',
                'STABLE_WINDOW_MEAN',
                'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN',
                'LAST_OBSERVED'
            )
            AND (
                (
                    delivery_pre_weight_value_available = 0
                    AND delivery_pre_weight_value_kind = 'NONE'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_last_observed_weight_g IS NULL
                )
                OR
                (
                    delivery_pre_weight_value_available = 1
                    AND delivery_pre_weight_value_kind <> 'NONE'
                    AND (
                        (
                            delivery_pre_measurement_status = 'STABLE'
                            AND delivery_pre_weight_value_kind =
                                'STABLE_WINDOW_MEAN'
                            AND delivery_pre_weight_g IS NOT NULL
                        )
                        OR
                        (
                            delivery_pre_measurement_status <> 'STABLE'
                            AND delivery_pre_last_observed_weight_g IS NOT NULL
                        )
                    )
                )
            )
        ))
            AND (delivery_pre_weight_value_kind IS NULL
                 OR delivery_pre_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            delivery_pre_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_pre_measurement_status = 'UNSTABLE'
            AND delivery_pre_weight_value_available = 1
            AND delivery_pre_weight_g BETWEEN -2147483648 AND 2147483647
            AND delivery_pre_last_observed_weight_g IS NULL
            AND delivery_pre_measurement_elapsed_ms = 5000
            AND delivery_pre_sample_count BETWEEN 5 AND 32
            AND delivery_pre_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_pre_sensor_health = 'OK'
            AND delivery_pre_fault_code IS NULL
            AND delivery_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_delivery_post_measurement CHECK (
        (
            ((
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
        ))
            AND (delivery_post_weight_value_kind IS NULL
                 OR delivery_post_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            delivery_post_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_post_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_post_measurement_status = 'UNSTABLE'
            AND delivery_post_weight_value_available = 1
            AND delivery_post_weight_g BETWEEN -2147483648 AND 2147483647
            AND delivery_post_last_observed_weight_g IS NULL
            AND delivery_post_measurement_elapsed_ms = 5000
            AND delivery_post_sample_count BETWEEN 5 AND 32
            AND delivery_post_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_post_sensor_health = 'OK'
            AND delivery_post_fault_code IS NULL
            AND delivery_post_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_post_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_delivery_post_weight_value CHECK (
        (
            ((
            delivery_post_measurement_uid IS NULL
            AND delivery_post_weight_value_available IS NULL
            AND delivery_post_weight_value_kind IS NULL
        )
        OR
        (
            delivery_post_measurement_uid IS NOT NULL
            AND delivery_post_weight_value_available IN (0, 1)
            AND delivery_post_weight_value_kind IN (
                'NONE',
                'STABLE_WINDOW_MEAN',
                'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN',
                'LAST_OBSERVED'
            )
            AND (
                (
                    delivery_post_weight_value_available = 0
                    AND delivery_post_weight_value_kind = 'NONE'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_last_observed_weight_g IS NULL
                )
                OR
                (
                    delivery_post_weight_value_available = 1
                    AND delivery_post_weight_value_kind <> 'NONE'
                    AND (
                        (
                            delivery_post_measurement_status = 'STABLE'
                            AND delivery_post_weight_value_kind =
                                'STABLE_WINDOW_MEAN'
                            AND delivery_post_weight_g IS NOT NULL
                        )
                        OR
                        (
                            delivery_post_measurement_status <> 'STABLE'
                            AND delivery_post_last_observed_weight_g IS NOT NULL
                        )
                    )
                )
            )
        ))
            AND (delivery_post_weight_value_kind IS NULL
                 OR delivery_post_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            delivery_post_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_post_measurement_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            AND delivery_post_measurement_status = 'UNSTABLE'
            AND delivery_post_weight_value_available = 1
            AND delivery_post_weight_g BETWEEN -2147483648 AND 2147483647
            AND delivery_post_last_observed_weight_g IS NULL
            AND delivery_post_measurement_elapsed_ms = 5000
            AND delivery_post_sample_count BETWEEN 5 AND 32
            AND delivery_post_calibration_version BETWEEN 0 AND 4294967295
            AND delivery_post_sensor_health = 'OK'
            AND delivery_post_fault_code IS NULL
            AND delivery_post_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND delivery_post_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_delivery_shape CHECK (
        result_type <> 'DELIVERY'
        OR
        (
            delivery_pre_measurement_uid IS NOT NULL
            AND (delivery_pre_measurement_status = 'STABLE'
                 OR (delivery_pre_measurement_status = 'UNSTABLE'
                     AND delivery_pre_weight_value_kind = 'TIMEOUT_MEDIAN'))
            AND delivery_pre_weight_g IS NOT NULL
            AND delivery_pre_weight_value_available = 1
            AND delivery_pre_weight_value_kind IN (
                'STABLE_WINDOW_MEAN', 'TIMEOUT_MEDIAN')
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_completion_reason IS NOT NULL
            AND delivery_manual_review_required = 0
            AND negative_weight_anomaly IN (0, 1)
            AND (
                (
                    delivery_completion_reason IN (
                        'USER_ENDED',
                        'SELECTION_WINDOW_EXPIRED'
                    )
                    AND (delivery_post_measurement_status = 'STABLE'
                 OR (delivery_post_measurement_status = 'UNSTABLE'
                     AND delivery_post_weight_value_kind = 'TIMEOUT_MEDIAN'))
                    AND delivery_post_weight_g IS NOT NULL
                    AND delivery_post_weight_value_available = 1
                    AND delivery_post_weight_value_kind IN (
                'STABLE_WINDOW_MEAN', 'TIMEOUT_MEDIAN')
                    AND delivery_net_weight_g IS NOT NULL
                )
                OR
                (
                    delivery_completion_reason =
                        'TERMINAL_WEIGHT_FAILURE'
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
    );
