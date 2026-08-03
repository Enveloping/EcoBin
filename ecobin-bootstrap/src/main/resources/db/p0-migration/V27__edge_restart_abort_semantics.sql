-- An edge-process restart cancels unfinished physical work. Durable completion
-- facts still win and are replayed by the edge; only an explicit
-- DEVICE_COMMAND_OBSERVED/FAILED/EDGE_RESTARTED reaches these terminal states.

ALTER TABLE dev_device_command
    DROP CHECK ck_dev_command_physical_state,
    DROP CHECK ck_dev_command_state_shape,
    ADD CONSTRAINT ck_dev_command_physical_state CHECK (
        physical_state IN (
            'CREATED',
            'QUEUED',
            'EDGE_ACCEPTED',
            'PHYSICAL_STARTED',
            'PHYSICAL_SUCCEEDED',
            'PHYSICAL_FAILED',
            'PRE_START_FAILED',
            'EDGE_RESTARTED'
        )
    ),
    ADD CONSTRAINT ck_dev_command_state_shape CHECK (
        (
            physical_state = 'CREATED'
            AND queued_at IS NULL
            AND edge_accepted_at IS NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'QUEUED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'EDGE_ACCEPTED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state = 'PHYSICAL_STARTED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NOT NULL
            AND physical_ended_at IS NULL
        )
        OR
        (
            physical_state IN (
                'PHYSICAL_SUCCEEDED', 'PHYSICAL_FAILED'
            )
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NOT NULL
            AND physical_ended_at IS NOT NULL
        )
        OR
        (
            physical_state = 'PRE_START_FAILED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_started_at IS NULL
            AND physical_ended_at IS NOT NULL
        )
        OR
        (
            physical_state = 'EDGE_RESTARTED'
            AND queued_at IS NOT NULL
            AND edge_accepted_at IS NOT NULL
            AND physical_ended_at IS NOT NULL
        )
    );

ALTER TABLE dev_delivery_session
    DROP CHECK ck_dev_delivery_session_status,
    DROP CHECK ck_dev_delivery_session_terminal_shape,
    ADD CONSTRAINT ck_dev_delivery_session_status CHECK (
        status IN (
            'PREPARED',
            'AUTHORIZATION_QUEUED',
            'IN_PROGRESS',
            'RESULT_PENDING_RECOVERY',
            'BUSINESS_CONFIRMED',
            'PRE_OPEN_ENDED',
            'DEVICE_ABORTED'
        )
    ),
    ADD CONSTRAINT ck_dev_delivery_session_terminal_shape CHECK (
        (
            status IN (
                'PREPARED',
                'AUTHORIZATION_QUEUED',
                'IN_PROGRESS',
                'RESULT_PENDING_RECOVERY'
            )
            AND ended_at IS NULL
            AND end_reason IS NULL
        )
        OR
        (
            status IN (
                'BUSINESS_CONFIRMED',
                'PRE_OPEN_ENDED',
                'DEVICE_ABORTED'
            )
            AND ended_at IS NOT NULL
            AND end_reason IS NOT NULL
            AND CHAR_LENGTH(TRIM(end_reason)) > 0
        )
    );

ALTER TABLE rec_clean_operation
    DROP CHECK ck_rec_clean_operation_status,
    DROP CHECK ck_rec_clean_operation_state_shape,
    ADD CONSTRAINT ck_rec_clean_operation_status CHECK (
        status IN (
            'PREPARED',
            'EDGE_SAVED',
            'IN_PROGRESS',
            'RECOVERY_REQUIRED',
            'PRE_UNLOCK_ENDED',
            'COMPLETED',
            'ABORTED'
        )
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
        OR
        (
            status = 'ABORTED'
            AND completion_record_id IS NULL
            AND ended_at IS NOT NULL
            AND end_reason = 'EDGE_RESTARTED'
        )
    );

ALTER TABLE rec_port_baseline_measurement
    DROP CHECK ck_rec_baseline_measurement_state,
    ADD CONSTRAINT ck_rec_baseline_measurement_state CHECK (
        (
            status = 'PENDING'
            AND physical_result_id IS NULL
            AND stable_total_weight_g IS NULL
            AND fault_code IS NULL
            AND result_baseline_id IS NULL
            AND completed_at IS NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND physical_result_id IS NOT NULL
            AND stable_total_weight_g IS NOT NULL
            AND stable_total_weight_g >= 0
            AND fault_code IS NULL
            AND result_baseline_id IS NOT NULL
            AND completed_at IS NOT NULL
        )
        OR
        (
            status = 'FAILED'
            AND stable_total_weight_g IS NULL
            AND fault_code IS NOT NULL
            AND CHAR_LENGTH(TRIM(fault_code)) > 0
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
            AND (
                physical_result_id IS NOT NULL
                OR (
                    physical_result_id IS NULL
                    AND fault_code = 'EDGE_RESTARTED'
                )
            )
        )
        OR
        (
            status = 'STALE_IGNORED'
            AND physical_result_id IS NOT NULL
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
        )
    );

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
                        initial_sample_id IS NULL
                        AND initial_sample_conclusion IS NULL
                        AND terminal_sample_id IS NULL
                        AND terminal_sample_conclusion IS NULL
                        AND (
                            failure_code = 'EDGE_RESTARTED'
                            OR (
                                trigger_type = 'CLEAN_COMPLETE'
                                AND baseline_state_snapshot IN (
                                    'INVALID', 'MISSING'
                                )
                                AND failure_code =
                                    'WEIGHT_BASELINE_UNAVAILABLE'
                            )
                        )
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

CREATE TABLE rec_port_clean_restart_interlock (
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    deployment_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    source_clean_operation_id BIGINT NOT NULL,
    activated_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (tenant_id, organization_id, deployment_id, port_id),
    CONSTRAINT uq_rec_clean_restart_source
        UNIQUE (source_clean_operation_id),
    CONSTRAINT ck_rec_clean_restart_times CHECK (
        activated_at >= created_at
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_rec_clean_restart_port
        FOREIGN KEY (
            tenant_id, organization_id, deployment_id, port_id
        )
        REFERENCES dev_port (
            tenant_id, organization_id, deployment_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_restart_operation
        FOREIGN KEY (
            tenant_id, organization_id, deployment_id,
            port_id, source_clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id, organization_id, deployment_id,
            port_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;
