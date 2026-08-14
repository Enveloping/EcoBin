-- Make a definitively rejected authorization creation a local terminal fact.
--
-- A signed WeChat PARAM_ERROR/NO_AUTH/SIGN_ERROR response, or a local
-- merchant-profile mismatch before the call, proves that WeChat did not
-- accept the immutable request. Keeping such a row in CREATED made the
-- current authorization slot impossible to replace, while replaying the old
-- task could only resend the same bad snapshot. Untrusted response evidence
-- remains UNKNOWN and continues to occupy the slot.

ALTER TABLE fund_wechat_transfer_authorization
    DROP CHECK ck_fund_transfer_authorization_state,
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
    );

UPDATE fund_wechat_transfer_authorization
SET local_state = 'CREATE_REJECTED',
    submitted_at = COALESCE(submitted_at, updated_at),
    channel_updated_at = COALESCE(channel_updated_at, updated_at),
    lock_version = lock_version + 1
WHERE local_state = 'CREATED'
  AND last_api_error_code IN (
      'PARAM_ERROR', 'NO_AUTH', 'SIGN_ERROR', 'MCHID_MISMATCH'
  );

UPDATE fund_wechat_transfer_authorization
SET local_state = 'UNKNOWN',
    state_conflict = 1,
    lock_version = lock_version + 1
WHERE local_state = 'CREATED'
  AND last_api_error_code IN (
      'SIGNATURE_ERROR', 'RESPONSE_SIGNATURE_INVALID'
  );
