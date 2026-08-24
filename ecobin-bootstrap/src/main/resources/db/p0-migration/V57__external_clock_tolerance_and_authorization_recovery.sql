-- V57: treat timestamps produced by WeChat or an edge device as evidence,
-- never as a database-ordering authority over backend timestamps.
--
-- Raw external timestamps are preserved when supplied.  A nullable external
-- timestamp plus an explicit clock-quality fact allows an unsynchronised edge
-- to continue reporting while backend receive time remains authoritative for
-- ordering, deadlines and projections.

-- WeChat authorization package lifetime is not the same as the 24-hour
-- confirmation deadline.  A package returned by a display query is valid for
-- only ten minutes and must be refreshed independently.
ALTER TABLE fund_wechat_transfer_authorization
    ADD COLUMN package_expires_at DATETIME(3) NULL
        AFTER package_info;

UPDATE fund_wechat_transfer_authorization
SET package_expires_at = LEAST(
    DATE_ADD(updated_at, INTERVAL 10 MINUTE),
    confirmation_deadline_at
)
WHERE package_info IS NOT NULL
  AND confirmation_deadline_at IS NOT NULL;

ALTER TABLE fund_wechat_transfer_authorization
    DROP CHECK ck_fund_transfer_authorization_state,
    DROP CHECK ck_fund_transfer_authorization_times,
    ADD CONSTRAINT ck_fund_transfer_authorization_state CHECK (
        local_state IN (
            'CREATED',
            'CREATE_REJECTED',
            'WAIT_USER_CONFIRM',
            'ACTIVE',
            'CLOSED',
            'EXPIRED',
            'UNKNOWN'
        )
        AND (
            (
                local_state = 'CREATED'
                AND channel_state IS NULL
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND channel_created_at IS NULL
                AND confirmation_deadline_at IS NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'CREATE_REJECTED'
                AND channel_state IS NULL
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND last_api_error_code IS NOT NULL
                AND channel_created_at IS NULL
                AND confirmation_deadline_at IS NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'WAIT_USER_CONFIRM'
                AND channel_state = 'WAIT_USER_CONFIRM'
                AND authorization_id IS NULL
                AND package_info IS NOT NULL
                AND package_expires_at IS NOT NULL
                AND channel_created_at IS NOT NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'ACTIVE'
                AND channel_state = 'TAKING_EFFECT'
                AND authorization_id IS NOT NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND channel_created_at IS NOT NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NOT NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'CLOSED'
                AND channel_state = 'CLOSED'
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND channel_created_at IS NOT NULL
                AND confirmation_deadline_at IS NOT NULL
                AND closed_at IS NOT NULL
                AND close_reason IS NOT NULL
            )
            OR
            (
                local_state = 'EXPIRED'
                AND channel_state = 'WAIT_USER_CONFIRM'
                AND last_api_error_code = 'NOT_FOUND'
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND channel_created_at IS NOT NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NULL
                AND closed_at IS NOT NULL
                AND close_reason =
                    'USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION'
                AND closed_at >= DATE_ADD(
                    confirmation_deadline_at,
                    INTERVAL 30 DAY
                )
            )
            OR local_state = 'UNKNOWN'
        )
        AND (
            (package_info IS NULL AND package_expires_at IS NULL)
            OR (package_info IS NOT NULL AND package_expires_at IS NOT NULL)
        )
    ),
    ADD CONSTRAINT ck_fund_transfer_authorization_times CHECK (
        updated_at >= created_at
        AND (submitted_at IS NULL OR submitted_at >= created_at)
        AND (
            confirmation_deadline_at IS NULL
            OR confirmation_deadline_at =
                DATE_ADD(channel_created_at, INTERVAL 24 HOUR)
        )
        AND (
            authorized_at IS NULL
            OR authorized_at >= channel_created_at
        )
        AND (
            closed_at IS NULL
            OR closed_at >= channel_created_at
        )
    );

-- Machine-acceptance clock health is diagnostic.  An unsynchronised device
-- may pass the functional acceptance checks and report no observed_at.
ALTER TABLE dev_device_acceptance_evidence
    ADD COLUMN clock_quality VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ESTIMATED'
        AFTER trusted_time_healthy;

UPDATE dev_device_acceptance_evidence
SET clock_quality = CASE
    WHEN trusted_time_healthy = 1 THEN 'SYNCED'
    ELSE 'ESTIMATED'
END;

ALTER TABLE dev_device_acceptance_evidence
    DROP CHECK ck_dev_acceptance_evidence_result_v42,
    DROP CHECK ck_dev_acceptance_evidence_times,
    MODIFY COLUMN observed_at DATETIME(3) NULL,
    MODIFY COLUMN clock_quality VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
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
                AND JSON_LENGTH(failure_reasons_json) = 0
            )
        )
    ),
    ADD CONSTRAINT ck_dev_acceptance_evidence_times CHECK (
        evidence_schema_version > 0
        AND created_at = received_at
    );

-- Remote-support status ordering uses backend receive order plus the stable
-- row id.  The edge occurrence time remains optional diagnostic evidence.
ALTER TABLE dev_remote_support_status_event
    ADD COLUMN clock_quality VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ESTIMATED'
        AFTER occurred_at;

