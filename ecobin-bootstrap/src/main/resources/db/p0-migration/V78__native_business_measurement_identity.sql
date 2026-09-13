-- P1BG: preserve native EBM1 measurement identities within the trusted device scope.
-- Only business measurement identity predicates change; numeric/quality/null rules stay intact.
-- One atomic ALTER; no historical backfill, new columns, grants or funds changes.
-- Reserved EBM1 identities must equal their boot/event fields, including UUIDv4-shaped overlaps.
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
            ((delivery_pre_measurement_uid NOT LIKE '45424d31-00%' AND delivery_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_pre_mcu_event_sequence),8,'0'))))
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
            AND ((delivery_pre_measurement_uid NOT LIKE '45424d31-00%' AND delivery_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_pre_mcu_event_sequence),8,'0'))))
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
            AND ((delivery_pre_measurement_uid NOT LIKE '45424d31-00%' AND delivery_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_pre_mcu_event_sequence),8,'0'))))
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
            ((delivery_post_measurement_uid NOT LIKE '45424d31-00%' AND delivery_post_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_post_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_post_mcu_event_sequence),8,'0'))))
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
            AND ((delivery_post_measurement_uid NOT LIKE '45424d31-00%' AND delivery_post_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_post_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_post_mcu_event_sequence),8,'0'))))
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
            AND ((delivery_post_measurement_uid NOT LIKE '45424d31-00%' AND delivery_post_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR delivery_post_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(delivery_post_mcu_boot_id),16,'0'),13,4),LPAD(HEX(delivery_post_mcu_event_sequence),8,'0'))))
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
    ),
