-- V42: publish each permanent device's complete public miniapp entry URL to
-- the edge.  The application configuration remains the only source of the
-- global base URL; this singleton stores rollout progress, not configuration.

CREATE TABLE dev_device_entry_url_rollout (
    singleton_id TINYINT NOT NULL,
    rollout_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    base_url_sha256 BINARY(32) NOT NULL,
    rollout_status VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    next_asset_id BIGINT NOT NULL DEFAULT 0,
    started_at DATETIME(3) NOT NULL,
    completed_at DATETIME(3) NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (singleton_id),
    CONSTRAINT ck_dev_entry_url_rollout_singleton CHECK (
        singleton_id = 1
    ),
    CONSTRAINT ck_dev_entry_url_rollout_uid CHECK (
        rollout_uid = LOWER(rollout_uid)
        AND rollout_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_dev_entry_url_rollout_state CHECK (
        rollout_status IN ('PENDING', 'DONE')
        AND next_asset_id >= 0
        AND (
            (rollout_status = 'PENDING' AND completed_at IS NULL)
            OR
            (rollout_status = 'DONE' AND completed_at IS NOT NULL)
        )
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

-- Historical v1 acceptance rows remain immutable.  New v2 evidence must
-- contain the edge-persisted URL digest and PASSED requires an exact stored
-- URL fact; MCU/screen delivery is deliberately not represented here.
ALTER TABLE dev_device_acceptance_evidence
    ADD COLUMN device_entry_url_stored TINYINT NULL
        AFTER camera_upload_healthy,
    ADD COLUMN device_entry_url_sha256 BINARY(32) NULL
        AFTER device_entry_url_stored,
    DROP CHECK ck_dev_acceptance_evidence_result_v38,
    ADD CONSTRAINT ck_dev_acceptance_entry_url_v42 CHECK (
        (
            evidence_schema_version = 1
            AND device_entry_url_stored IS NULL
            AND device_entry_url_sha256 IS NULL
        )
        OR
        (
            evidence_schema_version >= 2
            AND device_entry_url_stored IN (0, 1)
            AND device_entry_url_sha256 IS NOT NULL
        )
    ),
    ADD CONSTRAINT ck_dev_acceptance_evidence_result_v42 CHECK (
        evaluation_status IN ('PASSED', 'FAILED')
        AND JSON_TYPE(failure_reasons_json) = 'ARRAY'
        AND JSON_TYPE(evidence_json) = 'OBJECT'
        AND (
            evaluation_status = 'FAILED'
            OR (
                onenet_online = 1
                AND persistent_store_healthy = 1
                AND trusted_time_healthy = 1
                AND configuration_persistence_healthy = 1
                AND mcu_communication_healthy = 1
                AND sensors_healthy = 1
                AND cameras_capture_healthy = 1
                AND camera_upload_healthy = 1
                AND (
                    evidence_schema_version = 1
                    OR device_entry_url_stored = 1
                )
                AND JSON_LENGTH(failure_reasons_json) = 0
            )
        )
    );

-- URL rollout is a platform-scoped control task anchored to the permanent
-- asset, just like pre-assignment acceptance and its confirmation.
ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_sources_v37,
    ADD CONSTRAINT ck_ops_task_sources_v42 CHECK (
        (
            task_category = 'INBOX_PROCESSING'
            AND source_inbox_id IS NOT NULL
            AND source_device_asset_id IS NULL
            AND source_device_command_id IS NULL
        )
        OR
        (
            task_category <> 'INBOX_PROCESSING'
            AND source_inbox_id IS NULL
            AND (
                (
                    source_device_asset_id IS NULL
                    AND source_device_command_id IS NULL
                )
                OR
                (
                    source_device_asset_id IS NOT NULL
                    AND (
                        (
                            scope_kind = 'PLATFORM'
                            AND tenant_id IS NULL
                            AND organization_id IS NULL
                            AND source_device_command_id IS NULL
                            AND task_type IN (
                                'REQUEST_DEVICE_ACCEPTANCE',
                                'SYNC_DEVICE_ENTRY_URL',
                                'CONFIRM_EDGE_EVENT'
                            )
                        )
                        OR
                        (
                            scope_kind = 'ORGANIZATION'
                            AND (
                                source_device_command_id IS NOT NULL
                                OR
                                (
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