ALTER TABLE dev_remote_support_status_event
    DROP CHECK ck_dev_remote_support_status_event_times,
    MODIFY COLUMN occurred_at DATETIME(3) NULL,
    MODIFY COLUMN clock_quality VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_dev_remote_support_status_event_clock CHECK (
        clock_quality IN ('SYNCED', 'ESTIMATED', 'UNAVAILABLE')
    ),
    ADD CONSTRAINT ck_dev_remote_support_status_event_times CHECK (
        created_at = received_at
    );

DROP INDEX ix_dev_remote_support_status_history
    ON dev_remote_support_status_event;

CREATE INDEX ix_dev_remote_support_status_history_v57
    ON dev_remote_support_status_event (
        session_id, received_at DESC, id DESC
    );

-- A fullness report remains usable when the edge cannot establish UTC.
ALTER TABLE rec_fullness_sample
    DROP CHECK ck_rec_fullness_sample_times,
    MODIFY COLUMN device_occurred_at DATETIME(3) NULL,
    ADD CONSTRAINT ck_rec_fullness_sample_times CHECK (
        created_at >= backend_received_at
    );

ALTER TABLE dev_fullness_state_fact
    MODIFY COLUMN device_occurred_at DATETIME(3) NULL;

ALTER TABLE rec_fullness_state_change
    MODIFY COLUMN device_occurred_at DATETIME(3) NULL;

-- Remove comparisons between edge-produced timestamps and backend-produced
-- timestamps while retaining all backend-internal ordering checks.
ALTER TABLE rec_delivery_order
    DROP CHECK ck_rec_delivery_order_times,
    ADD CONSTRAINT ck_rec_delivery_order_times CHECK (
        created_at >= backend_received_at
        AND updated_at >= created_at
        AND (
            first_approved_at IS NULL
            OR first_approved_at >= created_at
        )
    );

ALTER TABLE rec_clean_record
    DROP CHECK ck_rec_clean_record_times,
    ADD CONSTRAINT ck_rec_clean_record_times CHECK (
        completed_at >= backend_received_at
        AND created_at >= completed_at
        AND updated_at >= created_at
    );

ALTER TABLE dev_photo_upload_grant_request
    DROP CHECK ck_dev_photo_grant_request_times,
    ADD CONSTRAINT ck_dev_photo_grant_request_times CHECK (
        updated_at >= created_at
        AND backend_received_at <= created_at
    );

ALTER TABLE rec_photo_terminal_fact
    DROP CHECK ck_rec_photo_terminal_state,
    DROP CHECK ck_rec_photo_terminal_times,
    ADD CONSTRAINT ck_rec_photo_terminal_state CHECK (
        (
            status = 'AVAILABLE'
            AND photo_uid IS NOT NULL
            AND object_url IS NOT NULL
            AND sha256 IS NOT NULL
            AND size_bytes > 0
            AND missing_reason IS NULL
        )
        OR
        (
            status = 'PERMANENTLY_MISSING'
            AND object_url IS NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    photo_uid IS NULL
                    AND sha256 IS NULL
                    AND size_bytes IS NULL
                    AND captured_at IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                )
            )
        )
    ),
    ADD CONSTRAINT ck_rec_photo_terminal_times CHECK (
        updated_at >= created_at
        AND backend_received_at <= created_at
        AND (linked_at IS NULL OR linked_at >= created_at)
    );

ALTER TABLE rec_delivery_photo
    DROP CHECK ck_rec_delivery_photo_times,
    ADD CONSTRAINT ck_rec_delivery_photo_times CHECK (
        updated_at >= created_at
        AND (linked_at IS NULL OR linked_at >= created_at)
    );

ALTER TABLE rec_clean_photo
    DROP CHECK ck_rec_clean_photo_times,
    ADD CONSTRAINT ck_rec_clean_photo_times CHECK (
        updated_at >= created_at
        AND (linked_at IS NULL OR linked_at >= created_at)
    );

-- Factory-seal schema v2 records whether the two device timestamps are
-- trustworthy.  Under an unsynchronised clock they are deliberately null;
-- backend completion_received_at still proves when the event was accepted.
ALTER TABLE dev_factory_seal_authorization
    ADD COLUMN completion_clock_quality VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER operator_confirmation_uid;

UPDATE dev_factory_seal_authorization
SET completion_clock_quality = 'SYNCED'
WHERE authorization_status = 'SEALED';

ALTER TABLE dev_factory_seal_authorization
    DROP CHECK ck_dev_factory_seal_status,
    ADD CONSTRAINT ck_dev_factory_seal_status CHECK (
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
            AND completion_clock_quality IS NULL
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
            AND completion_clock_quality IS NULL
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
            AND completion_clock_quality IS NULL
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
            AND completion_clock_quality IN (
                'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
            )
            AND (
                (
                    completion_clock_quality = 'SYNCED'
                    AND sealed_at IS NOT NULL
                    AND cleanup_completed_at IS NOT NULL
                )
                OR (
                    completion_clock_quality IN ('ESTIMATED', 'UNAVAILABLE')
                    AND sealed_at IS NULL
                    AND cleanup_completed_at IS NULL
                )
            )
            AND completion_received_at IS NOT NULL
        )
    );
