-- Add the ordinary-merchant APIv3 user-authorized collection model.
--
-- This migration is deliberately expand-compatible with the currently
-- deployed user-confirm flow. Existing and concurrently created legacy rows
-- default to USER_CONFIRM. The new application must explicitly choose
-- AUTHORIZED and provide one immutable TAKING_EFFECT authorization snapshot.

ALTER TABLE iam_organization_user
    ADD CONSTRAINT uq_iam_org_user_transfer_authorization_ref UNIQUE (
        tenant_id,
        organization_miniapp_id,
        openid,
        organization_id,
        id
    );

CREATE TABLE fund_wechat_transfer_authorization (
    id BIGINT NOT NULL AUTO_INCREMENT,
    authorization_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    miniapp_merchant_binding_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    out_authorization_no VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL,
    authorization_id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    mchid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    appid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    openid_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scene_id_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_display_name_snapshot VARCHAR(32) NOT NULL,
    user_recv_perception_snapshot VARCHAR(256) NULL,
    authorization_notify_url_snapshot VARCHAR(256)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    notify_url_sha256 BINARY(32) NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    local_state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    package_info VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    close_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    state_conflict TINYINT NOT NULL DEFAULT 0,
    submitted_at DATETIME(3) NULL,
    channel_created_at DATETIME(3) NULL,
    confirmation_deadline_at DATETIME(3) NULL,
    authorized_at DATETIME(3) NULL,
    closed_at DATETIME(3) NULL,
    channel_updated_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    current_authorization_slot TINYINT
        GENERATED ALWAYS AS (
            CASE
                WHEN local_state IN (
                    'CREATED',
                    'WAIT_USER_CONFIRM',
                    'ACTIVE',
                    'UNKNOWN'
                ) THEN 1
                ELSE NULL
            END
        ) STORED,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_transfer_authorization_uid UNIQUE (authorization_uid),
    CONSTRAINT uq_fund_transfer_authorization_out_no
        UNIQUE (out_authorization_no),
    CONSTRAINT uq_fund_transfer_authorization_wechat_id
        UNIQUE (authorization_id),
    CONSTRAINT uq_fund_transfer_authorization_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_transfer_authorization_current UNIQUE (
        merchant_profile_id,
        appid_snapshot,
        openid_snapshot,
        scene_id_snapshot,
        current_authorization_slot
    ),
    CONSTRAINT uq_fund_transfer_auth_withdrawal_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        organization_user_id,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        mchid_snapshot,
        appid_snapshot,
        openid_snapshot,
        out_authorization_no,
        authorization_id
    ),
    CONSTRAINT uq_fund_transfer_auth_transfer_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        mchid_snapshot,
        appid_snapshot,
        openid_snapshot,
        out_authorization_no,
        authorization_id
    ),
    CONSTRAINT ck_fund_transfer_authorization_uid_v4 CHECK (
        authorization_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_transfer_authorization_numbers CHECK (
        out_authorization_no REGEXP '^[A-Za-z0-9]{8,32}$'
        AND (
            authorization_id IS NULL
            OR (
                BINARY authorization_id = BINARY TRIM(authorization_id)
                AND CHAR_LENGTH(authorization_id) BETWEEN 1 AND 32
            )
        )
    ),
    CONSTRAINT ck_fund_transfer_authorization_request CHECK (
        CHAR_LENGTH(TRIM(mchid_snapshot)) > 0
        AND CHAR_LENGTH(TRIM(appid_snapshot)) > 0
        AND CHAR_LENGTH(TRIM(openid_snapshot)) > 0
        AND CHAR_LENGTH(TRIM(scene_id_snapshot)) > 0
        AND CHAR_LENGTH(TRIM(user_display_name_snapshot)) BETWEEN 1 AND 32
        AND (
            user_recv_perception_snapshot IS NULL
            OR CHAR_LENGTH(TRIM(user_recv_perception_snapshot)) BETWEEN 1 AND 256
        )
        AND BINARY authorization_notify_url_snapshot =
            BINARY TRIM(authorization_notify_url_snapshot)
        AND authorization_notify_url_snapshot REGEXP '^https://[^?#]+$'
    ),
    CONSTRAINT ck_fund_transfer_authorization_state CHECK (
        local_state IN (
            'CREATED',
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
    ),
    CONSTRAINT ck_fund_transfer_authorization_flags CHECK (
        state_conflict IN (0, 1)
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_transfer_authorization_times CHECK (
        updated_at >= created_at
        AND (submitted_at IS NULL OR submitted_at >= created_at)
        AND (
            channel_created_at IS NULL
            OR channel_created_at >= created_at
        )
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
        AND (
            channel_updated_at IS NULL
            OR channel_updated_at >= created_at
        )
    ),
    CONSTRAINT fk_fund_transfer_authorization_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_authorization_recipient
        FOREIGN KEY (
            tenant_id,
            organization_miniapp_id,
            openid_snapshot,
            organization_id,
            organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_miniapp_id,
            openid,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_authorization_merchant
        FOREIGN KEY (merchant_profile_id, mchid_snapshot)
        REFERENCES fund_wechat_merchant_profile (id, mchid)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_authorization_binding
        FOREIGN KEY (
            tenant_id,
            organization_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            merchant_profile_id,
            appid_snapshot
        )
        REFERENCES fund_miniapp_merchant_binding (
            tenant_id,
            organization_id,
            id,
            organization_miniapp_id,
            merchant_profile_id,
            appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_transfer_authorization_recipient_fk (
        tenant_id,
        organization_id,
        organization_user_id,
        organization_miniapp_id,
        openid_snapshot
    ),
    INDEX ix_fund_transfer_authorization_merchant_fk (
        merchant_profile_id,
        mchid_snapshot
    ),
    INDEX ix_fund_transfer_authorization_binding_fk (
        tenant_id,
        organization_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        merchant_profile_id,
        appid_snapshot
    ),
    INDEX ix_fund_transfer_authorization_user_state (
        tenant_id,
        organization_id,
        organization_user_id,
        local_state,
        updated_at,
        id
    ),
    INDEX ix_fund_transfer_authorization_deadline (
        local_state,
        confirmation_deadline_at,
        id
    ),
    INDEX ix_fund_transfer_authorization_channel (
        merchant_profile_id,
        channel_state,
        channel_updated_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wechat_transfer_authorization_observation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    observation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    transfer_authorization_id BIGINT NOT NULL,
    observation_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL,
    source_scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ORGANIZATION',
    source_inbox_id BIGINT NULL,
    source_task_attempt_id BIGINT NULL,
    raw_channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    close_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    out_authorization_no VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL,
    observed_out_authorization_no VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_authorization_id VARCHAR(32)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_appid VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_openid VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_scene_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_user_display_name VARCHAR(32) NULL,
    observed_user_recv_perception VARCHAR(256) NULL,
    package_info VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NULL,
    observed_channel_created_at DATETIME(3) NULL,
    observed_authorized_at DATETIME(3) NULL,
    observed_closed_at DATETIME(3) NULL,
    content_sha256 BINARY(32) NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_transfer_auth_observation_uid UNIQUE (observation_uid),
    CONSTRAINT uq_fund_transfer_auth_observation_inbox
        UNIQUE (source_inbox_id),
    CONSTRAINT uq_fund_transfer_auth_observation_attempt
        UNIQUE (source_task_attempt_id),
    CONSTRAINT uq_fund_transfer_auth_observation_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_transfer_auth_observation_uid_v4 CHECK (
        observation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_transfer_auth_observation_type CHECK (
        observation_type IN ('CREATE_RESPONSE', 'CALLBACK', 'QUERY')
    ),
    CONSTRAINT ck_fund_transfer_auth_observation_source CHECK (
        source_scope_kind = 'ORGANIZATION'
        AND (
            (
                evidence_source_kind = 'INBOX'
                AND source_inbox_id IS NOT NULL
                AND source_task_attempt_id IS NULL
            )
            OR
            (
                evidence_source_kind = 'TASK_ATTEMPT'
                AND source_inbox_id IS NULL
                AND source_task_attempt_id IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_fund_transfer_auth_observation_identity CHECK (
        out_authorization_no REGEXP '^[A-Za-z0-9]{8,32}$'
        AND (
            observed_out_authorization_no IS NULL
            OR observed_out_authorization_no REGEXP '^[A-Za-z0-9]{8,32}$'
        )
        AND (
            observed_authorization_id IS NULL
            OR CHAR_LENGTH(TRIM(observed_authorization_id)) BETWEEN 1 AND 32
        )
        AND (
            observed_user_recv_perception IS NULL
            OR CHAR_LENGTH(TRIM(observed_user_recv_perception))
                BETWEEN 1 AND 256
        )
    ),
    CONSTRAINT ck_fund_transfer_auth_observation_times CHECK (
        observed_at >= created_at
        AND (
            observed_authorized_at IS NULL
            OR observed_channel_created_at IS NULL
            OR observed_authorized_at >= observed_channel_created_at
        )
        AND (
            observed_closed_at IS NULL
            OR observed_channel_created_at IS NULL
            OR observed_closed_at >= observed_channel_created_at
        )
    ),
    CONSTRAINT fk_fund_transfer_auth_observation_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_auth_observation_authorization
        FOREIGN KEY (
            tenant_id,
            organization_id,
            transfer_authorization_id
        )
        REFERENCES fund_wechat_transfer_authorization (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_auth_observation_inbox_identity
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_auth_observation_inbox_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_inbox_id
        )
        REFERENCES ops_inbox_message (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_auth_observation_attempt_identity
        FOREIGN KEY (source_task_attempt_id)
        REFERENCES ops_task_attempt (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_auth_observation_attempt_scope
        FOREIGN KEY (
            source_scope_kind,
            tenant_id,
            organization_id,
            source_task_attempt_id
        )
        REFERENCES ops_task_attempt (
            scope_kind,
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_transfer_auth_observation_authorization_fk (
        tenant_id,
        organization_id,
        transfer_authorization_id
    ),
    INDEX ix_fund_transfer_auth_observation_timeline (
        transfer_authorization_id,
        observed_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE fund_withdrawal_order
    ADD COLUMN collection_mode_snapshot
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'USER_CONFIRM'
        AFTER openid_snapshot,
    ADD COLUMN transfer_authorization_id BIGINT NULL
        AFTER collection_mode_snapshot,
    ADD COLUMN out_authorization_no_snapshot
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER transfer_authorization_id,
    ADD COLUMN authorization_id_snapshot
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER out_authorization_no_snapshot,
    ADD CONSTRAINT ck_fund_withdrawal_collection_mode CHECK (
        (
            collection_mode_snapshot = 'USER_CONFIRM'
            AND transfer_authorization_id IS NULL
            AND out_authorization_no_snapshot IS NULL
            AND authorization_id_snapshot IS NULL
        )
        OR
        (
            collection_mode_snapshot = 'AUTHORIZED'
            AND transfer_authorization_id IS NOT NULL
            AND out_authorization_no_snapshot IS NOT NULL
            AND authorization_id_snapshot IS NOT NULL
        )
    ),
    ADD CONSTRAINT uq_fund_withdrawal_transfer_mode_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        amount_cent,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        appid_snapshot,
        openid_snapshot,
        collection_mode_snapshot,
        transfer_authorization_id,
        out_authorization_no_snapshot,
        authorization_id_snapshot
    ),
    ADD CONSTRAINT fk_fund_withdrawal_transfer_authorization
        FOREIGN KEY (
            tenant_id,
            organization_id,
            transfer_authorization_id,
            organization_user_id,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            mchid_snapshot,
            appid_snapshot,
            openid_snapshot,
            out_authorization_no_snapshot,
            authorization_id_snapshot
        )
        REFERENCES fund_wechat_transfer_authorization (
            tenant_id,
            organization_id,
            id,
            organization_user_id,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            mchid_snapshot,
            appid_snapshot,
            openid_snapshot,
            out_authorization_no,
            authorization_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_withdrawal_transfer_authorization_fk (
        tenant_id,
        organization_id,
        transfer_authorization_id,
        organization_user_id,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        mchid_snapshot,
        appid_snapshot,
        openid_snapshot,
        out_authorization_no_snapshot,
        authorization_id_snapshot
    );

ALTER TABLE fund_wechat_transfer
    MODIFY COLUMN transfer_page_style_snapshot
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    MODIFY COLUMN notify_url_sha256 BINARY(32) NULL,
    ADD COLUMN collection_mode_snapshot
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'USER_CONFIRM'
        AFTER openid_snapshot,
    ADD COLUMN transfer_authorization_id BIGINT NULL
        AFTER collection_mode_snapshot,
    ADD COLUMN out_authorization_no_snapshot
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER transfer_authorization_id,
    ADD COLUMN authorization_id_snapshot
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER out_authorization_no_snapshot,
    DROP CHECK ck_fund_wechat_transfer_request,
    ADD CONSTRAINT ck_fund_wechat_transfer_request CHECK (
        amount_cent BETWEEN 10 AND 20000
        AND report_type_snapshot = 'RECYCLED_GOODS_NAME'
        AND report_content_snapshot = 'MIXED_RECYCLABLES'
        AND CHAR_LENGTH(TRIM(transfer_remark)) BETWEEN 1 AND 32
        AND (
            (
                collection_mode_snapshot = 'USER_CONFIRM'
                AND transfer_authorization_id IS NULL
                AND out_authorization_no_snapshot IS NULL
                AND authorization_id_snapshot IS NULL
                AND transfer_page_style_snapshot = 'STANDARD'
                AND notify_url_sha256 IS NOT NULL
            )
            OR
            (
                collection_mode_snapshot = 'AUTHORIZED'
                AND transfer_authorization_id IS NOT NULL
                AND out_authorization_no_snapshot IS NOT NULL
                AND authorization_id_snapshot IS NOT NULL
                AND transfer_page_style_snapshot IS NULL
                AND notify_url_snapshot IS NULL
                AND notify_url_sha256 IS NULL
            )
        )
    ),
    ADD CONSTRAINT fk_fund_wechat_transfer_withdrawal_mode
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdrawal_order_id,
            amount_cent,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            appid_snapshot,
            openid_snapshot,
            collection_mode_snapshot,
            transfer_authorization_id,
            out_authorization_no_snapshot,
            authorization_id_snapshot
        )
        REFERENCES fund_withdrawal_order (
            tenant_id,
            organization_id,
            id,
            amount_cent,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            appid_snapshot,
            openid_snapshot,
            collection_mode_snapshot,
            transfer_authorization_id,
            out_authorization_no_snapshot,
            authorization_id_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_wechat_transfer_authorization
        FOREIGN KEY (
            tenant_id,
            organization_id,
            transfer_authorization_id,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            mchid_snapshot,
            appid_snapshot,
            openid_snapshot,
            out_authorization_no_snapshot,
            authorization_id_snapshot
        )
        REFERENCES fund_wechat_transfer_authorization (
            tenant_id,
            organization_id,
            id,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            mchid_snapshot,
            appid_snapshot,
            openid_snapshot,
            out_authorization_no,
            authorization_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_wechat_transfer_withdrawal_mode_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id,
        amount_cent,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        appid_snapshot,
        openid_snapshot,
        collection_mode_snapshot,
        transfer_authorization_id,
        out_authorization_no_snapshot,
        authorization_id_snapshot
    ),
    ADD INDEX ix_fund_wechat_transfer_authorization_fk (
        tenant_id,
        organization_id,
        transfer_authorization_id,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        mchid_snapshot,
        appid_snapshot,
        openid_snapshot,
        out_authorization_no_snapshot,
        authorization_id_snapshot
    );