DROP CHECK ck_dev_result_clean_pre_measurement,
    DROP CHECK ck_dev_result_clean_pre_weight_value,
    DROP CHECK ck_dev_result_clean_final_measurement,
    DROP CHECK ck_dev_result_clean_final_weight_value,
    DROP CHECK ck_dev_result_clean_extra_fields,
    DROP CHECK ck_dev_result_clean_shape,
    ADD CONSTRAINT ck_dev_result_clean_pre_measurement CHECK (
        (
            ((
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
            ((clean_pre_measurement_uid NOT LIKE '45424d31-00%' AND clean_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_pre_mcu_event_sequence),8,'0'))))
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
        ))
            AND (clean_pre_weight_value_kind IS NULL
                 OR clean_pre_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            clean_pre_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND clean_pre_measurement_uid IS NOT NULL
            AND ((clean_pre_measurement_uid NOT LIKE '45424d31-00%' AND clean_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_pre_mcu_event_sequence),8,'0'))))
            AND clean_pre_measurement_status = 'UNSTABLE'
            AND clean_pre_weight_value_available = 1
            AND clean_pre_weight_g BETWEEN -2147483648 AND 2147483647
            AND clean_pre_last_observed_weight_g IS NULL
            AND clean_pre_measurement_elapsed_ms = 5000
            AND clean_pre_sample_count BETWEEN 5 AND 32
            AND clean_pre_calibration_version BETWEEN 0 AND 4294967295
            AND clean_pre_sensor_health = 'OK'
            AND clean_pre_fault_code IS NULL
            AND clean_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_clean_pre_weight_value CHECK (
        (
            (clean_pre_measurement_uid IS NULL
        OR (
            clean_pre_weight_value_available = 1
            AND clean_pre_weight_value_kind = 'STABLE_WINDOW_MEAN'
            AND clean_pre_measurement_status = 'STABLE'
            AND clean_pre_weight_g IS NOT NULL
        ))
            AND (clean_pre_weight_value_kind IS NULL
                 OR clean_pre_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            clean_pre_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND clean_pre_measurement_uid IS NOT NULL
            AND ((clean_pre_measurement_uid NOT LIKE '45424d31-00%' AND clean_pre_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_pre_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_pre_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_pre_mcu_event_sequence),8,'0'))))
            AND clean_pre_measurement_status = 'UNSTABLE'
            AND clean_pre_weight_value_available = 1
            AND clean_pre_weight_g BETWEEN -2147483648 AND 2147483647
            AND clean_pre_last_observed_weight_g IS NULL
            AND clean_pre_measurement_elapsed_ms = 5000
            AND clean_pre_sample_count BETWEEN 5 AND 32
            AND clean_pre_calibration_version BETWEEN 0 AND 4294967295
            AND clean_pre_sensor_health = 'OK'
            AND clean_pre_fault_code IS NULL
            AND clean_pre_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_pre_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_clean_final_measurement CHECK (
        (
            ((
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
            ((clean_final_measurement_uid NOT LIKE '45424d31-00%' AND clean_final_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_final_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_final_mcu_event_sequence),8,'0'))))
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
        ))
            AND (clean_final_weight_value_kind IS NULL
                 OR clean_final_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            clean_final_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND clean_final_measurement_uid IS NOT NULL
            AND ((clean_final_measurement_uid NOT LIKE '45424d31-00%' AND clean_final_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_final_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_final_mcu_event_sequence),8,'0'))))
            AND clean_final_measurement_status = 'UNSTABLE'
            AND clean_final_weight_value_available = 1
            AND clean_final_weight_g BETWEEN -2147483648 AND 2147483647
            AND clean_final_last_observed_weight_g IS NULL
            AND clean_final_measurement_elapsed_ms = 5000
            AND clean_final_sample_count BETWEEN 5 AND 32
            AND clean_final_calibration_version BETWEEN 0 AND 4294967295
            AND clean_final_sensor_health = 'OK'
            AND clean_final_fault_code IS NULL
            AND clean_final_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_final_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_clean_final_weight_value CHECK (
        (
            (clean_final_measurement_uid IS NULL
        OR (
            (
                clean_final_weight_value_available = 0
                AND clean_final_weight_value_kind = 'NONE'
                AND clean_final_weight_g IS NULL
                AND clean_final_last_observed_weight_g IS NULL
            )
            OR
            (
                clean_final_weight_value_available = 1
                AND clean_final_weight_value_kind <> 'NONE'
                AND (
                    (
                        clean_final_measurement_status = 'STABLE'
                        AND clean_final_weight_value_kind =
                            'STABLE_WINDOW_MEAN'
                        AND clean_final_weight_g IS NOT NULL
                    )
                    OR
                    (
                        clean_final_measurement_status <> 'STABLE'
                        AND clean_final_last_observed_weight_g IS NOT NULL
                    )
                )
            )
        ))
            AND (clean_final_weight_value_kind IS NULL
                 OR clean_final_weight_value_kind <> 'TIMEOUT_MEDIAN')
        )
        OR COALESCE((
            clean_final_weight_value_kind = 'TIMEOUT_MEDIAN'
            AND clean_final_measurement_uid IS NOT NULL
            AND ((clean_final_measurement_uid NOT LIKE '45424d31-00%' AND clean_final_measurement_uid REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') OR clean_final_measurement_uid = LOWER(CONCAT('45424d31-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),1,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),5,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),9,4),'-',SUBSTRING(LPAD(HEX(clean_final_mcu_boot_id),16,'0'),13,4),LPAD(HEX(clean_final_mcu_event_sequence),8,'0'))))
            AND clean_final_measurement_status = 'UNSTABLE'
            AND clean_final_weight_value_available = 1
            AND clean_final_weight_g BETWEEN -2147483648 AND 2147483647
            AND clean_final_last_observed_weight_g IS NULL
            AND clean_final_measurement_elapsed_ms = 5000
            AND clean_final_sample_count BETWEEN 5 AND 32
            AND clean_final_calibration_version BETWEEN 0 AND 4294967295
            AND clean_final_sensor_health = 'OK'
            AND clean_final_fault_code IS NULL
            AND clean_final_mcu_boot_id BETWEEN 1 AND 9007199254740991
            AND clean_final_mcu_event_sequence BETWEEN 1 AND 4294967295
        ), FALSE)
    ),
    ADD CONSTRAINT ck_dev_result_clean_extra_fields CHECK (
        (
            result_type = 'CLEAN'
            AND clean_pre_weight_value_available IN (0, 1)
            AND clean_pre_weight_value_kind IN (
                'NONE', 'STABLE_WINDOW_MEAN', 'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN', 'LAST_OBSERVED', 'TIMEOUT_MEDIAN'
            )
            AND clean_final_weight_value_available IN (0, 1)
            AND clean_final_weight_value_kind IN (
                'NONE', 'STABLE_WINDOW_MEAN', 'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN', 'LAST_OBSERVED', 'TIMEOUT_MEDIAN'
            )
            AND (
                (
                    cleaner_physical_close_confirmed IN (0, 1)
                    AND clean_action_sequence BETWEEN 1 AND 65535
                )
                OR
                (
                    cleaner_physical_close_confirmed IS NULL
                    AND clean_action_sequence IS NULL
                )
            )
        )
        OR
        (
            result_type <> 'CLEAN'
            AND clean_pre_weight_value_available IS NULL
            AND clean_pre_weight_value_kind IS NULL
            AND clean_final_weight_value_available IS NULL
            AND clean_final_weight_value_kind IS NULL
            AND cleaner_physical_close_confirmed IS NULL
            AND clean_action_sequence IS NULL
        )
    ),
    ADD CONSTRAINT ck_dev_result_clean_shape CHECK (
        result_type <> 'CLEAN'
        OR
        (
            clean_pre_measurement_uid IS NOT NULL
            AND (clean_pre_measurement_status = 'STABLE'
                 OR (clean_pre_measurement_status = 'UNSTABLE'
                     AND clean_pre_weight_value_kind = 'TIMEOUT_MEDIAN'))
            AND clean_pre_weight_g IS NOT NULL
            AND clean_pre_weight_value_available = 1
            AND clean_pre_weight_value_kind IN ('STABLE_WINDOW_MEAN', 'TIMEOUT_MEDIAN')
            AND clean_final_measurement_uid IS NOT NULL
            AND cleaner_completion_confirmed = 1
            AND clean_lock_power_state = 'DEENERGIZED'
            AND (
                (
                    cleaner_physical_close_confirmed = 1
                    AND clean_solenoid_health IN ('OK', 'UNKNOWN')
                    AND clean_door_inferred_state = 'UNKNOWN'
                    AND clean_door_state_basis = 'CLEANER_CONFIRMATION'
                    AND clean_action_sequence BETWEEN 1 AND 65535
                )
                OR
                (
                    cleaner_physical_close_confirmed IS NULL
                    AND clean_action_sequence IS NULL
                    AND clean_solenoid_health = 'OK'
                    AND clean_door_inferred_state = 'CLOSED'
                    AND clean_door_state_basis = 'INFERRED_FROM_LOCK_POWER'
                )
            )
            AND (
                (
                    (clean_final_measurement_status = 'STABLE'
                     OR (clean_final_measurement_status = 'UNSTABLE'
                         AND clean_final_weight_value_kind = 'TIMEOUT_MEDIAN'))
                    AND clean_final_weight_value_available = 1
                    AND clean_final_weight_g IS NOT NULL
                    AND (clean_final_weight_value_kind <> 'TIMEOUT_MEDIAN'
                         OR clean_new_baseline_weight_g IS NOT NULL)
                    AND clean_new_baseline_weight_g = clean_final_weight_g
                )
                OR
                (
                    clean_final_measurement_status IN (
                        'UNSTABLE', 'TIMEOUT', 'SENSOR_FAULT', 'OVERLOAD',
                        'PROTOCOL_ERROR', 'CONFIG_ERROR', 'DISCONNECTED'
                    )
                    AND clean_new_baseline_weight_g IS NULL
                    AND clean_final_fault_code IS NOT NULL
                )
            )
        )
    );

