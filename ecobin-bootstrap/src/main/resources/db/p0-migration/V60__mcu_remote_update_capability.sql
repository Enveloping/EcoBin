-- V60: persist the device's explicitly observed MCU remote-update wiring
-- capability without turning that optional production feature into a factory
-- acceptance requirement. Existing assets and schema-v3 evidence remain
-- UNKNOWN (NULL); no capability is inferred from protocol revision or firmware.

ALTER TABLE dev_device_acceptance_evidence
    ADD COLUMN mcu_remote_update_capable TINYINT NULL
        AFTER mcu_communication_healthy,
    ADD CONSTRAINT ck_dev_acceptance_mcu_remote_update_v60 CHECK (
        (
            evidence_schema_version < 4
            AND mcu_remote_update_capable IS NULL
        )
        OR (
            evidence_schema_version >= 4
            AND mcu_remote_update_capable IS NOT NULL
            AND mcu_remote_update_capable IN (0, 1)
        )
    );

ALTER TABLE dev_device_asset
    ADD COLUMN mcu_remote_update_capable TINYINT NULL
        AFTER mcu_fixed_frame_revision,
    ADD CONSTRAINT ck_dev_asset_mcu_remote_update_v60 CHECK (
        mcu_remote_update_capable IS NULL
        OR mcu_remote_update_capable IN (0, 1)
    );
