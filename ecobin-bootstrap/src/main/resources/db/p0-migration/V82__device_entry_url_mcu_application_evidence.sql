-- V82: factory acceptance v5 proves that the MCU atomically enqueued the
-- complete Nextion QR-code command.  Under the product trust boundary, this
-- enqueue fact is the authoritative display fact; USART completion and HMI
-- readback are deliberately outside the protocol.

ALTER TABLE dev_device_acceptance_evidence
    ADD COLUMN device_entry_url_mcu_applied TINYINT NULL
        AFTER device_entry_url_sha256,
    ADD COLUMN device_entry_url_applied_sha256 BINARY(32) NULL
        AFTER device_entry_url_mcu_applied,
    ADD COLUMN device_entry_url_applied_mcu_boot_id BIGINT NULL
        AFTER device_entry_url_applied_sha256,
    ADD COLUMN device_entry_url_display_basis VARCHAR(48)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER device_entry_url_applied_mcu_boot_id,
    ADD CONSTRAINT ck_dev_acceptance_entry_url_apply_v82 CHECK (
        (
            evidence_schema_version < 5
            AND device_entry_url_mcu_applied IS NULL
            AND device_entry_url_applied_sha256 IS NULL
            AND device_entry_url_applied_mcu_boot_id IS NULL
            AND device_entry_url_display_basis IS NULL
        )
        OR (
            evidence_schema_version >= 5
            AND device_entry_url_mcu_applied IN (0, 1)
            AND (
                (
                    device_entry_url_mcu_applied = 1
                    AND device_entry_url_applied_sha256 IS NOT NULL
                    AND device_entry_url_applied_mcu_boot_id > 0
                    AND device_entry_url_display_basis =
                        'UART3_COMMAND_ATOMICALLY_QUEUED'
                )
                OR (
                    device_entry_url_mcu_applied = 0
                    AND device_entry_url_applied_sha256 IS NULL
                    AND device_entry_url_applied_mcu_boot_id IS NULL
                    AND device_entry_url_display_basis = 'NOT_APPLIED'
                )
            )
        )
    ),
    DROP CHECK ck_dev_acceptance_evidence_result,
    ADD CONSTRAINT ck_dev_acceptance_evidence_result CHECK (
        evaluation_status IN ('PASSED', 'FAILED')
        AND clock_quality IN ('SYNCED', 'ESTIMATED', 'UNAVAILABLE')
        AND JSON_TYPE(failure_reasons_json) = 'ARRAY'
        AND JSON_TYPE(evidence_json) = 'OBJECT'
        AND (
            evaluation_status = 'FAILED'
            OR (
                onenet_online = 1
                AND persistent_store_healthy = 1
                AND configuration_persistence_healthy = 1
                AND mcu_communication_healthy = 1
                AND sensors_healthy = 1
                AND cameras_capture_healthy = 1
                AND camera_upload_healthy = 1
                AND (
                    evidence_schema_version = 1
                    OR device_entry_url_stored = 1
                )
                AND (
                    evidence_schema_version < 5
                    OR (
                        device_entry_url_mcu_applied = 1
                        AND device_entry_url_display_basis =
                            'UART3_COMMAND_ATOMICALLY_QUEUED'
                    )
                )
                AND JSON_LENGTH(failure_reasons_json) = 0
            )
        )
    );
