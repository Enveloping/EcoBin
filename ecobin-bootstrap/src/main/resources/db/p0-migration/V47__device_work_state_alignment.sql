-- V47: align terminal reliable-device outcomes with their business generations.
-- A technically aborted baseline has no trusted physical result and frees the
-- unique active-port slot. Any later physical result is stored as stale.

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
            status = 'TECHNICAL_ABORTED'
            AND physical_result_id IS NULL
            AND stable_total_weight_g IS NULL
            AND fault_code IS NOT NULL
            AND CHAR_LENGTH(TRIM(fault_code)) > 0
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
        )
        OR
        (
            status = 'STALE_IGNORED'
            AND physical_result_id IS NOT NULL
            AND stable_total_weight_g IS NULL
            AND result_baseline_id IS NULL
            AND completed_at IS NOT NULL
        )
    ),
    ADD INDEX ix_rec_baseline_measurement_generation (
        port_id,
        bag_id,
        device_config_version_id,
        initiator_kind,
        status,
        completed_at
    );
