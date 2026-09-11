-- V69: issue-only closure for one delivery with an unknowable physical result.
--
-- This is deliberately not DELIVERY_COMPLETE. The terminal device fact moves
-- only dev_delivery_session to DEVICE_ABORTED, releases the exact occupancy,
-- and archives already-existing measurements/photos for diagnosis. No
-- recycling order or funds-side row is created from this record.

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v66,
    ADD CONSTRAINT ck_ops_task_sources_v69 CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_asset_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_asset_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR (
                    source_device_asset_id IS NOT NULL
                    AND (
                        (
                            scope_kind = 'PLATFORM'
                            AND tenant_id IS NULL
                            AND organization_id IS NULL
                            AND source_device_command_id IS NULL
                            AND task_type IN (
                                'REQUEST_DEVICE_ACCEPTANCE',
                                'AUTHORIZE_FACTORY_SEAL',
                                'START_MCU_FIRMWARE_UPDATE',
                                'START_BUSINESS_RUNTIME_UPDATE',
                                'CANCEL_BUSINESS_RUNTIME_UPDATE',
                                'SYNC_DEVICE_ENTRY_URL',
                                'OPEN_REMOTE_SUPPORT_TUNNEL',
                                'CLOSE_REMOTE_SUPPORT_TUNNEL',
                                'QUARANTINE_DELIVERY_RECOVERY',
                                'CONFIRM_EDGE_EVENT'
                            )
                        )
                        OR (
                            scope_kind = 'ORGANIZATION'
                            AND (
                                source_device_command_id IS NOT NULL
                                OR (
                                    source_device_command_id IS NULL
                                    AND task_type IN (
                                        'CONFIRM_EDGE_EVENT',
                                        'PROVIDE_PHOTO_UPLOAD_GRANT'
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    );

CREATE TABLE dev_delivery_recovery_quarantine (
    id BIGINT NOT NULL AUTO_INCREMENT,
    recovery_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    asset_id BIGINT NOT NULL,
    delivery_session_id BIGINT NOT NULL,
    original_command_id BIGINT NOT NULL,
    original_command_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    original_task_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    recovery_command_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    recovery_task_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    requested_by_platform_admin_id BIGINT NOT NULL,
    expected_session_version BIGINT NOT NULL,
    physical_outcome_unknown_confirmed TINYINT NOT NULL,
    cause_fixed_confirmed TINYINT NOT NULL,
    device_power_cycled_confirmed TINYINT NOT NULL,
    motion_area_clear_confirmed TINYINT NOT NULL,
    delivery_door_closed_confirmed TINYINT NOT NULL,
    mechanism_clear_confirmed TINYINT NOT NULL,
    reason VARCHAR(500) NOT NULL,
    state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_value VARCHAR(8)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    requested_at DATETIME(3) NOT NULL,
    applied_at DATETIME(3) NULL,
    source_inbox_id BIGINT NULL,
    terminal_event_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    terminal_event_payload_sha256 BINARY(32) NULL,
    resolution_evidence_sha256 BINARY(32) NULL,
    operator_confirmations_json JSON NULL,
    device_evidence_json JSON NULL,
    existing_data_json JSON NULL,
    terminal_payload_json JSON NULL,
    active_delivery_session_id BIGINT
        GENERATED ALWAYS AS (
            CASE WHEN state = 'QUEUED'
                THEN delivery_session_id ELSE NULL END
        ) STORED,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_delivery_recovery_uid UNIQUE (recovery_uid),
    CONSTRAINT uq_dev_delivery_recovery_command
        UNIQUE (recovery_command_uid),
    CONSTRAINT uq_dev_delivery_recovery_task UNIQUE (recovery_task_uid),
    CONSTRAINT uq_dev_delivery_recovery_event UNIQUE (terminal_event_uid),
    CONSTRAINT uq_dev_delivery_recovery_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_dev_delivery_recovery_active_session
        UNIQUE (active_delivery_session_id),
    CONSTRAINT ck_dev_delivery_recovery_uids CHECK (
        recovery_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND original_command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND original_task_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND recovery_command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            recovery_task_uid IS NULL
            OR recovery_task_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
        AND (
            terminal_event_uid IS NULL
            OR terminal_event_uid = recovery_uid
        )
    ),
    CONSTRAINT ck_dev_delivery_recovery_identity CHECK (
        recovery_uid <> original_command_uid
        AND recovery_uid <> recovery_command_uid
        AND original_command_uid <> recovery_command_uid
    ),
    CONSTRAINT ck_dev_delivery_recovery_confirmation CHECK (
        physical_outcome_unknown_confirmed = 1
        AND cause_fixed_confirmed = 1
        AND device_power_cycled_confirmed = 1
        AND motion_area_clear_confirmed = 1
        AND delivery_door_closed_confirmed = 1
        AND mechanism_clear_confirmed = 1
    ),
    CONSTRAINT ck_dev_delivery_recovery_business_value CHECK (
        business_value = 'NONE'
    ),
    CONSTRAINT ck_dev_delivery_recovery_state CHECK (
        state IN ('QUEUED', 'APPLIED', 'CANCELLED')
        AND CHAR_LENGTH(TRIM(reason)) BETWEEN 1 AND 500
        AND expected_session_version >= 0
        AND lock_version >= 0
        AND (
            (
                state = 'QUEUED'
                AND applied_at IS NULL
                AND source_inbox_id IS NULL
                AND terminal_event_uid IS NULL
                AND terminal_event_payload_sha256 IS NULL
                AND resolution_evidence_sha256 IS NULL
                AND operator_confirmations_json IS NULL
                AND device_evidence_json IS NULL
                AND existing_data_json IS NULL
                AND terminal_payload_json IS NULL
            )
            OR (
                state = 'APPLIED'
                AND recovery_task_uid IS NOT NULL
                AND applied_at IS NOT NULL
                AND source_inbox_id IS NOT NULL
                AND terminal_event_uid = recovery_uid
                AND terminal_event_payload_sha256 IS NOT NULL
                AND resolution_evidence_sha256 IS NOT NULL
                AND operator_confirmations_json IS NOT NULL
                AND device_evidence_json IS NOT NULL
                AND existing_data_json IS NOT NULL
                AND terminal_payload_json IS NOT NULL
            )
            OR (
                state = 'CANCELLED'
                AND applied_at IS NULL
                AND source_inbox_id IS NULL
                AND terminal_event_uid IS NULL
                AND terminal_event_payload_sha256 IS NULL
                AND resolution_evidence_sha256 IS NULL
                AND operator_confirmations_json IS NULL
                AND device_evidence_json IS NULL
                AND existing_data_json IS NULL
                AND terminal_payload_json IS NULL
            )
        )
    ),
    CONSTRAINT ck_dev_delivery_recovery_times CHECK (
        updated_at >= created_at
        AND requested_at >= created_at
        AND (applied_at IS NULL OR applied_at >= requested_at)
    ),
    CONSTRAINT fk_dev_delivery_recovery_organization
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_recovery_asset
        FOREIGN KEY (tenant_id, organization_id, asset_id)
        REFERENCES dev_device_asset (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_recovery_session
        FOREIGN KEY (tenant_id, organization_id, asset_id,
                     delivery_session_id)
        REFERENCES dev_delivery_session (
            tenant_id, organization_id, asset_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_recovery_original_command
        FOREIGN KEY (tenant_id, organization_id, asset_id,
                     original_command_id)
        REFERENCES dev_device_command (
            tenant_id, organization_id, asset_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_recovery_admin
        FOREIGN KEY (requested_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_delivery_recovery_inbox
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_delivery_recovery_asset (
        asset_id, state, requested_at, id
    ),
    INDEX ix_dev_delivery_recovery_session_fk (
        tenant_id, organization_id, asset_id, delivery_session_id
    ),
    INDEX ix_dev_delivery_recovery_command_fk (
        tenant_id, organization_id, asset_id, original_command_id
    ),
    INDEX ix_dev_delivery_recovery_admin (
        requested_by_platform_admin_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;
