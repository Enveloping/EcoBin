-- Freeze Native payment requests and reserve immutable idempotency facts for
-- staff-authorized wallet adjustments.

ALTER TABLE fund_wechat_payment
    ADD COLUMN notify_url_snapshot
        VARCHAR(500) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER time_expire;

-- V31 uses one canonical digest for all Native requests. Historical rows keep
-- a nullable URL snapshot and may only be resumed when the current URL still
-- matches notify_url_sha256.
UPDATE fund_wechat_payment
SET request_sha256 = UNHEX(SHA2(CONCAT(
        'NATIVE_PAYMENT_REQUEST_V2|',
        OCTET_LENGTH(mchid_snapshot), ':', mchid_snapshot, '|',
        OCTET_LENGTH(appid_snapshot), ':', appid_snapshot, '|',
        OCTET_LENGTH(out_trade_no), ':', out_trade_no, '|',
        request_amount_cent, '|',
        OCTET_LENGTH(currency), ':', currency, '|',
        OCTET_LENGTH(description), ':', description, '|',
        TIMESTAMPDIFF(
            MICROSECOND,
            TIMESTAMP('1970-01-01 00:00:00.000'),
            time_expire
        ) DIV 1000,
        '|', LOWER(HEX(notify_url_sha256))
    ), 256));

ALTER TABLE fund_wallet_adjustment
    ADD COLUMN expected_wallet_version BIGINT NULL
        AFTER wallet_id,
    ADD COLUMN request_sha256 BINARY(32) NULL
        AFTER expected_wallet_version,
    ADD CONSTRAINT ck_fund_wallet_adjustment_request CHECK (
        (
            expected_wallet_version IS NULL
            AND request_sha256 IS NULL
        )
        OR
        (
            expected_wallet_version IS NOT NULL
            AND expected_wallet_version >= 0
            AND request_sha256 IS NOT NULL
        )
    );

UPDATE iam_permission_definition
SET description = '人工增减用户可用余额并生成不可变资金明细'
WHERE permission_code = 'wallet.adjust';
