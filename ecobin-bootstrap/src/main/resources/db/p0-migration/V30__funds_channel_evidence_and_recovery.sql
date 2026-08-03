-- Preserve authoritative WeChat query evidence and make payout recovery exact.

ALTER TABLE fund_wechat_transfer
    ADD COLUMN notify_url_snapshot
        VARCHAR(500) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER transfer_page_style_snapshot;

-- V30 is the first release that reproduces every original transfer parameter.
-- Existing rows can still be verified from their immutable snapshots and the
-- previously stored notification URL digest. The URL itself remains nullable
-- for those rows and is only reused when the current URL has that digest.
UPDATE fund_wechat_transfer
SET request_sha256 = UNHEX(SHA2(CONCAT(
        'TRANSFER_REQUEST_V2|',
        OCTET_LENGTH(mchid_snapshot), ':', mchid_snapshot, '|',
        OCTET_LENGTH(appid_snapshot), ':', appid_snapshot, '|',
        OCTET_LENGTH(out_bill_no), ':', out_bill_no, '|',
        OCTET_LENGTH(openid_snapshot), ':', openid_snapshot, '|',
        amount_cent, '|',
        OCTET_LENGTH(scene_id_snapshot), ':', scene_id_snapshot, '|',
        OCTET_LENGTH(report_type_snapshot), ':', report_type_snapshot, '|',
        OCTET_LENGTH(report_content_snapshot), ':', report_content_snapshot,
        '|', OCTET_LENGTH(transfer_remark), ':', transfer_remark, '|',
        OCTET_LENGTH(transfer_page_style_snapshot), ':',
        transfer_page_style_snapshot, '|', LOWER(HEX(notify_url_sha256))
    ), 256));

ALTER TABLE fund_wechat_payment_observation
    ADD COLUMN observed_mchid
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER transaction_id,
    ADD COLUMN observed_appid
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER observed_mchid,
    ADD COLUMN observed_out_trade_no
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER observed_appid,
    ADD COLUMN observed_currency
        CHAR(3) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER payer_openid;

ALTER TABLE fund_wechat_transfer_observation
    ADD COLUMN observed_mchid
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER terminal_fail_reason,
    ADD COLUMN observed_appid
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER observed_mchid,
    ADD COLUMN observed_out_bill_no
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER out_bill_no;

ALTER TABLE fund_payout_gate_event
    ADD COLUMN restore_request_sha256 BINARY(32) NULL
        AFTER restored_by_platform_admin_id,
    ADD COLUMN gate_version_before BIGINT NULL
        AFTER restore_request_sha256,
    ADD COLUMN gate_version_after BIGINT NULL
        AFTER gate_version_before,
    ADD CONSTRAINT ck_fund_payout_gate_event_restore_idempotency CHECK (
        (
            event_type = 'PAUSED'
            AND restore_request_sha256 IS NULL
            AND gate_version_before IS NULL
            AND gate_version_after IS NULL
        )
        OR
        (
            event_type = 'RESTORED'
            AND (
                (
                    restore_request_sha256 IS NULL
                    AND gate_version_before IS NULL
                    AND gate_version_after IS NULL
                )
                OR
                (
                    restore_request_sha256 IS NOT NULL
                    AND gate_version_before IS NOT NULL
                    AND gate_version_before >= 0
                    AND gate_version_after = gate_version_before + 1
                )
            )
        )
    );

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_dispatch_wait,
    MODIFY COLUMN dispatch_wait_reason
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD CONSTRAINT ck_ops_task_dispatch_wait CHECK (
        (
            state = 'PENDING'
            AND (
                dispatch_wait_reason IS NULL
                OR dispatch_wait_reason IN (
                    'DEVICE_OFFLINE',
                    'DEVICE_PRESENCE_UNKNOWN',
                    'RUNTIME_MISSING',
                    'AWAITING_DEVICE_EVIDENCE'
                )
                OR dispatch_wait_reason REGEXP
                    '^PAYOUT_NOT_ENOUGH:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            )
        )
        OR
        (
            state <> 'PENDING'
            AND dispatch_wait_reason IS NULL
        )
    );

ALTER TABLE ops_reconciliation_issue
    DROP FOREIGN KEY fk_ops_reconciliation_issue_first_run,
    DROP FOREIGN KEY fk_ops_reconciliation_issue_latest_run,
    MODIFY COLUMN first_seen_run_id BIGINT NULL,
    MODIFY COLUMN latest_seen_run_id BIGINT NULL,
    ADD COLUMN first_seen_task_attempt_id BIGINT NULL
        AFTER latest_seen_run_id,
    ADD COLUMN latest_seen_task_attempt_id BIGINT NULL
        AFTER first_seen_task_attempt_id,
    ADD CONSTRAINT ck_ops_reconciliation_issue_source CHECK (
        (
            (
                first_seen_run_id IS NOT NULL
                AND first_seen_task_attempt_id IS NULL
            )
            OR
            (
                first_seen_run_id IS NULL
                AND first_seen_task_attempt_id IS NOT NULL
            )
        )
        AND
        (
            (
                latest_seen_run_id IS NOT NULL
                AND latest_seen_task_attempt_id IS NULL
            )
            OR
            (
                latest_seen_run_id IS NULL
                AND latest_seen_task_attempt_id IS NOT NULL
            )
        )
    ),
    ADD CONSTRAINT fk_ops_reconciliation_issue_first_run_v30
        FOREIGN KEY (first_seen_run_id)
        REFERENCES ops_reconciliation_run (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_ops_reconciliation_issue_latest_run_v30
        FOREIGN KEY (latest_seen_run_id)
        REFERENCES ops_reconciliation_run (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_ops_reconciliation_issue_first_attempt
        FOREIGN KEY (first_seen_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_ops_reconciliation_issue_latest_attempt
        FOREIGN KEY (latest_seen_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_ops_reconciliation_issue_first_attempt_fk (
        first_seen_task_attempt_id
    ),
    ADD INDEX ix_ops_reconciliation_issue_latest_attempt_fk (
        latest_seen_task_attempt_id
    );
