-- Close the normal post-delivery fullness loop for the accepted fixed-frame
-- runtime. The device result is inserted before the recycling sample in one
-- transaction, so the old immediate two-way mandatory reference could never
-- be satisfied by MySQL. rec_fullness_sample remains the authoritative,
-- immutable link to its scoped physical result; the optional device-side
-- back-pointer stays null so dev_physical_result remains append-only.

ALTER TABLE rec_fullness_sample
    DROP FOREIGN KEY fk_rec_fullness_sample_result_target;

ALTER TABLE rec_fullness_detection
    ADD COLUMN measurement_timeout_ms BIGINT NULL
        AFTER confirmation_wait_ms,
    ADD COLUMN calculation_basis
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER measurement_timeout_ms;

UPDATE rec_fullness_detection detection
JOIN dev_port_config_snapshot snapshot
  ON snapshot.tenant_id = detection.tenant_id
 AND snapshot.organization_id = detection.organization_id
 AND snapshot.deployment_id = detection.deployment_id
 AND snapshot.config_version_id =
     detection.device_config_version_id
 AND snapshot.port_id = detection.port_id
 AND snapshot.id = detection.port_config_snapshot_id
SET detection.measurement_timeout_ms =
        snapshot.weight_measurement_timeout_ms,
    detection.calculation_basis =
        'FIXED_FRAME_TOTAL_WEIGHT'
WHERE detection.measurement_timeout_ms IS NULL
   OR detection.calculation_basis IS NULL;

ALTER TABLE rec_fullness_detection
    MODIFY COLUMN measurement_timeout_ms BIGINT NOT NULL,
    MODIFY COLUMN calculation_basis
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_rec_fullness_detection_compatibility CHECK (
        measurement_timeout_ms BETWEEN 1000 AND 6000
        AND calculation_basis = 'FIXED_FRAME_TOTAL_WEIGHT'
    );

ALTER TABLE dev_physical_result
    ADD COLUMN fullness_sensor_kind
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER infrared_health,
    ADD COLUMN fullness_sample_basis
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER fullness_sensor_kind,
    ADD COLUMN fullness_representative_distance_mm BIGINT NULL
        AFTER fullness_sample_basis,
    ADD COLUMN fullness_requested_sample_count INT NULL
        AFTER fullness_representative_distance_mm,
    ADD COLUMN fullness_valid_sample_count INT NULL
        AFTER fullness_requested_sample_count,
    DROP CHECK ck_dev_result_target_shape,
    ADD CONSTRAINT ck_dev_result_target_shape CHECK (
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
    ADD CONSTRAINT ck_dev_result_fullness_source_shape CHECK (
        (
            result_type <> 'FULLNESS_SAMPLE'
            AND fullness_sensor_kind IS NULL
            AND fullness_sample_basis IS NULL
            AND fullness_representative_distance_mm IS NULL
            AND fullness_requested_sample_count IS NULL
            AND fullness_valid_sample_count IS NULL
        )
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND fullness_sensor_kind IN (
                'ULTRASONIC',
                'DIGITAL_INFRARED'
            )
            AND fullness_sample_basis IN (
                'MEASURED_MEDIAN',
                'NO_ECHO_CLEAR_FALLBACK',
                'INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK',
                'NOT_SAMPLED'
            )
            AND fullness_requested_sample_count BETWEEN 0 AND 255
            AND fullness_valid_sample_count BETWEEN 0
                AND fullness_requested_sample_count
            AND (
                (
                    fullness_sample_basis = 'MEASURED_MEDIAN'
                    AND fullness_representative_distance_mm
                        BETWEEN 0 AND 4294967295
                )
                OR
                (
                    fullness_sample_basis <> 'MEASURED_MEDIAN'
                    AND fullness_representative_distance_mm IS NULL
                )
            )
        )
    );

