-- Simulation provenance remains append-only diagnostic evidence. Machine
-- acceptance evaluates observable functionality and no longer treats a
-- simulated MCU or camera source as an automatic failure.

ALTER TABLE dev_device_acceptance_evidence
    DROP CHECK ck_dev_acceptance_evidence_result,
    ADD CONSTRAINT ck_dev_acceptance_evidence_result_v38 CHECK (
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
                AND JSON_LENGTH(failure_reasons_json) = 0
            )
        )
    );
