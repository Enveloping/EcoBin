ALTER TABLE ops_task_attempt
    ADD COLUMN external_request_id VARCHAR(128)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER external_api_error_code,
    ADD CONSTRAINT ck_ops_attempt_external_request_v61 CHECK (
        external_request_id IS NULL
        OR (
            result_recorded_at IS NOT NULL
            AND external_request_id REGEXP
                '^[A-Za-z0-9._:-]{1,128}$'
        )
    );
