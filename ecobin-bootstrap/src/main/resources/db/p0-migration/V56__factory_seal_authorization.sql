-- V56: bind one reliable factory-seal authorization to the current
-- authoritative machine-acceptance generation and ordered factory-bag set.

ALTER TABLE dev_device_asset
    ADD COLUMN acceptance_generation BIGINT UNSIGNED NOT NULL DEFAULT 0
        AFTER acceptance_status;

UPDATE dev_device_asset
SET acceptance_generation = 1
WHERE acceptance_status = 'PASSED';

ALTER TABLE dev_device_asset
    ADD CONSTRAINT ck_dev_asset_acceptance_generation_v56 CHECK (
        acceptance_generation BETWEEN 0 AND 9007199254740991
    );

ALTER TABLE dev_device_acceptance_evidence
    ADD CONSTRAINT uq_dev_acceptance_asset_evidence_v56
        UNIQUE (asset_id, evidence_uid);

CREATE TABLE dev_factory_seal_authorization (
    id BIGINT NOT NULL AUTO_INCREMENT,
    asset_id BIGINT NOT NULL,
    hardware_sn_snapshot VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    acceptance_generation BIGINT UNSIGNED NOT NULL,
    acceptance_evidence_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    acceptance_challenge_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    acceptance_evidence_sha256 BINARY(32) NOT NULL,
    factory_bag_revision BIGINT UNSIGNED NOT NULL,
    factory_bag_set_sha256 BINARY(32) NOT NULL,
    command_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reliable_task_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    authorization_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    acknowledged_at DATETIME(3) NULL,
    cancelled_at DATETIME(3) NULL,
    cancellation_reason VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    completion_event_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    completion_payload_sha256 BINARY(32) NULL,
    image_release_id VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    image_release_sha256 BINARY(32) NULL,
    factory_report_sha256 BINARY(32) NULL,
    authorization_binding_sha256 BINARY(32) NULL,
    operator_confirmation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    sealed_at DATETIME(3) NULL,
    cleanup_completed_at DATETIME(3) NULL,
    completion_received_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_dev_factory_seal_asset_generation
        UNIQUE (asset_id, acceptance_generation),
    CONSTRAINT uq_dev_factory_seal_command UNIQUE (command_uid),
    CONSTRAINT uq_dev_factory_seal_task UNIQUE (reliable_task_uid),
    CONSTRAINT uq_dev_factory_seal_completion_event
        UNIQUE (completion_event_uid),
    CONSTRAINT uq_dev_factory_seal_evidence
        UNIQUE (asset_id, acceptance_evidence_uid),
    CONSTRAINT ck_dev_factory_seal_uids CHECK (
        acceptance_evidence_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND acceptance_challenge_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND command_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND reliable_task_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            completion_event_uid IS NULL
            OR completion_event_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
        AND (
            operator_confirmation_uid IS NULL
            OR operator_confirmation_uid REGEXP
                '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        )
    ),
    CONSTRAINT ck_dev_factory_seal_identity CHECK (
        CHAR_LENGTH(TRIM(hardware_sn_snapshot)) BETWEEN 1 AND 64
        AND acceptance_generation BETWEEN 1 AND 9007199254740991
        AND factory_bag_revision BETWEEN 0 AND 9007199254740991
    ),
    CONSTRAINT ck_dev_factory_seal_status CHECK (
        (
            authorization_status = 'PENDING'
            AND acknowledged_at IS NULL
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'ACKNOWLEDGED'
            AND acknowledged_at IS NOT NULL
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'CANCELLED'
            AND acknowledged_at IS NULL
            AND cancelled_at IS NOT NULL
            AND cancellation_reason IS NOT NULL
            AND cancellation_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'SEALED'
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NOT NULL
            AND completion_payload_sha256 IS NOT NULL
            AND image_release_id IS NOT NULL
            AND CHAR_LENGTH(image_release_id) BETWEEN 1 AND 128
            AND image_release_id REGEXP '^[!-~]+$'
            AND image_release_sha256 IS NOT NULL
            AND factory_report_sha256 IS NOT NULL
            AND authorization_binding_sha256 IS NOT NULL
            AND operator_confirmation_uid IS NOT NULL
            AND sealed_at IS NOT NULL
            AND cleanup_completed_at IS NOT NULL
            AND completion_received_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_dev_factory_seal_times CHECK (
        updated_at >= created_at
        AND (acknowledged_at IS NULL OR acknowledged_at >= created_at)
        AND (cancelled_at IS NULL OR cancelled_at >= created_at)
        AND (completion_received_at IS NULL
            OR completion_received_at >= created_at)
        AND (cleanup_completed_at IS NULL OR sealed_at IS NOT NULL)
        AND (cleanup_completed_at IS NULL OR cleanup_completed_at >= sealed_at)
    ),
    CONSTRAINT fk_dev_factory_seal_asset
        FOREIGN KEY (asset_id) REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_factory_seal_evidence
        FOREIGN KEY (asset_id, acceptance_evidence_uid)
        REFERENCES dev_device_acceptance_evidence (asset_id, evidence_uid)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_factory_seal_task
        FOREIGN KEY (reliable_task_uid) REFERENCES ops_reliable_task (task_uid)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_factory_seal_pending (
        authorization_status, created_at, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v52,
    ADD CONSTRAINT ck_ops_task_sources_v56 CHECK (
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
                                'SYNC_DEVICE_ENTRY_URL',
                                'OPEN_REMOTE_SUPPORT_TUNNEL',
                                'CLOSE_REMOTE_SUPPORT_TUNNEL',
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