ALTER TABLE rec_fullness_sample
    ADD COLUMN fullness_sensor_kind
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER infrared_full,
    ADD COLUMN fullness_sample_basis
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER fullness_sensor_kind,
    ADD COLUMN representative_distance_mm BIGINT NULL
        AFTER fullness_sample_basis,
    ADD COLUMN requested_sample_count INT NULL
        AFTER representative_distance_mm,
    ADD COLUMN valid_sample_count INT NULL
        AFTER requested_sample_count,
    ADD COLUMN calculation_basis
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER valid_sample_count,
    DROP CHECK ck_rec_fullness_sample_weight,
    DROP CHECK ck_rec_fullness_sample_conclusion,
    ADD CONSTRAINT ck_rec_fullness_sample_weight CHECK (
        (
            weight_status = 'RELIABLE'
            AND stable_total_weight_g IS NOT NULL
            AND (
                (
                    baseline_weight_g IS NULL
                    AND raw_net_weight_g IS NULL
                )
                OR
                (
                    baseline_weight_g IS NOT NULL
                    AND raw_net_weight_g IS NOT NULL
                )
            )
            AND threshold_weight_g > 0
            AND displayed_fullness_percent IS NOT NULL
            AND displayed_fullness_percent >= 0
            AND weight_full IN (0, 1)
        )
        OR
        (
            weight_status IN ('FAILED', 'NOT_REQUIRED')
            AND stable_total_weight_g IS NULL
            AND baseline_weight_g IS NULL
            AND threshold_weight_g IS NULL
            AND raw_net_weight_g IS NULL
            AND displayed_fullness_percent IS NULL
            AND weight_full IS NULL
        )
    ),
    ADD CONSTRAINT ck_rec_fullness_sample_conclusion CHECK (
        (
            conclusion = 'NOT_FULL'
            AND full_reason IS NULL
            AND weight_full = 0
        )
        OR
        (
            conclusion = 'FULL'
            AND full_reason = 'WEIGHT'
            AND weight_full = 1
        )
        OR
        (
            conclusion = 'SOURCE_FAILED'
            AND full_reason IS NULL
        )
    ),
    ADD CONSTRAINT ck_rec_fullness_sample_source_snapshot CHECK (
        (
            fullness_sensor_kind IS NULL
            AND fullness_sample_basis IS NULL
            AND representative_distance_mm IS NULL
            AND requested_sample_count IS NULL
            AND valid_sample_count IS NULL
            AND calculation_basis IS NULL
        )
        OR
        (
            fullness_sensor_kind = 'DIGITAL_INFRARED'
            AND fullness_sample_basis = 'NOT_SAMPLED'
            AND representative_distance_mm IS NULL
            AND requested_sample_count IN (0, 1)
            AND valid_sample_count = requested_sample_count
            AND calculation_basis =
                'FIXED_FRAME_TOTAL_WEIGHT'
        )
    );

ALTER TABLE rec_port_capacity_state
    DROP CHECK ck_rec_capacity_gate,
    ADD CONSTRAINT ck_rec_capacity_gate CHECK (
        detection_gate IN (
            'UNKNOWN',
            'PENDING',
            'IN_PROGRESS',
            'READY',
            'FAILED'
        )
        AND confirmed_fullness_state IN (
            'UNKNOWN',
            'NOT_FULL',
            'FULL'
        )
        AND (
            displayed_fullness_percent IS NULL
            OR displayed_fullness_percent >= 0
        )
        AND (
            (
                baseline_state = 'VALID'
                AND (
                    (
                        raw_net_weight_g IS NULL
                        AND displayed_fullness_percent IS NULL
                    )
                    OR
                    (
                        latest_stable_total_weight_g IS NOT NULL
                        AND raw_net_weight_g IS NOT NULL
                        AND displayed_fullness_percent IS NOT NULL
                    )
                )
            )
            OR
            (
                baseline_state IN (
                    'UNINITIALIZED',
                    'INVALID'
                )
                AND raw_net_weight_g IS NULL
                AND (
                    displayed_fullness_percent IS NULL
                    OR latest_stable_total_weight_g IS NOT NULL
                )
            )
        )
        AND (
            detection_gate IN ('PENDING', 'IN_PROGRESS')
            =
            (current_detection_id IS NOT NULL)
        )
        AND (
            detection_gate <> 'READY'
            OR confirmed_fullness_state IN (
                'NOT_FULL',
                'FULL'
            )
        )
        AND lock_version >= 0
    );
