-- Align the cleaning operation/result tables with the frozen no-door-sensor
-- contract. Exact device times may be absent; boolean facts carry the safety
-- boundary without fabricating timestamps from backend receipt time.

ALTER TABLE rec_clean_operation
    ADD COLUMN edge_saved_confirmed TINYINT NOT NULL DEFAULT 0
        AFTER status,
    ADD COLUMN first_unlock_may_have_executed TINYINT NOT NULL DEFAULT 0
        AFTER edge_saved_confirmed,
    ADD COLUMN clean_lock_deenergized_confirmed TINYINT NOT NULL DEFAULT 0
        AFTER first_unlock_may_have_executed,
    ADD COLUMN cleaner_physical_close_confirmed TINYINT NOT NULL DEFAULT 0
        AFTER clean_lock_deenergized_confirmed,
    DROP CHECK ck_rec_clean_operation_state_shape,
    DROP CHECK ck_rec_clean_operation_times;

UPDATE rec_clean_operation
SET edge_saved_confirmed =
        CASE
            WHEN status IN (
                'EDGE_SAVED', 'IN_PROGRESS', 'RECOVERY_REQUIRED', 'COMPLETED'
            ) OR edge_saved_at IS NOT NULL
            THEN 1 ELSE 0
        END,
    first_unlock_may_have_executed =
        CASE
            WHEN status IN ('IN_PROGRESS', 'RECOVERY_REQUIRED', 'COMPLETED')
                OR first_possible_unlock_at IS NOT NULL
            THEN 1 ELSE 0
        END,
    clean_lock_deenergized_confirmed =
        CASE
            WHEN status = 'COMPLETED' OR solenoid_powered_off_at IS NOT NULL
            THEN 1 ELSE 0
        END,
    cleaner_physical_close_confirmed =
        CASE
            WHEN status = 'COMPLETED' OR cleaner_confirmed_closed_at IS NOT NULL
            THEN 1 ELSE 0
        END;

ALTER TABLE rec_clean_operation
    ADD CONSTRAINT ck_rec_clean_operation_evidence_flags CHECK (
        edge_saved_confirmed IN (0, 1)
        AND first_unlock_may_have_executed IN (0, 1)
        AND clean_lock_deenergized_confirmed IN (0, 1)
        AND cleaner_physical_close_confirmed IN (0, 1)
    ),
    ADD CONSTRAINT ck_rec_clean_operation_state_shape CHECK (
        (
            status = 'PREPARED'
            AND edge_saved_confirmed = 0
            AND first_unlock_may_have_executed = 0
            AND clean_lock_deenergized_confirmed = 0
            AND cleaner_physical_close_confirmed = 0
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'EDGE_SAVED'
            AND edge_saved_confirmed = 1
            AND first_unlock_may_have_executed = 0
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'IN_PROGRESS'
            AND edge_saved_confirmed = 1
            AND first_unlock_may_have_executed = 1
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'RECOVERY_REQUIRED'
            AND edge_saved_confirmed = 1
            AND first_unlock_may_have_executed = 1
            AND completion_record_id IS NULL
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status = 'PRE_UNLOCK_ENDED'
            AND first_unlock_may_have_executed = 0
            AND clean_lock_deenergized_confirmed = 0
            AND cleaner_physical_close_confirmed = 0
            AND completion_record_id IS NULL
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND edge_saved_confirmed = 1
            AND first_unlock_may_have_executed = 1
            AND clean_lock_deenergized_confirmed = 1
            AND cleaner_physical_close_confirmed = 1
            AND completion_record_id IS NOT NULL
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
        )
    ),
    ADD CONSTRAINT ck_rec_clean_operation_times CHECK (
        updated_at >= created_at
        AND start_authorization_expires_at > created_at
        AND (edge_saved_at IS NULL OR edge_saved_at >= created_at)
        AND (
            execution_deadline_at IS NULL
            OR execution_deadline_at > created_at
        )
        AND (
            first_possible_unlock_at IS NULL
            OR first_possible_unlock_at >= created_at
        )
        AND (
            solenoid_powered_off_at IS NULL
            OR first_possible_unlock_at IS NULL
            OR solenoid_powered_off_at >= first_possible_unlock_at
        )
        AND (
            cleaner_confirmed_closed_at IS NULL
            OR first_possible_unlock_at IS NULL
            OR cleaner_confirmed_closed_at >= first_possible_unlock_at
        )
        AND (
            pre_unlock_end_requested_at IS NULL
            OR pre_unlock_end_requested_at >= created_at
        )
        AND (ended_at IS NULL OR ended_at >= created_at)
    );

