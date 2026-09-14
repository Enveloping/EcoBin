-- Admit only the three explicit post-start clean result failures that require
-- the existing manual bag recovery interlock.  The initial-weight zero-action
-- failure remains PRE_UNLOCK_ENDED and is deliberately absent from ABORTED.

ALTER TABLE rec_clean_operation
    DROP CHECK ck_rec_clean_operation_state_shape,
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
            AND (
                end_reason IN (
                    'EDGE_RESTARTED',
                    'MCU_RESTART_FINAL_RESULT_UNAVAILABLE',
                    'MCU_COMMUNICATION_UNAVAILABLE'
                )
                OR
                (
                    edge_saved_confirmed = 1
                    AND first_unlock_may_have_executed = 1
                    AND end_reason IN (
                        'MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE',
                        'MCU_WORK_CANCELLED',
                        'MCU_WORK_FAILED'
                    )
                )
            )
        )
    );

ALTER TABLE rec_clean_bag_recovery
    DROP CHECK ck_rec_clean_bag_recovery_fault,
    ADD CONSTRAINT ck_rec_clean_bag_recovery_fault CHECK (
        source_fault_code IN (
            'MCU_RESTART_FINAL_RESULT_UNAVAILABLE',
            'MCU_COMMUNICATION_UNAVAILABLE',
            'EDGE_RESTARTED',
            'MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE',
            'MCU_WORK_CANCELLED',
            'MCU_WORK_FAILED'
        )
    );
