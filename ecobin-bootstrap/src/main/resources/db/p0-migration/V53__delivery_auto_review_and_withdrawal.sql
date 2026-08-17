-- V53 freezes delivery auto-review and delivery-triggered auto withdrawal.
-- Existing organizations keep ALL_MANUAL and auto withdrawal disabled.

ALTER TABLE rec_organization_delivery_config
    DROP CHECK ck_rec_delivery_config_m0,
    ADD CONSTRAINT ck_rec_delivery_config_v53 CHECK (
        review_mode IN (
            'ALL_MANUAL',
            'NORMAL_AUTO_IMMEDIATE',
            'NORMAL_AUTO_AFTER_24H',
            'NORMAL_AUTO_AFTER_48H'
        )
        AND open_balance_floor_cent < 0
        AND max_review_abs_weight_g BETWEEN 1 AND 1000000
    ),
    ADD CONSTRAINT uq_rec_delivery_config_review_ref UNIQUE (
        tenant_id,
        organization_id,
        id,
        version_no,
        review_mode
    );

ALTER TABLE rec_delivery_order
    ADD COLUMN review_mode_snapshot
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'ALL_MANUAL'
        AFTER delivery_config_content_sha256,
    ADD COLUMN automatic_review_due_at DATETIME(3) NULL
        AFTER review_mode_snapshot,
    ADD CONSTRAINT ck_rec_delivery_order_auto_review CHECK (
        (
            review_mode_snapshot = 'ALL_MANUAL'
            AND automatic_review_due_at IS NULL
        )
        OR
        (
            review_mode_snapshot IN (
                'NORMAL_AUTO_IMMEDIATE',
                'NORMAL_AUTO_AFTER_24H',
                'NORMAL_AUTO_AFTER_48H'
            )
            AND automatic_review_due_at IS NOT NULL
            AND automatic_review_due_at >= backend_received_at
        )
    ),
    ADD CONSTRAINT fk_rec_delivery_order_review_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            delivery_config_version_id,
            delivery_config_version_no,
            review_mode_snapshot
        )
        REFERENCES rec_organization_delivery_config (
            tenant_id,
            organization_id,
            id,
            version_no,
            review_mode
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_delivery_order_auto_review (
        tenant_id,
        organization_id,
        review_status,
        automatic_review_due_at,
        id
    );

ALTER TABLE rec_delivery_revision
    DROP CHECK ck_rec_delivery_revision_actor,
    ADD CONSTRAINT ck_rec_delivery_revision_actor_v53 CHECK (
        (
            reviewer_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
        )
        OR
        (
            reviewer_kind = 'STAFF'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
        )
        OR
        (
            reviewer_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
        )
    );

ALTER TABLE fund_organization_withdraw_config
    ADD COLUMN auto_withdrawal_enabled TINYINT NOT NULL DEFAULT 0
        AFTER manual_review_free_threshold_cent,
    ADD COLUMN auto_min_cent BIGINT NULL
        AFTER auto_withdrawal_enabled,
    ADD COLUMN auto_max_cent BIGINT NULL
        AFTER auto_min_cent,
    ADD COLUMN auto_review_free_threshold_cent BIGINT NULL
        AFTER auto_max_cent,
    DROP CHECK ck_fund_withdraw_config_m0,
    ADD CONSTRAINT ck_fund_withdraw_config_v53 CHECK (
        10 <= manual_min_cent
        AND manual_min_cent <= manual_max_cent
        AND manual_max_cent <= hard_limit_cent
        AND hard_limit_cent <= 20000
        AND manual_review_free_threshold_cent BETWEEN 0 AND manual_max_cent
        AND auto_withdrawal_enabled IN (0, 1)
        AND (
            (
                auto_withdrawal_enabled = 0
                AND auto_min_cent IS NULL
                AND auto_max_cent IS NULL
                AND auto_review_free_threshold_cent IS NULL
            )
            OR
            (
                auto_withdrawal_enabled = 1
                AND auto_min_cent IS NOT NULL
                AND auto_max_cent IS NOT NULL
                AND auto_review_free_threshold_cent IS NOT NULL
                AND auto_min_cent BETWEEN 10 AND auto_max_cent
                AND auto_max_cent <= hard_limit_cent
                AND auto_review_free_threshold_cent
                    BETWEEN 0 AND auto_max_cent
            )
        )
    );

ALTER TABLE fund_withdrawal_order
    DROP FOREIGN KEY fk_fund_withdrawal_config,
    DROP CHECK ck_fund_withdrawal_amounts,
    RENAME COLUMN manual_min_cent_snapshot
        TO effective_min_cent_snapshot,
    RENAME COLUMN manual_max_cent_snapshot
        TO effective_max_cent_snapshot,
    RENAME COLUMN manual_review_free_threshold_cent_snapshot
        TO effective_review_free_threshold_cent_snapshot,
    ADD COLUMN source_type
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'MANUAL'
        AFTER withdrawal_order_no,
    ADD COLUMN source_delivery_order_no
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER source_type,
    ADD COLUMN review_required_at_creation TINYINT NOT NULL DEFAULT 1
        AFTER source_delivery_order_no,
    ADD CONSTRAINT ck_fund_withdrawal_amounts_v53 CHECK (
        10 <= effective_min_cent_snapshot
        AND effective_min_cent_snapshot <= amount_cent
        AND amount_cent <= effective_max_cent_snapshot
        AND effective_max_cent_snapshot <= hard_limit_cent_snapshot
        AND hard_limit_cent_snapshot <= 20000
        AND effective_review_free_threshold_cent_snapshot
            BETWEEN 0 AND effective_max_cent_snapshot
    ),
    ADD CONSTRAINT ck_fund_withdrawal_source_v53 CHECK (
        review_required_at_creation IN (0, 1)
        AND (
            (
                source_type = 'MANUAL'
                AND source_delivery_order_no IS NULL
            )
            OR
            (
                source_type = 'DELIVERY_AUTO'
                AND source_delivery_order_no IS NOT NULL
                AND CHAR_LENGTH(TRIM(source_delivery_order_no))
                    BETWEEN 8 AND 64
            )
        )
    ),
    ADD CONSTRAINT fk_fund_withdrawal_config_v53
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdraw_config_id,
            withdraw_config_version_no
        )
        REFERENCES fund_organization_withdraw_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_withdrawal_source (
        tenant_id,
        organization_id,
        source_type,
        source_delivery_order_no,
        id
    );