ALTER TABLE dev_physical_result
    ADD COLUMN clean_pre_weight_value_available TINYINT NULL
        AFTER clean_pre_last_observed_weight_g,
    ADD COLUMN clean_pre_weight_value_kind
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER clean_pre_weight_value_available,
    ADD COLUMN clean_final_weight_value_available TINYINT NULL
        AFTER clean_final_last_observed_weight_g,
    ADD COLUMN clean_final_weight_value_kind
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER clean_final_weight_value_available,
    ADD COLUMN cleaner_physical_close_confirmed TINYINT NULL
        AFTER cleaner_completion_confirmed,
    ADD COLUMN clean_action_sequence INT NULL
        AFTER cleaner_physical_close_confirmed,
    DROP CHECK ck_dev_result_clean_shape;

UPDATE dev_physical_result
SET clean_pre_weight_value_available =
        CASE WHEN clean_pre_weight_g IS NOT NULL
             OR clean_pre_last_observed_weight_g IS NOT NULL
             THEN 1 ELSE 0 END,
    clean_pre_weight_value_kind =
        CASE WHEN clean_pre_measurement_status = 'STABLE'
             THEN 'STABLE_WINDOW_MEAN'
             WHEN clean_pre_last_observed_weight_g IS NOT NULL
             THEN 'LAST_OBSERVED'
             ELSE 'NONE' END,
    clean_final_weight_value_available =
        CASE WHEN clean_final_weight_g IS NOT NULL
             OR clean_final_last_observed_weight_g IS NOT NULL
             THEN 1 ELSE 0 END,
    clean_final_weight_value_kind =
        CASE WHEN clean_final_measurement_status = 'STABLE'
             THEN 'STABLE_WINDOW_MEAN'
             WHEN clean_final_last_observed_weight_g IS NOT NULL
             THEN 'LAST_OBSERVED'
             ELSE 'NONE' END
WHERE result_type = 'CLEAN';

ALTER TABLE dev_physical_result
    ADD CONSTRAINT ck_dev_result_clean_extra_fields CHECK (
        (
            result_type = 'CLEAN'
            AND clean_pre_weight_value_available IN (0, 1)
            AND clean_pre_weight_value_kind IN (
                'NONE', 'STABLE_WINDOW_MEAN', 'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN', 'LAST_OBSERVED'
            )
            AND clean_final_weight_value_available IN (0, 1)
            AND clean_final_weight_value_kind IN (
                'NONE', 'STABLE_WINDOW_MEAN', 'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN', 'LAST_OBSERVED'
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
    ADD CONSTRAINT ck_dev_result_clean_pre_weight_value CHECK (
        clean_pre_measurement_uid IS NULL
        OR (
            clean_pre_weight_value_available = 1
            AND clean_pre_weight_value_kind = 'STABLE_WINDOW_MEAN'
            AND clean_pre_measurement_status = 'STABLE'
            AND clean_pre_weight_g IS NOT NULL
        )
    ),
    ADD CONSTRAINT ck_dev_result_clean_final_weight_value CHECK (
        clean_final_measurement_uid IS NULL
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
        )
    ),
    ADD CONSTRAINT ck_dev_result_clean_shape CHECK (
        result_type <> 'CLEAN'
        OR
        (
            clean_pre_measurement_uid IS NOT NULL
            AND clean_pre_measurement_status = 'STABLE'
            AND clean_pre_weight_g IS NOT NULL
            AND clean_pre_weight_value_available = 1
            AND clean_pre_weight_value_kind = 'STABLE_WINDOW_MEAN'
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
                    clean_final_measurement_status = 'STABLE'
                    AND clean_final_weight_value_available = 1
                    AND clean_final_weight_g IS NOT NULL
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

ALTER TABLE rec_clean_record
    DROP CHECK ck_rec_clean_record_times,
    MODIFY COLUMN device_occurred_at DATETIME(3) NULL,
    ADD COLUMN backend_received_at DATETIME(3) NULL
        AFTER device_occurred_at;

UPDATE rec_clean_record
SET backend_received_at = completed_at;

ALTER TABLE rec_clean_record
    MODIFY COLUMN backend_received_at DATETIME(3) NOT NULL
        DEFAULT (UTC_TIMESTAMP(3)),
    ADD CONSTRAINT ck_rec_clean_record_times CHECK (
        (
            device_occurred_at IS NULL
            OR backend_received_at >= device_occurred_at
        )
        AND completed_at >= backend_received_at
        AND created_at >= completed_at
        AND updated_at >= created_at
    );

ALTER TABLE rec_clean_anomaly
    DROP CHECK ck_rec_clean_anomaly_times;

UPDATE rec_clean_anomaly
SET created_at = detected_at
WHERE created_at < detected_at;

ALTER TABLE rec_clean_anomaly
    ADD CONSTRAINT ck_rec_clean_anomaly_times
        CHECK (created_at >= detected_at);
