-- EcoBin P0 funds facts.
-- V6 creates exactly 20 tables for immutable configuration, user and
-- organization ledgers, recharge, withdrawal, WeChat evidence, and the
-- platform payout gate. It contains no environment account or business seed.

CREATE TABLE fund_wechat_merchant_profile (
    id BIGINT NOT NULL AUTO_INCREMENT,
    merchant_profile_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    mchid VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    merchant_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scene_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    report_type VARCHAR(64) NOT NULL,
    report_content VARCHAR(200) NOT NULL,
    transfer_page_style VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    non_secret_config_ref VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_merchant_profile_uid UNIQUE (merchant_profile_uid),
    CONSTRAINT uq_fund_merchant_profile_mchid UNIQUE (mchid),
    CONSTRAINT uq_fund_merchant_profile_payment_ref UNIQUE (id, mchid),
    CONSTRAINT ck_fund_merchant_profile_uid_v4 CHECK (
        merchant_profile_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_merchant_profile_kind CHECK (
        merchant_kind = 'ORDINARY_MERCHANT'
    ),
    CONSTRAINT ck_fund_merchant_profile_status CHECK (
        status IN ('ENABLED', 'DISABLED')
    ),
    CONSTRAINT ck_fund_merchant_profile_scene CHECK (
        CHAR_LENGTH(TRIM(mchid)) > 0
        AND CHAR_LENGTH(TRIM(scene_id)) > 0
        AND report_type = 'RECYCLED_GOODS_NAME'
        AND report_content = 'MIXED_RECYCLABLES'
        AND transfer_page_style = 'STANDARD'
    ),
    CONSTRAINT ck_fund_merchant_profile_non_secret CHECK (
        non_secret_config_ref IS NULL
        OR (
            CHAR_LENGTH(TRIM(non_secret_config_ref)) > 0
            AND LOWER(non_secret_config_ref) NOT LIKE '%secret%'
            AND LOWER(non_secret_config_ref) NOT LIKE '%private_key%'
            AND LOWER(non_secret_config_ref) NOT LIKE '%api_v3_key%'
        )
    ),
    CONSTRAINT ck_fund_merchant_profile_values CHECK (lock_version >= 0),
    CONSTRAINT ck_fund_merchant_profile_times CHECK (updated_at >= created_at),
    INDEX ix_fund_merchant_profile_status (status, id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_miniapp_merchant_binding (
    id BIGINT NOT NULL AUTO_INCREMENT,
    binding_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    appid VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    miniapp_lock_version_snapshot BIGINT NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    verified_by_platform_admin_id BIGINT NOT NULL,
    verified_at DATETIME(3) NOT NULL,
    disabled_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_binding_uid UNIQUE (binding_uid),
    CONSTRAINT uq_fund_binding_miniapp UNIQUE (organization_miniapp_id),
    CONSTRAINT uq_fund_binding_appid UNIQUE (appid),
    CONSTRAINT uq_fund_binding_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_binding_payment_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        organization_miniapp_id,
        merchant_profile_id,
        appid
    ),
    CONSTRAINT ck_fund_binding_uid_v4 CHECK (
        binding_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_binding_status CHECK (
        status IN ('VERIFIED', 'DISABLED')
    ),
    CONSTRAINT ck_fund_binding_state_times CHECK (
        (
            status = 'VERIFIED'
            AND disabled_at IS NULL
        )
        OR
        (
            status = 'DISABLED'
            AND disabled_at IS NOT NULL
            AND disabled_at >= verified_at
        )
    ),
    CONSTRAINT ck_fund_binding_values CHECK (
        miniapp_lock_version_snapshot >= 0
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_binding_times CHECK (
        verified_at >= created_at
        AND updated_at >= created_at
    ),
    CONSTRAINT fk_fund_binding_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_binding_miniapp
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_miniapp_id
        )
        REFERENCES iam_organization_miniapp (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_binding_merchant
        FOREIGN KEY (merchant_profile_id)
        REFERENCES fund_wechat_merchant_profile (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_binding_verifier
        FOREIGN KEY (verified_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_binding_miniapp_fk (
        tenant_id,
        organization_id,
        organization_miniapp_id
    ),
    INDEX ix_fund_binding_merchant_fk (merchant_profile_id),
    INDEX ix_fund_binding_verifier_fk (verified_by_platform_admin_id),
    INDEX ix_fund_binding_merchant_status (
        merchant_profile_id,
        status,
        id
    ),
    INDEX ix_fund_binding_org_status (
        tenant_id,
        organization_id,
        status,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_payout_gate (
    merchant_profile_id BIGINT NOT NULL,
    gate_state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    current_pause_event_id BIGINT NULL,
    paused_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (merchant_profile_id),
    CONSTRAINT ck_fund_payout_gate_state CHECK (
        (
            gate_state = 'OPEN'
            AND current_pause_event_id IS NULL
            AND paused_at IS NULL
        )
        OR
        (
            gate_state = 'PAUSED_NOT_ENOUGH'
            AND current_pause_event_id IS NOT NULL
            AND paused_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_payout_gate_values CHECK (lock_version >= 0),
    CONSTRAINT ck_fund_payout_gate_times CHECK (
        updated_at >= created_at
        AND (paused_at IS NULL OR paused_at >= created_at)
    ),
    CONSTRAINT fk_fund_payout_gate_merchant
        FOREIGN KEY (merchant_profile_id)
        REFERENCES fund_wechat_merchant_profile (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_payout_gate_state (gate_state, merchant_profile_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_organization_withdraw_config (
    id BIGINT NOT NULL AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    version_no BIGINT NOT NULL,
    content_sha256 BINARY(32) NOT NULL,
    hard_limit_cent BIGINT NOT NULL,
    manual_min_cent BIGINT NOT NULL,
    manual_max_cent BIGINT NOT NULL,
    manual_review_free_threshold_cent BIGINT NOT NULL,
    publication_source VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    published_by_staff_account_id BIGINT NULL,
    published_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_withdraw_config_org_version
        UNIQUE (tenant_id, organization_id, version_no),
    CONSTRAINT uq_fund_withdraw_config_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_withdraw_config_scope_version
        UNIQUE (tenant_id, organization_id, id, version_no),
    CONSTRAINT uq_fund_withdraw_config_order_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        version_no,
        hard_limit_cent,
        manual_min_cent,
        manual_max_cent,
        manual_review_free_threshold_cent
    ),
    CONSTRAINT ck_fund_withdraw_config_version CHECK (
        version_no BETWEEN 1 AND 9007199254740991
    ),
    CONSTRAINT ck_fund_withdraw_config_m0 CHECK (
        10 <= manual_min_cent
        AND manual_min_cent <= manual_max_cent
        AND manual_max_cent <= hard_limit_cent
        AND hard_limit_cent <= 20000
        AND manual_review_free_threshold_cent = 0
    ),
    CONSTRAINT ck_fund_withdraw_config_publisher CHECK (
        (
            publication_source = 'STAFF'
            AND published_by_staff_account_id IS NOT NULL
        )
        OR
        (
            publication_source = 'SYSTEM'
            AND published_by_staff_account_id IS NULL
        )
    ),
    CONSTRAINT ck_fund_withdraw_config_times CHECK (
        published_at >= created_at
    ),
    CONSTRAINT fk_fund_withdraw_config_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdraw_config_publisher
        FOREIGN KEY (tenant_id, published_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_withdraw_config_org_version (
        tenant_id,
        organization_id,
        version_no DESC
    ),
    INDEX ix_fund_withdraw_config_publisher_fk (
        tenant_id,
        published_by_staff_account_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_organization_withdraw_config_head (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    current_config_id BIGINT NOT NULL,
    current_version_no BIGINT NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    switched_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_fund_withdraw_head_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_fund_withdraw_head_values CHECK (
        current_version_no BETWEEN 1 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_withdraw_head_times CHECK (updated_at >= switched_at),
    CONSTRAINT fk_fund_withdraw_head_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdraw_head_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            current_config_id,
            current_version_no
        )
        REFERENCES fund_organization_withdraw_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_withdraw_head_config_fk (
        tenant_id,
        organization_id,
        current_config_id,
        current_version_no
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_organization_wallet_entry_counter (
    organization_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    last_visibility_sequence_no BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (organization_id),
    CONSTRAINT uq_fund_wallet_counter_scope
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT ck_fund_wallet_counter_values CHECK (
        last_visibility_sequence_no BETWEEN 0 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT fk_fund_wallet_counter_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_wallet_counter_org_fk (tenant_id, organization_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_user_wallet (
    id BIGINT NOT NULL AUTO_INCREMENT,
    wallet_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    available_balance_cent BIGINT NOT NULL DEFAULT 0,
    frozen_withdrawal_cent BIGINT NOT NULL DEFAULT 0,
    last_entry_sequence_no BIGINT NOT NULL DEFAULT 0,
    delivery_gate_state VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'OPEN',
    delivery_gate_threshold_snapshot_cent BIGINT NULL,
    delivery_gate_trigger_entry_id BIGINT NULL,
    delivery_gate_latched_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_user_wallet_uid UNIQUE (wallet_uid),
    CONSTRAINT uq_fund_user_wallet_user
        UNIQUE (tenant_id, organization_id, organization_user_id),
    CONSTRAINT uq_fund_user_wallet_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_user_wallet_scope_id_user
        UNIQUE (tenant_id, organization_id, id, organization_user_id),
    CONSTRAINT ck_fund_user_wallet_uid_v4 CHECK (
        wallet_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_user_wallet_values CHECK (
        frozen_withdrawal_cent >= 0
        AND last_entry_sequence_no BETWEEN 0 AND 9007199254740991
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_user_wallet_gate CHECK (
        (
            delivery_gate_state = 'OPEN'
            AND delivery_gate_threshold_snapshot_cent IS NULL
            AND delivery_gate_trigger_entry_id IS NULL
            AND delivery_gate_latched_at IS NULL
        )
        OR
        (
            delivery_gate_state = 'MANUAL_RECOVERY_REQUIRED'
            AND delivery_gate_threshold_snapshot_cent IS NOT NULL
            AND delivery_gate_threshold_snapshot_cent < 0
            AND delivery_gate_trigger_entry_id IS NOT NULL
            AND delivery_gate_latched_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_user_wallet_times CHECK (
        updated_at >= created_at
        AND (
            delivery_gate_latched_at IS NULL
            OR delivery_gate_latched_at >= created_at
        )
    ),
    CONSTRAINT fk_fund_user_wallet_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_user_wallet_user
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_user_wallet_user_fk (
        tenant_id,
        organization_id,
        organization_user_id
    ),
    INDEX ix_fund_user_wallet_gate (
        tenant_id,
        organization_id,
        delivery_gate_state,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_organization_payout_account (
    id BIGINT NOT NULL AUTO_INCREMENT,
    account_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    available_payout_cent BIGINT NOT NULL DEFAULT 0,
    frozen_withdrawal_cent BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_payout_account_uid UNIQUE (account_uid),
    CONSTRAINT uq_fund_payout_account_org
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT uq_fund_payout_account_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_payout_account_uid_v4 CHECK (
        account_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_payout_account_values CHECK (
        available_payout_cent >= 0
        AND frozen_withdrawal_cent >= 0
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_payout_account_times CHECK (updated_at >= created_at),
    CONSTRAINT fk_fund_payout_account_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_payout_account_org_fk (tenant_id, organization_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_recharge_order (
    id BIGINT NOT NULL AUTO_INCREMENT,
    recharge_order_no VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    created_by_staff_account_id BIGINT NOT NULL,
    gross_amount_cent BIGINT NOT NULL,
    fee_rate_ppm INT NOT NULL,
    fee_rounding_mode VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fee_amount_cent BIGINT NOT NULL,
    net_amount_cent BIGINT NOT NULL,
    business_state VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    expires_at DATETIME(3) NOT NULL,
    paid_at DATETIME(3) NULL,
    posted_at DATETIME(3) NULL,
    closed_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_recharge_order_no UNIQUE (recharge_order_no),
    CONSTRAINT uq_fund_recharge_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_recharge_payment_ref
        UNIQUE (
            tenant_id,
            organization_id,
            id,
            gross_amount_cent,
            expires_at
        ),
    CONSTRAINT ck_fund_recharge_order_no CHECK (
        recharge_order_no REGEXP '^[A-Za-z0-9_-]{8,40}$'
    ),
    CONSTRAINT ck_fund_recharge_amounts CHECK (
        gross_amount_cent BETWEEN 100 AND 20000000
        AND fee_rate_ppm = 6000
        AND fee_rounding_mode = 'CEILING_TO_CENT'
        AND fee_amount_cent =
            FLOOR((CAST(gross_amount_cent AS DECIMAL(30,0)) * 6000 + 999999)
                / 1000000)
        AND CAST(gross_amount_cent AS DECIMAL(30,0)) =
            CAST(fee_amount_cent AS DECIMAL(30,0))
            + CAST(net_amount_cent AS DECIMAL(30,0))
        AND net_amount_cent > 0
    ),
    CONSTRAINT ck_fund_recharge_state CHECK (
        business_state IN (
            'PENDING_PAYMENT',
            'PAID_PENDING_POST',
            'POSTED',
            'CLOSED',
            'EXPIRED'
        )
    ),
    CONSTRAINT ck_fund_recharge_state_times CHECK (
        (
            business_state = 'PENDING_PAYMENT'
            AND paid_at IS NULL
            AND posted_at IS NULL
            AND closed_at IS NULL
        )
        OR
        (
            business_state = 'PAID_PENDING_POST'
            AND paid_at IS NOT NULL
            AND posted_at IS NULL
            AND closed_at IS NULL
        )
        OR
        (
            business_state = 'POSTED'
            AND paid_at IS NOT NULL
            AND posted_at IS NOT NULL
            AND posted_at >= paid_at
            AND closed_at IS NULL
        )
        OR
        (
            business_state IN ('CLOSED', 'EXPIRED')
            AND paid_at IS NULL
            AND posted_at IS NULL
            AND closed_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_recharge_values CHECK (lock_version >= 0),
    CONSTRAINT ck_fund_recharge_times CHECK (
        expires_at > created_at
        AND updated_at >= created_at
        AND (paid_at IS NULL OR paid_at >= created_at)
        AND (closed_at IS NULL OR closed_at >= created_at)
    ),
    CONSTRAINT fk_fund_recharge_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_recharge_creator
        FOREIGN KEY (tenant_id, created_by_staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_recharge_creator_fk (
        tenant_id,
        created_by_staff_account_id
    ),
    INDEX ix_fund_recharge_org_state (
        tenant_id,
        organization_id,
        business_state,
        created_at,
        recharge_order_no
    ),
    INDEX ix_fund_recharge_expiry (
        business_state,
        expires_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wechat_payment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    payment_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    recharge_order_id BIGINT NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    miniapp_merchant_binding_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    mchid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    appid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    out_trade_no VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_amount_cent BIGINT NOT NULL,
    currency CHAR(3) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    description VARCHAR(127) NOT NULL,
    time_expire DATETIME(3) NOT NULL,
    notify_url_sha256 BINARY(32) NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    code_url VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NULL,
    transaction_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    channel_updated_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_wechat_payment_uid UNIQUE (payment_uid),
    CONSTRAINT uq_fund_wechat_payment_recharge UNIQUE (recharge_order_id),
    CONSTRAINT uq_fund_wechat_payment_out_trade
        UNIQUE (merchant_profile_id, out_trade_no),
    CONSTRAINT uq_fund_wechat_payment_transaction
        UNIQUE (merchant_profile_id, transaction_id),
    CONSTRAINT uq_fund_wechat_payment_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_wechat_payment_uid_v4 CHECK (
        payment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_wechat_payment_out_trade CHECK (
        out_trade_no REGEXP '^[A-Za-z0-9]{8,32}$'
    ),
    CONSTRAINT ck_fund_wechat_payment_request CHECK (
        request_amount_cent BETWEEN 100 AND 20000000
        AND currency = 'CNY'
        AND CHAR_LENGTH(TRIM(description)) BETWEEN 1 AND 127
        AND time_expire > created_at
    ),
    CONSTRAINT ck_fund_wechat_payment_code_url CHECK (
        code_url IS NULL
        OR (
            code_url LIKE 'weixin://%'
            AND code_url NOT LIKE '% %'
        )
    ),
    CONSTRAINT ck_fund_wechat_payment_channel CHECK (
        channel_state IS NULL
        OR CHAR_LENGTH(TRIM(channel_state)) > 0
    ),
    CONSTRAINT ck_fund_wechat_payment_values CHECK (lock_version >= 0),
    CONSTRAINT ck_fund_wechat_payment_times CHECK (
        updated_at >= created_at
        AND (
            channel_updated_at IS NULL
            OR channel_updated_at >= created_at
        )
    ),
    CONSTRAINT fk_fund_wechat_payment_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_payment_recharge
        FOREIGN KEY (
            tenant_id,
            organization_id,
            recharge_order_id,
            request_amount_cent,
            time_expire
        )
        REFERENCES fund_recharge_order (
            tenant_id,
            organization_id,
            id,
            gross_amount_cent,
            expires_at
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_payment_merchant
        FOREIGN KEY (merchant_profile_id, mchid_snapshot)
        REFERENCES fund_wechat_merchant_profile (id, mchid)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_payment_binding
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
    INDEX ix_fund_wechat_payment_recharge_fk (
        tenant_id,
        organization_id,
        recharge_order_id,
        request_amount_cent,
        time_expire
    ),
    INDEX ix_fund_wechat_payment_merchant_fk (
        merchant_profile_id,
        mchid_snapshot
    ),
    INDEX ix_fund_wechat_payment_binding_fk (
        tenant_id,
        organization_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        merchant_profile_id,
        appid_snapshot
    ),
    INDEX ix_fund_wechat_payment_state (
        channel_state,
        channel_updated_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_withdrawal_order (
    id BIGINT NOT NULL AUTO_INCREMENT,
    withdrawal_order_no VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    wallet_id BIGINT NOT NULL,
    organization_payout_account_id BIGINT NOT NULL,
    withdraw_config_id BIGINT NOT NULL,
    withdraw_config_version_no BIGINT NOT NULL,
    hard_limit_cent_snapshot BIGINT NOT NULL,
    manual_min_cent_snapshot BIGINT NOT NULL,
    manual_max_cent_snapshot BIGINT NOT NULL,
    manual_review_free_threshold_cent_snapshot BIGINT NOT NULL,
    amount_cent BIGINT NOT NULL,
    miniapp_merchant_binding_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    mchid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    appid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    openid_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    business_state VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    negative_balance_pause TINYINT NOT NULL DEFAULT 0,
    post_boundary_risk TINYINT NOT NULL DEFAULT 0,
    pre_channel_block_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    channel_boundary_at DATETIME(3) NULL,
    long_unsettled_at DATETIME(3) NULL,
    reviewed_at DATETIME(3) NULL,
    channel_terminal_at DATETIME(3) NULL,
    ended_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_withdrawal_order_no UNIQUE (withdrawal_order_no),
    CONSTRAINT uq_fund_withdrawal_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_withdrawal_wallet_ref
        UNIQUE (tenant_id, organization_id, id, wallet_id),
    CONSTRAINT uq_fund_withdrawal_transfer_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        amount_cent,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        appid_snapshot,
        openid_snapshot
    ),
    CONSTRAINT ck_fund_withdrawal_order_no CHECK (
        withdrawal_order_no REGEXP '^[A-Za-z0-9_-]{8,40}$'
    ),
    CONSTRAINT ck_fund_withdrawal_amounts CHECK (
        10 <= manual_min_cent_snapshot
        AND manual_min_cent_snapshot <= amount_cent
        AND amount_cent <= manual_max_cent_snapshot
        AND manual_max_cent_snapshot <= hard_limit_cent_snapshot
        AND hard_limit_cent_snapshot <= 20000
        AND manual_review_free_threshold_cent_snapshot = 0
    ),
    CONSTRAINT ck_fund_withdrawal_state CHECK (
        business_state IN (
            'PENDING_REVIEW',
            'READY_TO_SUBMIT',
            'CHANNEL_PROCESSING',
            'SUCCEEDED',
            'REJECTED',
            'LOCAL_CANCELLED',
            'LOCAL_ABORTED_BEFORE_CHANNEL',
            'CHANNEL_FAILED',
            'CHANNEL_CANCELLED'
        )
    ),
    CONSTRAINT ck_fund_withdrawal_flags CHECK (
        negative_balance_pause IN (0, 1)
        AND post_boundary_risk IN (0, 1)
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_withdrawal_channel_boundary CHECK (
        (
            business_state IN (
                'PENDING_REVIEW',
                'READY_TO_SUBMIT',
                'REJECTED',
                'LOCAL_CANCELLED',
                'LOCAL_ABORTED_BEFORE_CHANNEL'
            )
            AND channel_boundary_at IS NULL
            AND channel_terminal_at IS NULL
        )
        OR
        (
            business_state = 'CHANNEL_PROCESSING'
            AND channel_boundary_at IS NOT NULL
            AND channel_terminal_at IS NULL
        )
        OR
        (
            business_state IN (
                'SUCCEEDED',
                'CHANNEL_FAILED',
                'CHANNEL_CANCELLED'
            )
            AND channel_boundary_at IS NOT NULL
            AND channel_terminal_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_withdrawal_terminal_times CHECK (
        (
            business_state IN (
                'PENDING_REVIEW',
                'READY_TO_SUBMIT',
                'CHANNEL_PROCESSING'
            )
            AND ended_at IS NULL
        )
        OR
        (
            business_state IN (
                'SUCCEEDED',
                'REJECTED',
                'LOCAL_CANCELLED',
                'LOCAL_ABORTED_BEFORE_CHANNEL',
                'CHANNEL_FAILED',
                'CHANNEL_CANCELLED'
            )
            AND ended_at IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_withdrawal_times CHECK (
        updated_at >= created_at
        AND (reviewed_at IS NULL OR reviewed_at >= created_at)
        AND (channel_boundary_at IS NULL OR channel_boundary_at >= created_at)
        AND (
            channel_terminal_at IS NULL
            OR channel_terminal_at >= channel_boundary_at
        )
        AND (
            long_unsettled_at IS NULL
            OR (
                business_state IN (
                    'CHANNEL_PROCESSING',
                    'SUCCEEDED',
                    'CHANNEL_FAILED',
                    'CHANNEL_CANCELLED'
                )
                AND channel_boundary_at IS NOT NULL
                AND long_unsettled_at >=
                    DATE_ADD(channel_boundary_at, INTERVAL 30 MINUTE)
            )
        )
        AND (ended_at IS NULL OR ended_at >= created_at)
    ),
    CONSTRAINT fk_fund_withdrawal_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_user
        FOREIGN KEY (tenant_id, organization_id, organization_user_id)
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_wallet
        FOREIGN KEY (
            tenant_id,
            organization_id,
            wallet_id,
            organization_user_id
        )
        REFERENCES fund_user_wallet (
            tenant_id,
            organization_id,
            id,
            organization_user_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_account
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_payout_account_id
        )
        REFERENCES fund_organization_payout_account (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdraw_config_id,
            withdraw_config_version_no,
            hard_limit_cent_snapshot,
            manual_min_cent_snapshot,
            manual_max_cent_snapshot,
            manual_review_free_threshold_cent_snapshot
        )
        REFERENCES fund_organization_withdraw_config (
            tenant_id,
            organization_id,
            id,
            version_no,
            hard_limit_cent,
            manual_min_cent,
            manual_max_cent,
            manual_review_free_threshold_cent
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_binding
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
    INDEX ix_fund_withdrawal_user_fk (
        tenant_id,
        organization_id,
        organization_user_id
    ),
    INDEX ix_fund_withdrawal_wallet_fk (
        tenant_id,
        organization_id,
        wallet_id,
        organization_user_id
    ),
    INDEX ix_fund_withdrawal_account_fk (
        tenant_id,
        organization_id,
        organization_payout_account_id
    ),
    INDEX ix_fund_withdrawal_config_fk (
        tenant_id,
        organization_id,
        withdraw_config_id,
        withdraw_config_version_no,
        hard_limit_cent_snapshot,
        manual_min_cent_snapshot,
        manual_max_cent_snapshot,
        manual_review_free_threshold_cent_snapshot
    ),
    INDEX ix_fund_withdrawal_binding_fk (
        tenant_id,
        organization_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        merchant_profile_id,
        appid_snapshot
    ),
    INDEX ix_fund_withdrawal_org_state (
        tenant_id,
        organization_id,
        business_state,
        created_at,
        withdrawal_order_no
    ),
    INDEX ix_fund_withdrawal_user_time (
        organization_user_id,
        created_at,
        withdrawal_order_no
    ),
    INDEX ix_fund_withdrawal_unsettled (
        business_state,
        long_unsettled_at,
        id
    ),
    INDEX ix_fund_withdrawal_terminal_summary (
        tenant_id,
        organization_id,
        business_state,
        channel_terminal_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_active_withdrawal (
    wallet_id BIGINT NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    withdrawal_order_id BIGINT NOT NULL,
    acquired_at DATETIME(3) NOT NULL,
    PRIMARY KEY (wallet_id),
    CONSTRAINT uq_fund_active_withdrawal_order UNIQUE (withdrawal_order_id),
    CONSTRAINT uq_fund_active_withdrawal_scope
        UNIQUE (tenant_id, organization_id, wallet_id),
    CONSTRAINT fk_fund_active_withdrawal_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_active_withdrawal_wallet
        FOREIGN KEY (tenant_id, organization_id, wallet_id)
        REFERENCES fund_user_wallet (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_active_withdrawal_order
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdrawal_order_id,
            wallet_id
        )
        REFERENCES fund_withdrawal_order (
            tenant_id,
            organization_id,
            id,
            wallet_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_active_withdrawal_wallet_fk (
        tenant_id,
        organization_id,
        wallet_id
    ),
    INDEX ix_fund_active_withdrawal_order_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id,
        wallet_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wallet_adjustment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    adjustment_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    wallet_id BIGINT NOT NULL,
    amount_delta_cent BIGINT NOT NULL,
    available_before_cent BIGINT NOT NULL,
    available_after_cent BIGINT NOT NULL,
    actor_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    reason VARCHAR(500) NULL,
    succeeded_audit_id BIGINT NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_wallet_adjustment_uid UNIQUE (adjustment_uid),
    CONSTRAINT uq_fund_wallet_adjustment_audit UNIQUE (succeeded_audit_id),
    CONSTRAINT uq_fund_wallet_adjustment_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_wallet_adjustment_uid_v4 CHECK (
        adjustment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_wallet_adjustment_amount CHECK (
        amount_delta_cent <> 0
        AND CAST(available_before_cent AS DECIMAL(30,0))
            + CAST(amount_delta_cent AS DECIMAL(30,0))
            = CAST(available_after_cent AS DECIMAL(30,0))
    ),
    CONSTRAINT ck_fund_wallet_adjustment_actor CHECK (
        (
            actor_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            actor_kind = 'STAFF_ACCOUNT'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_wallet_adjustment_times CHECK (
        occurred_at >= created_at
    ),
    CONSTRAINT fk_fund_wallet_adjustment_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_adjustment_wallet
        FOREIGN KEY (tenant_id, organization_id, wallet_id)
        REFERENCES fund_user_wallet (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_adjustment_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_adjustment_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_wallet_adjustment_wallet_fk (
        tenant_id,
        organization_id,
        wallet_id
    ),
    INDEX ix_fund_wallet_adjustment_platform_fk (platform_admin_id),
    INDEX ix_fund_wallet_adjustment_staff_fk (
        tenant_id,
        staff_account_id
    ),
    INDEX ix_fund_wallet_adjustment_wallet_time (
        wallet_id,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_withdrawal_review (
    id BIGINT NOT NULL AUTO_INCREMENT,
    review_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    withdrawal_order_id BIGINT NOT NULL,
    decision VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reviewer_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    platform_admin_id BIGINT NULL,
    staff_account_id BIGINT NULL,
    note VARCHAR(500) NULL,
    succeeded_audit_id BIGINT NOT NULL,
    reviewed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_withdrawal_review_uid UNIQUE (review_uid),
    CONSTRAINT uq_fund_withdrawal_review_order UNIQUE (withdrawal_order_id),
    CONSTRAINT uq_fund_withdrawal_review_audit UNIQUE (succeeded_audit_id),
    CONSTRAINT uq_fund_withdrawal_review_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_withdrawal_review_uid_v4 CHECK (
        review_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_withdrawal_review_decision CHECK (
        decision IN ('APPROVED', 'REJECTED')
    ),
    CONSTRAINT ck_fund_withdrawal_review_actor CHECK (
        (
            reviewer_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            reviewer_kind = 'STAFF_ACCOUNT'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_withdrawal_review_times CHECK (
        reviewed_at >= created_at
    ),
    CONSTRAINT fk_fund_withdrawal_review_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_review_order
        FOREIGN KEY (tenant_id, organization_id, withdrawal_order_id)
        REFERENCES fund_withdrawal_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_review_platform
        FOREIGN KEY (platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_withdrawal_review_staff
        FOREIGN KEY (tenant_id, staff_account_id)
        REFERENCES iam_staff_account (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_withdrawal_review_order_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id
    ),
    INDEX ix_fund_withdrawal_review_platform_fk (platform_admin_id),
    INDEX ix_fund_withdrawal_review_staff_fk (
        tenant_id,
        staff_account_id
    ),
    INDEX ix_fund_withdrawal_review_staff_time (
        tenant_id,
        staff_account_id,
        reviewed_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wechat_transfer (
    id BIGINT NOT NULL AUTO_INCREMENT,
    transfer_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    withdrawal_order_id BIGINT NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    miniapp_merchant_binding_id BIGINT NOT NULL,
    organization_miniapp_id BIGINT NOT NULL,
    out_bill_no VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    transfer_bill_no VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    amount_cent BIGINT NOT NULL,
    mchid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    appid_snapshot VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    openid_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    scene_id_snapshot VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    report_type_snapshot VARCHAR(64) NOT NULL,
    report_content_snapshot VARCHAR(200) NOT NULL,
    transfer_remark VARCHAR(32) NOT NULL,
    transfer_page_style_snapshot VARCHAR(32) CHARACTER SET ascii
        COLLATE ascii_bin NOT NULL,
    notify_url_sha256 BINARY(32) NOT NULL,
    request_sha256 BINARY(32) NOT NULL,
    channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    terminal_classification VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'NON_TERMINAL',
    package_info VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    terminal_fail_reason VARCHAR(255) NULL,
    state_conflict TINYINT NOT NULL DEFAULT 0,
    submitted_at DATETIME(3) NULL,
    channel_updated_at DATETIME(3) NULL,
    terminal_at DATETIME(3) NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_wechat_transfer_uid UNIQUE (transfer_uid),
    CONSTRAINT uq_fund_wechat_transfer_withdrawal UNIQUE (withdrawal_order_id),
    CONSTRAINT uq_fund_wechat_transfer_out_bill UNIQUE (out_bill_no),
    CONSTRAINT uq_fund_wechat_transfer_bill UNIQUE (transfer_bill_no),
    CONSTRAINT uq_fund_wechat_transfer_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_wechat_transfer_merchant_id
        UNIQUE (merchant_profile_id, id),
    CONSTRAINT ck_fund_wechat_transfer_uid_v4 CHECK (
        transfer_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_wechat_transfer_out_bill CHECK (
        out_bill_no REGEXP '^[A-Za-z0-9]{8,32}$'
    ),
    CONSTRAINT ck_fund_wechat_transfer_request CHECK (
        amount_cent BETWEEN 10 AND 20000
        AND report_type_snapshot = 'RECYCLED_GOODS_NAME'
        AND report_content_snapshot = 'MIXED_RECYCLABLES'
        AND transfer_page_style_snapshot = 'STANDARD'
        AND CHAR_LENGTH(TRIM(transfer_remark)) BETWEEN 1 AND 32
    ),
    CONSTRAINT ck_fund_wechat_transfer_terminal CHECK (
        terminal_classification IN (
            'NON_TERMINAL',
            'SUCCESS',
            'FAIL',
            'CANCELLED',
            'CONFLICT',
            'UNKNOWN'
        )
        AND (
            (
                terminal_classification IN ('NON_TERMINAL', 'UNKNOWN')
                AND terminal_at IS NULL
            )
            OR
            (
                terminal_classification IN (
                    'SUCCESS',
                    'FAIL',
                    'CANCELLED',
                    'CONFLICT'
                )
                AND terminal_at IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_fund_wechat_transfer_fail_reason CHECK (
        terminal_fail_reason IS NULL
        OR terminal_classification IN ('FAIL', 'CANCELLED', 'CONFLICT')
    ),
    CONSTRAINT ck_fund_wechat_transfer_flags CHECK (
        state_conflict IN (0, 1)
        AND lock_version >= 0
    ),
    CONSTRAINT ck_fund_wechat_transfer_times CHECK (
        updated_at >= created_at
        AND (submitted_at IS NULL OR submitted_at >= created_at)
        AND (
            channel_updated_at IS NULL
            OR channel_updated_at >= created_at
        )
        AND (terminal_at IS NULL OR terminal_at >= created_at)
    ),
    CONSTRAINT fk_fund_wechat_transfer_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_transfer_withdrawal
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdrawal_order_id,
            amount_cent,
            merchant_profile_id,
            miniapp_merchant_binding_id,
            organization_miniapp_id,
            appid_snapshot,
            openid_snapshot
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
            openid_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_transfer_merchant
        FOREIGN KEY (
            merchant_profile_id,
            mchid_snapshot
        )
        REFERENCES fund_wechat_merchant_profile (
            id,
            mchid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wechat_transfer_binding
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
    INDEX ix_fund_wechat_transfer_withdrawal_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id,
        amount_cent,
        merchant_profile_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        appid_snapshot,
        openid_snapshot
    ),
    INDEX ix_fund_wechat_transfer_merchant_fk (
        merchant_profile_id,
        mchid_snapshot
    ),
    INDEX ix_fund_wechat_transfer_binding_fk (
        tenant_id,
        organization_id,
        miniapp_merchant_binding_id,
        organization_miniapp_id,
        merchant_profile_id,
        appid_snapshot
    ),
    INDEX ix_fund_wechat_transfer_state (
        channel_state,
        channel_updated_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_user_wallet_entry (
    id BIGINT NOT NULL AUTO_INCREMENT,
    entry_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    wallet_id BIGINT NOT NULL,
    organization_user_id BIGINT NOT NULL,
    entry_sequence_no BIGINT NOT NULL,
    visibility_sequence_no BIGINT NOT NULL,
    event_type VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    available_delta_cent BIGINT NOT NULL,
    available_before_cent BIGINT NOT NULL,
    available_after_cent BIGINT NOT NULL,
    frozen_delta_cent BIGINT NOT NULL,
    frozen_before_cent BIGINT NOT NULL,
    frozen_after_cent BIGINT NOT NULL,
    delivery_revision_id BIGINT NULL,
    withdrawal_order_id BIGINT NULL,
    adjustment_id BIGINT NULL,
    fund_phase VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_wallet_entry_uid UNIQUE (entry_uid),
    CONSTRAINT uq_fund_wallet_entry_sequence
        UNIQUE (wallet_id, entry_sequence_no),
    CONSTRAINT uq_fund_wallet_entry_visibility
        UNIQUE (organization_id, visibility_sequence_no),
    CONSTRAINT uq_fund_wallet_entry_delivery UNIQUE (delivery_revision_id),
    CONSTRAINT uq_fund_wallet_entry_withdrawal_phase
        UNIQUE (withdrawal_order_id, fund_phase),
    CONSTRAINT uq_fund_wallet_entry_adjustment UNIQUE (adjustment_id),
    CONSTRAINT uq_fund_wallet_entry_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_wallet_entry_gate_ref
        UNIQUE (tenant_id, organization_id, id, wallet_id),
    CONSTRAINT ck_fund_wallet_entry_uid_v4 CHECK (
        entry_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_wallet_entry_sequences CHECK (
        entry_sequence_no BETWEEN 1 AND 9007199254740991
        AND visibility_sequence_no BETWEEN 1 AND 9007199254740991
    ),
    CONSTRAINT ck_fund_wallet_entry_event CHECK (
        event_type IN (
            'DELIVERY_INITIAL_REVIEW',
            'DELIVERY_CORRECTION',
            'WITHDRAWAL_FREEZE',
            'WITHDRAWAL_SUCCEEDED',
            'WITHDRAWAL_RELEASED',
            'MANUAL_ADJUSTMENT'
        )
    ),
    CONSTRAINT ck_fund_wallet_entry_available_math CHECK (
        CAST(available_before_cent AS DECIMAL(30,0))
            + CAST(available_delta_cent AS DECIMAL(30,0))
            = CAST(available_after_cent AS DECIMAL(30,0))
    ),
    CONSTRAINT ck_fund_wallet_entry_frozen_math CHECK (
        frozen_before_cent >= 0
        AND frozen_after_cent >= 0
        AND CAST(frozen_before_cent AS DECIMAL(30,0))
            + CAST(frozen_delta_cent AS DECIMAL(30,0))
            = CAST(frozen_after_cent AS DECIMAL(30,0))
    ),
    CONSTRAINT ck_fund_wallet_entry_source CHECK (
        (
            event_type IN (
                'DELIVERY_INITIAL_REVIEW',
                'DELIVERY_CORRECTION'
            )
            AND delivery_revision_id IS NOT NULL
            AND withdrawal_order_id IS NULL
            AND adjustment_id IS NULL
            AND fund_phase IS NULL
        )
        OR
        (
            event_type IN (
                'WITHDRAWAL_FREEZE',
                'WITHDRAWAL_SUCCEEDED',
                'WITHDRAWAL_RELEASED'
            )
            AND delivery_revision_id IS NULL
            AND withdrawal_order_id IS NOT NULL
            AND adjustment_id IS NULL
            AND fund_phase IN ('FREEZE', 'FINAL')
            AND (
                (event_type = 'WITHDRAWAL_FREEZE' AND fund_phase = 'FREEZE')
                OR
                (
                    event_type IN (
                        'WITHDRAWAL_SUCCEEDED',
                        'WITHDRAWAL_RELEASED'
                    )
                    AND fund_phase = 'FINAL'
                )
            )
        )
        OR
        (
            event_type = 'MANUAL_ADJUSTMENT'
            AND delivery_revision_id IS NULL
            AND withdrawal_order_id IS NULL
            AND adjustment_id IS NOT NULL
            AND fund_phase IS NULL
        )
    ),
    CONSTRAINT ck_fund_wallet_entry_nonzero CHECK (
        available_delta_cent <> 0 OR frozen_delta_cent <> 0
    ),
    CONSTRAINT fk_fund_wallet_entry_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_entry_wallet
        FOREIGN KEY (
            tenant_id,
            organization_id,
            wallet_id,
            organization_user_id
        )
        REFERENCES fund_user_wallet (
            tenant_id,
            organization_id,
            id,
            organization_user_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_entry_delivery
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_revision_id
        )
        REFERENCES rec_delivery_revision (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_entry_withdrawal
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdrawal_order_id
        )
        REFERENCES fund_withdrawal_order (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_wallet_entry_adjustment
        FOREIGN KEY (
            tenant_id,
            organization_id,
            adjustment_id
        )
        REFERENCES fund_wallet_adjustment (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_wallet_entry_wallet_fk (
        tenant_id,
        organization_id,
        wallet_id,
        organization_user_id
    ),
    INDEX ix_fund_wallet_entry_delivery_fk (
        tenant_id,
        organization_id,
        delivery_revision_id
    ),
    INDEX ix_fund_wallet_entry_withdrawal_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id
    ),
    INDEX ix_fund_wallet_entry_adjustment_fk (
        tenant_id,
        organization_id,
        adjustment_id
    ),
    INDEX ix_fund_wallet_entry_wallet_timeline (
        wallet_id,
        entry_sequence_no,
        occurred_at,
        id
    ),
    INDEX ix_fund_wallet_entry_org_timeline (
        tenant_id,
        organization_id,
        occurred_at,
        organization_user_id,
        entry_sequence_no,
        id
    ),
    INDEX ix_fund_wallet_entry_org_type (
        tenant_id,
        organization_id,
        event_type,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_organization_payout_entry (
    id BIGINT NOT NULL AUTO_INCREMENT,
    entry_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    payout_account_id BIGINT NOT NULL,
    event_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    available_delta_cent BIGINT NOT NULL,
    available_before_cent BIGINT NOT NULL,
    available_after_cent BIGINT NOT NULL,
    frozen_delta_cent BIGINT NOT NULL,
    frozen_before_cent BIGINT NOT NULL,
    frozen_after_cent BIGINT NOT NULL,
    recharge_order_id BIGINT NULL,
    withdrawal_order_id BIGINT NULL,
    fund_phase VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL,
    recharge_gross_cent BIGINT NULL,
    recharge_fee_cent BIGINT NULL,
    recharge_net_cent BIGINT NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_payout_entry_uid UNIQUE (entry_uid),
    CONSTRAINT uq_fund_payout_entry_recharge UNIQUE (recharge_order_id),
    CONSTRAINT uq_fund_payout_entry_withdrawal_phase
        UNIQUE (withdrawal_order_id, fund_phase),
    CONSTRAINT uq_fund_payout_entry_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_payout_entry_uid_v4 CHECK (
        entry_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_payout_entry_event CHECK (
        event_type IN (
            'RECHARGE_POSTED',
            'WITHDRAWAL_FREEZE',
            'WITHDRAWAL_SUCCEEDED',
            'WITHDRAWAL_RELEASED'
        )
    ),
    CONSTRAINT ck_fund_payout_entry_available_math CHECK (
        available_before_cent >= 0
        AND available_after_cent >= 0
        AND CAST(available_before_cent AS DECIMAL(30,0))
            + CAST(available_delta_cent AS DECIMAL(30,0))
            = CAST(available_after_cent AS DECIMAL(30,0))
    ),
    CONSTRAINT ck_fund_payout_entry_frozen_math CHECK (
        frozen_before_cent >= 0
        AND frozen_after_cent >= 0
        AND CAST(frozen_before_cent AS DECIMAL(30,0))
            + CAST(frozen_delta_cent AS DECIMAL(30,0))
            = CAST(frozen_after_cent AS DECIMAL(30,0))
    ),
    CONSTRAINT ck_fund_payout_entry_source CHECK (
        (
            event_type = 'RECHARGE_POSTED'
            AND recharge_order_id IS NOT NULL
            AND withdrawal_order_id IS NULL
            AND fund_phase IS NULL
            AND recharge_gross_cent IS NOT NULL
            AND recharge_fee_cent IS NOT NULL
            AND recharge_net_cent IS NOT NULL
            AND CAST(recharge_gross_cent AS DECIMAL(30,0)) =
                CAST(recharge_fee_cent AS DECIMAL(30,0))
                + CAST(recharge_net_cent AS DECIMAL(30,0))
            AND available_delta_cent = recharge_net_cent
            AND frozen_delta_cent = 0
        )
        OR
        (
            event_type IN (
                'WITHDRAWAL_FREEZE',
                'WITHDRAWAL_SUCCEEDED',
                'WITHDRAWAL_RELEASED'
            )
            AND recharge_order_id IS NULL
            AND withdrawal_order_id IS NOT NULL
            AND recharge_gross_cent IS NULL
            AND recharge_fee_cent IS NULL
            AND recharge_net_cent IS NULL
            AND (
                (event_type = 'WITHDRAWAL_FREEZE' AND fund_phase = 'FREEZE')
                OR
                (
                    event_type IN (
                        'WITHDRAWAL_SUCCEEDED',
                        'WITHDRAWAL_RELEASED'
                    )
                    AND fund_phase = 'FINAL'
                )
            )
        )
    ),
    CONSTRAINT ck_fund_payout_entry_nonzero CHECK (
        available_delta_cent <> 0 OR frozen_delta_cent <> 0
    ),
    CONSTRAINT fk_fund_payout_entry_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_entry_account
        FOREIGN KEY (tenant_id, organization_id, payout_account_id)
        REFERENCES fund_organization_payout_account (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_entry_recharge
        FOREIGN KEY (tenant_id, organization_id, recharge_order_id)
        REFERENCES fund_recharge_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_entry_withdrawal
        FOREIGN KEY (tenant_id, organization_id, withdrawal_order_id)
        REFERENCES fund_withdrawal_order (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_payout_entry_account_fk (
        tenant_id,
        organization_id,
        payout_account_id
    ),
    INDEX ix_fund_payout_entry_recharge_fk (
        tenant_id,
        organization_id,
        recharge_order_id
    ),
    INDEX ix_fund_payout_entry_withdrawal_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id
    ),
    INDEX ix_fund_payout_entry_account_time (
        payout_account_id,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wechat_payment_observation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    observation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    payment_id BIGINT NOT NULL,
    observation_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ORGANIZATION',
    source_inbox_id BIGINT NULL,
    source_task_attempt_id BIGINT NULL,
    raw_channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    transaction_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    total_amount_cent BIGINT NULL,
    payer_total_cent BIGINT NULL,
    payer_openid VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    channel_occurred_at DATETIME(3) NULL,
    content_sha256 BINARY(32) NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_payment_observation_uid UNIQUE (observation_uid),
    CONSTRAINT uq_fund_payment_observation_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_fund_payment_observation_attempt
        UNIQUE (source_task_attempt_id),
    CONSTRAINT uq_fund_payment_observation_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_payment_observation_uid_v4 CHECK (
        observation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_payment_observation_type CHECK (
        observation_type IN (
            'CREATE_RESPONSE',
            'CALLBACK',
            'QUERY',
            'CLOSE_RESPONSE',
            'STATEMENT'
        )
    ),
    CONSTRAINT ck_fund_payment_observation_source CHECK (
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
    CONSTRAINT ck_fund_payment_observation_amounts CHECK (
        (total_amount_cent IS NULL OR total_amount_cent >= 0)
        AND (payer_total_cent IS NULL OR payer_total_cent >= 0)
    ),
    CONSTRAINT ck_fund_payment_observation_times CHECK (
        observed_at >= created_at
    ),
    CONSTRAINT fk_fund_payment_observation_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payment_observation_payment
        FOREIGN KEY (tenant_id, organization_id, payment_id)
        REFERENCES fund_wechat_payment (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_payment_observation_payment_fk (
        tenant_id,
        organization_id,
        payment_id
    ),
    INDEX ix_fund_payment_observation_inbox_pending (
        source_scope_kind,
        tenant_id,
        organization_id,
        source_inbox_id
    ),
    INDEX ix_fund_payment_observation_attempt_pending (
        source_scope_kind,
        tenant_id,
        organization_id,
        source_task_attempt_id
    ),
    INDEX ix_fund_payment_observation_timeline (
        payment_id,
        observed_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_wechat_transfer_observation (
    id BIGINT NOT NULL AUTO_INCREMENT,
    observation_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    transfer_id BIGINT NOT NULL,
    observation_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_source_kind VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_scope_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ORGANIZATION',
    source_inbox_id BIGINT NULL,
    source_task_attempt_id BIGINT NULL,
    raw_channel_state VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    api_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    payout_gate_trigger_code VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN api_error_code = 'NOT_ENOUGH' THEN 'NOT_ENOUGH'
                ELSE NULL
            END
        ) STORED,
    terminal_fail_reason VARCHAR(255) NULL,
    out_bill_no VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    transfer_bill_no VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    package_info VARCHAR(2048) CHARACTER SET ascii COLLATE ascii_bin NULL,
    amount_cent BIGINT NULL,
    openid VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    channel_occurred_at DATETIME(3) NULL,
    content_sha256 BINARY(32) NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_transfer_observation_uid UNIQUE (observation_uid),
    CONSTRAINT uq_fund_transfer_observation_inbox UNIQUE (source_inbox_id),
    CONSTRAINT uq_fund_transfer_observation_attempt
        UNIQUE (source_task_attempt_id),
    CONSTRAINT uq_fund_transfer_observation_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT uq_fund_transfer_observation_scope_transfer
        UNIQUE (tenant_id, organization_id, id, transfer_id),
    CONSTRAINT uq_fund_transfer_observation_id_transfer
        UNIQUE (id, transfer_id),
    CONSTRAINT uq_fund_transfer_observation_gate_trigger
        UNIQUE (id, transfer_id, payout_gate_trigger_code),
    CONSTRAINT ck_fund_transfer_observation_uid_v4 CHECK (
        observation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_transfer_observation_type CHECK (
        observation_type IN (
            'SUBMIT_RESPONSE',
            'CALLBACK',
            'QUERY',
            'CANCEL_RESPONSE',
            'STATEMENT'
        )
    ),
    CONSTRAINT ck_fund_transfer_observation_source CHECK (
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
    CONSTRAINT ck_fund_transfer_observation_identity CHECK (
        out_bill_no REGEXP '^[A-Za-z0-9]{8,32}$'
        AND (amount_cent IS NULL OR amount_cent BETWEEN 10 AND 20000)
    ),
    CONSTRAINT ck_fund_transfer_observation_times CHECK (
        observed_at >= created_at
    ),
    CONSTRAINT fk_fund_transfer_observation_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_transfer_observation_transfer
        FOREIGN KEY (tenant_id, organization_id, transfer_id)
        REFERENCES fund_wechat_transfer (tenant_id, organization_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_transfer_observation_transfer_fk (
        tenant_id,
        organization_id,
        transfer_id
    ),
    INDEX ix_fund_transfer_observation_inbox_pending (
        source_scope_kind,
        tenant_id,
        organization_id,
        source_inbox_id
    ),
    INDEX ix_fund_transfer_observation_attempt_pending (
        source_scope_kind,
        tenant_id,
        organization_id,
        source_task_attempt_id
    ),
    INDEX ix_fund_transfer_observation_timeline (
        transfer_id,
        observed_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fund_payout_gate_event (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    merchant_profile_id BIGINT NOT NULL,
    event_type VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    triggering_transfer_observation_id BIGINT NULL,
    triggering_transfer_id BIGINT NULL,
    triggering_api_error_code VARCHAR(16)
        CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN event_type = 'PAUSED' THEN 'NOT_ENOUGH'
                ELSE NULL
            END
        ) STORED,
    original_pause_event_id BIGINT NULL,
    original_pause_event_type VARCHAR(16) CHARACTER SET ascii
        COLLATE ascii_bin NULL,
    restored_by_platform_admin_id BIGINT NULL,
    note VARCHAR(500) NULL,
    occurred_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_payout_gate_event_uid UNIQUE (event_uid),
    CONSTRAINT uq_fund_payout_gate_event_trigger
        UNIQUE (triggering_transfer_observation_id),
    CONSTRAINT uq_fund_payout_gate_event_restore
        UNIQUE (original_pause_event_id),
    CONSTRAINT uq_fund_payout_gate_event_merchant_id
        UNIQUE (merchant_profile_id, id),
    CONSTRAINT uq_fund_payout_gate_event_merchant_type
        UNIQUE (merchant_profile_id, id, event_type),
    CONSTRAINT ck_fund_payout_gate_event_uid_v4 CHECK (
        event_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_payout_gate_event_shape CHECK (
        (
            event_type = 'PAUSED'
            AND triggering_transfer_observation_id IS NOT NULL
            AND triggering_transfer_id IS NOT NULL
            AND original_pause_event_id IS NULL
            AND original_pause_event_type IS NULL
            AND restored_by_platform_admin_id IS NULL
        )
        OR
        (
            event_type = 'RESTORED'
            AND triggering_transfer_observation_id IS NULL
            AND triggering_transfer_id IS NULL
            AND original_pause_event_id IS NOT NULL
            AND original_pause_event_type = 'PAUSED'
            AND restored_by_platform_admin_id IS NOT NULL
        )
    ),
    CONSTRAINT ck_fund_payout_gate_event_times CHECK (
        occurred_at >= created_at
    ),
    CONSTRAINT fk_fund_payout_gate_event_merchant
        FOREIGN KEY (merchant_profile_id)
        REFERENCES fund_wechat_merchant_profile (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_gate_event_observation
        FOREIGN KEY (
            triggering_transfer_observation_id,
            triggering_transfer_id
        )
        REFERENCES fund_wechat_transfer_observation (id, transfer_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_gate_event_not_enough
        FOREIGN KEY (
            triggering_transfer_observation_id,
            triggering_transfer_id,
            triggering_api_error_code
        )
        REFERENCES fund_wechat_transfer_observation (
            id,
            transfer_id,
            payout_gate_trigger_code
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_gate_event_transfer_merchant
        FOREIGN KEY (
            merchant_profile_id,
            triggering_transfer_id
        )
        REFERENCES fund_wechat_transfer (
            merchant_profile_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_gate_event_original
        FOREIGN KEY (
            merchant_profile_id,
            original_pause_event_id,
            original_pause_event_type
        )
        REFERENCES fund_payout_gate_event (
            merchant_profile_id,
            id,
            event_type
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_payout_gate_event_restorer
        FOREIGN KEY (restored_by_platform_admin_id)
        REFERENCES iam_platform_admin (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_fund_payout_gate_event_transfer_merchant_fk (
        merchant_profile_id,
        triggering_transfer_id
    ),
    INDEX ix_fund_payout_gate_event_observation_fk (
        triggering_transfer_observation_id,
        triggering_transfer_id,
        triggering_api_error_code
    ),
    INDEX ix_fund_payout_gate_event_original_fk (
        merchant_profile_id,
        original_pause_event_id,
        original_pause_event_type
    ),
    INDEX ix_fund_payout_gate_event_restorer_fk (
        restored_by_platform_admin_id
    ),
    INDEX ix_fund_payout_gate_event_timeline (
        merchant_profile_id,
        occurred_at,
        id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
