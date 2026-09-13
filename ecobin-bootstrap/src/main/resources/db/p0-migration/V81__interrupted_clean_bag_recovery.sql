-- Manual closure for a clean whose physical bag state became uncertain.
-- The operator may retain the frozen old bag, or confirm the originally
-- reserved new bag and establish a separate empty-bag baseline.  This never
-- creates a normal clean record or derives the interrupted clean weight.
-- EDGE_RESTARTED remains accepted only to close historical/older-edge
-- interlocks; the native runtime continues Pi-only restarts instead.

CREATE TABLE rec_clean_bag_recovery (
    id BIGINT NOT NULL AUTO_INCREMENT,
    recovery_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    port_id BIGINT NOT NULL,
    clean_operation_id BIGINT NOT NULL,
    actual_bag_id BIGINT NOT NULL,
    decision VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_fault_code VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    cleaner_organization_user_id BIGINT NOT NULL,
    operator_reason VARCHAR(500) NOT NULL,
    requested_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_rec_clean_bag_recovery_uid UNIQUE (recovery_uid),
    CONSTRAINT uq_rec_clean_bag_recovery_operation
        UNIQUE (clean_operation_id),
    CONSTRAINT uq_rec_clean_bag_recovery_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_rec_clean_bag_recovery_uid CHECK (
        recovery_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_rec_clean_bag_recovery_decision CHECK (
        decision IN ('RETAIN_OLD_BAG', 'USE_RESERVED_NEW_BAG')
    ),
    CONSTRAINT ck_rec_clean_bag_recovery_state CHECK (
        (
            decision = 'RETAIN_OLD_BAG'
            AND status = 'COMPLETED'
            AND completed_at IS NOT NULL
        )
        OR
        (
            decision = 'USE_RESERVED_NEW_BAG'
            AND (
                (status IN ('BASELINE_PENDING', 'BASELINE_REQUIRED')
                    AND completed_at IS NULL)
                OR (status = 'COMPLETED' AND completed_at IS NOT NULL)
            )
        )
    ),
    CONSTRAINT ck_rec_clean_bag_recovery_fault CHECK (
        source_fault_code IN (
            'MCU_RESTART_FINAL_RESULT_UNAVAILABLE',
            'MCU_COMMUNICATION_UNAVAILABLE',
            'EDGE_RESTARTED'
        )
    ),
    CONSTRAINT ck_rec_clean_bag_recovery_reason CHECK (
        CHAR_LENGTH(TRIM(operator_reason)) BETWEEN 1 AND 500
    ),
    CONSTRAINT ck_rec_clean_bag_recovery_times CHECK (
        requested_at >= created_at
        AND updated_at >= created_at
        AND (completed_at IS NULL OR completed_at >= requested_at)
        AND lock_version >= 0
    ),
    CONSTRAINT fk_rec_clean_bag_recovery_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_bag_recovery_operation
        FOREIGN KEY (
            tenant_id, organization_id, asset_id,
            port_id, clean_operation_id
        )
        REFERENCES rec_clean_operation (
            tenant_id, organization_id, asset_id,
            port_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_bag_recovery_bag
        FOREIGN KEY (tenant_id, organization_id, actual_bag_id)
        REFERENCES rec_bag (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_rec_clean_bag_recovery_cleaner
        FOREIGN KEY (
            tenant_id, organization_id,
            cleaner_organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id, organization_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_rec_clean_bag_recovery_port (
        tenant_id, organization_id, asset_id, port_id, status, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rec_port_baseline_measurement
    ADD COLUMN clean_bag_recovery_id BIGINT NULL
        AFTER staff_account_id,
    ADD CONSTRAINT fk_rec_baseline_clean_bag_recovery
        FOREIGN KEY (
            tenant_id, organization_id, clean_bag_recovery_id
        )
        REFERENCES rec_clean_bag_recovery (
            tenant_id, organization_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_baseline_clean_bag_recovery (
        tenant_id, organization_id, clean_bag_recovery_id,
        started_at, id
    );

ALTER TABLE rec_bag_occupancy_event
    DROP CHECK ck_rec_bag_event_source,
    ADD CONSTRAINT ck_rec_bag_event_source CHECK (
        (
            event_type = 'INITIAL_INSTALLED'
            AND clean_operation_id IS NULL
        )
        OR
        (
            event_type IN (
                'RESERVED_FOR_CLEAN',
                'RESERVATION_RELEASED',
                'REMOVED_BY_CLEAN',
                'INSTALLED_BY_CLEAN',
                'REMOVED_BY_CLEAN_RECOVERY',
                'INSTALLED_BY_CLEAN_RECOVERY'
            )
            AND clean_operation_id IS NOT NULL
        )
    );

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
            AND end_reason IN (
                'EDGE_RESTARTED',
                'MCU_RESTART_FINAL_RESULT_UNAVAILABLE',
                'MCU_COMMUNICATION_UNAVAILABLE'
            )
        )
    );