ALTER TABLE fund_withdrawal_review
    DROP CHECK ck_fund_withdrawal_review_actor,
    ADD CONSTRAINT ck_fund_withdrawal_review_actor_v53 CHECK (
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
        OR
        (
            reviewer_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
        )
    );

CREATE TABLE fund_delivery_auto_withdrawal_decision (
    id BIGINT NOT NULL AUTO_INCREMENT,
    decision_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    delivery_revision_id BIGINT NOT NULL,
    source_delivery_order_no
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    withdraw_config_id BIGINT NOT NULL,
    withdraw_config_version_no BIGINT NOT NULL,
    reward_amount_cent BIGINT NOT NULL,
    outcome VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    skip_reason VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    withdrawal_order_id BIGINT NULL,
    decided_at DATETIME(3) NOT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_fund_auto_withdraw_decision_uid UNIQUE (decision_uid),
    CONSTRAINT uq_fund_auto_withdraw_revision UNIQUE (delivery_revision_id),
    CONSTRAINT uq_fund_auto_withdraw_order UNIQUE (withdrawal_order_id),
    CONSTRAINT uq_fund_auto_withdraw_scope_id
        UNIQUE (tenant_id, organization_id, id),
    CONSTRAINT ck_fund_auto_withdraw_decision_uid CHECK (
        decision_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_fund_auto_withdraw_outcome CHECK (
        reward_amount_cent > 0
        AND outcome IN (
            'CREATED_REVIEW_REQUIRED',
            'CREATED_READY_TO_SUBMIT',
            'SKIPPED'
        )
        AND (
            (
                outcome = 'SKIPPED'
                AND skip_reason IS NOT NULL
                AND withdrawal_order_id IS NULL
            )
            OR
            (
                outcome <> 'SKIPPED'
                AND skip_reason IS NULL
                AND withdrawal_order_id IS NOT NULL
            )
        )
    ),
    CONSTRAINT ck_fund_auto_withdraw_times CHECK (
        decided_at >= created_at
    ),
    CONSTRAINT fk_fund_auto_withdraw_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_auto_withdraw_revision
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
    CONSTRAINT fk_fund_auto_withdraw_config
        FOREIGN KEY (
            tenant_id,
            organization_id,
            withdraw_config_id,
            withdraw_config_version_no
        )
        REFERENCES fund_organization_withdraw_config (
            tenant_id,
            organization_id,
            id,
            version_no
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_fund_auto_withdraw_order
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
    INDEX ix_fund_auto_withdraw_org_time (
        tenant_id,
        organization_id,
        decided_at,
        id
    ),
    INDEX ix_fund_auto_withdraw_config_fk (
        tenant_id,
        organization_id,
        withdraw_config_id,
        withdraw_config_version_no
    ),
    INDEX ix_fund_auto_withdraw_order_fk (
        tenant_id,
        organization_id,
        withdrawal_order_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_category,
    ADD CONSTRAINT ck_ops_task_category_v53 CHECK (
        task_category IN (
            'BUSINESS_INTENT',
            'INBOX_PROCESSING',
            'TIMER',
            'RECONCILIATION'
        )
        AND execution_lane IN ('DEVICE', 'FUNDS', 'RECYCLING')
    );
